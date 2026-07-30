from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from vtnote.cloud_submissions import CloudSubmissionStore
from vtnote.config import Settings
from vtnote.configuration import ConfigurationService
from vtnote.database import initialize_database
from vtnote.artifacts import ensure_transcript_json
from vtnote.local_asr import AsrResult, LocalAsrProvenance
from vtnote.media import MediaInfo, PreparedAudio
from vtnote.models import (
    CloudSubmissionRecord,
    ItemRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
    StageRunRecord,
    TaskRecord,
)
from vtnote.paths import StoragePaths
from vtnote.provider_credentials import (
    TencentCredentialBundle,
    serialize_credential_bundle,
)
from vtnote.secrets import MemorySecretStore
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
)
from vtnote.tencent_asr import (
    CloudAsrOutcome,
    CloudAsrOutcomeKind,
    TencentRequestContext,
    TencentSubmissionReconciler,
    TencentTaskRef,
)
from vtnote.tencent_contract import TencentSentence
from vtnote.tencent_cos import SensitiveUrl
from vtnote.tasks import TaskService
from vtnote.transcribe_stage import (
    SnapshotTencentCredentialResolver,
    TranscribeStageHandler,
)
from vtnote.worker import (
    StageCancelled,
    StageContext,
    StageDeferred,
    StageExecutionError,
    Worker,
)
from vtnote.worker_store import WorkerStore


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


class FakeMedia:
    def __init__(self, prepared: PreparedAudio) -> None:
        self.prepared = prepared
        self.local_calls: list[tuple[str, Path]] = []
        self.cloud_calls: list[tuple[str, Path]] = []

    def prepare_for_local(self, item_id: str, source: Path) -> PreparedAudio:
        self.local_calls.append((item_id, source))
        return self.prepared

    def convert_for_cloud(self, item_id: str, source: Path) -> PreparedAudio:
        self.cloud_calls.append((item_id, source))
        return self.prepared


class FakeLocalTranscriber:
    def __init__(self, result: AsrResult) -> None:
        self.result = result
        self.calls: list[object] = []

    def transcribe(self, audio: PreparedAudio, context: object) -> AsrResult:
        self.calls.append((audio, context))
        return self.result


class FakeCloudClient:
    def __init__(
        self,
        *,
        create_error: Exception | None = None,
    ) -> None:
        self.create_error = create_error
        self.create_calls = 0

    def create(
        self,
        audio: PreparedAudio,
        context: TencentRequestContext,
        submission: object,
    ) -> TencentTaskRef:
        self.create_calls += 1
        if self.create_error is not None:
            raise self.create_error
        return TencentTaskRef(
            task_id="12345",
            request_id="request-create",
            submitted_at=NOW,
        )


class SuccessQueryClient:
    def __init__(self) -> None:
        self.calls = 0

    def query(
        self,
        task: TencentTaskRef,
        context: TencentRequestContext,
    ) -> CloudAsrOutcome:
        self.calls += 1
        return CloudAsrOutcome(
            CloudAsrOutcomeKind.SUCCESS,
            "provider_success",
            sentences=(
                TencentSentence(0, 1_000, "云端第一句"),
                TencentSentence(1_000, 2_000, "云端第二句"),
            ),
            provider_status="success",
            request_id="request-query",
        )


def paths(tmp_path: Path) -> StoragePaths:
    return StoragePaths.from_settings(
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
        )
    )


def prepared_audio(tmp_path: Path, *, cloud: bool) -> PreparedAudio:
    path = tmp_path / ("cloud.ogg" if cloud else "local.wav")
    path.write_bytes(b"encoded-audio")
    return PreparedAudio(
        path=path,
        asset_id="asset-1",
        converted=True,
        media_info=MediaInfo(
            duration_ms=4_000,
            size_bytes=path.stat().st_size,
            format_name="ogg" if cloud else "wav",
            audio_codec="opus" if cloud else "pcm_s16le",
            sample_rate=16_000,
            channels=1,
        ),
    )


