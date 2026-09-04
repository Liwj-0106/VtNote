from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from vtnote.schemas import Provenance, ProvenanceMethod, Transcript, TranscriptSegment
from vtnote.speaker_diarization import diarize_transcript


def test_diarization_writes_assignment_for_every_segment(tmp_path: Path) -> None:
    audio = tmp_path / "speech.wav"
    samples: list[int] = []
    for index in range(32_000):
        frequency = 220 if index < 16_000 else 880
        samples.append(round(12_000 * math.sin(2 * math.pi * frequency * index / 16_000)))
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    transcript = Transcript(
        language="zh-Hans",
        duration_ms=2_000,
        provenance=Provenance(
            method=ProvenanceMethod.LOCAL_ASR,
            provider="faster-whisper",
            model="large-v3-turbo",
        ),
        segments=(
            TranscriptSegment(id="seg_000001", start_ms=0, end_ms=1_000, text="一"),
            TranscriptSegment(id="seg_000002", start_ms=1_000, end_ms=2_000, text="二"),
        ),
    )

    result = diarize_transcript(audio, transcript)

    assert [item.segment_id for item in result.assignments] == [
        "seg_000001",
        "seg_000002",
    ]
    assert 1 <= result.speaker_count <= 2
    assert result.validate_against(transcript) is result

    inconsistent = result.model_copy(update={"speaker_count": result.speaker_count + 1})
    try:
        inconsistent.validate_against(transcript)
    except ValueError as error:
        assert str(error) == "speaker count does not match speaker assignments"
    else:
        raise AssertionError("inconsistent speaker count must be rejected")
