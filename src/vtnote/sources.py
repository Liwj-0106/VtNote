"""Canonical source-domain contracts shared by the API and durable worker."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from vtnote.artifacts import validate_source_subtitle


SourceKind = Literal[
    "bilibili",
    "youtube",
    "local_media",
    "uploaded_media",
    "local_subtitle",
    "uploaded_subtitle",
]
SubtitleKind = Literal["manual", "automatic", "unconfirmed"]

SOURCE_KINDS = frozenset(
    {
        "bilibili",
        "youtube",
        "local_media",
        "uploaded_media",
        "local_subtitle",
        "uploaded_subtitle",
    }
)
REMOTE_SOURCE_KINDS = frozenset({"bilibili", "youtube"})
SUBTITLE_KINDS = frozenset({"manual", "automatic", "unconfirmed"})
SUBTITLE_FORMAT_ORDER = {"vtt": 0, "srt": 1, "ass": 2, "json": 3}
_SUBTITLE_KIND_ORDER = {"manual": 0, "automatic": 1, "unconfirmed": 2}
_TRACK_ID = re.compile(r"^trk_[0-9a-f]{64}$")
_LANGUAGE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FORMAT = re.compile(r"^[a-z0-9]{1,16}$")
_CANDIDATE_ERROR_CODES = frozenset(
    {
        "subtitle_empty",
        "subtitle_expired",
        "subtitle_invalid",
        "subtitle_unavailable",
    }
)
_PLATFORM_SOURCE_ERROR_CODES = frozenset(
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


def _normalize_source_kind(value: object) -> SourceKind:
    if not isinstance(value, str) or value not in SOURCE_KINDS:
        raise ValueError("invalid source kind")
    return cast(SourceKind, value)


def _normalize_language(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid subtitle language")
    normalized = value.strip().replace("_", "-").casefold()
    if _LANGUAGE.fullmatch(normalized) is None:
        raise ValueError("invalid subtitle language")
    return normalized


def _normalize_format(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid subtitle format")
    normalized = value.strip().removeprefix(".").casefold()
    if _FORMAT.fullmatch(normalized) is None:
        raise ValueError("invalid subtitle format")
    return normalized


def _normalize_subtitle_kind(value: object) -> SubtitleKind:
    if not isinstance(value, str) or value not in SUBTITLE_KINDS:
        raise ValueError("invalid subtitle kind")
    return cast(SubtitleKind, value)


def _track_reference(
    *,
    source_kind: SourceKind,
    language: str,
    kind: SubtitleKind,
    format: str,
    stable_ordinal: int,
) -> str:
    identity = json.dumps(
        [source_kind, language, kind, format, stable_ordinal],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return f"trk_{hashlib.sha256(identity).hexdigest()}"


@dataclass(frozen=True, slots=True)
class SubtitleTrack:
    id: str
    source_kind: SourceKind = field(repr=False)
    language: str
    format: str
    kind: SubtitleKind
    stable_ordinal: int = field(repr=False)
    is_translated: bool = False
    is_live_chat: bool = False

    def __post_init__(self) -> None:
        source_kind = _normalize_source_kind(self.source_kind)
        language = _normalize_language(self.language)
        format = _normalize_format(self.format)
        kind = _normalize_subtitle_kind(self.kind)
        if type(self.stable_ordinal) is not int or self.stable_ordinal < 0:
            raise ValueError("invalid subtitle stable ordinal")
        if type(self.is_translated) is not bool or type(self.is_live_chat) is not bool:
            raise ValueError("invalid subtitle flags")
        expected = _track_reference(
            source_kind=source_kind,
            language=language,
            kind=kind,
            format=format,
            stable_ordinal=self.stable_ordinal,
        )
        if _TRACK_ID.fullmatch(self.id) is None or self.id != expected:
            raise ValueError("subtitle track id was not generated from stable metadata")
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "format", format)
        object.__setattr__(self, "kind", kind)

    @property
    def ui_label(self) -> str:
        return {
            "manual": "人工字幕",
            "automatic": "自动字幕",
            "unconfirmed": "字幕类型待确认",
        }[self.kind]


def make_subtitle_track(
    *,
    source_kind: SourceKind,
    language: str,
    format: str,
    kind: SubtitleKind,
    stable_ordinal: int,
    is_translated: bool = False,
    is_live_chat: bool = False,
) -> SubtitleTrack:
    """Create the only accepted opaque reference without resource locator input."""

    normalized_source = _normalize_source_kind(source_kind)
    normalized_language = _normalize_language(language)
    normalized_format = _normalize_format(format)
    normalized_kind = _normalize_subtitle_kind(kind)
    if type(stable_ordinal) is not int or stable_ordinal < 0:
        raise ValueError("invalid subtitle stable ordinal")
    reference = _track_reference(
        source_kind=normalized_source,
        language=normalized_language,
        kind=normalized_kind,
        format=normalized_format,
        stable_ordinal=stable_ordinal,
    )
    return SubtitleTrack(
        id=reference,
        source_kind=normalized_source,
        language=normalized_language,
        format=normalized_format,
        kind=normalized_kind,
        stable_ordinal=stable_ordinal,
        is_translated=is_translated,
        is_live_chat=is_live_chat,
    )


@dataclass(frozen=True, slots=True)
class SourceProbeResult:
    source_kind: SourceKind
    canonical_url: str | None
    title: str
    duration_ms: int | None
    subtitle_tracks: tuple[SubtitleTrack, ...] = ()
    redirect_trace: tuple[str, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        source_kind = _normalize_source_kind(self.source_kind)
        if (
            not isinstance(self.title, str)
            or not self.title.strip()
            or len(self.title) > 4_096
        ):
            raise ValueError("source title must be a non-empty bounded string")
        if self.duration_ms is not None and (
            type(self.duration_ms) is not int or self.duration_ms < 0
        ):
            raise ValueError("source duration must be a nonnegative integer")
        if source_kind in REMOTE_SOURCE_KINDS:
            if not isinstance(self.canonical_url, str) or not self.canonical_url:
                raise ValueError("remote source requires a canonical URL")
        elif self.canonical_url is not None:
            raise ValueError("local or uploaded source cannot have a canonical URL")
        if not isinstance(self.subtitle_tracks, tuple) or any(
            not isinstance(track, SubtitleTrack)
            or track.source_kind != source_kind
            for track in self.subtitle_tracks
        ):
            raise ValueError("invalid subtitle tracks for source")
        ordinals = [track.stable_ordinal for track in self.subtitle_tracks]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("subtitle candidate ordinals must be unique")
        if not isinstance(self.redirect_trace, tuple) or any(
            not isinstance(target, str) or not target
            for target in self.redirect_trace
        ):
            raise ValueError("invalid private redirect trace")
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "title", self.title.strip())


@dataclass(frozen=True, slots=True)
class SubtitleOutcome:
    track: SubtitleTrack
    content: bytes


@dataclass(frozen=True, slots=True)
class AudioOutcome:
    asset_id: str
    format: str
    duration_ms: int
    size_bytes: int


class SourceError(RuntimeError):
    """Base class for typed, adapter-safe source failures."""


class PlatformSourceError(SourceError):
    """A closed, non-sensitive platform extraction failure."""

    def __init__(self, code: str) -> None:
        if code not in _PLATFORM_SOURCE_ERROR_CODES:
            raise ValueError("invalid platform source error code")
        self.code = code
        super().__init__(code)


class SourceCapabilityError(SourceError):
    """A source adapter is intentionally unavailable in this installation."""

    def __init__(self, code: str) -> None:
        if code not in {"adapter_unavailable", "youtube_runtime_unavailable"}:
            raise ValueError("invalid source capability error code")
        self.code = code
        super().__init__(code)


class SubtitleCandidateError(SourceError):
    """A bounded failure that permits trying the next subtitle candidate."""

    def __init__(self, code: str) -> None:
        if code not in _CANDIDATE_ERROR_CODES:
            raise ValueError("invalid subtitle candidate error code")
        self.code = code
        super().__init__(code)


class SourceAdapter(Protocol):
    def probe(self, canonical_source: str) -> SourceProbeResult: ...

    def fetch_subtitle(
        self,
        probe: SourceProbeResult,
        track: SubtitleTrack,
    ) -> SubtitleOutcome: ...

    def fetch_audio(
        self,
        probe: SourceProbeResult,
        item_id: str,
    ) -> AudioOutcome: ...


class SubtitleTrackSelector:
    """Rank usable tracks with deterministic kind/language/format policy."""

    def __init__(self, preferred_languages: tuple[str, ...]) -> None:
        self.preferred_languages = tuple(
            dict.fromkeys(_normalize_language(language) for language in preferred_languages)
        )

    def rank(self, tracks: tuple[SubtitleTrack, ...]) -> tuple[SubtitleTrack, ...]:
        preferred_rank = {
            language: position
            for position, language in enumerate(self.preferred_languages)
        }

        def key(candidate: SubtitleTrack) -> tuple[int, int, int | str, int, int]:
            preferred = candidate.language in preferred_rank
            return (
                _SUBTITLE_KIND_ORDER[candidate.kind],
                0 if preferred else 1,
                (
                    preferred_rank[candidate.language]
                    if preferred
                    else candidate.language
                ),
                SUBTITLE_FORMAT_ORDER[candidate.format],
                candidate.stable_ordinal,
            )

        usable = (
            candidate
            for candidate in tracks
            if not candidate.is_live_chat
            and not candidate.is_translated
            and candidate.format in SUBTITLE_FORMAT_ORDER
        )
        return tuple(sorted(usable, key=key))


def _validate_candidate(track: SubtitleTrack, content: bytes) -> None:
    try:
        validate_source_subtitle(track.format, content)
    except ValueError as error:
        raise SubtitleCandidateError("subtitle_invalid") from error


def acquire_subtitle_or_audio(
    probe: SourceProbeResult,
    source: SourceAdapter,
    selector: SubtitleTrackSelector,
    *,
    item_id: str,
) -> SubtitleOutcome | AudioOutcome:
    """Try candidate failures only, then acquire audio exactly once."""

    for track in selector.rank(probe.subtitle_tracks):
        try:
            outcome = source.fetch_subtitle(probe, track)
        except SubtitleCandidateError:
            continue
        if not isinstance(outcome, SubtitleOutcome):
            raise TypeError("source adapter returned an invalid subtitle outcome")
        if outcome.track != track or not isinstance(outcome.content, bytes):
            raise TypeError("source adapter returned mismatched subtitle data")
        try:
            _validate_candidate(track, outcome.content)
        except SubtitleCandidateError:
            continue
        return outcome
    return source.fetch_audio(probe, item_id)