def local_result() -> AsrResult:
    return AsrResult(
        transcript=Transcript(
            language="zh-Hans",
            duration_ms=4_000,
            provenance=Provenance(
                method=ProvenanceMethod.LOCAL_ASR,
                provider="faster-whisper",
                model="large-v3-turbo",
            ),
            segments=(
                TranscriptSegment(
                    id="seg_000001",
                    start_ms=0,
                    end_ms=1_000,
                    text="本地结果",
                ),
            ),
        ),
        provenance=LocalAsrProvenance(
            model_revision="0" * 40,
            model_manifest_sha256="1" * 64,
            model_file_sha256={"model.bin": "2" * 64},
            library_versions={
                "faster_whisper": "1.2.1",
                "ctranslate2": "4.8.1",
                "cuda_runtime": "12.8.90",
                "cublas": "12.8.4.1",
                "cudnn": "9.10.2.21",
            },
        ),
    )


def local_snapshot(*, device: str = "cuda") -> dict[str, object]:
    return {
        "model": "large-v3-turbo",
        "device": device,
        "compute_type": "int8_float16",
        "vad_filter": True,
        "model_root": r"D:\Workspace\Project\VtNote-data\models\faster-whisper",
        "cache_root": r"D:\Workspace\Codex\cache\VtNote-runtime\models\faster-whisper",
    }


def cloud_profile() -> dict[str, object]:
    return {
        "id": "profile-id",
        "connection_id": "connection-id",
        "name": "Tencent",
        "purpose": "cloud_asr",
        "protocol": "tencent_recording_asr",
        "base_url": "https://asr.tencentcloudapi.com",
        "parameters": {
            "asr_region": "ap-guangzhou",
            "cos_configured": False,
        },
        "connection_revision": 1,
        "model": "16k_zh_en_2.0",
        "context_length": 8192,
        "options": {
            "language_scope": "zh_en_dialects",
            "res_text_format": 3,
            "sentence_max_length": 20,
        },
        "profile_revision": 1,
        "has_secret": True,
    }


def cloud_profile_with_cos() -> dict[str, object]:
    profile = cloud_profile()
    profile["parameters"] = {
        "asr_region": "ap-guangzhou",
        "cos_bucket": "vtnote-private-1250000000",
        "cos_region": "ap-guangzhou",
        "cos_prefix": "vtnote-runtime",
        "cos_private": True,
        "cos_configured": True,
    }
    return profile


def make_claim(
    tmp_path: Path,
    *,
    mode: str,
    profile: dict[str, object] | None,
) -> tuple[StoragePaths, object, WorkerStore, str]:
    selected_paths = paths(tmp_path)
    engine = initialize_database(selected_paths.database)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    with Session(engine) as session:
        task = TaskRecord(
            pipeline_snapshot_json={
                "schema_version": 1,
                "asr": {"mode": mode, "profile": profile},
                "local_whisper": local_snapshot(),
            }
        )
        item = ItemRecord(
            task=task,
            position=0,
            source_kind="local_media",
            source_locator=str(source),
            status="queued",
        )
        item.stage_runs = [
            StageRunRecord(
                stage="source",
                attempt=1,
                status="completed",
                finished_at=NOW,
            ),
            StageRunRecord(stage="transcribe", attempt=1, status="queued"),
        ]
        session.add(item)
        session.commit()
        item_id = item.id
    store = WorkerStore(engine)
    claim = store.claim_next("worker-1", NOW, timedelta(minutes=5))
    assert claim is not None and claim.stage == "transcribe"
    return selected_paths, claim, store, item_id


def credentials(_: object) -> TencentCredentialBundle:
    return TencentCredentialBundle(
        secret_id="AKID-example",
        secret_key="secret-key",
    )


def request_context(_: object) -> TencentRequestContext:
    return TencentRequestContext(credentials=credentials(None), timestamp=int(NOW.timestamp()))


