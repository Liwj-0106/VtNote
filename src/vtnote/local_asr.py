"""Recoverable local transcription using the pinned faster-whisper model."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vtnote.media import PreparedAudio
from vtnote.model_assets import ModelAssetError, ModelAssetService, ModelManifest
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    SpeakerMap,
    Transcript,
    TranscriptAlignment,
    TranscriptSegment,
    WordTiming,
    transcript_sha256,
)


class LocalAsrError(RuntimeError):
    def __init__(self, code: str, *, retry_action: str | None = None) -> None:
        self.code = code
        self.retry_action = retry_action
        super().__init__(code)


ChunkLoader = Callable[[int], Mapping[str, object] | None]
ChunkSaver = Callable[[int, dict[str, object]], None]
ProgressReporter = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class TranscriptionContext:
    local_whisper: Mapping[str, object]
    cancel_requested: Callable[[], bool]
    local_engine: str = "faster_whisper"
    language: str | None = None
    chunk_loader: ChunkLoader | None = None
    chunk_saver: ChunkSaver | None = None
    progress_reporter: ProgressReporter | None = None


@dataclass(frozen=True, slots=True)
class LocalAsrProvenance:
    model_revision: str
    model_manifest_sha256: str
    model_file_sha256: dict[str, str]
    library_versions: dict[str, str]
    provider: str = "faster-whisper"
    model: str = "large-v3-turbo"
    source_revision: str | None = None
    runtime_manifest_sha256: str | None = None

    def as_evidence(self) -> dict[str, object]:
        evidence = {
            "provider": self.provider,
            "model": self.model,
            "model_revision": self.model_revision,
            "model_manifest_sha256": self.model_manifest_sha256,
            "model_file_sha256": dict(self.model_file_sha256),
            "library_versions": dict(self.library_versions),
        }
        if self.source_revision is not None:
            evidence["source_revision"] = self.source_revision
        if self.runtime_manifest_sha256 is not None:
            evidence["runtime_manifest_sha256"] = self.runtime_manifest_sha256
        return evidence


@dataclass(frozen=True, slots=True)
class AsrResult:
    transcript: Transcript
    provenance: LocalAsrProvenance
    alignment: TranscriptAlignment | None = None
    detected_language_probability: float | None = None
    runtime_device: str = "cuda"
    recovered_chunks: int = 0
    speakers: SpeakerMap | None = None


@dataclass(frozen=True, slots=True)
class _Runtime:
    device: str
    compute_type: str


@dataclass(frozen=True, slots=True)
class _Options:
    schema_version: int
    cpu_fallback_enabled: bool
    word_timestamps: bool
    punctuation_normalization: bool
    speaker_diarization_enabled: bool
    chunk_duration_ms: int
    chunk_overlap_ms: int
    vad_parameters: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class _RawWord:
    start_ms: int
    end_ms: int
    text: str
    probability: float | None


@dataclass(frozen=True, slots=True)
class _RawSegment:
    start_ms: int
    end_ms: int
    text: str
    words: tuple[_RawWord, ...]


class WhisperModelLike(Protocol):
    def transcribe(self, audio: str, **kwargs: object) -> tuple[object, object]: ...


class InstalledModelAssets(Protocol):
    manifest: ModelManifest

    def require_installed_path(self) -> Path: ...


ModelFactory = Callable[..., WhisperModelLike]
_FIXED_MODEL = "large-v3-turbo"
_FIXED_DEVICE = "cuda"
_FIXED_COMPUTE_TYPE = "int8_float16"
_LEGACY_OPTION_KEYS = frozenset(
    {"model", "device", "compute_type", "vad_filter", "model_root", "cache_root"}
)
_V2_OPTION_KEYS = _LEGACY_OPTION_KEYS | frozenset(
    {
        "schema_version",
        "cpu_fallback_enabled",
        "word_timestamps",
        "punctuation_normalization",
        "speaker_diarization_enabled",
        "chunk_duration_ms",
        "chunk_overlap_ms",
        "vad_parameters",
    }
)
_WHITESPACE = re.compile(r"[\t \u00a0]+")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([，。！？；：、,.!?;:])")


def _default_model_factory(**kwargs: object) -> WhisperModelLike:
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
    wanted = {"cuda-cudart": "cuda_runtime", "libcublas": "cublas", "libcudnn": "cudnn"}
    found: dict[str, str] = {}
    try:
        candidates = tuple((Path(sys.prefix) / "conda-meta").glob("*.json"))
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


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _probability(value: object) -> float | None:
    return min(1.0, max(0.0, float(value))) if _finite_number(value) else None


def _normalized_text(value: str, *, enabled: bool) -> str:
    text = value.strip()
    if enabled:
        text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", _WHITESPACE.sub(" ", text)).strip()
    return text


class FasterWhisperTranscriber:
    """Run the pinned model with bounded chunks and durable resume callbacks."""

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
        self._models: dict[_Runtime, WhisperModelLike] = {}
        self._model_path: Path | None = None

    @staticmethod
    def _raise_if_canceled(context: TranscriptionContext) -> None:
        try:
            canceled = context.cancel_requested()
        except Exception:
            raise LocalAsrError("local_asr_cancel_check_failed") from None
        if canceled:
            raise LocalAsrError("local_asr_canceled")

    def _validate_options(self, context: TranscriptionContext) -> tuple[Path, _Options]:
        options = context.local_whisper
        if not isinstance(options, Mapping):
            raise LocalAsrError("local_asr_snapshot_invalid")
        if options.get("device") == "auto":
            raise LocalAsrError(
                "legacy_local_asr_snapshot_requires_retry",
                retry_action="create_new_task_with_current_settings",
            )
        keys = set(options)
        legacy = keys == _LEGACY_OPTION_KEYS
        if not legacy and keys != _V2_OPTION_KEYS:
            raise LocalAsrError("local_asr_snapshot_invalid")
        if (
            options.get("model") != _FIXED_MODEL
            or options.get("device") != _FIXED_DEVICE
            or options.get("compute_type") != _FIXED_COMPUTE_TYPE
            or options.get("vad_filter") is not True
            or not _same_path(options.get("model_root"), self.expected_model_root)
            or not _same_path(options.get("cache_root"), self.expected_cache_root)
        ):
            raise LocalAsrError("local_asr_snapshot_invalid")
        if legacy:
            return self.expected_cache_root, _Options(1, False, False, False, False, 0, 0, None)
        boolean_keys = (
            "cpu_fallback_enabled",
            "word_timestamps",
            "punctuation_normalization",
            "speaker_diarization_enabled",
        )
        duration = options.get("chunk_duration_ms")
        overlap = options.get("chunk_overlap_ms")
        vad_parameters = options.get("vad_parameters")
        if (
            options.get("schema_version") != 2
            or any(type(options.get(key)) is not bool for key in boolean_keys)
            or type(duration) is not int
            or not 60_000 <= duration <= 3_600_000
            or type(overlap) is not int
            or not 0 <= overlap < min(duration, 60_000)
            or not isinstance(vad_parameters, Mapping)
        ):
            raise LocalAsrError("local_asr_snapshot_invalid")
        return self.expected_cache_root, _Options(
            2,
            bool(options["cpu_fallback_enabled"]),
            bool(options["word_timestamps"]),
            bool(options["punctuation_normalization"]),
            bool(options["speaker_diarization_enabled"]),
            duration,
            overlap,
            dict(vad_parameters),
        )

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

    def _select_runtime(self, options: _Options) -> _Runtime:
        try:
            available = self._cuda_device_count()
        except Exception:
            if options.cpu_fallback_enabled:
                return _Runtime("cpu", "int8")
            raise LocalAsrError("local_asr_runtime_unavailable") from None
        if not isinstance(available, bool) and isinstance(available, int) and available >= 1:
            return _Runtime("cuda", "int8_float16")
        if options.cpu_fallback_enabled:
            return _Runtime("cpu", "int8")
        raise LocalAsrError("local_asr_cuda_unavailable")

    def _installed_path(self) -> Path:
        try:
            installed_path = self.assets.require_installed_path().resolve(strict=False)
        except ModelAssetError as error:
            raise LocalAsrError(error.code) from None
        except OSError:
            raise LocalAsrError("model_not_installed") from None
        authoritative = getattr(self.assets, "install_root", installed_path)
        try:
            authoritative_path = Path(authoritative).resolve(strict=False)
        except (TypeError, OSError):
            raise LocalAsrError("local_asr_model_path_invalid") from None
        if installed_path != authoritative_path:
            raise LocalAsrError("local_asr_model_path_invalid")
        if self._model_path is not None and self._model_path != installed_path:
            raise LocalAsrError("local_asr_model_revision_changed")
        self._model_path = installed_path
        return installed_path

    def _load_model(self, cache_root: Path, runtime: _Runtime) -> WhisperModelLike:
        installed_path = self._installed_path()
        if runtime in self._models:
            return self._models[runtime]
        try:
            model = self._model_factory(
                model_size_or_path=str(installed_path),
                device=runtime.device,
                compute_type=runtime.compute_type,
                download_root=str(cache_root),
                local_files_only=True,
            )
        except Exception:
            raise LocalAsrError("local_asr_model_load_failed") from None
        self._models[runtime] = model
        return model

    def _runtime_and_model(
        self, context: TranscriptionContext
    ) -> tuple[_Options, _Runtime, WhisperModelLike]:
        cache_root, options = self._validate_options(context)
        self._raise_if_canceled(context)
        runtime = self._select_runtime(options)
        try:
            model = self._load_model(cache_root, runtime)
        except LocalAsrError as error:
            if (
                runtime.device != "cuda"
                or not options.cpu_fallback_enabled
                or error.code != "local_asr_model_load_failed"
            ):
                raise
            runtime = _Runtime("cpu", "int8")
            model = self._load_model(cache_root, runtime)
        self._raise_if_canceled(context)
        return options, runtime, model

    def ensure_available(self, context: TranscriptionContext) -> WhisperModelLike:
        return self._runtime_and_model(context)[2]

    @staticmethod
    def _absolute_ms(value: float, *, chunk_start_ms: int, chunk_end_ms: int) -> int:
        milliseconds = round(value * 1000)
        if chunk_start_ms > 0 and milliseconds < chunk_start_ms - 1_000:
            if milliseconds <= chunk_end_ms - chunk_start_ms + 1_000:
                milliseconds += chunk_start_ms
        return max(0, milliseconds)

    def _segment(
        self,
        value: object,
        *,
        chunk_start_ms: int,
        chunk_end_ms: int,
        options: _Options,
    ) -> _RawSegment:
        start = getattr(value, "start", None)
        end = getattr(value, "end", None)
        text = getattr(value, "text", None)
        if not _finite_number(start) or not _finite_number(end) or not isinstance(text, str):
            raise LocalAsrError("local_asr_result_invalid")
        start_ms = self._absolute_ms(float(start), chunk_start_ms=chunk_start_ms, chunk_end_ms=chunk_end_ms)
        end_ms = self._absolute_ms(float(end), chunk_start_ms=chunk_start_ms, chunk_end_ms=chunk_end_ms)
        normalized = _normalized_text(text, enabled=options.punctuation_normalization)
        if end_ms <= start_ms or not normalized:
            raise LocalAsrError("local_asr_result_invalid")
        words: list[_RawWord] = []
        if options.word_timestamps:
            for raw_word in getattr(value, "words", None) or ():
                word_start = getattr(raw_word, "start", None)
                word_end = getattr(raw_word, "end", None)
                word_text = getattr(raw_word, "word", None)
                if not _finite_number(word_start) or not _finite_number(word_end) or not isinstance(word_text, str):
                    continue
                word_start_ms = self._absolute_ms(float(word_start), chunk_start_ms=chunk_start_ms, chunk_end_ms=chunk_end_ms)
                word_end_ms = self._absolute_ms(float(word_end), chunk_start_ms=chunk_start_ms, chunk_end_ms=chunk_end_ms)
                if word_end_ms > word_start_ms and word_text.strip():
                    words.append(_RawWord(word_start_ms, word_end_ms, word_text.strip(), _probability(getattr(raw_word, "probability", None))))
        return _RawSegment(start_ms, end_ms, normalized, tuple(words))

    @staticmethod
    def _chunk_signature(audio: PreparedAudio, context: TranscriptionContext, start_ms: int, end_ms: int) -> str:
        try:
            size = Path(audio.path).stat().st_size
        except OSError:
            raise LocalAsrError("local_asr_audio_invalid") from None
        payload = {
            "asset_id": audio.asset_id,
            "size": size,
            "duration_ms": audio.media_info.duration_ms,
            "options": dict(context.local_whisper),
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _serialize_chunk(
        signature: str,
        start_ms: int,
        end_ms: int,
        segments: tuple[_RawSegment, ...],
        language: str,
        probability: float | None,
        runtime: _Runtime,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "signature": signature,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "language": language,
            "language_probability": probability,
            "runtime_device": runtime.device,
            "segments": [
                {
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "words": [
                        {"start_ms": word.start_ms, "end_ms": word.end_ms, "text": word.text, "probability": word.probability}
                        for word in segment.words
                    ],
                }
                for segment in segments
            ],
        }

    @staticmethod
    def _restore_chunk(payload: Mapping[str, object], signature: str) -> tuple[tuple[_RawSegment, ...], str, float | None, str] | None:
        if payload.get("schema_version") != 1 or payload.get("signature") != signature:
            return None
        raw_segments = payload.get("segments")
        language = payload.get("language")
        device = payload.get("runtime_device")
        if not isinstance(raw_segments, list) or not isinstance(language, str) or device not in {"cuda", "cpu"}:
            return None
        segments: list[_RawSegment] = []
        try:
            for raw_segment in raw_segments:
                if not isinstance(raw_segment, dict):
                    return None
                raw_words = raw_segment.get("words", [])
                if not isinstance(raw_words, list):
                    return None
                words = tuple(
                    _RawWord(int(word["start_ms"]), int(word["end_ms"]), str(word["text"]), _probability(word.get("probability")))
                    for word in raw_words
                    if isinstance(word, dict)
                )
                segment = _RawSegment(int(raw_segment["start_ms"]), int(raw_segment["end_ms"]), str(raw_segment["text"]), words)
                if segment.end_ms <= segment.start_ms or not segment.text.strip():
                    return None
                segments.append(segment)
        except (KeyError, TypeError, ValueError):
            return None
        return tuple(segments), language, _probability(payload.get("language_probability")), str(device)

    def _infer_chunk(
        self,
        model: WhisperModelLike,
        runtime: _Runtime,
        audio: PreparedAudio,
        context: TranscriptionContext,
        options: _Options,
        start_ms: int,
        end_ms: int,
        *,
        clipped: bool,
    ) -> tuple[tuple[_RawSegment, ...], str, float | None]:
        kwargs: dict[str, object] = {"task": "transcribe", "vad_filter": True, "word_timestamps": options.word_timestamps}
        if options.vad_parameters is not None:
            kwargs["vad_parameters"] = dict(options.vad_parameters)
        if context.language is not None:
            kwargs["language"] = context.language
        if clipped:
            kwargs["clip_timestamps"] = f"{start_ms / 1000:.3f},{end_ms / 1000:.3f}"
        try:
            raw_segments, info = model.transcribe(str(audio.path), **kwargs)
            segments: list[_RawSegment] = []
            for raw in raw_segments:  # type: ignore[union-attr]
                self._raise_if_canceled(context)
                segments.append(self._segment(raw, chunk_start_ms=start_ms, chunk_end_ms=end_ms, options=options))
        except LocalAsrError:
            raise
        except Exception as error:
            message = str(error).casefold()
            if runtime.device == "cuda" and (
                "out of memory" in message or "cuda_error_out_of_memory" in message
            ):
                code = "local_asr_cuda_out_of_memory"
            elif runtime.device == "cuda" and any(
                token in message for token in ("cuda", "cudnn", "cublas")
            ):
                code = "local_asr_cuda_runtime_failed"
            else:
                code = "local_asr_inference_failed"
            raise LocalAsrError(code) from None
        return tuple(segments), _normalized_language(getattr(info, "language", None)), _probability(getattr(info, "language_probability", None))

    def _provenance(self) -> LocalAsrProvenance:
        manifest = self.assets.manifest
        try:
            versions = {str(name): str(version) for name, version in self._library_versions().items()}
        except Exception:
            raise LocalAsrError("local_asr_runtime_evidence_unavailable") from None
        expected = {"faster_whisper", "ctranslate2", "cuda_runtime", "cublas", "cudnn"}
        if set(versions) != expected or any(not value for value in versions.values()):
            raise LocalAsrError("local_asr_runtime_evidence_unavailable")
        return LocalAsrProvenance(
            model_revision=manifest.revision,
            model_manifest_sha256=manifest.manifest_sha256,
            model_file_sha256={item.path: item.sha256 for item in manifest.files},
            library_versions=versions,
        )

    @staticmethod
    def _chunk_ranges(duration_ms: int, options: _Options) -> tuple[tuple[int, int], ...]:
        if options.schema_version == 1 or duration_ms <= options.chunk_duration_ms:
            return ((0, duration_ms),)
        ranges: list[tuple[int, int]] = []
        start = 0
        while start < duration_ms:
            end = min(duration_ms, start + options.chunk_duration_ms)
            ranges.append((start, end))
            if end >= duration_ms:
                break
            start = end - options.chunk_overlap_ms
        return tuple(ranges)

    def transcribe(self, audio: PreparedAudio, context: TranscriptionContext) -> AsrResult:
        self._validate_audio(audio)
        options, runtime, model = self._runtime_and_model(context)
        ranges = self._chunk_ranges(audio.media_info.duration_ms, options)
        accepted: list[_RawSegment] = []
        language_votes: list[tuple[str, float]] = []
        recovered_chunks = 0
        used_devices: set[str] = set()
        for chunk_index, (start_ms, end_ms) in enumerate(ranges):
            self._raise_if_canceled(context)
            signature = self._chunk_signature(audio, context, start_ms, end_ms)
            cached = context.chunk_loader(chunk_index) if context.chunk_loader is not None else None
            restored = self._restore_chunk(cached, signature) if cached is not None else None
            if restored is not None:
                chunk_segments, language, language_probability, device = restored
                recovered_chunks += 1
                used_devices.add(device)
            else:
                try:
                    chunk_segments, language, language_probability = self._infer_chunk(
                        model, runtime, audio, context, options, start_ms, end_ms, clipped=len(ranges) > 1
                    )
                except LocalAsrError as error:
                    if runtime.device != "cuda" or not options.cpu_fallback_enabled or error.code not in {"local_asr_cuda_out_of_memory", "local_asr_cuda_runtime_failed"}:
                        raise
                    runtime = _Runtime("cpu", "int8")
                    model = self._load_model(self.expected_cache_root, runtime)
                    chunk_segments, language, language_probability = self._infer_chunk(
                        model, runtime, audio, context, options, start_ms, end_ms, clipped=len(ranges) > 1
                    )
                used_devices.add(runtime.device)
                if context.chunk_saver is not None:
                    context.chunk_saver(chunk_index, self._serialize_chunk(signature, start_ms, end_ms, chunk_segments, language, language_probability, runtime))
            language_votes.append((language, language_probability if language_probability is not None else 0.5))
            lower = start_ms if chunk_index == 0 else start_ms + options.chunk_overlap_ms // 2
            upper = end_ms if chunk_index == len(ranges) - 1 else end_ms - options.chunk_overlap_ms // 2
            accepted.extend(segment for segment in chunk_segments if lower <= (segment.start_ms + segment.end_ms) // 2 < upper)
            if context.progress_reporter is not None:
                context.progress_reporter(chunk_index + 1, len(ranges))
        accepted.sort(key=lambda segment: (segment.start_ms, segment.end_ms))
        if not accepted:
            raise LocalAsrError("local_asr_result_empty")
        segments: list[TranscriptSegment] = []
        words: list[WordTiming] = []
        for segment_index, raw_segment in enumerate(accepted, start=1):
            segment_id = f"seg_{segment_index:06d}"
            segments.append(TranscriptSegment(id=segment_id, start_ms=raw_segment.start_ms, end_ms=raw_segment.end_ms, text=raw_segment.text))
            for raw_word in raw_segment.words:
                words.append(WordTiming(segment_id=segment_id, index=len(words) + 1, start_ms=raw_word.start_ms, end_ms=raw_word.end_ms, text=raw_word.text, probability=raw_word.probability))
        totals: dict[str, float] = {}
        for language, weight in language_votes:
            totals[language] = totals.get(language, 0.0) + weight
        language = max(totals, key=totals.get)
        probabilities = [probability for candidate, probability in language_votes if candidate == language]
        detected_probability = sum(probabilities) / len(probabilities) if probabilities else None
        transcript = Transcript(
            language=language,
            duration_ms=max(audio.media_info.duration_ms, max(segment.end_ms for segment in segments)),
            provenance=Provenance(method=ProvenanceMethod.LOCAL_ASR, provider="faster-whisper", model=_FIXED_MODEL),
            segments=tuple(segments),
        )
        alignment = TranscriptAlignment(source_transcript_sha256=transcript_sha256(transcript), words=tuple(words)) if words else None
        return AsrResult(
            transcript=transcript,
            provenance=self._provenance(),
            alignment=alignment,
            detected_language_probability=detected_probability,
            runtime_device="cpu" if "cpu" in used_devices else "cuda",
            recovered_chunks=recovered_chunks,
        )
