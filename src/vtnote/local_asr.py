"""GPU-only local transcription using an explicitly installed Whisper model."""

from __future__ import annotations

import importlib.metadata
import json
import math
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from vtnote.media import PreparedAudio
from vtnote.model_assets import ModelAssetError, ModelAssetService, ModelManifest
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
)


class LocalAsrError(RuntimeError):
    """A stable worker-facing local ASR failure without raw runtime details."""

    def __init__(self, code: str, *, retry_action: str | None = None) -> None:
        self.code = code
        self.retry_action = retry_action
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TranscriptionContext:
    """Immutable task-local ASR settings plus a cooperative cancel check."""

    local_whisper: Mapping[str, object]
    cancel_requested: Callable[[], bool]
    language: str | None = None


@dataclass(frozen=True, slots=True)
class LocalAsrProvenance:
    model_revision: str
    model_manifest_sha256: str
    model_file_sha256: dict[str, str]
    library_versions: dict[str, str]

    def as_evidence(self) -> dict[str, object]:
        return {
            "provider": "faster-whisper",
            "model_revision": self.model_revision,
            "model_manifest_sha256": self.model_manifest_sha256,
            "model_file_sha256": dict(self.model_file_sha256),
            "library_versions": dict(self.library_versions),
        }


@dataclass(frozen=True, slots=True)
class AsrResult:
    transcript: Transcript
    provenance: LocalAsrProvenance


class WhisperModelLike(Protocol):
    def transcribe(self, audio: str, **kwargs: object) -> tuple[object, object]: ...


class InstalledModelAssets(Protocol):
    manifest: ModelManifest

    def require_installed_path(self) -> Path: ...


ModelFactory = Callable[..., WhisperModelLike]


_FIXED_MODEL = "large-v3-turbo"
_FIXED_DEVICE = "cuda"
_FIXED_COMPUTE_TYPE = "int8_float16"
_FIXED_VAD = True
_EXPECTED_OPTION_KEYS = frozenset(
    {
        "model",
        "device",
        "compute_type",
        "vad_filter",
        "model_root",
        "cache_root",
    }
)


def _default_model_factory(**kwargs: object) -> WhisperModelLike:
    # Keep the heavyweight runtime lazy so API/configuration processes do not
    # initialize CUDA merely by importing the application package.
    from faster_whisper import WhisperModel

    return WhisperModel(**kwargs)


def _default_cuda_device_count() -> int:
    import ctranslate2

    return int(ctranslate2.get_cuda_device_count())


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _conda_native_versions() -> dict[str, str]:
    wanted = {
        "cuda-cudart": "cuda_runtime",
        "libcublas": "cublas",
        "libcudnn": "cudnn",
    }
    found: dict[str, str] = {}
    metadata_root = Path(sys.prefix) / "conda-meta"
    try:
        candidates = tuple(metadata_root.glob("*.json"))
    except OSError:
        candidates = ()
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        key = wanted.get(payload.get("name"))
        version = payload.get("version")
        if key is not None and isinstance(version, str) and version:
            found[key] = version
    return found


def _default_library_versions() -> dict[str, str]:
    native = _conda_native_versions()
    return {
        "faster_whisper": _package_version("faster-whisper"),
        "ctranslate2": _package_version("ctranslate2"),
        "cuda_runtime": native.get("cuda_runtime", "unknown"),
        "cublas": native.get("cublas", "unknown"),
        "cudnn": native.get("cudnn", "unknown"),
    }