def test_local_mode_publishes_once_without_cloud_call(tmp_path: Path) -> None:
    selected_paths, claim, store, item_id = make_claim(
        tmp_path, mode="local", profile=None
    )
    selected_audio = prepared_audio(tmp_path, cloud=False)
    local = FakeLocalTranscriber(local_result())
    cloud = FakeCloudClient()
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(selected_audio),
        local_transcriber=local,
        cloud_client=cloud,
        credential_resolver=credentials,
    )

    result = handler.run(StageContext(store, claim, lambda: NOW))
    assert store.complete(claim, result, now=NOW + timedelta(seconds=1))

    stored = Transcript.model_validate_json(
        selected_paths.transcript(item_id).read_text(encoding="utf-8")
    )
    assert stored.segments[0].text == "本地结果"
    assert result.execution_evidence == MappingProxyType(
        {
            "source_method": "local_asr",
            "asr_route": "local",
            "provider": "faster_whisper",
            "model": "large-v3-turbo",
        }
    )
    assert cloud.create_calls == 0


def test_auto_without_cloud_profile_routes_local_with_safe_reason(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="auto", profile=None
    )
    local = FakeLocalTranscriber(local_result())
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=False)),
        local_transcriber=local,
        cloud_client=FakeCloudClient(),
        credential_resolver=credentials,
    )

    result = handler.run(StageContext(store, claim, lambda: NOW))

    assert result.execution_evidence is not None
    assert result.execution_evidence["asr_route"] == "local"
    assert result.execution_evidence["fallback_reason"] == "cloud_profile_unavailable"


def test_cloud_mode_without_snapshot_consent_stops_before_any_remote_call(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="cloud", profile=None
    )
    cloud = FakeCloudClient()
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=cloud,
        credential_resolver=credentials,
    )

    with pytest.raises(StageExecutionError) as caught:
        handler.run(StageContext(store, claim, lambda: NOW))

    assert caught.value.code == "cloud_asr_consent_required"
    assert cloud.create_calls == 0


def test_cloud_submit_defers_and_reconciler_wakes_query_only_publication(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, item_id = make_claim(
        tmp_path, mode="cloud", profile=cloud_profile()
    )
    cloud = FakeCloudClient()
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=cloud,
        credential_resolver=credentials,
    )
    context = StageContext(store, claim, lambda: NOW)

    with pytest.raises(StageDeferred) as deferred:
        handler.run(context)
    assert store.defer_external(
        claim,
        deferred.value,
        now=NOW + timedelta(seconds=1),
    )
    assert cloud.create_calls == 1

    query = SuccessQueryClient()
    reconciler = TencentSubmissionReconciler(
        engine=store.engine,
        client=query,
        request_context=request_context,
        cos_stager=None,
        worker_id="cloud-reconciler",
    )
    reconciled = reconciler.reconcile_one_due(NOW + timedelta(minutes=1))
    assert reconciled is not None
    assert reconciled.action == "provider_succeeded"
    assert query.calls == 1

    publication_claim = store.claim_next(
        "worker-2",
        NOW + timedelta(minutes=1, seconds=1),
        timedelta(minutes=5),
    )
    assert publication_claim is not None
    result = handler.run(
        StageContext(
            store,
            publication_claim,
            lambda: NOW + timedelta(minutes=1, seconds=1),
        )
    )
    assert store.complete(
        publication_claim,
        result,
        now=NOW + timedelta(minutes=1, seconds=2),
    )
    transcript = Transcript.model_validate_json(
        selected_paths.transcript(item_id).read_text(encoding="utf-8")
    )
    assert [segment.text for segment in transcript.segments] == [
        "云端第一句",
        "云端第二句",
    ]
    assert cloud.create_calls == 1

    with Session(store.engine) as session:
        submission = session.scalar(select(CloudSubmissionRecord))
        assert submission is not None
        assert submission.normalized_result_json == {
            "language": "zh-Hans",
            "sentences": [
                {"start_ms": 0, "end_ms": 1_000, "text": "云端第一句"},
                {"start_ms": 1_000, "end_ms": 2_000, "text": "云端第二句"},
            ],
        }


