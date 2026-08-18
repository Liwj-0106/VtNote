from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vtnote.local_asr import (
    FasterWhisperTranscriber,
    LocalAsrError,
    TranscriptionContext,
)
from vtnote.media import MediaInfo, PreparedAudio
from vtnote.model_assets import ModelFile, ModelManifest


@dataclass
class FakeAssets:
    installed_path: Path
    manifest: ModelManifest
    calls: int = 0

    def require_installed_path(self) -> Path:
        self.calls += 1
        return self.installed_path


class FakeModel:
    def __init__(
        self,
        segments: list[object],
        *,
        language: str = "zh",
        on_yield: Any = None,
    ) -> None:
        self._segments = segments
        self._language = language
        self._on_yield = on_yield
        self.calls: list[tuple[str, dict[str, object]]] = []

    def transcribe(self, audio: str, **kwargs: object):
        self.calls.append((audio, dict(kwargs)))

        def lazy_segments():
            for index, segment in enumerate(self._segments):
                if self._on_yield is not None:
                    self._on_yield(index)
                yield segment

        return lazy_segments(), SimpleNamespace(language=self._language)


def manifest() -> ModelManifest:
    return ModelManifest(
        schema_version=1,
        model_name="large-v3-turbo",
        repo_id="dropbox-dash/faster-whisper-large-v3-turbo",
        revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        files=(
            ModelFile("config.json", 1, "1" * 64),
            ModelFile("model.bin", 2, "2" * 64),
        ),
        manifest_sha256="3" * 64,
    )


def audio(tmp_path: Path, *, duration_ms: int = 5_000) -> PreparedAudio:
    path = tmp_path / "prepared.wav"
    path.write_bytes(b"RIFF-audio")
    return PreparedAudio(
        path=path,
        asset_id="asset-1",
        converted=True,
        media_info=MediaInfo(
            duration_ms=duration_ms,
            size_bytes=path.stat().st_size,
            format_name="wav",
            audio_codec="pcm_s16le",
            sample_rate=16_000,
            channels=1,
        ),
    )


def context(
    tmp_path: Path,
    *,
    device: str = "cuda",
    canceled=lambda: False,
) -> TranscriptionContext:
    return TranscriptionContext(
        local_whisper={
            "model": "large-v3-turbo",
            "device": device,
            "compute_type": "int8_float16",
            "vad_filter": True,
            "model_root": str(tmp_path / "data" / "models" / "faster-whisper"),
            "cache_root": str(tmp_path / "cache" / "models" / "faster-whisper"),
        },
        cancel_requested=canceled,
    )


def make_transcriber(
    tmp_path: Path,
    model: FakeModel,
    *,
    cuda_devices: int = 1,
) -> tuple[FasterWhisperTranscriber, FakeAssets, list[dict[str, object]]]:
    installed = tmp_path / "data" / "models" / "large-v3-turbo" / manifest().revision
    installed.mkdir(parents=True)
    assets = FakeAssets(installed, manifest())
    factory_calls: list[dict[str, object]] = []

    def factory(**kwargs: object) -> FakeModel:
        factory_calls.append(dict(kwargs))
        return model

    return (
        FasterWhisperTranscriber(
            assets=assets,
            expected_model_root=tmp_path / "data" / "models" / "faster-whisper",
            expected_cache_root=tmp_path / "cache" / "models" / "faster-whisper",
            model_factory=factory,
            cuda_device_count=lambda: cuda_devices,
            library_versions=lambda: {
                "faster_whisper": "1.2.1",
                "ctranslate2": "4.8.1",
                "cuda_runtime": "12.8.90",
                "cublas": "12.8.4.1",
                "cudnn": "9.10.2.21",
            },
        ),
        assets,
        factory_calls,
    )


def test_model_import_and_creation_are_lazy_and_gpu_options_are_exact(
    tmp_path: Path,
) -> None:
    model = FakeModel([SimpleNamespace(start=0.0, end=1.0, text="hello")])
    transcriber, assets, factory_calls = make_transcriber(tmp_path, model)

    assert assets.calls == 0
    assert factory_calls == []

    result = transcriber.transcribe(audio(tmp_path), context(tmp_path))

    assert assets.calls == 1
    assert factory_calls == [
        {
            "model_size_or_path": str(assets.installed_path),
            "device": "cuda",
            "compute_type": "int8_float16",
            "download_root": str(
                tmp_path / "cache" / "models" / "faster-whisper"
            ),
            "local_files_only": True,
        }
    ]
    assert model.calls == [
        (
            str(audio(tmp_path).path),
            {
                "task": "transcribe",
                "vad_filter": True,
                "word_timestamps": False,
            },
        )
    ]
    assert result.transcript.segments[0].text == "hello"


