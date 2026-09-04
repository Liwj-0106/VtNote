from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vtnote.local_asr import LocalAsrError, TranscriptionContext
from vtnote.local_asr_contract import (
    SENSEVOICE_ENGINE,
    SENSEVOICE_MODEL,
    build_local_asr_snapshot,
)
from vtnote.media import MediaInfo, PreparedAudio
from vtnote.model_assets import ModelFile, ModelManifest
from vtnote.sensevoice_asr import SenseVoiceTranscriber


@dataclass
class FakeAssets:
    installed_path: Path
    manifest: ModelManifest
    calls: int = 0

    def require_installed_path(self) -> Path:
        self.calls += 1
        return self.installed_path


class FakeStream:
    def __init__(self) -> None:
        self.waveforms: list[tuple[int, np.ndarray]] = []
        self.result = SimpleNamespace(text=None, lang=None)

    def accept_waveform(self, sample_rate: int, samples: np.ndarray) -> None:
        self.waveforms.append((sample_rate, samples.copy()))


class FakeRecognizer:
    def __init__(self) -> None:
        self.streams: list[FakeStream] = []
        self.decode_calls = 0

    def create_stream(self) -> FakeStream:
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def decode_stream(self, stream: FakeStream) -> None:
        self.decode_calls += 1
        stream.result = SimpleNamespace(text="  测试结果。  ", lang="<|zh|>")

    def decode_streams(self, streams: list[FakeStream]) -> None:
        for stream in streams:
            self.decode_stream(stream)


class FakeVadModelConfig:
    def __init__(self) -> None:
        self.silero_vad = SimpleNamespace(
            model="",
            threshold=0.0,
            min_silence_duration=0.0,
            min_speech_duration=0.0,
            max_speech_duration=0.0,
            window_size=512,
        )
        self.sample_rate = 0
        self.provider = ""

    def validate(self) -> bool:
        return bool(self.silero_vad.model) and self.sample_rate == 16_000


class FakeVad:
    def __init__(
        self,
        config: FakeVadModelConfig,
        *,
        buffer_size_in_seconds: int,
    ) -> None:
        self.config = config
        self.buffer_size_in_seconds = buffer_size_in_seconds
        self._accepted: list[np.ndarray] = []
        self._segments: list[object] = []

    def accept_waveform(self, samples: np.ndarray) -> None:
        self._accepted.append(np.asarray(samples, dtype=np.float32).copy())

    def flush(self) -> None:
        if self._accepted:
            self._segments.append(
                SimpleNamespace(
                    start=160,
                    samples=np.concatenate(self._accepted),
                )
            )

    def empty(self) -> bool:
        return not self._segments

    @property
    def front(self) -> object:
        return self._segments[0]

    def pop(self) -> None:
        self._segments.pop(0)


def _manifest(
    *,
    model_name: str,
    revision: str,
    files: tuple[ModelFile, ...],
    manifest_hash: str,
) -> ModelManifest:
    return ModelManifest(
        schema_version=1,
        model_name=model_name,
        repo_id=f"example/{model_name}",
        revision=revision,
        files=files,
        manifest_sha256=manifest_hash,
    )


def _assets(tmp_path: Path) -> tuple[FakeAssets, FakeAssets]:
    model_root = tmp_path / "sensevoice"
    model_root.mkdir()
    (model_root / "model.int8.onnx").write_bytes(b"model")
    (model_root / "tokens.txt").write_text("tokens", encoding="utf-8")
    model_assets = FakeAssets(
        model_root,
        _manifest(
            model_name=SENSEVOICE_MODEL,
            revision="1" * 40,
            files=(
                ModelFile("model.int8.onnx", 5, "2" * 64),
                ModelFile("tokens.txt", 6, "3" * 64),
            ),
            manifest_hash="4" * 64,
        ),
    )

    vad_root = tmp_path / "silero-vad"
    vad_root.mkdir()
    (vad_root / "silero_vad.onnx").write_bytes(b"vad")
    vad_assets = FakeAssets(
        vad_root,
        _manifest(
            model_name="silero-vad-v4",
            revision="5" * 40,
            files=(ModelFile("silero_vad.onnx", 3, "6" * 64),),
            manifest_hash="7" * 64,
        ),
    )
    return model_assets, vad_assets


def _audio(tmp_path: Path) -> PreparedAudio:
    path = tmp_path / "prepared.wav"
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(bytes(1_024 * 2))
    return PreparedAudio(
        path=path,
        asset_id="asset-1",
        converted=True,
        media_info=MediaInfo(
            duration_ms=64,
            size_bytes=path.stat().st_size,
            format_name="wav",
            audio_codec="pcm_s16le",
            sample_rate=16_000,
            channels=1,
        ),
    )