def test_auto_known_create_failure_falls_back_local_but_cloud_mode_stops(
    tmp_path: Path,
) -> None:
    from vtnote.tencent_asr import CloudAsrRequestError

    error = CloudAsrRequestError(
        CloudAsrOutcome(
            CloudAsrOutcomeKind.FALLBACK_ALLOWED,
            "provider_rate_limited",
        )
    )
    for mode, expected_error in (("auto", None), ("cloud", "provider_rate_limited")):
        case = tmp_path / mode
        case.mkdir()
        selected_paths, claim, store, _ = make_claim(
            case, mode=mode, profile=cloud_profile()
        )
        local = FakeLocalTranscriber(local_result())
        handler = TranscribeStageHandler(
            paths=selected_paths,
            media=FakeMedia(prepared_audio(case, cloud=True)),
            local_transcriber=local,
            cloud_client=FakeCloudClient(create_error=error),
            credential_resolver=credentials,
        )
        if expected_error is None:
            result = handler.run(StageContext(store, claim, lambda: NOW))
            assert result.execution_evidence is not None
            assert result.execution_evidence["asr_route"] == "cloud_to_local"
            assert result.execution_evidence["fallback_reason"] == "cloud_rate_limited"
            assert len(local.calls) == 1
        else:
            with pytest.raises(StageExecutionError) as caught:
                handler.run(StageContext(store, claim, lambda: NOW))
            assert caught.value.code == expected_error
            assert local.calls == []


def test_cloud_submission_unknown_auto_falls_back_with_billing_warning(
    tmp_path: Path,
) -> None:
    from vtnote.tencent_asr import CloudAsrRequestError

    error = CloudAsrRequestError(
        CloudAsrOutcome(
            CloudAsrOutcomeKind.UNKNOWN,
            "provider_create_response_unknown",
        )
    )
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="auto", profile=cloud_profile()
    )
    local = FakeLocalTranscriber(local_result())
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=local,
        cloud_client=FakeCloudClient(create_error=error),
        credential_resolver=credentials,
    )

    result = handler.run(StageContext(store, claim, lambda: NOW))

    assert result.warning == "cloud_submission_unknown_possible_charge"
    assert result.execution_evidence is not None
    assert result.execution_evidence["fallback_reason"] == "cloud_submission_unknown"
    with Session(store.engine) as session:
        submission = CloudSubmissionStore(session).get(
            session.scalar(select(CloudSubmissionRecord.id))
        )
        assert submission.state == "submission_unknown"


def test_cloud_mode_submission_unknown_stops_without_local_fallback(
    tmp_path: Path,
) -> None:
    from vtnote.tencent_asr import CloudAsrRequestError

    error = CloudAsrRequestError(
        CloudAsrOutcome(
            CloudAsrOutcomeKind.UNKNOWN,
            "provider_create_response_unknown",
        )
    )
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="cloud", profile=cloud_profile()
    )
    local = FakeLocalTranscriber(local_result())
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=local,
        cloud_client=FakeCloudClient(create_error=error),
        credential_resolver=credentials,
    )

    with pytest.raises(StageExecutionError) as caught:
        handler.run(StageContext(store, claim, lambda: NOW))

    assert caught.value.code == "cloud_submission_unknown"
    assert local.calls == []
    with Session(store.engine) as session:
        stage = session.get(StageRunRecord, claim.stage_run_id)
        assert stage is not None
        assert stage.external_submission_state == "submission_unknown"


def test_cos_route_uses_deterministic_private_object_and_defers(
    tmp_path: Path,
) -> None:
    class FakeCos:
        def __init__(self) -> None:
            self.puts: list[object] = []
            self.presigns: list[object] = []

        def put(self, selected: PreparedAudio, selected_context: object):
            self.puts.append((selected, selected_context))
            return selected_context.locator()

        def presign_get(self, locator: object, ttl: timedelta) -> SensitiveUrl:
            self.presigns.append((locator, ttl))
            return SensitiveUrl(
                "https://vtnote-private-1250000000.cos.ap-guangzhou.myqcloud.com/"
                f"{locator.object_key}?q-signature=test"
            )

    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="cloud", profile=cloud_profile_with_cos()
    )
    selected_audio = prepared_audio(tmp_path, cloud=True)
    selected_audio = PreparedAudio(
        path=selected_audio.path,
        asset_id=selected_audio.asset_id,
        converted=True,
        media_info=MediaInfo(
            duration_ms=selected_audio.media_info.duration_ms,
            size_bytes=5_000_000,
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )
    cos = FakeCos()
    cloud = FakeCloudClient()
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(selected_audio),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=cloud,
        credential_resolver=credentials,
        cos_stager_resolver=lambda _profile, _credentials: cos,  # type: ignore[arg-type]
    )

    with pytest.raises(StageDeferred):
        handler.run(StageContext(store, claim, lambda: NOW))

    assert cloud.create_calls == 1
    assert len(cos.puts) == 1
    assert len(cos.presigns) == 1
    with Session(store.engine) as session:
        submission = session.scalar(select(CloudSubmissionRecord))
        assert submission is not None
        assert submission.cos_bucket == "vtnote-private-1250000000"
        assert submission.cos_region == "ap-guangzhou"
        assert submission.cos_object_key is not None
        assert submission.cos_object_key.startswith("vtnote-runtime/")


