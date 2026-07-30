"""Strict Bilibili and YouTube source adapters over controlled yt-dlp operations."""

from __future__ import annotations

import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol, cast
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from yt_dlp.utils import ExtractorError

from vtnote.artifacts import validate_source_subtitle
from vtnote.config import Settings
from vtnote.media import (
    CommandRunner,
    FfmpegBinaries,
    FfmpegMediaProcessor,
    MediaInfo,
)
from vtnote.platform_transport import (
    PinnedHttpsTransport,
    SourceHttpRequest,
    TransportSecurityError,
)
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService
from vtnote.sources import (
    AudioOutcome,
    PlatformSourceError,
    SourceAdapter,
    SourceCapabilityError,
    SourceProbeResult,
    SubtitleCandidateError,
    SubtitleKind,
    SubtitleOutcome,
    SubtitleTrack,
    make_subtitle_track,
)
from vtnote.url_security import (
    Resolver,
    extracted_resource_hosts,
    extractor_aux_host_policy,
    page_host_policy,
    resource_host_policy,
)
from vtnote.youtube_runtime import YoutubeRuntime, inspect_youtube_runtime
from vtnote.ytdlp_bridge import (
    BoundTransportScope,
    build_controlled_platform_ytdlp,
)


Platform = Literal["bilibili", "youtube"]
_PLATFORMS = frozenset({"bilibili", "youtube"})
_PLATFORM_ERROR_CODES = frozenset(
    {
        "removed",
        "temporary",
        "auth_required",
        "region_restricted",
        "unsupported",
        "adapter_drift",
        "invalid_content",
    }
)
_SUBTITLE_EXTENSIONS = frozenset({"vtt", "srt", "ass", "json"})
_AUDIO_EXTENSIONS = frozenset(
    {"wav", "mp3", "m4a", "flac", "ogg", "opus", "webm"}
)
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_BILIBILI_ID = re.compile(r"^(?:BV[A-Za-z0-9]+|av[0-9]+)$", re.IGNORECASE)
_MAX_SUBTITLE_BYTES = 64 * 1024 * 1024
_MAX_AUDIO_BYTES = 8 * 1024 * 1024 * 1024


class YtDlpOperations(Protocol):
    """The narrow operation boundary implemented by the controlled transport."""

    def extract(self, canonical_url: str) -> dict[str, object]: ...

    def fetch_resource(self, resource_url: str, *, max_bytes: int) -> bytes: ...

    def download_resource(
        self,
        resource_url: str,
        target: Path,
        *,
        max_bytes: int,
    ) -> None: ...


class MediaValidator(Protocol):
    def validate_media(self, path: Path) -> MediaInfo: ...


class AudioRegistrar(Protocol):
    def register_downloaded_audio(self, item_id: str, path: Path) -> str: ...