def _context(
    *,
    canceled=lambda: False,
    loader=None,
    saver=None,
    progress=None,
) -> TranscriptionContext:
    snapshot = build_local_asr_snapshot(SENSEVOICE_ENGINE)
    return TranscriptionContext(
        local_whisper=snapshot["options"],
        cancel_requested=canceled,
        local_engine=SENSEVOICE_ENGINE,
        chunk_loader=loader,
        chunk_saver=saver,
        progress_reporter=progress,
    )


def _transcriber(
    tmp_path: Path,
) -> tuple[SenseVoiceTranscriber, FakeAssets, FakeAssets, FakeRecognizer, list[dict[str, object]]]:
    model_assets, vad_assets = _assets(tmp_path)
    recognizer = FakeRecognizer()
    factory_calls: list[dict[str, object]] = []

    def create_recognizer(**kwargs: object) -> FakeRecognizer:
        factory_calls.append(dict(kwargs))
        return recognizer

    module = SimpleNamespace(
        OfflineRecognizer=SimpleNamespace(from_sense_voice=create_recognizer),
        VadModelConfig=FakeVadModelConfig,
        VoiceActivityDetector=FakeVad,
    )
    return (
        SenseVoiceTranscriber(
            assets=model_assets,
            vad_assets=vad_assets,
            module_loader=lambda: module,
            library_versions=lambda: {
                "sherpa_onnx": "1.13.6",
                "onnxruntime": "1.23.2",
                "numpy": "2.4.1",
            },
        ),
        model_assets,
        vad_assets,
        recognizer,
        factory_calls,
    )


def test_transcribe_uses_pinned_cpu_runtime_and_emits_timestamped_result(
    tmp_path: Path,
) -> None:
    transcriber, model_assets, vad_assets, recognizer, factory_calls = _transcriber(
        tmp_path
    )
    checkpoints: dict[int, dict[str, object]] = {}
    progress: list[tuple[int, int]] = []

    result = transcriber.transcribe(
        _audio(tmp_path),
        _context(
            saver=lambda index, payload: checkpoints.__setitem__(index, payload),
            progress=lambda current, total: progress.append((current, total)),
        ),
    )

    assert factory_calls == [
        {
            "model": str(model_assets.installed_path / "model.int8.onnx"),
            "tokens": str(model_assets.installed_path / "tokens.txt"),
            "num_threads": 4,
            "sample_rate": 16_000,
            "provider": "cpu",
            "language": "auto",
            "use_itn": True,
            "debug": False,
        }
    ]
    assert recognizer.decode_calls == 1
    assert progress == [(1, 1)]
    assert checkpoints[0]["text"] == "测试结果。"
    assert result.runtime_device == "cpu"
    assert result.transcript.language == "zh-Hans"
    assert result.transcript.provenance.model_dump(mode="json") == {
        "method": "local_asr",
        "provider": "sherpa-onnx",
        "model": SENSEVOICE_MODEL,
    }
    assert [segment.model_dump() for segment in result.transcript.segments] == [
        {
            "id": "seg_000001",
            "start_ms": 10,
            "end_ms": 74,
            "text": "测试结果。",
        }
    ]
    assert result.provenance.model_revision == model_assets.manifest.revision
    assert result.provenance.source_revision == vad_assets.manifest.revision
    assert result.provenance.runtime_manifest_sha256 == (
        vad_assets.manifest.manifest_sha256
    )


def test_completed_vad_segment_is_recovered_without_decoding_again(
    tmp_path: Path,
) -> None:
    transcriber, _, _, recognizer, _ = _transcriber(tmp_path)
    checkpoints: dict[int, dict[str, object]] = {}
    selected_audio = _audio(tmp_path)
    first = transcriber.transcribe(
        selected_audio,
        _context(
            saver=lambda index, payload: checkpoints.__setitem__(index, payload)
        ),
    )
    second = transcriber.transcribe(
        selected_audio,
        _context(loader=checkpoints.get),
    )

    assert first.transcript == second.transcript
    assert recognizer.decode_calls == 1


def test_cancellation_before_preflight_does_not_touch_assets(tmp_path: Path) -> None:
    transcriber, model_assets, vad_assets, recognizer, factory_calls = _transcriber(
        tmp_path
    )

    with pytest.raises(LocalAsrError) as caught:
        transcriber.transcribe(
            _audio(tmp_path),
            _context(canceled=lambda: True),
        )

    assert caught.value.code == "local_asr_canceled"
    assert model_assets.calls == 0
    assert vad_assets.calls == 0
    assert recognizer.decode_calls == 0
    assert factory_calls == []