def test_provider_result_expiry_wakes_auto_task_for_local_fallback(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="auto", profile=cloud_profile()
    )
    local = FakeLocalTranscriber(local_result())
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=local,
        cloud_client=FakeCloudClient(),
        credential_resolver=credentials,
    )
    with pytest.raises(StageDeferred) as deferred:
        handler.run(StageContext(store, claim, lambda: NOW))
    assert store.defer_external(claim, deferred.value, now=NOW + timedelta(seconds=1))

    reconciler = TencentSubmissionReconciler(
        engine=store.engine,
        client=SuccessQueryClient(),
        request_context=request_context,
        cos_stager=None,
        worker_id="cloud-reconciler",
    )
    expired = reconciler.reconcile_one_due(NOW + timedelta(hours=25))
    assert expired is not None
    assert expired.action == "provider_result_expired"
    retry = store.claim_next(
        "worker-2",
        NOW + timedelta(hours=25, seconds=1),
        timedelta(minutes=5),
    )
    assert retry is not None
    result = handler.run(
        StageContext(
            store,
            retry,
            lambda: NOW + timedelta(hours=25, seconds=1),
        )
    )
    assert result.execution_evidence is not None
    assert result.execution_evidence["asr_route"] == "cloud_to_local"
    assert result.execution_evidence["fallback_reason"] == "cloud_result_invalid"
    assert len(local.calls) == 1


def test_cancel_after_task_id_persistence_cancels_local_stage_but_remote_reconciles(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="cloud", profile=cloud_profile()
    )
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=FakeCloudClient(),
        credential_resolver=credentials,
    )
    with pytest.raises(StageDeferred) as deferred:
        handler.run(StageContext(store, claim, lambda: NOW))
    assert store.defer_external(claim, deferred.value, now=NOW + timedelta(seconds=1))

    with Session(store.engine) as session:
        task_id = session.get(StageRunRecord, claim.stage_run_id).item.task_id
        tasks = TaskService(
            session,
            ConfigurationService(
                session,
                MemorySecretStore(),
                paths=selected_paths,
            ),
            selected_paths,
            object(),  # source URL policy is unused by cancellation
        )
        canceled = tasks.cancel_task(task_id)
        assert canceled.status == "canceled"
        stage = session.get(StageRunRecord, claim.stage_run_id)
        assert stage is not None and stage.status == "canceled"

    query = SuccessQueryClient()
    reconciler = TencentSubmissionReconciler(
        engine=store.engine,
        client=query,
        request_context=request_context,
        cos_stager=None,
        worker_id="cloud-reconciler",
    )
    result = reconciler.reconcile_one_due(NOW + timedelta(minutes=1))
    assert result is not None and result.action == "provider_succeeded"
    assert query.calls == 1
    with Session(store.engine) as session:
        stage = session.get(StageRunRecord, claim.stage_run_id)
        submission = session.scalar(select(CloudSubmissionRecord))
        assert stage is not None and stage.status == "canceled"
        assert submission is not None and submission.state == "succeeded"


