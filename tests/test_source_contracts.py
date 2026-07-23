from __future__ import annotations

from dataclasses import dataclass, field

from vtnote.sources import (
    AudioOutcome,
    SourceProbeResult,
    SubtitleOutcome,
    SubtitleTrack,
    SubtitleTrackSelector,
    acquire_subtitle_or_audio,
)


VALID_VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"


def test_subtitle_selector_uses_fixed_manual_language_and_format_order() -> None:
    tracks = (
        SubtitleTrack("translated", "zh-Hans", "vtt", True, is_translated=True),
        SubtitleTrack("live", "zh-Hans", "vtt", True, is_live_chat=True),
        SubtitleTrack("en-auto", "en", "vtt", False),
        SubtitleTrack("zh-auto", "zh-Hans", "srt", False),
        SubtitleTrack("en-manual", "en", "vtt", True),
        SubtitleTrack("ja-manual", "ja", "ass", True),
        SubtitleTrack("zh-json", "zh-Hans", "json", True),
        SubtitleTrack("zh-vtt", "zh-Hans", "vtt", True),
        SubtitleTrack("zh-srt", "zh-Hans", "srt", True),
        SubtitleTrack("unsupported", "zh-Hans", "ttml", True),
    )

    ranked = SubtitleTrackSelector(("zh-Hans", "en")).rank(tracks)

    assert [track.id for track in ranked] == [
        "zh-vtt",
        "zh-srt",
        "zh-json",
        "en-manual",
        "zh-auto",
        "en-auto",
        "ja-manual",
    ]


@dataclass
class FakeSource:
    subtitle_payloads: dict[str, bytes | Exception]
    audio_calls: int = 0
    attempted_tracks: list[str] = field(default_factory=list)

    def fetch_subtitle(self, track: SubtitleTrack) -> bytes:
        self.attempted_tracks.append(track.id)
        result = self.subtitle_payloads[track.id]
        if isinstance(result, Exception):
            raise result
        return result

    def fetch_audio(self) -> AudioOutcome:
        self.audio_calls += 1
        return AudioOutcome(
            asset_id="audio-asset",
            format="m4a",
            duration_ms=1_000,
            size_bytes=123,
        )


def test_subtitle_acquisition_falls_through_empty_invalid_and_unavailable_tracks() -> None:
    tracks = (
        SubtitleTrack("empty", "zh-Hans", "vtt", True),
        SubtitleTrack("invalid", "zh-Hans", "srt", True),
        SubtitleTrack("unavailable", "zh-Hans", "ass", True),
        SubtitleTrack("valid", "en", "vtt", True),
    )
    probe = SourceProbeResult(
        canonical_url="https://www.youtube.com/watch?v=abc",
        title="Example",
        platform="youtube",
        duration_ms=1_000,
        subtitles=tracks,
    )
    source = FakeSource(
        {
            "empty": b"",
            "invalid": b"not an srt",
            "unavailable": FileNotFoundError("gone"),
            "valid": VALID_VTT,
        }
    )

    outcome = acquire_subtitle_or_audio(
        probe, source, SubtitleTrackSelector(("zh-Hans", "en"))
    )

    assert isinstance(outcome, SubtitleOutcome)
    assert outcome.track.id == "valid"
    assert outcome.content == VALID_VTT
    assert source.attempted_tracks == ["empty", "invalid", "unavailable", "valid"]
    assert source.audio_calls == 0


def test_audio_is_acquired_once_only_after_every_subtitle_candidate_fails() -> None:
    tracks = (
        SubtitleTrack("empty", "zh-Hans", "vtt", True),
        SubtitleTrack("invalid", "en", "srt", False),
    )
    probe = SourceProbeResult(
        canonical_url="https://www.bilibili.com/video/BV1abc",
        title=None,
        platform="bilibili",
        duration_ms=None,
        subtitles=tracks,
    )
    source = FakeSource({"empty": b"", "invalid": b"invalid"})

    outcome = acquire_subtitle_or_audio(
        probe, source, SubtitleTrackSelector(("zh-Hans",))
    )

    assert isinstance(outcome, AudioOutcome)
    assert outcome.asset_id == "audio-asset"
    assert source.audio_calls == 1
