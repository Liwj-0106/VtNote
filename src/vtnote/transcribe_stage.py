"""Snapshot-driven cloud/local ASR routing and immutable publication."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from vtnote.artifacts import (
    ArtifactExistsError,
    ensure_transcript_json,
    ensure_transcript_alignment,
    ensure_speaker_map,
    ensure_transcription_recovery,
    transcript_from_timed_text,
    write_transcription_chunk_recovery,
)
from vtnote.cloud_submissions import (
    CloudSubmission,
    CloudSubmissionError,
    CloudSubmissionStore,
)
from vtnote.local_asr import (
    AsrResult,
    FasterWhisperTranscriber,
    LocalAsrError,
    TranscriptionContext,
)
from vtnote.local_asr_contract import (
    FASTER_WHISPER_ENGINE,
    LocalAsrSnapshotError,
    resolve_local_asr_snapshot,
)
from vtnote.media import FfmpegMediaProcessor, PreparedAudio
from vtnote.models import (
    CloudSubmissionRecord,
    ItemRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
    RuntimeAssetRecord,
)
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.provider_credentials import (
    CredentialReentryRequired,
    TencentCredentialBundle,
    parse_credential_bundle,
)
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService
from vtnote.schemas import ProvenanceMethod, Transcript
from vtnote.speaker_diarization import SpeakerDiarizationError, diarize_transcript
from vtnote.secrets import SecretStore
from vtnote.tencent_asr import (
    CloudAsrOutcomeKind,
    CloudAsrRequestError,
    RecordingCreateQueryClient,
    TencentRequestContext,
    bounded_poll_delay,
)
from vtnote.tencent_contract import (
    CloudProfileSnapshot,
    TencentLimits,
    TencentPreflight,
)
from vtnote.tencent_cos import (
    CosContext,
    TencentCosStager,
    build_qcloud_cos_sdk,
)
from vtnote.worker import (
    StageCancelled,
    StageContext,
    StageExecutionError,
)
from vtnote.worker_store import (
    StageDeferred,
    StageRequeue,
    StageResult,
)


class MediaPreparer(Protocol):
    def prepare_for_local(self, item_id: str, source: Path) -> PreparedAudio: ...

    def convert_for_cloud(
        self,
        item_id: str,
        source: Path,
        *,
        max_bytes: int | None = None,
    ) -> PreparedAudio: ...


class LocalTranscriber(Protocol):
    def transcribe(
        self,
        audio: PreparedAudio,
        context: TranscriptionContext,
    ) -> AsrResult: ...


CredentialResolver = Callable[[Mapping[str, object]], TencentCredentialBundle]
CosStagerResolver = Callable[
    [Mapping[str, object], TencentCredentialBundle],
    TencentCosStager,
]


class SnapshotTencentCredentialResolver:
    """Resolve only the exact retained connection/profile revisions in a task."""

    def __init__(self, *, engine: Engine, secrets: SecretStore) -> None:
        self.engine = engine
        self.secrets = secrets

    def __call__(
        self,
        profile: Mapping[str, object],
    ) -> TencentCredentialBundle:
        profile_id = profile.get("id")
        connection_id = profile.get("connection_id")
        profile_revision = profile.get("profile_revision")
        connection_revision = profile.get("connection_revision")
        if (
            not isinstance(profile_id, str)
            or not isinstance(connection_id, str)
            or type(profile_revision) is not int
            or type(connection_revision) is not int
        ):
            raise ValueError("cloud credential snapshot is invalid")
        with Session(self.engine) as session:
            profile_row = session.get(ProcessorProfileRecord, profile_id)
            connection = session.get(ProviderConnectionRecord, connection_id)
            if (
                profile_row is None
                or connection is None
                or profile_row.connection_id != connection.id
                or profile_row.revision != profile_revision
                or connection.revision != connection_revision
                or profile_row.purpose != "cloud_asr"
                or connection.protocol != "tencent_recording_asr"
            ):
                raise ValueError("cloud credential revision is unavailable")
            stored = self.secrets.get(connection.credential_ref)
        if stored is None:
            raise ValueError("cloud credentials are unavailable")
        try:
            bundle = parse_credential_bundle("tencent_recording_asr", stored)
        except CredentialReentryRequired:
            raise ValueError("cloud credentials require re-entry") from None
        if not isinstance(bundle, TencentCredentialBundle):
            raise ValueError("cloud credentials are invalid")
        return bundle


def build_snapshot_cos_stager(
    profile: Mapping[str, object],
    credentials: TencentCredentialBundle,
) -> TencentCosStager:
    parameters = profile.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("COS snapshot is invalid")
    bucket = parameters.get("cos_bucket")
    region = parameters.get("cos_region")
    if not isinstance(bucket, str) or not isinstance(region, str):
        raise ValueError("COS snapshot is incomplete")
    return TencentCosStager(
        build_qcloud_cos_sdk(
            credentials,
            bucket=bucket,
            region=region,
        )
    )


@dataclass(frozen=True, slots=True)
class _TranscribeInput:
    task_id: str
    source_kind: str
    source_locator: str
    snapshot: Mapping[str, object]


_CLOUD_ERROR_FALLBACK_REASON = {
    "cloud_audio_invalid": "cloud_server_error",
    "cloud_cos_unavailable": "cloud_profile_unavailable",
    "provider_rate_limited": "cloud_rate_limited",
    "provider_connect_failed": "cloud_network_error",
    "provider_network_error": "cloud_network_error",
    "provider_transcription_failed": "cloud_result_invalid",
    "provider_result_missing_timestamps": "cloud_result_invalid",
    "provider_result_expired": "cloud_result_invalid",
    "provider_query_response_invalid": "cloud_result_invalid",
}


class TranscribeStageHandler:
    """Execute from the task snapshot and publish at most one transcript."""

    def __init__(
        self,
        *,
        paths: StoragePaths,
        media: MediaPreparer | FfmpegMediaProcessor,
        local_transcriber: LocalTranscriber | FasterWhisperTranscriber,
        local_transcribers: Mapping[str, LocalTranscriber] | None = None,
        cloud_client: RecordingCreateQueryClient | None,
        credential_resolver: CredentialResolver,
        cos_stager_resolver: CosStagerResolver | None = None,
        cloud_limits: TencentLimits | None = None,
        gpu_lease_duration: timedelta = timedelta(minutes=10),
    ) -> None:
        self.paths = paths
        self.media = media
        self.local_transcriber = local_transcriber
        self.local_transcribers = dict(
            local_transcribers
            or {FASTER_WHISPER_ENGINE: local_transcriber}
        )
        self.cloud_client = cloud_client
        self.credential_resolver = credential_resolver
        self.cos_stager_resolver = cos_stager_resolver
        self.cloud_limits = cloud_limits or TencentLimits()
        self.preflight = TencentPreflight()
        self.gpu_lease_duration = gpu_lease_duration

    @staticmethod
    def _load_input(context: StageContext) -> _TranscribeInput:
        if context.claim.stage != "transcribe":
            raise StageExecutionError("invalid_transcribe_stage")
        with Session(context.store.engine) as session:
            item = session.get(ItemRecord, context.claim.item_id)
            if item is None or not isinstance(item.task.pipeline_snapshot_json, dict):
                raise StageExecutionError("transcribe_input_unavailable")
            return _TranscribeInput(
                task_id=item.task_id,
                source_kind=item.source_kind,
                source_locator=item.source_locator,
                snapshot=item.task.pipeline_snapshot_json,
            )

    @staticmethod
    def _selected_asr(
        context: StageContext,
        snapshot: Mapping[str, object],
    ) -> tuple[str, Mapping[str, object] | None]:
        asr = snapshot.get("asr")
        if not isinstance(asr, Mapping):
            raise StageExecutionError("asr_snapshot_invalid")
        selected: Mapping[str, object] = asr
        override = context.claim.retry_override
        if override is not None:
            strategy = override.get("strategy")
            if strategy == "local":
                selected = {"mode": "local", "profile": None}
            elif strategy == "cloud_confirmed":
                replacement = override.get("asr")
                if not isinstance(replacement, Mapping):
                    raise StageExecutionError("asr_snapshot_invalid")
                selected = replacement
            elif strategy != "same":
                raise StageExecutionError("asr_snapshot_invalid")
        mode = selected.get("mode")
        profile = selected.get("profile")
        if mode not in {"auto", "cloud", "local"}:
            raise StageExecutionError("asr_snapshot_invalid")
        if profile is not None and not isinstance(profile, Mapping):
            raise StageExecutionError("asr_snapshot_invalid")
        return str(mode), profile

    def _resolve_source(
        self,
        context: StageContext,
        selected: _TranscribeInput,
    ) -> Path:
        if selected.source_kind == "local_media":
            path = Path(selected.source_locator)
            if not path.is_absolute() or not path.is_file():
                raise StageExecutionError("transcribe_source_unavailable")
            return path
        with Session(context.store.engine) as session:
            assets = RuntimeAssetService(session, self.paths)
            try:
                if selected.source_kind == "uploaded_media":
                    row = session.get(RuntimeAssetRecord, selected.source_locator)
                    if (
                        row is None
                        or row.item_id != context.claim.item_id
                        or row.role != "uploaded_source"
                    ):
                        raise StageExecutionError("transcribe_source_unavailable")
                    if row.state == "trash":
                        assets.restore(row.id)
                    elif row.state != "active":
                        raise StageExecutionError("transcribe_source_unavailable")
                    return assets.resolve(row.id)
                if selected.source_kind == "url":
                    view = assets.active_for_role(
                        item_id=context.claim.item_id,
                        role="downloaded_audio",
                    )
                    if view is None:
                        view = assets.restore_trashed_for_role(
                            item_id=context.claim.item_id,
                            role="downloaded_audio",
                        )
                    if view is None:
                        raise StageExecutionError("transcribe_source_unavailable")
                    return assets.resolve(view.id)
            except StageExecutionError:
                raise
            except (OSError, ValueError, RuntimeAssetError, UnsafePathError):
                raise StageExecutionError("transcribe_source_unavailable") from None
        raise StageExecutionError("transcribe_source_invalid")

    @staticmethod
    def _audio_sha256(audio: PreparedAudio) -> str:
        digest = hashlib.sha256()
        try:
            with Path(audio.path).open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            raise StageExecutionError("prepared_audio_unavailable") from None
        return digest.hexdigest()

    @staticmethod
    def _profile(profile: Mapping[str, object]) -> CloudProfileSnapshot:
        options = profile.get("options")
        parameters = profile.get("parameters")
        if (
            profile.get("protocol") != "tencent_recording_asr"
            or profile.get("base_url") != "https://asr.tencentcloudapi.com"
            or not isinstance(options, Mapping)
            or not isinstance(parameters, Mapping)
        ):
            raise StageExecutionError("cloud_asr_profile_invalid")
        try:
            return CloudProfileSnapshot(
                model=str(profile.get("model")),
                language_scope=str(options.get("language_scope")),
                cos_configured=parameters.get("cos_configured") is True,
            )
        except ValueError:
            raise StageExecutionError("cloud_asr_profile_invalid") from None

    @staticmethod
    def _fallback_reason(code: str) -> str:
        return _CLOUD_ERROR_FALLBACK_REASON.get(code, "cloud_server_error")

    @staticmethod
    def _deferred(profile: Mapping[str, object]) -> StageDeferred:
        model = profile.get("model")
        if not isinstance(model, str):
            raise StageExecutionError("cloud_asr_profile_invalid")
        return StageDeferred(
            external_submission_state="submitted",
            execution_evidence={
                "source_method": "cloud_asr",
                "asr_route": "cloud",
                "provider": "tencent_recording_asr",
                "model": model,
            },
        )

    @staticmethod
    def _submission(
        context: StageContext,
    ) -> CloudSubmission | None:
        with Session(context.store.engine) as session:
            row = session.scalar(
                select(CloudSubmissionRecord).where(
                    CloudSubmissionRecord.stage_run_id
                    == context.claim.stage_run_id
                )
            )
            return None if row is None else CloudSubmissionStore._view(row)

    def _trash_owned_media(self, context: StageContext) -> None:
        with Session(context.store.engine) as session:
            item = session.get(ItemRecord, context.claim.item_id)
            if item is None:
                raise StageExecutionError("transcribe_source_unavailable")
            retain_source = item.task.options.get("audio_export_enabled", False)
            if not isinstance(retain_source, bool):
                raise StageExecutionError("asr_snapshot_invalid")
            disposable_roles = ["cloud_audio", "cloud_audio_inline", "local_audio"]
            if not retain_source:
                disposable_roles.extend(("uploaded_source", "downloaded_audio"))
            assets = RuntimeAssetService(session, self.paths)
            rows = session.scalars(
                select(RuntimeAssetRecord).where(
                    RuntimeAssetRecord.item_id == context.claim.item_id,
                    RuntimeAssetRecord.state == "active",
                    RuntimeAssetRecord.role.in_(
                        tuple(disposable_roles)
                    ),
                )
            ).all()
            for row in rows:
                try:
                    assets.trash(row.id, now=context.clock())
                except (OSError, ValueError, RuntimeAssetError, UnsafePathError):
                    raise StageExecutionError("runtime_asset_error") from None

    def _trash_owned_media_best_effort(self, context: StageContext) -> None:
        try:
            self._trash_owned_media(context)
        except Exception:
            # Preserve the primary cancel/failure code. RuntimeAssetService records
            # material lifecycle failures at its own persistence boundary.
            return

    def _publish(
        self,
        context: StageContext,
        transcript: Transcript,
        *,
        result: StageResult,
    ) -> StageResult:
        context.checkpoint()
        try:
            ensure_transcript_json(self.paths, context.claim.item_id, transcript)
        except (ArtifactExistsError, OSError, ValueError, UnsafePathError):
            raise StageExecutionError("transcript_publication_conflict") from None
        context.checkpoint()
        self._trash_owned_media(context)
        return result

    def _recover_local_publication(
        self,
        context: StageContext,
        *,
        result: StageResult,
        expected_provider: str,
        expected_model: str,
    ) -> StageResult | None:
        transcript_path = self.paths.transcript(context.claim.item_id)
        recovery_path = self.paths.transcription_recovery(
            context.claim.item_id,
            context.claim.stage_run_id,
        )
        if not transcript_path.exists() and not recovery_path.exists():
            return None
        if not recovery_path.is_file():
            raise StageExecutionError("transcript_publication_conflict")
        try:
            recovery = Transcript.model_validate_json(
                recovery_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValueError):
            raise StageExecutionError("transcript_publication_conflict") from None
        if (
            recovery.provenance.method != ProvenanceMethod.LOCAL_ASR
            or recovery.provenance.provider != expected_provider
            or recovery.provenance.model != expected_model
        ):
            raise StageExecutionError("transcript_publication_conflict")
        if transcript_path.exists():
            try:
                published = Transcript.model_validate_json(
                    transcript_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, ValueError):
                raise StageExecutionError("transcript_publication_conflict") from None
            if published != recovery:
                raise StageExecutionError("transcript_publication_conflict")
            context.checkpoint()
            self._trash_owned_media(context)
            return result
        return self._publish(
            context,
            recovery,
            result=result,
        )

    def _run_local(
        self,
        context: StageContext,
        selected: _TranscribeInput,
        *,
        route: str,
        fallback_reason: str | None = None,
        warning: str | None = None,
    ) -> StageResult:
        try:
            local_selection = resolve_local_asr_snapshot(selected.snapshot)
        except LocalAsrSnapshotError:
            raise StageExecutionError("local_asr_snapshot_invalid")
        local = local_selection.options
        local_transcriber = self.local_transcribers.get(local_selection.engine)
        if local_transcriber is None:
            raise StageExecutionError("local_asr_runtime_unavailable")
        evidence = {
            "source_method": "local_asr",
            "asr_route": route,
            "provider": local_selection.evidence_provider,
            "model": local_selection.model,
        }
        if fallback_reason is not None:
            evidence["fallback_reason"] = fallback_reason
        stage_result = StageResult(
            warning=warning,
            execution_evidence=evidence,
        )
        recovered = self._recover_local_publication(
            context,
            result=stage_result,
            expected_provider=local_selection.provider,
            expected_model=local_selection.model,
        )
        if recovered is not None:
            return recovered
        if local_selection.engine == FASTER_WHISPER_ENGINE:
            if not context.store.acquire_resource(
                context.claim,
                "local-asr:gpu:0",
                context.clock(),
                self.gpu_lease_duration,
            ):
                raise StageRequeue()

        def canceled() -> bool:
            try:
                context.checkpoint()
            except StageCancelled:
                return True
            return False

        transcription_context = TranscriptionContext(
            local_whisper=local,
            cancel_requested=canceled,
            local_engine=local_selection.engine,
        )

        # The real faster-whisper adapter can prove the model/CUDA runtime is
        # available before a long PCM conversion. Test doubles and alternate
        # adapters remain compatible because this preflight is optional.
        ensure_available = getattr(local_transcriber, "ensure_available", None)
        if callable(ensure_available):
            try:
                ensure_available(transcription_context)
            except LocalAsrError as error:
                if error.code == "local_asr_canceled":
                    raise StageCancelled() from None
                raise StageExecutionError(
                    error.code,
                    warning=fallback_reason,
                ) from None

        source = self._resolve_source(context, selected)
        context.checkpoint()
        try:
            audio = self.media.prepare_for_local(context.claim.item_id, source)
        except Exception:
            raise StageExecutionError("local_audio_preparation_failed") from None

        def load_chunk(chunk_index: int) -> Mapping[str, object] | None:
            path = self.paths.transcription_chunk_recovery(
                context.claim.item_id,
                context.claim.stage_run_id,
                chunk_index,
            )
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return None
            return payload if isinstance(payload, dict) else None

        def save_chunk(chunk_index: int, payload: dict[str, object]) -> None:
            context.checkpoint()
            try:
                write_transcription_chunk_recovery(
                    self.paths,
                    context.claim.item_id,
                    context.claim.stage_run_id,
                    chunk_index,
                    payload,
                )
            except (OSError, ValueError, UnsafePathError):
                raise LocalAsrError("local_asr_recovery_write_failed") from None

        def report_progress(current: int, total: int) -> None:
            context.checkpoint()
            if not context.store.update_progress(
                context.claim,
                {
                    "current": current,
                    "total": total,
                    "unit": "chunks",
                    "message_code": "transcribing_segments",
                },
                now=context.clock(),
            ):
                raise LocalAsrError("local_asr_canceled")

        transcription_context = TranscriptionContext(
            local_whisper=local,
            cancel_requested=canceled,
            local_engine=local_selection.engine,
            chunk_loader=load_chunk,
            chunk_saver=save_chunk,
            progress_reporter=report_progress,
        )

        try:
            result = local_transcriber.transcribe(
                audio,
                transcription_context,
            )
        except LocalAsrError as error:
            if error.code == "local_asr_canceled":
                raise StageCancelled() from None
            raise StageExecutionError(
                error.code,
                warning=fallback_reason,
            ) from None
        if result.transcript.provenance.provider != local_selection.provider or (
            result.transcript.provenance.model != local_selection.model
        ):
            raise StageExecutionError("local_asr_result_invalid")
        derived_warning: str | None = None
        if result.alignment is not None:
            try:
                ensure_transcript_alignment(
                    self.paths,
                    context.claim.item_id,
                    result.alignment,
                )
            except (ArtifactExistsError, OSError, ValueError, UnsafePathError):
                derived_warning = "word_alignment_unavailable"

        if result.speakers is not None:
            try:
                result.speakers.validate_against(result.transcript)
                ensure_speaker_map(
                    self.paths,
                    context.claim.item_id,
                    result.speakers,
                )
            except (
                ArtifactExistsError,
                OSError,
                ValueError,
                UnsafePathError,
            ):
                derived_warning = "speaker_diarization_unavailable"
        elif local.get("speaker_diarization_enabled") is True:
            try:
                speakers = diarize_transcript(audio.path, result.transcript)
                ensure_speaker_map(self.paths, context.claim.item_id, speakers)
            except (
                ArtifactExistsError,
                OSError,
                ValueError,
                UnsafePathError,
                SpeakerDiarizationError,
            ):
                derived_warning = "speaker_diarization_unavailable"
        try:
            ensure_transcription_recovery(
                self.paths,
                context.claim.item_id,
                context.claim.stage_run_id,
                result.transcript,
            )
        except (ArtifactExistsError, OSError, ValueError, UnsafePathError):
            raise StageExecutionError("transcript_recovery_conflict") from None
        if local.get("schema_version") == 2:
            evidence.update(
                {
                    "detected_language": result.transcript.language,
                    "runtime_device": result.runtime_device,
                    "chunk_recovery": (
                        "used" if result.recovered_chunks else "unused"
                    ),
                }
            )
        completed_result = StageResult(
            warning=derived_warning or warning,
            execution_evidence=evidence,
        )
        return self._publish(
            context,
            result.transcript,
            result=completed_result,
        )

    @staticmethod
    def _cloud_transcript(
        submission: CloudSubmission,
        profile: Mapping[str, object],
    ) -> Transcript:
        normalized = submission.normalized_result
        if not isinstance(normalized, dict):
            raise StageExecutionError("cloud_result_unavailable")
        language = normalized.get("language")
        sentences = normalized.get("sentences")
        model = profile.get("model")
        if (
            not isinstance(language, str)
            or not isinstance(sentences, list)
            or not isinstance(model, str)
        ):
            raise StageExecutionError("cloud_result_invalid")
        cues: list[tuple[int, int, str]] = []
        for sentence in sentences:
            if not isinstance(sentence, dict):
                raise StageExecutionError("cloud_result_invalid")
            start = sentence.get("start_ms")
            end = sentence.get("end_ms")
            text = sentence.get("text")
            if (
                type(start) is not int
                or type(end) is not int
                or not isinstance(text, str)
            ):
                raise StageExecutionError("cloud_result_invalid")
            cues.append((start, end, text))
        try:
            return transcript_from_timed_text(
                tuple(cues),
                language=language,
                duration_ms=max((cue[1] for cue in cues), default=0),
                method=ProvenanceMethod.CLOUD_ASR,
                provider="tencent_recording_asr",
                model=model,
            )
        except ValueError:
            raise StageExecutionError("cloud_result_invalid") from None

    def _handle_cloud_terminal(
        self,
        context: StageContext,
        selected: _TranscribeInput,
        *,
        mode: str,
        profile: Mapping[str, object],
        submission: CloudSubmission,
    ) -> StageResult:
        if submission.state == "succeeded":
            transcript = self._cloud_transcript(submission, profile)
            return self._publish(
                context,
                transcript,
                result=StageResult(
                    execution_evidence={
                        "source_method": "cloud_asr",
                        "asr_route": "cloud",
                        "provider": "tencent_recording_asr",
                        "model": str(profile["model"]),
                    }
                ),
            )
        code = submission.safe_error_code or "provider_failed"
        if submission.state == "submission_unknown":
            if mode == "cloud":
                raise StageExecutionError("cloud_submission_unknown")
            return self._run_local(
                context,
                selected,
                route="cloud_to_local",
                fallback_reason="cloud_submission_unknown",
                warning="cloud_submission_unknown_possible_charge",
            )
        if submission.state == "failed":
            if mode == "cloud":
                raise StageExecutionError(code)
            return self._run_local(
                context,
                selected,
                route="cloud_to_local",
                fallback_reason=self._fallback_reason(code),
            )
        raise StageExecutionError("cloud_submission_state_invalid")

    def _submit_cloud(
        self,
        context: StageContext,
        selected: _TranscribeInput,
        *,
        mode: str,
        profile: Mapping[str, object],
    ) -> StageResult:
        if self.cloud_client is None:
            if mode == "cloud":
                raise StageExecutionError("cloud_asr_unavailable")
            return self._run_local(
                context,
                selected,
                route="local",
                fallback_reason="cloud_profile_unavailable",
            )
        existing = self._submission(context)
        if existing is not None:
            if existing.state in {"submitted"}:
                if (
                    existing.next_poll_at is None
                    and existing.provider_task_id is not None
                    and existing.submitted_at is not None
                ):
                    with Session(context.store.engine) as session:
                        existing = CloudSubmissionStore(session).schedule_query(
                            existing.id,
                            existing.submitted_at
                            + bounded_poll_delay(
                                existing.provider_task_id,
                                existing.poll_attempt,
                            ),
                        )
                raise self._deferred(profile)
            if existing.state == "sending":
                with Session(context.store.engine) as session:
                    existing = CloudSubmissionStore(session).mark_unknown(
                        existing.id,
                        "provider_submission_state_unknown",
                        marked_at=context.clock(),
                    )
                return self._handle_cloud_terminal(
                    context,
                    selected,
                    mode=mode,
                    profile=profile,
                    submission=existing,
                )
            if existing.state in {"succeeded", "failed", "submission_unknown"}:
                return self._handle_cloud_terminal(
                    context,
                    selected,
                    mode=mode,
                    profile=profile,
                    submission=existing,
                )

        source = self._resolve_source(context, selected)
        context.checkpoint()
        cloud_profile = self._profile(profile)
        inline_limit = (
            None
            if cloud_profile.cos_configured
            else self.cloud_limits.inline_audio_bytes
        )
        try:
            audio = self.media.convert_for_cloud(
                context.claim.item_id,
                source,
                max_bytes=inline_limit,
            )
        except Exception:
            if mode == "cloud":
                raise StageExecutionError("cloud_audio_preparation_failed") from None
            return self._run_local(
                context,
                selected,
                route="cloud_to_local",
                fallback_reason="cloud_server_error",
            )
        eligibility = self.preflight.evaluate(
            audio,
            cloud_profile,
            self.cloud_limits,
        )
        if not eligibility.eligible:
            reason = eligibility.reason_code or "cloud_server_error"
            if mode == "cloud":
                raise StageExecutionError(reason)
            return self._run_local(
                context,
                selected,
                route="cloud_to_local",
                fallback_reason=self._fallback_reason(reason),
            )
        digest = self._audio_sha256(audio)
        parameters = profile["parameters"]
        assert isinstance(parameters, Mapping)
        locator = None
        stager = None
        cos_context = None
        if eligibility.route == "cos":
            if self.cos_stager_resolver is None:
                if mode == "cloud":
                    raise StageExecutionError("cloud_cos_unavailable")
                return self._run_local(
                    context,
                    selected,
                    route="cloud_to_local",
                    fallback_reason="cloud_profile_unavailable",
                )
            try:
                cos_context = CosContext(
                    task_id=selected.task_id,
                    audio_sha256=digest,
                    bucket=str(parameters.get("cos_bucket")),
                    region=str(parameters.get("cos_region")),
                    recoverable_copy=audio.asset_id is not None,
                )
                locator = cos_context.locator()
            except Exception:
                if mode == "cloud":
                    raise StageExecutionError("cloud_cos_configuration_invalid") from None
                return self._run_local(
                    context,
                    selected,
                    route="cloud_to_local",
                    fallback_reason="cloud_profile_unavailable",
                )
        try:
            credentials = self.credential_resolver(profile)
            if not isinstance(credentials, TencentCredentialBundle):
                raise ValueError("invalid credentials")
            if eligibility.route == "cos":
                assert self.cos_stager_resolver is not None
                stager = self.cos_stager_resolver(profile, credentials)
        except Exception:
            if mode == "cloud":
                raise StageExecutionError("cloud_credentials_unavailable") from None
            return self._run_local(
                context,
                selected,
                route="cloud_to_local",
                fallback_reason="cloud_profile_unavailable",
            )
        with Session(context.store.engine) as session:
            submissions = CloudSubmissionStore(session)
            try:
                submission = submissions.prepare(
                    context.claim.stage_run_id,
                    digest,
                    locator,
                )
            except CloudSubmissionError as error:
                raise StageExecutionError(error.code) from None
        context.checkpoint()
        signed_url = None
        signed_expires = None
        try:
            if stager is not None and cos_context is not None:
                stager.put(audio, cos_context)
                context.checkpoint()
                signed_ttl = timedelta(minutes=10)
                signed_url = stager.presign_get(locator, signed_ttl)
                signed_expires = context.clock() + signed_ttl
            with Session(context.store.engine) as session:
                submission = CloudSubmissionStore(session).mark_sending(
                    submission.id,
                    signed_url_expires_at=signed_expires,
                )
            request = TencentRequestContext(
                credentials=credentials,
                timestamp=int(context.clock().timestamp()),
                signed_url=signed_url,
            )
            task = self.cloud_client.create(audio, request, submission)
        except StageCancelled:
            if locator is not None:
                with Session(context.store.engine) as session:
                    current = CloudSubmissionStore(session).get(submission.id)
                    if current.state == "prepared":
                        CloudSubmissionStore(session).schedule_cancel_cleanup(
                            submission.id,
                            context.clock(),
                        )
            raise
        except CloudAsrRequestError as error:
            outcome = error.outcome
            with Session(context.store.engine) as session:
                submissions = CloudSubmissionStore(session)
                if outcome.kind == CloudAsrOutcomeKind.UNKNOWN:
                    terminal = submissions.mark_unknown(
                        submission.id,
                        outcome.safe_code,
                        marked_at=context.clock(),
                    )
                else:
                    terminal = submissions.mark_failed_before_submit(
                        submission.id,
                        outcome.safe_code,
                        failed_at=context.clock(),
                    )
            return self._handle_cloud_terminal(
                context,
                selected,
                mode=mode,
                profile=profile,
                submission=terminal,
            )
        except Exception:
            with Session(context.store.engine) as session:
                submissions = CloudSubmissionStore(session)
                current = submissions.get(submission.id)
                if current.state == "sending":
                    terminal = submissions.mark_unknown(
                        submission.id,
                        "provider_submission_state_unknown",
                        marked_at=context.clock(),
                    )
                elif current.state == "prepared":
                    terminal = submissions.mark_failed_before_submit(
                        submission.id,
                        "cloud_cos_staging_failed",
                        failed_at=context.clock(),
                    )
                else:
                    terminal = current
            return self._handle_cloud_terminal(
                context,
                selected,
                mode=mode,
                profile=profile,
                submission=terminal,
            )
        with Session(context.store.engine) as session:
            submissions = CloudSubmissionStore(session)
            submitted = submissions.mark_submitted(
                submission.id,
                task.task_id,
                task.request_id,
                task.submitted_at,
            )
            submissions.schedule_query(
                submitted.id,
                task.submitted_at + bounded_poll_delay(task.task_id, 0),
            )
        raise self._deferred(profile)

    def run(self, context: StageContext) -> StageResult:
        try:
            context.checkpoint()
            selected = self._load_input(context)
            mode, profile = self._selected_asr(context, selected.snapshot)
            if mode == "local":
                return self._run_local(context, selected, route="local")
            if profile is None:
                if mode == "cloud":
                    raise StageExecutionError("cloud_asr_consent_required")
                return self._run_local(
                    context,
                    selected,
                    route="local",
                    fallback_reason="cloud_profile_unavailable",
                )
            return self._submit_cloud(
                context,
                selected,
                mode=mode,
                profile=profile,
            )
        except (StageCancelled, StageExecutionError):
            self._trash_owned_media_best_effort(context)
            raise