def _normalized_language(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "und"
    language = value.strip().replace("_", "-")
    lowered = language.casefold()
    if lowered in {"zh", "zh-cn", "zh-hans"}:
        return "zh-Hans"
    if lowered in {"zh-tw", "zh-hk", "zh-hant"}:
        return "zh-Hant"
    return language


def _same_path(left: object, right: Path) -> bool:
    if not isinstance(left, str) or not left.strip():
        return False
    try:
        return Path(left).resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return False


class FasterWhisperTranscriber:
    """Create one CUDA model lazily and fully consume its segment generator."""

    def __init__(
        self,
        *,
        assets: InstalledModelAssets | ModelAssetService,
        expected_model_root: Path,
        expected_cache_root: Path,
        model_factory: ModelFactory | None = None,
        cuda_device_count: Callable[[], int] | None = None,
        library_versions: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.assets = assets
        self.expected_model_root = Path(expected_model_root).resolve(strict=False)
        self.expected_cache_root = Path(expected_cache_root).resolve(strict=False)
        self._model_factory = model_factory or _default_model_factory
        self._cuda_device_count = cuda_device_count or _default_cuda_device_count
        self._library_versions = library_versions or _default_library_versions
        self._model: WhisperModelLike | None = None
        self._model_path: Path | None = None

    @staticmethod
    def _raise_if_canceled(context: TranscriptionContext) -> None:
        try:
            canceled = context.cancel_requested()
        except Exception:
            raise LocalAsrError("local_asr_cancel_check_failed") from None
        if canceled:
            raise LocalAsrError("local_asr_canceled")

    def _validate_options(self, context: TranscriptionContext) -> Path:
        options = context.local_whisper
        if not isinstance(options, Mapping):
            raise LocalAsrError("local_asr_snapshot_invalid")
        if options.get("device") == "auto":
            raise LocalAsrError(
                "legacy_local_asr_snapshot_requires_retry",
                retry_action="create_new_task_with_current_settings",
            )
        if (
            set(options) != _EXPECTED_OPTION_KEYS
            or options.get("model") != _FIXED_MODEL
            or options.get("device") != _FIXED_DEVICE
            or options.get("compute_type") != _FIXED_COMPUTE_TYPE
            or options.get("vad_filter") is not _FIXED_VAD
            or not _same_path(options.get("model_root"), self.expected_model_root)
            or not _same_path(options.get("cache_root"), self.expected_cache_root)
        ):
            raise LocalAsrError("local_asr_snapshot_invalid")
        return self.expected_cache_root

    @staticmethod
    def _validate_audio(audio: PreparedAudio) -> None:
        info = audio.media_info
        if (
            not Path(audio.path).is_file()
            or info.audio_codec != "pcm_s16le"
            or info.sample_rate != 16_000
            or info.channels != 1
            or info.duration_ms <= 0
        ):
            raise LocalAsrError("local_asr_audio_invalid")

    def _cuda_ready(self) -> None:
        try:
            available = self._cuda_device_count()
        except Exception:
            raise LocalAsrError("local_asr_runtime_unavailable") from None
        if isinstance(available, bool) or not isinstance(available, int) or available < 1:
            raise LocalAsrError("local_asr_cuda_unavailable")

    def _load_model(self, cache_root: Path) -> WhisperModelLike:
        try:
            installed_path = self.assets.require_installed_path().resolve(strict=False)
        except ModelAssetError as error:
            raise LocalAsrError(error.code) from None
        except OSError:
            raise LocalAsrError("model_not_installed") from None
        authoritative_path = getattr(self.assets, "install_root", installed_path)
        try:
            authoritative_path = Path(authoritative_path).resolve(strict=False)
        except (TypeError, OSError):
            raise LocalAsrError("local_asr_model_path_invalid") from None
        if installed_path != authoritative_path:
            raise LocalAsrError("local_asr_model_path_invalid")
        if self._model is not None:
            if self._model_path != installed_path:
                raise LocalAsrError("local_asr_model_revision_changed")
            return self._model
        try:
            model = self._model_factory(
                model_size_or_path=str(installed_path),
                device=_FIXED_DEVICE,
                compute_type=_FIXED_COMPUTE_TYPE,
                download_root=str(cache_root),
                local_files_only=True,
            )
        except Exception:
            raise LocalAsrError("local_asr_model_load_failed") from None
        self._model = model
        self._model_path = installed_path
        return model

    @staticmethod
    def _segment(value: object, index: int) -> TranscriptSegment:
        start = getattr(value, "start", None)
        end = getattr(value, "end", None)
        text = getattr(value, "text", None)
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or not math.isfinite(start)
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
            or not math.isfinite(end)
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise LocalAsrError("local_asr_result_invalid")
        start_ms = max(0, round(float(start) * 1000))
        end_ms = round(float(end) * 1000)
        if end_ms <= start_ms:
            raise LocalAsrError("local_asr_result_invalid")
        return TranscriptSegment(
            id=f"seg_{index:06d}",
            start_ms=start_ms,
            end_ms=end_ms,
            text=text.strip(),
        )

    def _provenance(self) -> LocalAsrProvenance:
        manifest = self.assets.manifest
        try:
            versions = {
                str(name): str(version)
                for name, version in self._library_versions().items()
            }
        except Exception:
            raise LocalAsrError("local_asr_runtime_evidence_unavailable") from None
        expected_versions = {
            "faster_whisper",
            "ctranslate2",
            "cuda_runtime",
            "cublas",
            "cudnn",
        }
        if set(versions) != expected_versions or any(
            not value for value in versions.values()
        ):
            raise LocalAsrError("local_asr_runtime_evidence_unavailable")
        return LocalAsrProvenance(
            model_revision=manifest.revision,
            model_manifest_sha256=manifest.manifest_sha256,
            model_file_sha256={
                item.path: item.sha256 for item in manifest.files
            },
            library_versions=versions,
        )

    def transcribe(
        self,
        audio: PreparedAudio,
        context: TranscriptionContext,
    ) -> AsrResult:
        cache_root = self._validate_options(context)
        self._validate_audio(audio)
        self._raise_if_canceled(context)
        self._cuda_ready()
        model = self._load_model(cache_root)
        self._raise_if_canceled(context)
        kwargs: dict[str, object] = {
            "task": "transcribe",
            "vad_filter": _FIXED_VAD,
            "word_timestamps": False,
        }
        if context.language is not None:
            kwargs["language"] = context.language
        try:
            raw_segments, info = model.transcribe(str(audio.path), **kwargs)
            iterator = iter(raw_segments)  # type: ignore[arg-type]
        except LocalAsrError:
            raise
        except Exception:
            raise LocalAsrError("local_asr_inference_failed") from None

        segments: list[TranscriptSegment] = []
        try:
            while True:
                self._raise_if_canceled(context)
                try:
                    raw = next(iterator)
                except StopIteration:
                    break
                self._raise_if_canceled(context)
                segments.append(self._segment(raw, len(segments) + 1))
        except LocalAsrError:
            raise
        except Exception as error:
            message = str(error).casefold()
            code = (
                "local_asr_cuda_out_of_memory"
                if "out of memory" in message or "cuda_error_out_of_memory" in message
                else "local_asr_inference_failed"
            )
            raise LocalAsrError(code) from None
        if not segments:
            raise LocalAsrError("local_asr_result_empty")
        timeline = [(item.start_ms, item.end_ms) for item in segments]
        if timeline != sorted(timeline):
            raise LocalAsrError("local_asr_result_invalid")

        duration_ms = max(
            audio.media_info.duration_ms,
            max(item.end_ms for item in segments),
        )
        transcript = Transcript(
            language=_normalized_language(getattr(info, "language", None)),
            duration_ms=duration_ms,
            provenance=Provenance(
                method=ProvenanceMethod.LOCAL_ASR,
                provider="faster-whisper",
                model=_FIXED_MODEL,
            ),
            segments=tuple(segments),
        )
        return AsrResult(transcript=transcript, provenance=self._provenance())
