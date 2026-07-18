from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    Translation,
    TranslationEntry,
    canonical_transcript_bytes,
    transcript_sha256,
)


def segment(
    segment_id: str, start_ms: int, end_ms: int, text: str = "hello"
) -> TranscriptSegment:
    return TranscriptSegment(id=segment_id, start_ms=start_ms, end_ms=end_ms, text=text)


def transcript(*segments: TranscriptSegment) -> Transcript:
    return Transcript(
        language="en",
        duration_ms=max(item.end_ms for item in segments),
        provenance=Provenance(
            method=ProvenanceMethod.PLATFORM_SUBTITLE,
            provider="youtube",
            model=None,
        ),
        segments=list(segments),
    )


@pytest.mark.parametrize(
    ("start_ms", "end_ms"),
    [(-1, 100), (100, 100), (101, 100)],
)
def test_transcript_segment_requires_a_positive_time_range(start_ms: int, end_ms: int) -> None:
    with pytest.raises(ValidationError):
        segment("segment-1", start_ms, end_ms)


def test_transcript_requires_unique_segment_ids() -> None:
    with pytest.raises(ValidationError, match="segment IDs must be unique"):
        transcript(segment("same", 0, 100), segment("same", 100, 200))


def test_transcript_requires_chronological_cues() -> None:
    with pytest.raises(ValidationError, match="chronological"):
        transcript(segment("later", 100, 200), segment("earlier", 0, 100))


def test_transcript_duration_covers_its_last_cue() -> None:
    with pytest.raises(ValidationError, match="duration_ms"):
        Transcript(
            language="en",
            duration_ms=99,
            provenance=Provenance(
                method=ProvenanceMethod.PLATFORM_SUBTITLE,
                provider="manual_upload",
                model=None,
            ),
            segments=[segment("segment-1", 0, 100)],
        )


def test_transcript_persisted_shape_uses_fixed_v1_contract() -> None:
    source = transcript(segment("segment-1", 0, 100))

    assert json.loads(canonical_transcript_bytes(source)) == {
        "schema_version": 1,
        "language": "en",
        "duration_ms": 100,
        "provenance": {
            "method": "platform_subtitle",
            "provider": "youtube",
            "model": None,
        },
        "segments": [
            {"id": "segment-1", "start_ms": 0, "end_ms": 100, "text": "hello"}
        ],
    }


@pytest.mark.parametrize("method", ["manual", "native", "unknown"])
def test_provenance_rejects_methods_outside_the_fixed_contract(method: str) -> None:
    with pytest.raises(ValidationError):
        Provenance(method=method, provider="test", model=None)


def test_translation_must_match_every_source_cue_in_order() -> None:
    source = transcript(segment("cue-1", 0, 100), segment("cue-2", 100, 200))
    translation = Translation(
        language="zh-CN",
        source_transcript_sha256=transcript_sha256(source),
        entries=[
            TranslationEntry(cue_id="cue-2", text="第二句"),
            TranslationEntry(cue_id="cue-1", text="第一句"),
        ],
    )

    with pytest.raises(ValueError, match="exactly match"):
        translation.validate_against(source)


def test_translation_accepts_one_entry_for_each_source_cue() -> None:
    source = transcript(segment("cue-1", 0, 100), segment("cue-2", 100, 200))
    translation = Translation(
        language="zh-CN",
        source_transcript_sha256=transcript_sha256(source),
        entries=[
            TranslationEntry(cue_id="cue-1", text="第一句"),
            TranslationEntry(cue_id="cue-2", text="第二句"),
        ],
    )

    assert translation.validate_against(source) is translation
    assert translation.model_dump(mode="json") == {
        "schema_version": 1,
        "language": "zh-CN",
        "source_transcript_sha256": transcript_sha256(source),
        "entries": [
            {"cue_id": "cue-1", "text": "第一句"},
            {"cue_id": "cue-2", "text": "第二句"},
        ],
    }


def test_translation_rejects_a_different_source_hash() -> None:
    source = transcript(segment("cue-1", 0, 100))
    translation = Translation(
        language="zh-CN",
        source_transcript_sha256="0" * 64,
        entries=[TranslationEntry(cue_id="cue-1", text="第一句")],
    )

    with pytest.raises(ValueError, match="hash"):
        translation.validate_against(source)
