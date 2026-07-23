"""Small source contracts shared by later platform adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from vtnote.artifacts import validate_source_subtitle


SUBTITLE_FORMAT_ORDER = {"vtt": 0, "srt": 1, "ass": 2, "json": 3}


@dataclass(frozen=True, slots=True)
class SubtitleTrack:
    id: str
    language: str
    format: str
    is_manual: bool
    is_translated: bool = False
    is_live_chat: bool = False


@dataclass(frozen=True, slots=True)
class SourceProbeResult:
    canonical_url: str
    title: str | None
    platform: str
    duration_ms: int | None
    subtitles: tuple[SubtitleTrack, ...] = ()


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


class SourceAcquirer(Protocol):
    def fetch_subtitle(self, track: SubtitleTrack) -> bytes: ...

    def fetch_audio(self) -> AudioOutcome: ...


class SubtitleTrackSelector:
    """Rank usable tracks with the fixed v1 manual/language/format policy."""

    def __init__(self, preferred_languages: tuple[str, ...]) -> None:
        self.preferred_languages = tuple(
            dict.fromkeys(language.casefold() for language in preferred_languages)
        )

    def rank(self, tracks: tuple[SubtitleTrack, ...]) -> tuple[SubtitleTrack, ...]:
        preferred_rank = {
            language: position
            for position, language in enumerate(self.preferred_languages)
        }

        def key(track: SubtitleTrack) -> tuple[int, int | str, int, str]:
            language = track.language.casefold()
            preferred = language in preferred_rank
            if preferred and track.is_manual:
                group = 0
            elif preferred:
                group = 1
            elif track.is_manual:
                group = 2
            else:
                group = 3
            language_order: int | str = (
                preferred_rank[language] if preferred else language
            )
            return (
                group,
                language_order,
                SUBTITLE_FORMAT_ORDER[track.format.casefold()],
                track.id,
            )

        usable = (
            track
            for track in tracks
            if not track.is_live_chat
            and not track.is_translated
            and track.format.casefold() in SUBTITLE_FORMAT_ORDER
        )
        return tuple(sorted(usable, key=key))


def acquire_subtitle_or_audio(
    probe: SourceProbeResult,
    source: SourceAcquirer,
    selector: SubtitleTrackSelector,
) -> SubtitleOutcome | AudioOutcome:
    """Try every ranked subtitle before permitting one audio acquisition."""

    for track in selector.rank(probe.subtitles):
        try:
            content = source.fetch_subtitle(track)
            if not isinstance(content, bytes):
                raise TypeError("subtitle payload must be bytes")
            validate_source_subtitle(track.format, content)
        except (OSError, TypeError, ValueError, UnicodeError):
            continue
        return SubtitleOutcome(track=track, content=content)
    return source.fetch_audio()