def test_waiting_cloud_stage_does_not_block_unrelated_queued_work(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="cloud", profile=cloud_profile()
    )
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=FakeCloudClient(),
        credential_resolver=credentials,
    )
    with pytest.raises(StageDeferred) as deferred:
        handler.run(StageContext(store, claim, lambda: NOW))
    assert store.defer_external(claim, deferred.value, now=NOW + timedelta(seconds=1))

    with Session(store.engine) as session:
        other_task = TaskRecord(pipeline_snapshot_json={})
        other_item = ItemRecord(
            task=other_task,
            position=0,
            source_kind="local_media",
            source_locator=str(tmp_path / "other.mp4"),
            status="queued",
        )
        other_item.stage_runs = [
            StageRunRecord(stage="source", attempt=1, status="queued"),
            StageRunRecord(stage="transcribe", attempt=1, status="queued"),
        ]
        session.add(other_item)
        session.commit()
        expected = other_item.stage_runs[0].id

    unrelated = store.claim_next(
        "worker-2",
        NOW + timedelta(seconds=2),
        timedelta(minutes=5),
    )
    assert unrelated is not None
    assert unrelated.stage_run_id == expected


def test_conflicting_existing_transcript_is_never_overwritten(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, item_id = make_claim(
        tmp_path, mode="local", profile=None
    )
    existing = Transcript(
        language="en",
        duration_ms=1_000,
        provenance=Provenance(
            method=ProvenanceMethod.LOCAL_ASR,
            provider="faster-whisper",
            model="large-v3-turbo",
        ),
        segments=(
            TranscriptSegment(
                id="seg_000001",
                start_ms=0,
                end_ms=1_000,
                text="existing",
            ),
        ),
    )
    ensure_transcript_json(selected_paths, item_id, existing)
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=False)),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=None,
        credential_resolver=credentials,
    )

    with pytest.raises(StageExecutionError) as caught:
        handler.run(StageContext(store, claim, lambda: NOW))

    assert caught.value.code == "transcript_publication_conflict"
    stored = Transcript.model_validate_json(
        selected_paths.transcript(item_id).read_text(encoding="utf-8")
    )
    assert stored.segments[0].text == "existing"


def test_cancel_during_audio_preparation_stops_before_paid_submit(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="cloud", profile=cloud_profile()
    )

    class CancelingMedia(FakeMedia):
        def convert_for_cloud(self, item_id: str, source: Path) -> PreparedAudio:
            with Session(store.engine) as session:
                task_id = session.get(StageRunRecord, claim.stage_run_id).item.task_id
                TaskService(
                    session,
                    ConfigurationService(
                        session,
                        MemorySecretStore(),
                        paths=selected_paths,
                    ),
                    selected_paths,
                    object(),
                ).cancel_task(task_id)
            return super().convert_for_cloud(item_id, source)

    cloud = FakeCloudClient()
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=CancelingMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=cloud,
        credential_resolver=credentials,
    )

    with pytest.raises(StageCancelled):
        handler.run(StageContext(store, claim, lambda: NOW))

    assert cloud.create_calls == 0


def test_crash_after_task_id_persistence_recovers_without_second_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="cloud", profile=cloud_profile()
    )
    cloud = FakeCloudClient()
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=True)),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=cloud,
        credential_resolver=credentials,
    )
    original_schedule = CloudSubmissionStore.schedule_query

    def crash_after_task_id(self, submission_id: str, next_poll_at: datetime):
        raise SystemExit("simulated process loss")

    monkeypatch.setattr(CloudSubmissionStore, "schedule_query", crash_after_task_id)
    with pytest.raises(SystemExit):
        handler.run(StageContext(store, claim, lambda: NOW))
    assert cloud.create_calls == 1
    monkeypatch.setattr(CloudSubmissionStore, "schedule_query", original_schedule)

    store.recover_expired(NOW + timedelta(minutes=6))
    recovered = store.claim_next(
        "worker-2",
        NOW + timedelta(minutes=6, seconds=1),
        timedelta(minutes=5),
    )
    assert recovered is not None
    with pytest.raises(StageDeferred) as deferred:
        handler.run(
            StageContext(
                store,
                recovered,
                lambda: NOW + timedelta(minutes=6, seconds=1),
            )
        )
    assert store.defer_external(
        recovered,
        deferred.value,
        now=NOW + timedelta(minutes=6, seconds=2),
    )
    assert cloud.create_calls == 1
    with Session(store.engine) as session:
        submission = session.scalar(select(CloudSubmissionRecord))
        assert submission is not None
        assert submission.state == "submitted"
        assert submission.next_poll_at is not None


