from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest

from vtnote.sources import (
    AudioOutcome,
    SourceProbeResult,
    SubtitleCandidateError,
    SubtitleOutcome,
    SubtitleTrack,
    SubtitleTrackSelector,
    acquire_subtitle_or_audio,
    make_subtitle_track,
)


VALID_VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"


def track(
    ordinal: int,
    *,
    language: str = "zh-Hans",
    format: str = "vtt",
    kind: str = "manual",
    translated: bool = False,
    live_chat: bool = False,
) -> SubtitleTrack:
    return make_subtitle_track(
        source_kind="youtube",
        language=language,
        format=format,
        kind=kind,
        stable_ordinal=ordinal,
        is_translated=translated,
        is_live_chat=live_chat,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_kind", "vimeo"),
        ("title", ""),
        ("title", "   "),
        ("duration_ms", -1),
        ("duration_ms", True),
    ],
)
def test_probe_contract_enforces_runtime_source_kind_title_and_duration(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "source_kind": "youtube",
        "canonical_url": "https://www.youtube.com/watch?v=abc",
        "title": "Example",
        "duration_ms": 1_000,
        "subtitle_tracks": (),
    }
    values[field] = value
    with pytest.raises(ValueError):
        SourceProbeResult(**values)  # type: ignore[arg-type]


def test_local_probe_must_not_claim_a_canonical_url_and_remote_probe_requires_one() -> None:
    with pytest.raises(ValueError):
        SourceProbeResult(
            source_kind="youtube",
            canonical_url=None,
            title="Remote",
            duration_ms=None,
            subtitle_tracks=(),
        )
    with pytest.raises(ValueError):
        SourceProbeResult(
            source_kind="local_media",
            canonical_url="https://example.com/not-local",
            title="Local",
            duration_ms=None,
            subtitle_tracks=(),
        )


def test_track_reference_is_deterministic_normalized_and_contains_no_secret_input() -> None:
    first = make_subtitle_track(
        source_kind="youtube",
        language=" ZH_hans ",
        format=".VTT",
        kind="manual",
        stable_ordinal=7,
    )
    second = make_subtitle_track(
        source_kind="youtube",
        language="zh-hans",
        format="vtt",
        kind="manual",
        stable_ordinal=7,
    )
    different_ordinal = make_subtitle_track(
        source_kind="youtube",
        language="zh-hans",
        format="vtt",
        kind="manual",
        stable_ordinal=8,
    )

    assert first.id == second.id
    assert first.id.startswith("trk_")
    assert len(first.id) == 68
    assert different_ordinal.id != first.id
    assert "http" not in repr(first).casefold()
    assert "token" not in repr(first).casefold()


def test_track_rejects_adapter_supplied_id_and_invalid_kind() -> None:
    valid = track(0)
    with pytest.raises(ValueError):
        replace(valid, id="trk_" + ("0" * 64))
    with pytest.raises(ValueError):
        make_subtitle_track(
            source_kind="youtube",
            language="en",
            format="vtt",
            kind="machine",
            stable_ordinal=0,
        )


def test_probe_rejects_track_from_another_source_kind_and_hides_redirect_trace() -> None:
    youtube_track = track(0)
    with pytest.raises(ValueError):
        SourceProbeResult(
            source_kind="bilibili",
            canonical_url="https://www.bilibili.com/video/BV1abc",
            title="Example",
            duration_ms=None,
            subtitle_tracks=(youtube_track,),
        )

    probe = SourceProbeResult(
        source_kind="youtube",
        canonical_url="https://www.youtube.com/watch?v=abc",
        title="Example",
        duration_ms=None,
        subtitle_tracks=(youtube_track,),
        redirect_trace=("https://redirect.example/path?token=super-secret",),
    )
    assert "super-secret" not in repr(probe)


def test_probe_rejects_duplicate_candidate_ordinals() -> None:
    with pytest.raises(ValueError):
        SourceProbeResult(
            source_kind="youtube",
            canonical_url="https://www.youtube.com/watch?v=abc",
            title="Example",
            duration_ms=None,
            subtitle_tracks=(
                track(0, language="zh-Hans"),
                track(0, language="en"),
            ),
        )