class YtDlpOperationFailure(RuntimeError):
    """A transport/extractor failure stripped down to a closed machine code."""

    def __init__(self, code: str) -> None:
        if code not in _PLATFORM_ERROR_CODES:
            raise ValueError("invalid yt-dlp operation failure code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _InitialSource:
    extraction_url: str
    expected_id: str | None


@dataclass(frozen=True, slots=True)
class _SubtitleResource:
    track: SubtitleTrack
    url: str


@dataclass(frozen=True, slots=True)
class _Extraction:
    canonical_url: str
    title: str
    duration_ms: int | None
    tracks: tuple[_SubtitleResource, ...]
    info: dict[str, object]


def classify_extractor_failure(error: BaseException) -> str:
    """Map arbitrary extractor text to a safe, closed public error code."""

    message = str(error).casefold()
    if any(
        marker in message
        for marker in (
            "not made this video available in your country",
            "not available in your country",
            "geo-restricted",
            "geo restricted",
            "region restricted",
        )
    ):
        return "region_restricted"
    if any(
        marker in message
        for marker in (
            "sign in",
            "log in",
            "login",
            "private video",
            "members-only",
            "confirm your age",
            "authentication",
        )
    ):
        return "auth_required"
    if any(marker in message for marker in ("removed", "deleted", "no longer available")):
        return "removed"
    if any(
        marker in message
        for marker in (
            "http error 429",
            "http error 502",
            "http error 503",
            "http error 504",
            "service unavailable",
            "temporarily unavailable",
            "timed out",
            "timeout",
        )
    ):
        return "temporary"
    if "unsupported url" in message or "unsupported site" in message:
        return "unsupported"
    if "invalid subtitle" in message or "invalid content" in message:
        return "invalid_content"
    return "adapter_drift"


def _safe_https_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PlatformSourceError("adapter_drift")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise PlatformSourceError("adapter_drift") from None
    if (
        parts.scheme.casefold() != "https"
        or not parts.hostname
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise PlatformSourceError("adapter_drift")
    return value


def _youtube_initial(source: str) -> _InitialSource:
    try:
        parts = urlsplit(source)
        port = parts.port
    except ValueError:
        raise PlatformSourceError("unsupported") from None
    if (
        parts.scheme.casefold() != "https"
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise PlatformSourceError("unsupported")
    host = (parts.hostname or "").casefold().rstrip(".")
    path = parts.path.rstrip("/")
    video_id: str | None = None
    if host in {"youtube.com", "www.youtube.com"} and path == "/watch":
        values = parse_qs(parts.query, keep_blank_values=True).get("v", ())
        if len(values) == 1:
            video_id = values[0]
    elif host == "youtu.be":
        components = [part for part in path.split("/") if part]
        if len(components) == 1:
            video_id = components[0]
    elif host in {"youtube.com", "www.youtube.com"}:
        components = [part for part in path.split("/") if part]
        if len(components) == 2 and components[0] in {"shorts", "live"}:
            video_id = components[1]
    if video_id is None or _YOUTUBE_ID.fullmatch(video_id) is None:
        raise PlatformSourceError("unsupported")
    canonical = f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"
    return _InitialSource(canonical, video_id)


def _bilibili_page(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        values = parse_qs(urlsplit(value).query, keep_blank_values=True).get("p", ())
    except ValueError:
        return None
    if len(values) != 1 or not values[0].isdigit():
        return None
    page = int(values[0])
    return page if page > 0 else None


def _canonical_bilibili(video_id: str, page: int | None) -> str:
    canonical = f"https://www.bilibili.com/video/{video_id}"
    if page is not None:
        canonical = f"{canonical}?{urlencode({'p': page})}"
    return canonical


def _bilibili_initial(source: str) -> _InitialSource:
    try:
        parts = urlsplit(source)
        port = parts.port
    except ValueError:
        raise PlatformSourceError("unsupported") from None
    if (
        parts.scheme.casefold() != "https"
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise PlatformSourceError("unsupported")
    host = (parts.hostname or "").casefold().rstrip(".")
    components = [part for part in parts.path.split("/") if part]
    if host == "b23.tv" and len(components) == 1:
        return _InitialSource(
            urlunsplit(("https", "b23.tv", f"/{components[0]}", "", "")),
            None,
        )
    if (
        host != "www.bilibili.com"
        or len(components) != 2
        or components[0] != "video"
        or _BILIBILI_ID.fullmatch(components[1]) is None
    ):
        raise PlatformSourceError("unsupported")
    video_id = components[1]
    if video_id[:2].casefold() == "av":
        video_id = f"av{int(video_id[2:])}"
    page = _bilibili_page(source)
    return _InitialSource(
        _canonical_bilibili(video_id, page),
        video_id.casefold(),
    )


def _duration_ms(value: object) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise PlatformSourceError("adapter_drift")
    return round(value * 1000)


def _title(value: object) -> str:
    if not isinstance(value, str):
        raise PlatformSourceError("adapter_drift")
    normalized = value.strip()
    if not normalized or len(normalized) > 4096:
        raise PlatformSourceError("adapter_drift")
    return normalized


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, object], value)


class YtDlpSourceAdapter(SourceAdapter):
    """One platform adapter with no persistent raw extractor or resource URLs."""

    def __init__(
        self,
        *,
        platform: str,
        operations: YtDlpOperations,
        paths: StoragePaths,
        media_validator: MediaValidator,
        audio_registrar: AudioRegistrar,
    ) -> None:
        if platform not in _PLATFORMS:
            raise ValueError("unsupported yt-dlp source platform")
        self.platform = cast(Platform, platform)
        self.operations = operations
        self.paths = paths
        self.media_validator = media_validator
        self.audio_registrar = audio_registrar

    def _initial(self, source: str) -> _InitialSource:
        if not isinstance(source, str):
            raise PlatformSourceError("unsupported")
        if self.platform == "youtube":
            return _youtube_initial(source)
        return _bilibili_initial(source)

    def _run_extract(self, url: str) -> dict[str, object]:
        try:
            info = self.operations.extract(url)
        except YtDlpOperationFailure as error:
            raise PlatformSourceError(error.code) from None
        except ExtractorError as error:
            raise PlatformSourceError(classify_extractor_failure(error)) from None
        except PlatformSourceError:
            raise
        except Exception:
            raise PlatformSourceError("adapter_drift") from None
        if not isinstance(info, dict):
            raise PlatformSourceError("adapter_drift")
        return info

    def _identity_and_url(
        self,
        info: dict[str, object],
        initial: _InitialSource,
    ) -> str:
        extractor = info.get("extractor_key")
        if not isinstance(extractor, str) or extractor.casefold() != self.platform:
            raise PlatformSourceError("adapter_drift")
        raw_id = info.get("id")
        if not isinstance(raw_id, str):
            raise PlatformSourceError("adapter_drift")
        if self.platform == "youtube":
            if _YOUTUBE_ID.fullmatch(raw_id) is None:
                raise PlatformSourceError("adapter_drift")
            normalized_id = raw_id
            canonical = f"https://www.youtube.com/watch?{urlencode({'v': raw_id})}"
        else:
            if raw_id.casefold().startswith("av"):
                candidate = raw_id
            elif raw_id.isdigit():
                candidate = f"av{int(raw_id)}"
            else:
                candidate = raw_id
            if _BILIBILI_ID.fullmatch(candidate) is None:
                raise PlatformSourceError("adapter_drift")
            normalized_id = candidate.casefold()
            canonical = _canonical_bilibili(
                candidate,
                _bilibili_page(info.get("webpage_url"))
                or _bilibili_page(initial.extraction_url),
            )
        if initial.expected_id is not None and (
            normalized_id.casefold() != initial.expected_id.casefold()
        ):
            raise PlatformSourceError("adapter_drift")
        return canonical

    def _tracks(self, info: dict[str, object]) -> tuple[_SubtitleResource, ...]:
        resources: list[_SubtitleResource] = []
        ordinal = 0
        for group_name, default_kind in (
            ("subtitles", None),
            ("automatic_captions", "automatic"),
        ):
            group = _mapping(info.get(group_name))
            for language, raw_variants in group.items():
                if not isinstance(language, str) or not isinstance(raw_variants, list):
                    raise PlatformSourceError("adapter_drift")
                for raw_variant in raw_variants:
                    current_ordinal = ordinal
                    ordinal += 1
                    if not isinstance(raw_variant, dict):
                        raise PlatformSourceError("adapter_drift")
                    variant = cast(dict[str, object], raw_variant)
                    extension = variant.get("ext")
                    if not isinstance(extension, str):
                        continue
                    extension = extension.casefold()
                    is_live_chat = (
                        language.casefold() == "live_chat"
                        or bool(variant.get("is_live_chat"))
                    )
                    is_translated = bool(variant.get("is_translated"))
                    if (
                        extension not in _SUBTITLE_EXTENSIONS
                        or is_live_chat
                        or is_translated
                    ):
                        continue
                    raw_kind = variant.get("kind", default_kind)
                    if raw_kind in {"manual", "automatic", "unconfirmed"}:
                        kind = cast(SubtitleKind, raw_kind)
                    elif raw_kind is None:
                        kind = (
                            "unconfirmed"
                            if self.platform == "bilibili"
                            else "manual"
                        )
                    else:
                        kind = "unconfirmed"
                    try:
                        resource_url = _safe_https_url(variant.get("url"))
                        track = make_subtitle_track(
                            source_kind=self.platform,
                            language=language,
                            format=extension,
                            kind=kind,
                            stable_ordinal=current_ordinal,
                            is_translated=False,
                            is_live_chat=False,
                        )
                    except ValueError:
                        continue
                    resources.append(_SubtitleResource(track, resource_url))
        return tuple(resources)

    def _extraction(self, source: str) -> _Extraction:
        initial = self._initial(source)
        info = self._run_extract(initial.extraction_url)
        return _Extraction(
            canonical_url=self._identity_and_url(info, initial),
            title=_title(info.get("title")),
            duration_ms=_duration_ms(info.get("duration")),
            tracks=self._tracks(info),
            info=info,
        )

    @staticmethod
    def _same_probe(
        probe: SourceProbeResult,
        extraction: _Extraction,
    ) -> bool:
        return (
            probe.canonical_url == extraction.canonical_url
            and probe.title == extraction.title
            and probe.duration_ms == extraction.duration_ms
            and probe.subtitle_tracks
            == tuple(resource.track for resource in extraction.tracks)
        )

    def probe(self, canonical_source: str) -> SourceProbeResult:
        extraction = self._extraction(canonical_source)
        return SourceProbeResult(
            source_kind=self.platform,
            canonical_url=extraction.canonical_url,
            title=extraction.title,
            duration_ms=extraction.duration_ms,
            subtitle_tracks=tuple(
                resource.track for resource in extraction.tracks
            ),
            redirect_trace=(extraction.canonical_url,),
        )

    def fetch_subtitle(
        self,
        probe: SourceProbeResult,
        track: SubtitleTrack,
    ) -> SubtitleOutcome:
        if (
            probe.source_kind != self.platform
            or track.source_kind != self.platform
            or probe.canonical_url is None
        ):
            raise PlatformSourceError("adapter_drift")
        extraction = self._extraction(probe.canonical_url)
        if not self._same_probe(probe, extraction):
            raise PlatformSourceError("adapter_drift")
        matching = [
            resource
            for resource in extraction.tracks
            if resource.track == track
        ]
        if len(matching) != 1:
            raise PlatformSourceError("adapter_drift")
        try:
            content = self.operations.fetch_resource(
                matching[0].url,
                max_bytes=_MAX_SUBTITLE_BYTES,
            )
        except YtDlpOperationFailure as error:
            if error.code in {"removed", "temporary"}:
                raise SubtitleCandidateError("subtitle_unavailable") from None
            if error.code == "invalid_content":
                raise SubtitleCandidateError("subtitle_invalid") from None
            raise PlatformSourceError(error.code) from None
        except Exception:
            raise PlatformSourceError("adapter_drift") from None
        if not isinstance(content, bytes) or not content:
            raise SubtitleCandidateError("subtitle_empty")
        try:
            validate_source_subtitle(track.format, content)
        except ValueError:
            raise SubtitleCandidateError("subtitle_invalid") from None
        return SubtitleOutcome(track, content)

    @staticmethod
    def _best_audio(info: dict[str, object]) -> tuple[str, str]:
        candidates: list[tuple[float, int, str, str]] = []
        formats = info.get("formats")
        if not isinstance(formats, list):
            raise PlatformSourceError("adapter_drift")
        for ordinal, raw_format in enumerate(formats):
            if not isinstance(raw_format, dict):
                continue
            entry = cast(dict[str, object], raw_format)
            extension = entry.get("ext")
            if (
                entry.get("vcodec") != "none"
                or entry.get("acodec") in {None, "none"}
                or not isinstance(extension, str)
                or extension.casefold() not in _AUDIO_EXTENSIONS
            ):
                continue
            try:
                url = _safe_https_url(entry.get("url"))
            except PlatformSourceError:
                continue
            abr = entry.get("abr")
            score = (
                float(abr)
                if isinstance(abr, (int, float))
                and not isinstance(abr, bool)
                and math.isfinite(abr)
                else -1.0
            )
            candidates.append((score, -ordinal, url, extension.casefold()))
        if not candidates:
            raise PlatformSourceError("invalid_content")
        _, _, url, extension = max(candidates)
        return url, extension

    def fetch_audio(
        self,
        probe: SourceProbeResult,
        item_id: str,
    ) -> AudioOutcome:
        if probe.source_kind != self.platform or probe.canonical_url is None:
            raise PlatformSourceError("adapter_drift")
        extraction = self._extraction(probe.canonical_url)
        if not self._same_probe(probe, extraction):
            raise PlatformSourceError("adapter_drift")
        resource_url, extension = self._best_audio(extraction.info)
        try:
            destination = self.paths.downloaded_audio(item_id, extension)
            self.paths.assert_runtime_destination(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.paths.assert_runtime_destination(destination)
            if destination.exists():
                raise PlatformSourceError("invalid_content")
            self.operations.download_resource(
                resource_url,
                destination,
                max_bytes=_MAX_AUDIO_BYTES,
            )
            if (
                not destination.is_file()
                or destination.stat().st_size <= 0
                or destination.stat().st_size > _MAX_AUDIO_BYTES
            ):
                raise PlatformSourceError("invalid_content")
            media_info = self.media_validator.validate_media(destination)
            asset_id = self.audio_registrar.register_downloaded_audio(
                item_id,
                destination,
            )
        except PlatformSourceError:
            raise
        except YtDlpOperationFailure as error:
            raise PlatformSourceError(error.code) from None
        except (OSError, UnsafePathError, ValueError):
            raise PlatformSourceError("invalid_content") from None
        except Exception:
            raise PlatformSourceError("adapter_drift") from None
        if not isinstance(asset_id, str) or not asset_id:
            raise PlatformSourceError("adapter_drift")
        return AudioOutcome(
            asset_id=asset_id,
            format=extension,
            duration_ms=media_info.duration_ms,
            size_bytes=media_info.size_bytes,
        )


class PlatformSourceRegistry(SourceAdapter):
    """Route supported platform URLs while preserving capability-specific errors."""

    def __init__(
        self,
        *,
        bilibili: SourceAdapter | None,
        youtube: SourceAdapter | None,
        youtube_unavailable_code: str = "youtube_runtime_unavailable",
    ) -> None:
        self.bilibili = bilibili
        self.youtube = youtube
        self.youtube_unavailable_code = youtube_unavailable_code

    @staticmethod
    def _kind(source: str) -> Platform:
        try:
            host = (urlsplit(source).hostname or "").casefold().rstrip(".")
        except (TypeError, ValueError):
            raise PlatformSourceError("unsupported") from None
        if host in {"youtube.com", "www.youtube.com", "youtu.be"}:
            return "youtube"
        if host in {"www.bilibili.com", "b23.tv"}:
            return "bilibili"
        raise PlatformSourceError("unsupported")

    def _adapter(self, kind: Platform) -> SourceAdapter:
        selected = self.youtube if kind == "youtube" else self.bilibili
        if selected is None:
            code = (
                self.youtube_unavailable_code
                if kind == "youtube"
                else "adapter_unavailable"
            )
            raise SourceCapabilityError(code)
        return selected

    def probe(self, canonical_source: str) -> SourceProbeResult:
        return self._adapter(self._kind(canonical_source)).probe(canonical_source)

    def fetch_subtitle(
        self,
        probe: SourceProbeResult,
        track: SubtitleTrack,
    ) -> SubtitleOutcome:
        if probe.source_kind not in {"bilibili", "youtube"}:
            raise PlatformSourceError("unsupported")
        return self._adapter(cast(Platform, probe.source_kind)).fetch_subtitle(
            probe,
            track,
        )

    def fetch_audio(
        self,
        probe: SourceProbeResult,
        item_id: str,
    ) -> AudioOutcome:
        if probe.source_kind not in {"bilibili", "youtube"}:
            raise PlatformSourceError("unsupported")
        return self._adapter(cast(Platform, probe.source_kind)).fetch_audio(
            probe,
            item_id,
        )


class ControlledYtDlpOperations:
    """Real operations using the pinned transport and ephemeral exact URLs."""

    def __init__(
        self,
        *,
        platform: Platform,
        transport: PinnedHttpsTransport,
        output_root: Path,
        youtube_runtime: YoutubeRuntime | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if platform not in _PLATFORMS:
            raise ValueError("invalid controlled yt-dlp platform")
        if platform == "youtube" and youtube_runtime is None:
            raise ValueError("YouTube operations require a managed runtime")
        if platform == "bilibili" and youtube_runtime is not None:
            raise ValueError("Bilibili operations cannot use a YouTube runtime")
        self.platform = platform
        self.transport = transport
        self.output_root = Path(output_root)
        self.youtube_runtime = youtube_runtime
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._current = threading.local()

    @staticmethod
    def _resource_urls_from(info: dict[str, object]) -> frozenset[str]:
        urls: set[str] = set()
        for group_name in ("subtitles", "automatic_captions"):
            group = info.get(group_name)
            if not isinstance(group, dict):
                continue
            for variants in group.values():
                if not isinstance(variants, list):
                    continue
                for variant in variants:
                    if not isinstance(variant, dict):
                        continue
                    try:
                        urls.add(_safe_https_url(variant.get("url")))
                    except PlatformSourceError:
                        continue
        formats = info.get("formats")
        if isinstance(formats, list):
            for entry in formats:
                if not isinstance(entry, dict):
                    continue
                try:
                    urls.add(_safe_https_url(entry.get("url")))
                except PlatformSourceError:
                    continue
        return frozenset(urls)

    def extract(self, canonical_url: str) -> dict[str, object]:
        scope = BoundTransportScope.for_probe(
            page_host_policy(self.platform),
            extractor_aux_host_policy(self.platform),
        )
        try:
            bridge = build_controlled_platform_ytdlp(
                self.transport,
                self.output_root,
                scope=scope,
                runtime=self.youtube_runtime,
            )
        except (OSError, RuntimeError, TransportSecurityError, ValueError):
            raise YtDlpOperationFailure("adapter_drift") from None
        try:
            info = bridge.probe(canonical_url)
        except Exception as error:
            raise YtDlpOperationFailure(classify_extractor_failure(error)) from None
        finally:
            bridge.close()
        if not isinstance(info, dict):
            raise YtDlpOperationFailure("adapter_drift")
        resources = self._resource_urls_from(cast(dict[str, object], info))
        expires_at = self.clock() + timedelta(minutes=10)
        self._current.resource_urls = resources
        self._current.expires_at = expires_at
        return cast(dict[str, object], info)

    def _resource_policy(self, resource_url: str):
        urls = getattr(self._current, "resource_urls", frozenset())
        expiry = getattr(self._current, "expires_at", None)
        if (
            resource_url not in urls
            or not isinstance(expiry, datetime)
            or expiry <= self.clock()
        ):
            raise YtDlpOperationFailure("adapter_drift")
        try:
            host = urlsplit(resource_url).hostname
            if not host:
                raise ValueError
            return resource_host_policy(
                self.platform,
                extracted_resource_hosts(frozenset({host})),
                expires_at=expiry,
            )
        except ValueError:
            raise YtDlpOperationFailure("adapter_drift") from None

    @staticmethod
    def _status_error(status: int) -> str | None:
        if status in {401, 403}:
            return "auth_required"
        if status in {404, 410}:
            return "removed"
        if status == 429 or 500 <= status <= 599:
            return "temporary"
        if status < 200 or status >= 300:
            return "invalid_content"
        return None

    def _request(self, resource_url: str, *, max_bytes: int):
        try:
            response = self.transport.request(
                SourceHttpRequest(
                    url=resource_url,
                    max_wire_bytes=max_bytes,
                    max_decoded_bytes=max_bytes,
                ),
                self._resource_policy(resource_url),
            )
        except YtDlpOperationFailure:
            raise
        except TransportSecurityError:
            raise YtDlpOperationFailure("adapter_drift") from None
        except (OSError, ValueError):
            raise YtDlpOperationFailure("temporary") from None
        code = self._status_error(response.status)
        if code is not None:
            response.close()
            raise YtDlpOperationFailure(code)
        return response

    def fetch_resource(self, resource_url: str, *, max_bytes: int) -> bytes:
        response = self._request(resource_url, max_bytes=max_bytes)
        try:
            content = response.read()
        except TransportSecurityError:
            raise YtDlpOperationFailure("adapter_drift") from None
        except (OSError, ValueError):
            raise YtDlpOperationFailure("temporary") from None
        finally:
            response.close()
        if not isinstance(content, bytes) or len(content) > max_bytes:
            raise YtDlpOperationFailure("invalid_content")
        return content

    def download_resource(
        self,
        resource_url: str,
        target: Path,
        *,
        max_bytes: int,
    ) -> None:
        response = self._request(resource_url, max_bytes=max_bytes)
        written = 0
        destination_path = Path(target)
        staging = destination_path.with_name(
            f".{destination_path.name}.{uuid4()}.partial"
        )
        try:
            if destination_path.exists():
                raise YtDlpOperationFailure("invalid_content")
            with staging.open("xb") as destination:
                for chunk in response:
                    written += len(chunk)
                    if written > max_bytes:
                        raise YtDlpOperationFailure("invalid_content")
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            os.rename(staging, destination_path)
        except YtDlpOperationFailure:
            raise
        except TransportSecurityError:
            raise YtDlpOperationFailure("adapter_drift") from None
        except (OSError, ValueError):
            raise YtDlpOperationFailure("temporary") from None
        finally:
            response.close()
        if written <= 0:
            raise YtDlpOperationFailure("invalid_content")


class FfmpegMediaValidator:
    def __init__(self, processor: object) -> None:
        self.processor = processor

    def validate_media(self, path: Path) -> MediaInfo:
        probe_local = getattr(self.processor, "probe_local", None)
        if not callable(probe_local):
            raise ValueError("media validator is unavailable")
        result = probe_local(path)
        if not isinstance(result, MediaInfo):
            raise ValueError("media validator returned invalid data")
        return result


class RuntimeAssetAudioRegistrar:
    """Open a short database session only when a completed audio file is owned."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], object],
        paths: StoragePaths,
    ) -> None:
        self.session_factory = session_factory
        self.paths = paths

    def register_downloaded_audio(self, item_id: str, path: Path) -> str:
        session = self.session_factory()
        close = getattr(session, "close", None)
        try:
            assets = RuntimeAssetService(session, self.paths)  # type: ignore[arg-type]
            view = assets.register_staged(
                item_id=item_id,
                role="downloaded_audio",
                relative_path=self.paths.runtime_relative(path),
            )
            commit = getattr(session, "commit", None)
            if not callable(commit):
                raise RuntimeAssetError("session_commit_unavailable")
            commit()
            return view.id
        except Exception:
            rollback = getattr(session, "rollback", None)
            if callable(rollback):
                rollback()
            raise
        finally:
            if callable(close):
                close()


def build_default_platform_registry(
    *,
    settings: Settings,
    resolver: Resolver,
    session_factory: Callable[[], object],
) -> PlatformSourceRegistry:
    """Build production adapters without downloading or probing any platform."""

    paths = StoragePaths.from_settings(settings)
    transport = PinnedHttpsTransport(resolver=resolver)
    media_validator = FfmpegMediaValidator(
        FfmpegMediaProcessor(
            runner=CommandRunner(),
            binaries=FfmpegBinaries.discover(),
        )
    )
    registrar = RuntimeAssetAudioRegistrar(
        session_factory=session_factory,
        paths=paths,
    )
    bilibili = YtDlpSourceAdapter(
        platform="bilibili",
        operations=ControlledYtDlpOperations(
            platform="bilibili",
            transport=transport,
            output_root=paths.runtime("yt-dlp", "bilibili"),
        ),
        paths=paths,
        media_validator=media_validator,
        audio_registrar=registrar,
    )
    runtime_status = inspect_youtube_runtime(settings)
    youtube: YtDlpSourceAdapter | None = None
    if runtime_status.youtube_ready and runtime_status.runtime is not None:
        youtube = YtDlpSourceAdapter(
            platform="youtube",
            operations=ControlledYtDlpOperations(
                platform="youtube",
                transport=transport,
                output_root=paths.runtime("yt-dlp", "youtube"),
                youtube_runtime=runtime_status.runtime,
            ),
            paths=paths,
            media_validator=media_validator,
            audio_registrar=registrar,
        )
    return PlatformSourceRegistry(
        bilibili=bilibili,
        youtube=youtube,
        youtube_unavailable_code="youtube_runtime_unavailable",
    )