def test_crash_after_cos_put_recovers_same_object_before_single_asr_create(
    tmp_path: Path,
) -> None:
    class CrashOnceCos:
        def __init__(self) -> None:
            self.crash = True
            self.keys: list[str] = []

        def put(self, selected: PreparedAudio, selected_context: object):
            locator = selected_context.locator()
            self.keys.append(locator.object_key)
            if self.crash:
                self.crash = False
                raise SystemExit("simulated process loss after COS put")
            return locator

        def presign_get(self, locator: object, ttl: timedelta) -> SensitiveUrl:
            return SensitiveUrl(
                "https://vtnote-private-1250000000.cos.ap-guangzhou.myqcloud.com/"
                f"{locator.object_key}?q-signature=test"
            )

    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="cloud", profile=cloud_profile_with_cos()
    )
    selected_audio = prepared_audio(tmp_path, cloud=True)
    selected_audio = PreparedAudio(
        path=selected_audio.path,
        asset_id=selected_audio.asset_id,
        converted=True,
        media_info=MediaInfo(
            duration_ms=4_000,
            size_bytes=5_000_000,
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )
    cos = CrashOnceCos()
    cloud = FakeCloudClient()
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(selected_audio),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=cloud,
        credential_resolver=credentials,
        cos_stager_resolver=lambda _profile, _credentials: cos,  # type: ignore[arg-type]
    )

    with pytest.raises(SystemExit):
        handler.run(StageContext(store, claim, lambda: NOW))
    assert cloud.create_calls == 0

    store.recover_expired(NOW + timedelta(minutes=6))
    recovered = store.claim_next(
        "worker-2",
        NOW + timedelta(minutes=6, seconds=1),
        timedelta(minutes=5),
    )
    assert recovered is not None
    with pytest.raises(StageDeferred):
        handler.run(
            StageContext(
                store,
                recovered,
                lambda: NOW + timedelta(minutes=6, seconds=1),
            )
        )

    assert cloud.create_calls == 1
    assert len(cos.keys) == 2
    assert cos.keys[0] == cos.keys[1]


