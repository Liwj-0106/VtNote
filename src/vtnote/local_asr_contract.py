"""Immutable local-ASR engine snapshots and backward-compatible resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


LocalAsrEngine = Literal["faster_whisper", "sensevoice_sherpa_onnx"]

FASTER_WHISPER_ENGINE: LocalAsrEngine = "faster_whisper"
FASTER_WHISPER_MODEL = "large-v3-turbo"
SENSEVOICE_ENGINE: LocalAsrEngine = "sensevoice_sherpa_onnx"
SENSEVOICE_MODEL = "sensevoice-small-int8-2024-07-17"
LOCAL_ASR_ENGINES = frozenset({FASTER_WHISPER_ENGINE, SENSEVOICE_ENGINE})

_SENSEVOICE_OPTIONS: Mapping[str, object] = {
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


class LocalAsrSnapshotError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LocalAsrSelection:
    engine: LocalAsrEngine
    model: str
    options: Mapping[str, object]

    @property
    def provider(self) -> str:
        return (
            "faster-whisper"
            if self.engine == FASTER_WHISPER_ENGINE
            else "sherpa-onnx"
        )

    @property
    def evidence_provider(self) -> str:
        return self.engine


def build_local_asr_snapshot(engine: str) -> dict[str, object]:
    if engine == FASTER_WHISPER_ENGINE:
        return {
            "schema_version": 1,
            "engine": FASTER_WHISPER_ENGINE,
            "model": FASTER_WHISPER_MODEL,
            "options": {},
        }
    if engine == SENSEVOICE_ENGINE:
        return {
            "schema_version": 1,
            "engine": SENSEVOICE_ENGINE,
            "model": SENSEVOICE_MODEL,
            "options": dict(_SENSEVOICE_OPTIONS),
        }
    raise LocalAsrSnapshotError("local_asr_engine_invalid")


def resolve_local_asr_snapshot(snapshot: Mapping[str, object]) -> LocalAsrSelection:
    raw = snapshot.get("local_asr")
    if raw is None:
        legacy = snapshot.get("local_whisper")
        if not isinstance(legacy, Mapping):
            raise LocalAsrSnapshotError("local_asr_snapshot_invalid")
        model = legacy.get("model")
        if not isinstance(model, str) or not model.strip():
            raise LocalAsrSnapshotError("local_asr_snapshot_invalid")
        return LocalAsrSelection(
            engine=FASTER_WHISPER_ENGINE,
            model=model,
            options=legacy,
        )
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"schema_version", "engine", "model", "options"}
        or raw.get("schema_version") != 1
    ):
        raise LocalAsrSnapshotError("local_asr_snapshot_invalid")
    engine = raw.get("engine")
    model = raw.get("model")
    options = raw.get("options")
    if (
        engine not in LOCAL_ASR_ENGINES
        or not isinstance(model, str)
        or not model.strip()
        or not isinstance(options, Mapping)
    ):
        raise LocalAsrSnapshotError("local_asr_snapshot_invalid")
    if engine == FASTER_WHISPER_ENGINE:
        whisper = snapshot.get("local_whisper")
        if model != FASTER_WHISPER_MODEL or not isinstance(whisper, Mapping):
            raise LocalAsrSnapshotError("local_asr_snapshot_invalid")
        return LocalAsrSelection(
            engine=FASTER_WHISPER_ENGINE,
            model=model,
            options=whisper,
        )
    if (
        engine == SENSEVOICE_ENGINE
        and model == SENSEVOICE_MODEL
        and dict(options) == dict(_SENSEVOICE_OPTIONS)
    ):
        return LocalAsrSelection(
            engine=SENSEVOICE_ENGINE,
            model=model,
            options=dict(options),
        )
    raise LocalAsrSnapshotError("local_asr_snapshot_invalid")