def test_availability_preflight_loads_runtime_without_touching_audio(
    tmp_path: Path,
) -> None:
    model = FakeModel([SimpleNamespace(start=0.0, end=1.0, text="hello")])
    transcriber, assets, factory_calls = make_transcriber(tmp_path, model)

    available = transcriber.ensure_available(context(tmp_path))

    assert available is model
    assert assets.calls == 1
    assert len(factory_calls) == 1
    assert model.calls == []


def test_cuda_unavailable_is_explicit_and_never_creates_cpu_model(
    tmp_path: Path,
) -> None:
    model = FakeModel([SimpleNamespace(start=0.0, end=1.0, text="hello")])
    transcriber, assets, factory_calls = make_transcriber(
        tmp_path, model, cuda_devices=0
    )

    with pytest.raises(LocalAsrError) as caught:
        transcriber.transcribe(audio(tmp_path), context(tmp_path))

    assert caught.value.code == "local_asr_cuda_unavailable"
    assert assets.calls == 0
    assert factory_calls == []


def test_lazy_segment_generator_is_fully_iterated_and_normalized(
    tmp_path: Path,
) -> None:
    yielded: list[int] = []
    model = FakeModel(
        [
            SimpleNamespace(start=0.0004, end=1.2346, text="  第一段  "),
            SimpleNamespace(start=1.5, end=3.0, text="\nSecond line\t"),
        ],
        on_yield=lambda index: yielded.append(index),
    )
    transcriber, _, _ = make_transcriber(tmp_path, model)

    result = transcriber.transcribe(audio(tmp_path), context(tmp_path))

    assert yielded == [0, 1]
    assert result.transcript.language == "zh-Hans"
    assert result.transcript.duration_ms == 5_000
    assert [
        segment.model_dump() for segment in result.transcript.segments
    ] == [
        {
            "id": "seg_000001",
            "start_ms": 0,
            "end_ms": 1_235,
            "text": "第一段",
        },
        {
            "id": "seg_000002",
            "start_ms": 1_500,
            "end_ms": 3_000,
            "text": "Second line",
        },
    ]


def test_cancellation_is_checked_between_lazy_segments(tmp_path: Path) -> None:
    yielded: list[int] = []
    canceled = False

    def mark_after_first(index: int) -> None:
        nonlocal canceled
        yielded.append(index)
        if index == 0:
            canceled = True

    model = FakeModel(
        [
            SimpleNamespace(start=0.0, end=1.0, text="first"),
            SimpleNamespace(start=1.0, end=2.0, text="second"),
        ],
        on_yield=mark_after_first,
    )
    transcriber, _, _ = make_transcriber(tmp_path, model)

    with pytest.raises(LocalAsrError) as caught:
        transcriber.transcribe(
            audio(tmp_path),
            context(tmp_path, canceled=lambda: canceled),
        )

    assert caught.value.code == "local_asr_canceled"
    assert yielded == [0]


def test_legacy_task_snapshot_auto_is_rejected_with_retry_action(
    tmp_path: Path,
) -> None:
    model = FakeModel([SimpleNamespace(start=0.0, end=1.0, text="hello")])
    transcriber, _, factory_calls = make_transcriber(tmp_path, model)

    with pytest.raises(LocalAsrError) as caught:
        transcriber.transcribe(
            audio(tmp_path),
            context(tmp_path, device="auto"),
        )

    assert caught.value.code == "legacy_local_asr_snapshot_requires_retry"
    assert caught.value.retry_action == "create_new_task_with_current_settings"
    assert factory_calls == []


def test_provenance_has_model_revision_file_hashes_and_cuda_library_versions(
    tmp_path: Path,
) -> None:
    model = FakeModel([SimpleNamespace(start=0.0, end=1.0, text="hello")])
    transcriber, assets, _ = make_transcriber(tmp_path, model)

    result = transcriber.transcribe(audio(tmp_path), context(tmp_path))

    assert result.transcript.provenance.model_dump(mode="json") == {
        "method": "local_asr",
        "provider": "faster-whisper",
        "model": "large-v3-turbo",
    }
    assert result.provenance.model_revision == assets.manifest.revision
    assert result.provenance.model_file_sha256 == {
        "config.json": "1" * 64,
        "model.bin": "2" * 64,
    }
    assert result.provenance.library_versions == {
        "faster_whisper": "1.2.1",
        "ctranslate2": "4.8.1",
        "cuda_runtime": "12.8.90",
        "cublas": "12.8.4.1",
        "cudnn": "9.10.2.21",
    }
