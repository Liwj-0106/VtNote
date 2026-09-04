from __future__ import annotations

import pytest

from vtnote.local_asr_contract import (
    FASTER_WHISPER_ENGINE,
    SENSEVOICE_ENGINE,
    SENSEVOICE_MODEL,
    LocalAsrSnapshotError,
    build_local_asr_snapshot,
    resolve_local_asr_snapshot,
)


def test_historical_whisper_snapshot_resolves_without_rewrite() -> None:
    legacy = {"model": "large-v3-turbo", "schema_version": 2}
    snapshot = {"local_whisper": legacy}

    selected = resolve_local_asr_snapshot(snapshot)

    assert selected.engine == FASTER_WHISPER_ENGINE
    assert selected.options is legacy


def test_new_faster_whisper_snapshot_resolves_to_whisper_options() -> None:
    local_asr = build_local_asr_snapshot(FASTER_WHISPER_ENGINE)
    whisper = {"model": "large-v3-turbo", "schema_version": 2}

    selected = resolve_local_asr_snapshot(
        {"local_asr": local_asr, "local_whisper": whisper}
    )

    assert selected.engine == FASTER_WHISPER_ENGINE
    assert selected.model == "large-v3-turbo"
    assert selected.options is whisper


def test_sensevoice_snapshot_round_trips_with_frozen_options() -> None:
    local_asr = build_local_asr_snapshot(SENSEVOICE_ENGINE)

    selected = resolve_local_asr_snapshot({"local_asr": local_asr})

    assert selected.engine == SENSEVOICE_ENGINE
    assert selected.model == SENSEVOICE_MODEL
    assert selected.provider == "sherpa-onnx"
    assert selected.evidence_provider == SENSEVOICE_ENGINE
    assert selected.options == {
        "schema_version": 1,
        "language": "auto",
        "use_itn": True,
        "num_threads": 4,
        "vad_threshold": 0.2,
        "vad_min_silence_seconds": 0.25,
        "vad_min_speech_seconds": 0.25,
        "vad_max_speech_seconds": 20.0,
        "speaker_diarization_enabled": False,
    }


def test_unknown_engine_never_falls_through_to_whisper() -> None:
    with pytest.raises(LocalAsrSnapshotError):
        resolve_local_asr_snapshot(
            {
                "local_asr": {
                    "schema_version": 1,
                    "engine": "unknown",
                    "model": "large-v3-turbo",
                    "options": {},
                },
                "local_whisper": {"model": "large-v3-turbo"},
            }
        )