def test_subtitle_selector_uses_kind_language_format_and_stable_ordinal_order() -> None:
    tracks = (
        track(0, translated=True),
        track(1, live_chat=True),
        track(2, language="en", kind="automatic"),
        track(3, language="zh-Hans", format="srt", kind="automatic"),
        track(4, language="en", kind="manual"),
        track(5, language="ja", format="ass", kind="manual"),
        track(6, language="zh-Hans", format="json", kind="manual"),
        track(8, language="zh-Hans", kind="manual"),
        track(7, language="zh-Hans", kind="manual"),
        track(9, language="zh-Hans", kind="unconfirmed"),
        track(10, format="ttml", kind="manual"),
    )

    ranked = SubtitleTrackSelector(("zh-Hans", "en")).rank(tracks)

    assert [candidate.stable_ordinal for candidate in ranked] == [
        7,
        8,
        6,
        4,
        5,
        3,
        2,
        9,
    ]


@dataclass
class FakeAdapter:
    subtitle_payloads: dict[str, bytes | Exception]
    audio_calls: int = 0
    attempted_tracks: list[str] = field(default_factory=list)

    def probe(self, canonical_source: str) -> SourceProbeResult:
        raise AssertionError("not used by acquisition tests")

    def fetch_subtitle(
        self,
        probe: SourceProbeResult,
        candidate: SubtitleTrack,
    ) -> SubtitleOutcome:
        self.attempted_tracks.append(candidate.id)
        result = self.subtitle_payloads[candidate.id]
        if isinstance(result, Exception):
            raise result
        return SubtitleOutcome(track=candidate, content=result)

    def fetch_audio(self, probe: SourceProbeResult, item_id: str) -> AudioOutcome:
        assert item_id == "22222222-2222-4222-8222-222222222222"
        self.audio_calls += 1
        return AudioOutcome(
            asset_id="audio-asset",
            format="m4a",
            duration_ms=1_000,
            size_bytes=123,
        )


def probe_with(*tracks: SubtitleTrack) -> SourceProbeResult:
    return SourceProbeResult(
        source_kind="youtube",
        canonical_url="https://www.youtube.com/watch?v=abc",
        title="Example",
        duration_ms=1_000,
        subtitle_tracks=tracks,
    )


def acquire(probe: SourceProbeResult, adapter: FakeAdapter) -> SubtitleOutcome | AudioOutcome:
    return acquire_subtitle_or_audio(
        probe,
        adapter,
        SubtitleTrackSelector(("zh-Hans", "en")),
        item_id="22222222-2222-4222-8222-222222222222",
    )


def test_only_candidate_error_and_local_validation_failure_advance_to_next_track() -> None:
    unavailable = track(0)
    invalid = track(1, format="srt")
    valid = track(2, language="en")
    adapter = FakeAdapter(
        {
            unavailable.id: SubtitleCandidateError("subtitle_unavailable"),
            invalid.id: b"not an srt",
            valid.id: VALID_VTT,
        }
    )

    outcome = acquire(probe_with(unavailable, invalid, valid), adapter)

    assert isinstance(outcome, SubtitleOutcome)
    assert outcome.track == valid
    assert adapter.attempted_tracks == [unavailable.id, invalid.id, valid.id]
    assert adapter.audio_calls == 0


@pytest.mark.parametrize(
    "failure",
    [
        OSError("transport failed"),
        ValueError("adapter bug"),
        TypeError("wrong adapter state"),
    ],
)
def test_fatal_adapter_errors_propagate_without_fetching_audio(
    failure: Exception,
) -> None:
    candidate = track(0)
    adapter = FakeAdapter({candidate.id: failure})

    with pytest.raises(type(failure), match=str(failure)):
        acquire(probe_with(candidate), adapter)

    assert adapter.audio_calls == 0


def test_audio_is_acquired_once_only_after_every_candidate_failure() -> None:
    first = track(0)
    second = track(1, language="en", kind="automatic")
    adapter = FakeAdapter(
        {
            first.id: SubtitleCandidateError("subtitle_unavailable"),
            second.id: b"invalid",
        }
    )

    outcome = acquire(probe_with(first, second), adapter)

    assert isinstance(outcome, AudioOutcome)
    assert outcome.asset_id == "audio-asset"
    assert adapter.audio_calls == 1