def test_unknown_cos_submission_keeps_deferred_remote_cleanup_deadline(
    tmp_path: Path,
) -> None:
    from vtnote.tencent_asr import CloudAsrRequestError

    class FakeCos:
        def put(self, selected: PreparedAudio, selected_context: object):
            return selected_context.locator()

        def presign_get(self, locator: object, ttl: timedelta) -> SensitiveUrl:
            return SensitiveUrl(
                "https://vtnote-private-1250000000.cos.ap-guangzhou.myqcloud.com/"
                f"{locator.object_key}?q-signature=test"
            )

    selected_paths, claim, store, _ = make_claim(
        tmp_path, mode="auto", profile=cloud_profile_with_cos()
    )
    selected_audio = prepared_audio(tmp_path, cloud=True)
    selected_audio = PreparedAudio(
        path=selected_audio.path,
        asset_id=selected_audio.asset_id,
        converted=True,
        media_info=MediaInfo(
            duration_ms=4_000,
            size_bytes=5_000_000,
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(selected_audio),
        local_transcriber=FakeLocalTranscriber(local_result()),
        cloud_client=FakeCloudClient(
            create_error=CloudAsrRequestError(
                CloudAsrOutcome(
                    CloudAsrOutcomeKind.UNKNOWN,
                    "provider_create_response_unknown",
                )
            )
        ),
        credential_resolver=credentials,
        cos_stager_resolver=lambda _profile, _credentials: FakeCos(),  # type: ignore[arg-type]
    )

    result = handler.run(StageContext(store, claim, lambda: NOW))

    assert result.warning == "cloud_submission_unknown_possible_charge"
    with Session(store.engine) as session:
        submission = session.scalar(select(CloudSubmissionRecord))
        assert submission is not None
        assert submission.state == "submission_unknown"
        assert submission.signed_url_expires_at == NOW + timedelta(minutes=10)
        assert submission.cleanup_due_at == NOW + timedelta(minutes=40)


def test_worker_persists_external_wait_and_yields_main_loop(
    tmp_path: Path,
) -> None:
    _, claim, store, _ = make_claim(tmp_path, mode="cloud", profile=cloud_profile())
    assert store.requeue(claim, now=NOW + timedelta(seconds=1))
    stopped = False

    class DeferringHandler:
        def run(self, context: StageContext):
            nonlocal stopped
            stopped = True
            raise StageDeferred(
                external_submission_state="submitted",
                execution_evidence={
                    "source_method": "cloud_asr",
                    "asr_route": "cloud",
                    "provider": "tencent_recording_asr",
                    "model": "16k_zh_en_2.0",
                },
            )

    Worker(
        store=store,
        worker_id="worker-main",
        handlers={"transcribe": DeferringHandler()},
        lease_duration=timedelta(minutes=5),
        clock=lambda: NOW + timedelta(seconds=2),
        sleeper=lambda _: None,
        stop_requested=lambda: stopped,
    ).run()

    with Session(store.engine) as session:
        stage = session.get(StageRunRecord, claim.stage_run_id)
        assert stage is not None
        assert stage.status == "waiting_external"
        assert stage.lease_owner is None
        assert stage.progress_json["message_code"] == "waiting_cloud_asr"


def test_cloud_credentials_resolve_only_exact_snapshot_revisions(
    tmp_path: Path,
) -> None:
    selected_paths = paths(tmp_path)
    engine = initialize_database(selected_paths.database)
    secrets = MemorySecretStore()
    with Session(engine) as session:
        connection = ProviderConnectionRecord(
            name="Tencent",
            protocol="tencent_recording_asr",
            base_url="https://asr.tencentcloudapi.com",
            parameters={
                "asr_region": "ap-guangzhou",
                "cos_configured": False,
            },
            credential_ref="connection:test",
            revision=3,
        )
        profile = ProcessorProfileRecord(
            name="Tencent profile",
            purpose="cloud_asr",
            connection=connection,
            model="16k_zh_en_2.0",
            options={"language_scope": "zh_en_dialects"},
            revision=2,
        )
        session.add(profile)
        session.commit()
        snapshot = {
            "id": profile.id,
            "connection_id": connection.id,
            "profile_revision": 2,
            "connection_revision": 3,
        }
        connection_id = connection.id
    secrets.set(
        "connection:test",
        serialize_credential_bundle(
            "tencent_recording_asr",
            {"secret_id": "AKID-exact", "secret_key": "exact-key"},
        ),
    )
    resolver = SnapshotTencentCredentialResolver(engine=engine, secrets=secrets)

    bundle = resolver(snapshot)
    assert bundle.secret_id.get_secret_value() == "AKID-exact"

    with Session(engine) as session:
        connection = session.get(ProviderConnectionRecord, connection_id)
        assert connection is not None
        connection.revision = 4
        session.commit()
    with pytest.raises(ValueError, match="revision"):
        resolver(snapshot)


def test_crash_after_local_publication_recovers_without_second_inference(
    tmp_path: Path,
) -> None:
    selected_paths, claim, store, item_id = make_claim(
        tmp_path, mode="local", profile=None
    )
    local = FakeLocalTranscriber(local_result())
    handler = TranscribeStageHandler(
        paths=selected_paths,
        media=FakeMedia(prepared_audio(tmp_path, cloud=False)),
        local_transcriber=local,
        cloud_client=None,
        credential_resolver=credentials,
    )

    first = handler.run(StageContext(store, claim, lambda: NOW))
    assert selected_paths.transcript(item_id).is_file()
    assert len(local.calls) == 1
    # Simulate process loss after the immutable file write but before store.complete.
    del first
    store.recover_expired(NOW + timedelta(minutes=6))

    recovered = store.claim_next(
        "worker-2",
        NOW + timedelta(minutes=6, seconds=1),
        timedelta(minutes=5),
    )
    assert recovered is not None
    result = handler.run(
        StageContext(
            store,
            recovered,
            lambda: NOW + timedelta(minutes=6, seconds=1),
        )
    )

    assert len(local.calls) == 1
    assert store.complete(
        recovered,
        result,
        now=NOW + timedelta(minutes=6, seconds=2),
    )
