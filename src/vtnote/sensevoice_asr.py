"""Fast, recoverable SenseVoiceSmall transcription through sherpa-onnx."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import re
import wave
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from vtnote.local_asr import (
    AsrResult,
    InstalledModelAssets,
    LocalAsrError,
    LocalAsrProvenance,
    TranscriptionContext,
)
from vtnote.local_asr_contract import SENSEVOICE_MODEL
from vtnote.media import PreparedAudio
from vtnote.model_assets import ModelAssetError, ModelAssetService
from vtnote.schemas import Provenance, ProvenanceMethod, Transcript, TranscriptSegment


_MODEL_FILE = "model.int8.onnx"
_TOKENS_FILE = "tokens.txt"
_VAD_FILE = "silero_vad.onnx"
_SAMPLE_RATE = 16_000
_LANGUAGE_TAG = re.compile(r"^<\|([a-z]+)\|>$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _Options:
    language: str
    use_itn: bool
    num_threads: int
    vad_threshold: float
    vad_min_silence_seconds: float
    vad_min_speech_seconds: float
    vad_max_speech_seconds: float


@dataclass(frozen=True, slots=True)
class _RecognizedSegment:
    start_ms: int
    end_ms: int
    text: str
    language: str


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _load_sherpa_onnx() -> object:
    import sherpa_onnx

    return sherpa_onnx


def _normalized_language(value: object) -> str:
    if not isinstance(value, str):
        return "und"
    candidate = value.strip()
    match = _LANGUAGE_TAG.fullmatch(candidate)
    if match is not None:
        candidate = match.group(1)
    lowered = candidate.casefold()
    return {
        "zh": "zh-Hans",
        "yue": "yue",
        "en": "en",
        "ja": "ja",
        "ko": "ko",
    }.get(lowered, lowered or "und")


def _has_speech_text(value: str) -> bool:
    return any(character.isalnum() for character in value)


class SenseVoiceTranscriber:
    """Use Silero VAD and the pinned SenseVoiceSmall INT8 model on CPU."""

    def __init__(
        self,
        *,
        assets: InstalledModelAssets | ModelAssetService,
        vad_assets: InstalledModelAssets | ModelAssetService,
        module_loader: Callable[[], object] | None = None,
        library_versions: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.assets = assets
        self.vad_assets = vad_assets
        self._module_loader = module_loader or _load_sherpa_onnx
        self._library_versions = library_versions or (
            lambda: {
                "sherpa_onnx": _package_version("sherpa-onnx"),
                "onnxruntime": _package_version("onnxruntime"),
                "numpy": _package_version("numpy"),
            }
        )
        self._module: object | None = None
        self._recognizer: object | None = None
        self._model_path: Path | None = None
        self._vad_path: Path | None = None

    @staticmethod
    def _raise_if_canceled(context: TranscriptionContext) -> None:
        try:
            canceled = context.cancel_requested()
        except Exception:
            raise LocalAsrError("local_asr_cancel_check_failed") from None
        if canceled:
            raise LocalAsrError("local_asr_canceled")

    @staticmethod
    def _validate_options(context: TranscriptionContext) -> _Options:
        value = context.local_whisper
        expected = {
            "schema_version",
            "language",
            "use_itn",
            "num_threads",
            "vad_threshold",
            "vad_min_silence_seconds",
            "vad_min_speech_seconds",
            "vad_max_speech_seconds",
            "speaker_diarization_enabled",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise LocalAsrError("local_asr_snapshot_invalid")
        language = value.get("language")
        threads = value.get("num_threads")
        threshold = value.get("vad_threshold")
        minimum_silence = value.get("vad_min_silence_seconds")
        minimum_speech = value.get("vad_min_speech_seconds")
        maximum_speech = value.get("vad_max_speech_seconds")
        if (
            value.get("schema_version") != 1
            or language not in {"auto", "zh", "en", "ja", "ko", "yue"}
            or type(value.get("use_itn")) is not bool
            or type(threads) is not int
            or not 1 <= threads <= 16
            or type(value.get("speaker_diarization_enabled")) is not bool
            or value.get("speaker_diarization_enabled") is not False
            or any(
                isinstance(candidate, bool)
                or not isinstance(candidate, (int, float))
                or not math.isfinite(float(candidate))
                for candidate in (
                    threshold,
                    minimum_silence,
                    minimum_speech,
                    maximum_speech,
                )
            )
            or not 0.05 <= float(threshold) <= 0.95
            or not 0.05 <= float(minimum_silence) <= 2.0
            or not 0.05 <= float(minimum_speech) <= 2.0
            or not 2.0 <= float(maximum_speech) <= 30.0
        ):
            raise LocalAsrError("local_asr_snapshot_invalid")
        return _Options(
            language=str(language),
            use_itn=bool(value["use_itn"]),
            num_threads=int(threads),
            vad_threshold=float(threshold),
            vad_min_silence_seconds=float(minimum_silence),
            vad_min_speech_seconds=float(minimum_speech),
            vad_max_speech_seconds=float(maximum_speech),
        )

    @staticmethod
    def _validate_audio(audio: PreparedAudio) -> None:
        info = audio.media_info
        if (
            not Path(audio.path).is_file()
            or info.audio_codec != "pcm_s16le"
            or info.sample_rate != _SAMPLE_RATE
            or info.channels != 1
            or info.duration_ms <= 0
        ):
            raise LocalAsrError("local_asr_audio_invalid")

    @staticmethod
    def _installed_file(
        assets: InstalledModelAssets | ModelAssetService,
        name: str,
    ) -> Path:
        try:
            root = assets.require_installed_path().resolve(strict=True)
            path = (root / name).resolve(strict=True)
        except ModelAssetError as error:
            raise LocalAsrError(error.code) from None
        except OSError:
            raise LocalAsrError("model_not_installed") from None
        if path.parent != root or not path.is_file():
            raise LocalAsrError("local_asr_model_path_invalid")
        return path

    def _runtime(
        self,
        options: _Options,
    ) -> tuple[object, object, Path]:
        model_path = self._installed_file(self.assets, _MODEL_FILE)
        tokens_path = self._installed_file(self.assets, _TOKENS_FILE)
        vad_path = self._installed_file(self.vad_assets, _VAD_FILE)
        if self._model_path not in {None, model_path} or self._vad_path not in {
            None,
            vad_path,
        }:
            raise LocalAsrError("local_asr_model_revision_changed")
        self._model_path = model_path
        self._vad_path = vad_path
        if self._module is None:
            try:
                self._module = self._module_loader()
            except Exception:
                raise LocalAsrError("local_asr_runtime_unavailable") from None
        if self._recognizer is None:
            try:
                factory = getattr(
                    getattr(self._module, "OfflineRecognizer"),
                    "from_sense_voice",
                )
                self._recognizer = factory(
                    model=str(model_path),
                    tokens=str(tokens_path),
                    num_threads=options.num_threads,
                    sample_rate=_SAMPLE_RATE,
                    provider="cpu",
                    language=options.language,
                    use_itn=options.use_itn,
                    debug=False,
                )
            except Exception:
                raise LocalAsrError("local_asr_model_load_failed") from None
        return self._module, self._recognizer, vad_path

    def ensure_available(self, context: TranscriptionContext) -> object:
        options = self._validate_options(context)
        self._raise_if_canceled(context)
        recognizer = self._runtime(options)[1]
        self._raise_if_canceled(context)
        return recognizer

    def _new_vad(self, module: object, model: Path, options: _Options) -> object:
        try:
            config = getattr(module, "VadModelConfig")()
            config.silero_vad.model = str(model)
            config.silero_vad.threshold = options.vad_threshold
            config.silero_vad.min_silence_duration = (
                options.vad_min_silence_seconds
            )
            config.silero_vad.min_speech_duration = options.vad_min_speech_seconds
            config.silero_vad.max_speech_duration = options.vad_max_speech_seconds
            config.sample_rate = _SAMPLE_RATE
            config.provider = "cpu"
            if not config.validate():
                raise ValueError("invalid VAD configuration")
            return getattr(module, "VoiceActivityDetector")(
                config,
                buffer_size_in_seconds=120,
            )
        except Exception:
            raise LocalAsrError("local_asr_model_load_failed") from None

    def _signature(
        self,
        audio: PreparedAudio,
        context: TranscriptionContext,
        *,
        start_sample: int,
        sample_count: int,
    ) -> str:
        try:
            size = Path(audio.path).stat().st_size
        except OSError:
            raise LocalAsrError("local_asr_audio_invalid") from None
        payload = {
            "asset_id": audio.asset_id,
            "size": size,
            "duration_ms": audio.media_info.duration_ms,
            "options": dict(context.local_whisper),
            "model_manifest": self.assets.manifest.manifest_sha256,
            "vad_manifest": self.vad_assets.manifest.manifest_sha256,
            "start_sample": start_sample,
            "sample_count": sample_count,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _restore(
        payload: Mapping[str, object] | None,
        signature: str,
    ) -> tuple[str, str] | None:
        if (
            payload is None
            or payload.get("schema_version") != 1
            or payload.get("signature") != signature
            or not isinstance(payload.get("text"), str)
            or not isinstance(payload.get("language"), str)
        ):
            return None
        return str(payload["text"]), str(payload["language"])

    @staticmethod
    def _result(stream: object) -> tuple[str, str]:
        result = getattr(stream, "result", None)
        text = getattr(result, "text", None)
        language = getattr(result, "lang", None)
        if not isinstance(text, str):
            raise LocalAsrError("local_asr_result_invalid")
        return text.strip(), _normalized_language(language)

    def _decode_ready(
        self,
        *,
        vad: object,
        recognizer: object,
        audio: PreparedAudio,
        context: TranscriptionContext,
        start_index: int,
    ) -> tuple[list[_RecognizedSegment], int]:
        pending: list[tuple[int, int, int, str, object]] = []
        recognized: list[_RecognizedSegment] = []
        index = start_index
        while not vad.empty():
            self._raise_if_canceled(context)
            front = vad.front
            start_sample = int(front.start)
            samples = np.asarray(front.samples, dtype=np.float32)
            sample_count = int(samples.shape[0])
            signature = self._signature(
                audio,
                context,
                start_sample=start_sample,
                sample_count=sample_count,
            )
            cached = (
                context.chunk_loader(index)
                if context.chunk_loader is not None
                else None
            )
            restored = self._restore(cached, signature)
            if restored is None:
                stream = recognizer.create_stream()
                stream.accept_waveform(_SAMPLE_RATE, samples)
                pending.append((index, start_sample, sample_count, signature, stream))
            else:
                text, language = restored
                if text and _has_speech_text(text):
                    recognized.append(
                        _RecognizedSegment(
                            round(start_sample * 1000 / _SAMPLE_RATE),
                            round((start_sample + sample_count) * 1000 / _SAMPLE_RATE),
                            text,
                            language,
                        )
                    )
            index += 1
            vad.pop()

        if pending:
            try:
                streams = [entry[-1] for entry in pending]
                if len(streams) == 1:
                    recognizer.decode_stream(streams[0])
                else:
                    recognizer.decode_streams(streams)
            except Exception:
                raise LocalAsrError("local_asr_inference_failed") from None
            for chunk_index, start_sample, sample_count, signature, stream in pending:
                self._raise_if_canceled(context)
                text, language = self._result(stream)
                if context.chunk_saver is not None:
                    context.chunk_saver(
                        chunk_index,
                        {
                            "schema_version": 1,
                            "signature": signature,
                            "text": text,
                            "language": language,
                        },
                    )
                if text and _has_speech_text(text):
                    recognized.append(
                        _RecognizedSegment(
                            round(start_sample * 1000 / _SAMPLE_RATE),
                            round((start_sample + sample_count) * 1000 / _SAMPLE_RATE),
                            text,
                            language,
                        )
                    )
        return recognized, index

    def _provenance(self) -> LocalAsrProvenance:
        try:
            versions = {
                str(name): str(version)
                for name, version in self._library_versions().items()
            }
        except Exception:
            raise LocalAsrError("local_asr_runtime_evidence_unavailable") from None
        if set(versions) != {"sherpa_onnx", "onnxruntime", "numpy"} or any(
            not version for version in versions.values()
        ):
            raise LocalAsrError("local_asr_runtime_evidence_unavailable")
        manifest = self.assets.manifest
        vad_manifest = self.vad_assets.manifest
        return LocalAsrProvenance(
            model_revision=manifest.revision,
            model_manifest_sha256=manifest.manifest_sha256,
            model_file_sha256={item.path: item.sha256 for item in manifest.files},
            library_versions=versions,
            provider="sherpa-onnx",
            model=SENSEVOICE_MODEL,
            source_revision=vad_manifest.revision,
            runtime_manifest_sha256=vad_manifest.manifest_sha256,
        )

    def transcribe(
        self,
        audio: PreparedAudio,
        context: TranscriptionContext,
    ) -> AsrResult:
        self._validate_audio(audio)
        options = self._validate_options(context)
        self._raise_if_canceled(context)
        module, recognizer, vad_path = self._runtime(options)
        vad = self._new_vad(module, vad_path, options)
        recognized: list[_RecognizedSegment] = []
        segment_index = 0
        try:
            with wave.open(str(audio.path), "rb") as source:
                if (
                    source.getnchannels() != 1
                    or source.getsampwidth() != 2
                    or source.getframerate() != _SAMPLE_RATE
                    or source.getcomptype() != "NONE"
                ):
                    raise LocalAsrError("local_asr_audio_invalid")
                total_frames = source.getnframes()
                frames_per_read = _SAMPLE_RATE * 100
                total_reads = max(1, math.ceil(total_frames / frames_per_read))
                processed_reads = 0
                buffer = np.empty(0, dtype=np.float32)
                window_size = int(vad.config.silero_vad.window_size)
                while True:
                    self._raise_if_canceled(context)
                    data = source.readframes(frames_per_read)
                    if not data:
                        if buffer.size:
                            padded = np.pad(buffer, (0, window_size - buffer.size))
                            vad.accept_waveform(padded)
                        vad.flush()
                        ready, segment_index = self._decode_ready(
                            vad=vad,
                            recognizer=recognizer,
                            audio=audio,
                            context=context,
                            start_index=segment_index,
                        )
                        recognized.extend(ready)
                        break
                    samples = np.frombuffer(data, dtype="<i2").astype(np.float32)
                    samples /= 32768.0
                    buffer = np.concatenate((buffer, samples))
                    while buffer.size >= window_size:
                        vad.accept_waveform(buffer[:window_size])
                        buffer = buffer[window_size:]
                    ready, segment_index = self._decode_ready(
                        vad=vad,
                        recognizer=recognizer,
                        audio=audio,
                        context=context,
                        start_index=segment_index,
                    )
                    recognized.extend(ready)
                    processed_reads += 1
                    if context.progress_reporter is not None:
                        context.progress_reporter(
                            min(processed_reads, total_reads),
                            total_reads,
                        )
        except LocalAsrError:
            raise
        except (OSError, EOFError, wave.Error):
            raise LocalAsrError("local_asr_audio_invalid") from None
        except Exception:
            raise LocalAsrError("local_asr_inference_failed") from None

        recognized.sort(key=lambda item: (item.start_ms, item.end_ms))
        if not recognized:
            raise LocalAsrError("local_asr_result_empty")
        segments = tuple(
            TranscriptSegment(
                id=f"seg_{index:06d}",
                start_ms=item.start_ms,
                end_ms=max(item.start_ms + 1, item.end_ms),
                text=item.text,
            )
            for index, item in enumerate(recognized, start=1)
        )
        language_weights: dict[str, int] = {}
        for item in recognized:
            language_weights[item.language] = language_weights.get(item.language, 0) + (
                item.end_ms - item.start_ms
            )
        language = max(language_weights, key=language_weights.get)
        transcript = Transcript(
            language=language,
            duration_ms=max(audio.media_info.duration_ms, segments[-1].end_ms),
            provenance=Provenance(
                method=ProvenanceMethod.LOCAL_ASR,
                provider="sherpa-onnx",
                model=SENSEVOICE_MODEL,
            ),
            segments=segments,
        )
        return AsrResult(
            transcript=transcript,
            provenance=self._provenance(),
            runtime_device="cpu",
        )
