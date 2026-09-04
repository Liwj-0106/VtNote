"""Strict platform source adapters over controlled yt-dlp operations."""

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
from vtnote.bilibili_collections import (
    BilibiliCollectionAdapter,
    request_bilibili_json,
)
from vtnote.config import Settings
from vtnote.media import (
    CommandRunner,
    FfmpegBinaries,
    FfmpegMediaProcessor,
    MediaInfo,
)
from vtnote.platform_transport import (
    LoopbackHttpProxyConnector,
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
    SourceCollectionProbeResult,
    SourceProbeResult,
    SubtitleCandidateError,
    SubtitleKind,
    SubtitleOutcome,
    SubtitleTrack,
    ThumbnailOutcome,
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
    BrowserCookieStore,
    BoundTransportScope,
    NetscapeCookieFileStore,
    PlatformCookieStore,
    build_controlled_platform_ytdlp,
    controlled_public_headers,
)


Platform = Literal["bilibili", "douyin", "youtube"]
_PLATFORMS = frozenset({"bilibili", "douyin", "youtube"})
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
    {"wav", "mp3", "m4a", "flac", "ogg", "opus", "webm", "mp4"}
)
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_BILIBILI_ID = re.compile(r"^(?:BV[A-Za-z0-9]+|av[0-9]+)$", re.IGNORECASE)
_DOUYIN_ID = re.compile(r"^[0-9]{6,32}$")
_DOUYIN_SHORT_CODE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")
_MAX_SUBTITLE_BYTES = 64 * 1024 * 1024
_MAX_THUMBNAIL_BYTES = 16 * 1024 * 1024
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


def _transport_failure_code(error: TransportSecurityError) -> str:
    if error.category in {"connection_failed", "read_failed"}:
        return "temporary"
    return "adapter_drift"


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
            "fresh cookies",
            "cookies are needed",
            "could not copy chrome cookie database",
            "could not copy edge cookie database",
            "failed to load cookies",
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
            "unable to extract initial state",
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


def _canonical_douyin(video_id: str) -> str:
    return f"https://www.douyin.com/video/{video_id}"


def _douyin_search_modal_id(
    source: str,
    components: list[str],
) -> str | None:
    reviewed_path = (
        len(components) >= 2
        and components[0] == "search"
    ) or (
        len(components) >= 4
        and components[0] == "video"
        and _DOUYIN_ID.fullmatch(components[1]) is not None
        and components[2] == "search"
    )
    if not reviewed_path:
        return None
    try:
        values = parse_qs(
            urlsplit(source).query,
            keep_blank_values=True,
        ).get("modal_id", ())
    except ValueError:
        return None
    if len(values) != 1 or _DOUYIN_ID.fullmatch(values[0]) is None:
        return None
    return values[0]


def _douyin_initial(source: str) -> _InitialSource:
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
    if (
        host == "v.douyin.com"
        and len(components) == 1
        and _DOUYIN_SHORT_CODE.fullmatch(components[0]) is not None
    ):
        return _InitialSource(
            urlunsplit(("https", "v.douyin.com", f"/{components[0]}/", "", "")),
            None,
        )
    if host in {"douyin.com", "www.douyin.com"}:
        modal_id = _douyin_search_modal_id(source, components)
        if modal_id is not None:
            return _InitialSource(_canonical_douyin(modal_id), modal_id)
    if (
        host not in {"douyin.com", "www.douyin.com"}
        or len(components) != 2
        or components[0] != "video"
        or _DOUYIN_ID.fullmatch(components[1]) is None
    ):
        raise PlatformSourceError("unsupported")
    return _InitialSource(_canonical_douyin(components[1]), components[1])


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


def _optional_text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return None
    return normalized[:maximum]


def _published_date(info: dict[str, object]) -> str | None:
    raw_timestamp = info.get("release_timestamp", info.get("timestamp"))
    if (
        not isinstance(raw_timestamp, bool)
        and isinstance(raw_timestamp, (int, float))
        and math.isfinite(raw_timestamp)
        and 0 < raw_timestamp < 4_102_444_800
    ):
        return datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).date().isoformat()
    raw_date = info.get("upload_date", info.get("release_date"))
    if isinstance(raw_date, str) and re.fullmatch(r"[0-9]{8}", raw_date):
        try:
            return datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    return None


def _thumbnail_url(info: dict[str, object]) -> str | None:
    candidates: list[object] = [info.get("thumbnail")]
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        candidates.extend(
            entry.get("url")
            for entry in reversed(thumbnails)
            if isinstance(entry, dict)
        )
    for candidate in candidates:
        if not isinstance(candidate, str) or len(candidate) > 4_096:
            continue
        normalized = f"https:{candidate}" if candidate.startswith("//") else candidate
        try:
            parts = urlsplit(normalized)
            port = parts.port
        except ValueError:
            continue
        host = (parts.hostname or "").casefold().rstrip(".")
        if (
            parts.scheme.casefold() == "http"
            and (host == "hdslb.com" or host.endswith(".hdslb.com"))
            and port in {None, 80}
            and parts.username is None
            and parts.password is None
            and not parts.fragment
        ):
            normalized = urlunsplit(("https", host, parts.path, parts.query, ""))
        try:
            return _safe_https_url(normalized)
        except PlatformSourceError:
            continue
    return None


def _thumbnail_media_type(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if (
        len(content) >= 12
        and content[4:8] == b"ftyp"
        and content[8:12] in {b"avif", b"avis"}
    ):
        return "image/avif"
    raise PlatformSourceError("invalid_content")


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
        self._probe_cache = threading.local()

    def _initial(self, source: str) -> _InitialSource:
        if not isinstance(source, str):
            raise PlatformSourceError("unsupported")
        if self.platform == "youtube":
            return _youtube_initial(source)
        if self.platform == "bilibili":
            return _bilibili_initial(source)
        return _douyin_initial(source)

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
        elif self.platform == "bilibili":
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
        else:
            if _DOUYIN_ID.fullmatch(raw_id) is None:
                raise PlatformSourceError("adapter_drift")
            normalized_id = raw_id
            canonical = _canonical_douyin(raw_id)
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
        author = next(
            (
                value
                for value in (
                    _optional_text(extraction.info.get("uploader"), maximum=512),
                    _optional_text(extraction.info.get("channel"), maximum=512),
                    _optional_text(extraction.info.get("creator"), maximum=512),
                )
                if value is not None
            ),
            None,
        )
        probe = SourceProbeResult(
            source_kind=self.platform,
            canonical_url=extraction.canonical_url,
            title=extraction.title,
            duration_ms=extraction.duration_ms,
            author=author,
            published_at=_published_date(extraction.info),
            thumbnail_url=_thumbnail_url(extraction.info),
            description=_optional_text(
                extraction.info.get("description"), maximum=20_000
            ),
            subtitle_tracks=tuple(
                resource.track for resource in extraction.tracks
            ),
            redirect_trace=(extraction.canonical_url,),
        )
        self._probe_cache.probe = probe
        self._probe_cache.extraction = extraction
        return probe

    def _extraction_for_probe(self, probe: SourceProbeResult) -> _Extraction:
        cached_probe = getattr(self._probe_cache, "probe", None)
        cached_extraction = getattr(self._probe_cache, "extraction", None)
        if cached_probe is probe and isinstance(cached_extraction, _Extraction):
            return cached_extraction
        if probe.canonical_url is None:
            raise PlatformSourceError("adapter_drift")
        extraction = self._extraction(probe.canonical_url)
        if not self._same_probe(probe, extraction):
            raise PlatformSourceError("adapter_drift")
        return extraction

    def fetch_thumbnail(self, canonical_source: str) -> ThumbnailOutcome:
        extraction = self._extraction(canonical_source)
        resource_url = _thumbnail_url(extraction.info)
        if resource_url is None:
            raise PlatformSourceError("invalid_content")
        try:
            content = self.operations.fetch_resource(
                resource_url,
                max_bytes=_MAX_THUMBNAIL_BYTES,
            )
        except YtDlpOperationFailure as error:
            raise PlatformSourceError(error.code) from None
        except Exception:
            raise PlatformSourceError("adapter_drift") from None
        if not isinstance(content, bytes) or not content:
            raise PlatformSourceError("invalid_content")
        return ThumbnailOutcome(
            content=content,
            media_type=cast(
                Literal["image/avif", "image/gif", "image/jpeg", "image/png", "image/webp"],
                _thumbnail_media_type(content),
            ),
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
        extraction = self._extraction_for_probe(probe)
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

    def _best_audio(self, info: dict[str, object]) -> tuple[str, str]:
        candidates: list[tuple[float, int, str, str]] = []
        media_candidates: list[tuple[float, int, str, str]] = []
        formats = info.get("formats")
        if not isinstance(formats, list):
            raise PlatformSourceError("adapter_drift")
        for ordinal, raw_format in enumerate(formats):
            if not isinstance(raw_format, dict):
                continue
            entry = cast(dict[str, object], raw_format)
            extension = entry.get("ext")
            if (
                entry.get("acodec") in {None, "none"}
                or not isinstance(extension, str)
                or extension.casefold() not in _AUDIO_EXTENSIONS
            ):
                continue
            try:
                url = _safe_https_url(entry.get("url"))
            except PlatformSourceError:
                continue
            abr = entry.get("abr")
            tbr = entry.get("tbr")
            score = (
                float(abr)
                if isinstance(abr, (int, float))
                and not isinstance(abr, bool)
                and math.isfinite(abr)
                else float(tbr)
                if isinstance(tbr, (int, float))
                and not isinstance(tbr, bool)
                and math.isfinite(tbr)
                else -1.0
            )
            candidate = (score, -ordinal, url, extension.casefold())
            if entry.get("vcodec") == "none":
                candidates.append(candidate)
            elif self.platform in {"bilibili", "douyin"}:
                media_candidates.append(candidate)
        if not candidates and self.platform in {"bilibili", "douyin"}:
            candidates = media_candidates
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
        extraction = self._extraction_for_probe(probe)
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
        douyin: SourceAdapter | None = None,
        bilibili_collections: BilibiliCollectionAdapter | None = None,
        youtube_unavailable_code: str = "youtube_runtime_unavailable",
    ) -> None:
        self.bilibili = bilibili
        self.youtube = youtube
        self.douyin = douyin
        self.bilibili_collections = bilibili_collections
        self.youtube_unavailable_code = youtube_unavailable_code

    @staticmethod
    def _kind(source: str) -> Platform:
        try:
            host = (urlsplit(source).hostname or "").casefold().rstrip(".")
        except (TypeError, ValueError):
            raise PlatformSourceError("unsupported") from None
        if host in {"youtube.com", "www.youtube.com", "youtu.be"}:
            return "youtube"
        if host in {"www.bilibili.com", "space.bilibili.com", "b23.tv"}:
            return "bilibili"
        if host in {"douyin.com", "www.douyin.com", "v.douyin.com"}:
            return "douyin"
        raise PlatformSourceError("unsupported")

    def _adapter(self, kind: Platform) -> SourceAdapter:
        selected = {
            "bilibili": self.bilibili,
            "douyin": self.douyin,
            "youtube": self.youtube,
        }[kind]
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

    def fetch_thumbnail(self, canonical_source: str) -> ThumbnailOutcome:
        return self._adapter(self._kind(canonical_source)).fetch_thumbnail(
            canonical_source
        )

    def probe_collection(
        self,
        canonical_source: str,
    ) -> SourceCollectionProbeResult | None:
        if self.bilibili_collections is None:
            return None
        return self.bilibili_collections.probe_collection(canonical_source)

    def fetch_subtitle(
        self,
        probe: SourceProbeResult,
        track: SubtitleTrack,
    ) -> SubtitleOutcome:
        if probe.source_kind not in _PLATFORMS:
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
        if probe.source_kind not in _PLATFORMS:
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
        browser_cookies: PlatformCookieStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if platform not in _PLATFORMS:
            raise ValueError("invalid controlled yt-dlp platform")
        if platform == "youtube" and youtube_runtime is None:
            raise ValueError("YouTube operations require a managed runtime")
        if platform != "youtube" and youtube_runtime is not None:
            raise ValueError("non-YouTube operations cannot use a YouTube runtime")
        self.platform = platform
        self.transport = transport
        self.output_root = Path(output_root)
        self.youtube_runtime = youtube_runtime
        self.browser_cookies = browser_cookies
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._current = threading.local()

    @staticmethod
    def _resource_urls_from(info: dict[str, object]) -> frozenset[str]:
        urls: set[str] = set()
        thumbnail = _thumbnail_url(info)
        if thumbnail is not None:
            urls.add(thumbnail)
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

    def _bilibili_api_extract(self, canonical_url: str) -> dict[str, object]:
        """Recover a public video when Bilibili serves yt-dlp a risk-control page."""

        try:
            initial = _bilibili_initial(canonical_url)
            if initial.expected_id is None:
                raise PlatformSourceError("adapter_drift")
            components = [
                component
                for component in urlsplit(initial.extraction_url).path.split("/")
                if component
            ]
            if len(components) != 2:
                raise PlatformSourceError("adapter_drift")
            submitted_id = components[1]
            identity_query = (
                {"aid": submitted_id[2:]}
                if submitted_id[:2].casefold() == "av"
                else {"bvid": submitted_id}
            )
            view_payload = request_bilibili_json(
                self.transport,
                "https://api.bilibili.com/x/web-interface/view?"
                + urlencode(identity_query),
                referer=initial.extraction_url,
            )
            view = _mapping(view_payload.get("data"))
            bvid = view.get("bvid")
            aid = view.get("aid")
            if (
                not isinstance(bvid, str)
                or _BILIBILI_ID.fullmatch(bvid) is None
                or isinstance(aid, bool)
                or not isinstance(aid, int)
                or aid <= 0
            ):
                raise PlatformSourceError("adapter_drift")
            if submitted_id[:2].casefold() == "av":
                if submitted_id[2:] != str(aid):
                    raise PlatformSourceError("adapter_drift")
            elif submitted_id.casefold() != bvid.casefold():
                raise PlatformSourceError("adapter_drift")

            pages = view.get("pages")
            if not isinstance(pages, list) or not pages:
                raise PlatformSourceError("adapter_drift")
            requested_page = _bilibili_page(initial.extraction_url) or 1
            selected_page = next(
                (
                    _mapping(page)
                    for page in pages
                    if _mapping(page).get("page") == requested_page
                ),
                {},
            )
            cid = selected_page.get("cid")
            duration = selected_page.get("duration", view.get("duration"))
            if (
                isinstance(cid, bool)
                or not isinstance(cid, int)
                or cid <= 0
                or isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration <= 0
            ):
                raise PlatformSourceError("adapter_drift")

            title = _title(view.get("title"))
            if len(pages) > 1:
                part = _optional_text(selected_page.get("part"), maximum=1_024)
                title = f"{title} p{requested_page:02d}{f' {part}' if part else ''}"
            webpage_url = _canonical_bilibili(
                submitted_id,
                _bilibili_page(initial.extraction_url),
            )
            play_payload = request_bilibili_json(
                self.transport,
                "https://api.bilibili.com/x/player/playurl?"
                + urlencode(
                    {
                        "bvid": bvid,
                        "cid": cid,
                        "fnval": 16,
                        "fourk": 1,
                    }
                ),
                referer=webpage_url,
            )
            play = _mapping(play_payload.get("data"))
            dash = _mapping(play.get("dash"))
            raw_audio = dash.get("audio")
            formats: list[dict[str, object]] = []
            if isinstance(raw_audio, list):
                for ordinal, raw_entry in enumerate(raw_audio):
                    entry = _mapping(raw_entry)
                    mime_type = entry.get("mimeType", entry.get("mime_type"))
                    extension = {
                        "audio/mp4": "m4a",
                        "audio/webm": "webm",
                    }.get(mime_type)
                    resource_url = entry.get("baseUrl", entry.get("base_url"))
                    codec = entry.get("codecs")
                    bandwidth = entry.get("bandwidth")
                    try:
                        safe_url = _safe_https_url(resource_url)
                    except PlatformSourceError:
                        continue
                    if extension is None:
                        continue
                    formats.append(
                        {
                            "format_id": str(entry.get("id", f"audio-{ordinal}")),
                            "url": safe_url,
                            "ext": extension,
                            "acodec": codec if isinstance(codec, str) and codec else "unknown",
                            "vcodec": "none",
                            "abr": (
                                bandwidth / 1_000
                                if isinstance(bandwidth, (int, float))
                                and not isinstance(bandwidth, bool)
                                and math.isfinite(bandwidth)
                                else -1
                            ),
                        }
                    )
            if not formats:
                raw_combined = play.get("durl")
                if isinstance(raw_combined, list):
                    for ordinal, raw_entry in enumerate(raw_combined):
                        entry = _mapping(raw_entry)
                        try:
                            safe_url = _safe_https_url(entry.get("url"))
                        except PlatformSourceError:
                            continue
                        formats.append(
                            {
                                "format_id": f"combined-{ordinal}",
                                "url": safe_url,
                                "ext": "mp4",
                                "acodec": "unknown",
                                "vcodec": "unknown",
                                "tbr": -1,
                            }
                        )
            if not formats:
                raise PlatformSourceError("invalid_content")

            subtitles: dict[str, list[dict[str, object]]] = {}
            try:
                subtitle_payload = request_bilibili_json(
                    self.transport,
                    "https://api.bilibili.com/x/player/v2?"
                    + urlencode({"bvid": bvid, "cid": cid}),
                    referer=webpage_url,
                )
            except PlatformSourceError as error:
                if error.code not in {"auth_required", "invalid_content", "temporary"}:
                    raise
            else:
                raw_subtitles = _mapping(
                    _mapping(subtitle_payload.get("data")).get("subtitle")
                ).get("subtitles")
                if isinstance(raw_subtitles, list):
                    for raw_entry in raw_subtitles:
                        entry = _mapping(raw_entry)
                        language = entry.get("lan")
                        resource_url = entry.get(
                            "subtitle_url",
                            entry.get("subtitleUrl"),
                        )
                        if isinstance(resource_url, str) and resource_url.startswith("//"):
                            resource_url = f"https:{resource_url}"
                        try:
                            safe_url = _safe_https_url(resource_url)
                        except PlatformSourceError:
                            continue
                        if not isinstance(language, str) or not language or len(language) > 64:
                            continue
                        subtitles.setdefault(language, []).append(
                            {
                                "ext": "json",
                                "url": safe_url,
                                "kind": "manual",
                            }
                        )

            owner = _mapping(view.get("owner"))
            return {
                "id": submitted_id if submitted_id[:2].casefold() == "av" else bvid,
                "extractor_key": "BiliBili",
                "webpage_url": webpage_url,
                "title": title,
                "duration": duration,
                "uploader": owner.get("name"),
                "timestamp": view.get("pubdate"),
                "thumbnail": view.get("pic"),
                "description": view.get("desc"),
                "subtitles": subtitles,
                "automatic_captions": {},
                "formats": formats,
            }
        except YtDlpOperationFailure:
            raise
        except PlatformSourceError as error:
            raise YtDlpOperationFailure(error.code) from None
        except Exception:
            raise YtDlpOperationFailure("adapter_drift") from None

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
                browser_cookiejar=(
                    self.browser_cookies.new_cookiejar()
                    if self.browser_cookies is not None
                    else None
                ),
            )
        except Exception as error:
            raise YtDlpOperationFailure(classify_extractor_failure(error)) from None
        try:
            info = bridge.probe(canonical_url)
        except Exception as error:
            message = str(error).casefold()
            classified = classify_extractor_failure(error)
            if self.platform == "bilibili" and (
                classified in {"adapter_drift", "temporary"}
                or any(
                    marker in message
                    for marker in (
                        "unable to extract initial state",
                        "http error 412",
                        "precondition failed",
                    )
                )
            ):
                info = self._bilibili_api_extract(canonical_url)
            else:
                raise YtDlpOperationFailure(classified) from None
        finally:
            bridge.close()
        if not isinstance(info, dict):
            raise YtDlpOperationFailure("adapter_drift")
        resources = self._resource_urls_from(cast(dict[str, object], info))
        expires_at = self.clock() + timedelta(minutes=10)
        self._current.resource_urls = resources
        self._current.expires_at = expires_at
        self._current.referer = canonical_url
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
        for attempt in range(2):
            try:
                referer = getattr(self._current, "referer", None)
                if not isinstance(referer, str):
                    raise ValueError
                response = self.transport.request(
                    SourceHttpRequest(
                        url=resource_url,
                        headers=controlled_public_headers(referer=referer),
                        max_wire_bytes=max_bytes,
                        max_decoded_bytes=max_bytes,
                    ),
                    self._resource_policy(resource_url),
                )
            except YtDlpOperationFailure:
                raise
            except TransportSecurityError as error:
                code = _transport_failure_code(error)
                if code == "temporary" and attempt == 0:
                    continue
                raise YtDlpOperationFailure(code) from None
            except (OSError, ValueError):
                if attempt == 0:
                    continue
                raise YtDlpOperationFailure("temporary") from None
            break
        code = self._status_error(response.status)
        if code is not None:
            response.close()
            raise YtDlpOperationFailure(code)
        return response

    def fetch_resource(self, resource_url: str, *, max_bytes: int) -> bytes:
        for attempt in range(2):
            response = self._request(resource_url, max_bytes=max_bytes)
            try:
                content = response.read()
            except TransportSecurityError as error:
                code = _transport_failure_code(error)
                if code == "temporary" and attempt == 0:
                    continue
                raise YtDlpOperationFailure(code) from None
            except (OSError, ValueError):
                if attempt == 0:
                    continue
                raise YtDlpOperationFailure("temporary") from None
            finally:
                response.close()
            break
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
        destination_path = Path(target)
        for attempt in range(2):
            response = self._request(resource_url, max_bytes=max_bytes)
            written = 0
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
            except TransportSecurityError as error:
                code = _transport_failure_code(error)
                if code == "temporary" and attempt == 0:
                    continue
                raise YtDlpOperationFailure(code) from None
            except (OSError, ValueError):
                if attempt == 0:
                    continue
                raise YtDlpOperationFailure("temporary") from None
            finally:
                response.close()
                try:
                    staging.unlink(missing_ok=True)
                except OSError:
                    pass
            if written <= 0:
                raise YtDlpOperationFailure("invalid_content")
            return


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
    connector = (
        LoopbackHttpProxyConnector(settings.platform_proxy_url)
        if settings.platform_proxy_url is not None
        else None
    )
    transport = PinnedHttpsTransport(resolver=resolver, connector=connector)
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
    browser_cookies: BrowserCookieStore | None = None
    if settings.platform_cookie_browser is not None:
        try:
            browser_cookies = BrowserCookieStore(
                settings.platform_cookie_browser
            )
        except RuntimeError:
            # Cookie import is optional platform capability and must not make
            # the local API or durable worker unavailable.
            browser_cookies = None
    douyin_cookies: PlatformCookieStore | None = browser_cookies
    youtube_cookies: PlatformCookieStore | None = browser_cookies
    if settings.platform_douyin_cookie_file is not None:
        try:
            douyin_cookies = NetscapeCookieFileStore(
                settings.platform_douyin_cookie_file,
                "douyin",
            )
        except RuntimeError:
            douyin_cookies = None
    if settings.platform_youtube_cookie_file is not None:
        try:
            youtube_cookies = NetscapeCookieFileStore(
                settings.platform_youtube_cookie_file,
                "youtube",
            )
        except RuntimeError:
            youtube_cookies = None
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
    douyin = YtDlpSourceAdapter(
        platform="douyin",
        operations=ControlledYtDlpOperations(
            platform="douyin",
            transport=transport,
            output_root=paths.runtime("yt-dlp", "douyin"),
            browser_cookies=douyin_cookies,
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
                browser_cookies=youtube_cookies,
            ),
            paths=paths,
            media_validator=media_validator,
            audio_registrar=registrar,
        )
    return PlatformSourceRegistry(
        bilibili=bilibili,
        bilibili_collections=BilibiliCollectionAdapter(transport),
        douyin=douyin,
        youtube=youtube,
        youtube_unavailable_code="youtube_runtime_unavailable",
    )
