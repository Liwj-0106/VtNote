from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from vtnote.cloud_submissions import (
    CloudSubmission,
    CloudSubmissionStore,
    CosLocator,
)
from vtnote.database import initialize_database
from vtnote.diagnostics import sanitize_diagnostic
from vtnote.config import Settings
from vtnote.media import MediaInfo, PreparedAudio
from vtnote.models import (
    CloudSubmissionRecord,
    ItemRecord,
    ResourceLeaseRecord,
    StageRunRecord,
    TaskRecord,
)
from vtnote.provider_credentials import TencentCredentialBundle
from vtnote.paths import StoragePaths
from vtnote.runtime_assets import RuntimeAssetService
from vtnote.tencent_asr import (
    CloudAsrOutcome,
    CloudAsrOutcomeKind,
    CloudAsrRequestError,
    ReconcileResult,
    TencentHttpResponse,
    TencentConnectivityTester,
    TencentRecordingClient,
    TencentRequestContext,
    TencentSubmissionReconciler,
    TencentTaskRef,
    TencentTransportFailure,
    UploadedSpeechSampleResolver,
    bounded_poll_delay,
)
from vtnote.tencent_contract import TENCENT_ASR_ENDPOINT, TencentSentence


NOW = datetime(2026, 7, 29, 16, 0, tzinfo=timezone.utc)
SIGNED_AT = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
STAGE_ID = "33333333-3333-4333-8333-333333333333"
SUBMISSION_ID = "44444444-4444-4444-8444-444444444444"
TASK_ID = "18446744073709551615"


@dataclass
class RecordedCall:
    url: str
    headers: dict[str, str]
    body: bytes


class StubTransport:
    def __init__(
        self,
        responses: list[TencentHttpResponse | TencentTransportFailure],
    ) -> None:
        self.responses = responses
        self.calls: list[RecordedCall] = []

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> TencentHttpResponse:
        self.calls.append(RecordedCall(url, headers, body))
        response = self.responses.pop(0)
        if isinstance(response, TencentTransportFailure):
            raise response
        return response


class TestSignedUrl:
    def reveal(self) -> str:
        return (
            "https://private-audio-1250000000.cos.ap-guangzhou.myqcloud.com/"
            "vtnote-runtime/11111111-1111-4111-8111-111111111111/"
            "ed8910887ecb144f.ogg?q-sign-algorithm=sha1&q-signature=hidden"
        )


def context(*, signed_url: TestSignedUrl | None = None) -> TencentRequestContext:
    return TencentRequestContext(
        credentials=TencentCredentialBundle(
            secret_id=SecretStr("AKID-example"),
            secret_key=SecretStr("secret-key"),
        ),
        timestamp=1_700_000_000,
        signed_url=signed_url,
    )


def prepared_audio(tmp_path: Path, data: bytes = b"audio") -> PreparedAudio:
    path = tmp_path / "encoded.ogg"
    path.write_bytes(data)
    return PreparedAudio(
        path=path,
        asset_id="asset-1",
        converted=True,
        media_info=MediaInfo(
            duration_ms=3_000,
            size_bytes=len(data),
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )


def submission(
    data: bytes = b"audio",
    *,
    locator: CosLocator | None = None,
) -> CloudSubmission:
    return CloudSubmission(
        id=SUBMISSION_ID,
        stage_run_id=STAGE_ID,
        provider="tencent_recording_asr",
        provider_task_id=None,
        provider_request_id=None,
        audio_sha256=hashlib.sha256(data).hexdigest(),
        cos_locator=locator,
        state="sending",
        safe_error_code=None,
        next_poll_at=None,
        poll_attempt=0,
        last_query_at=None,
        signed_url_expires_at=None,
        cleanup_due_at=None,
        remote_terminal_at=None,
        submitted_at=None,
        result_expires_at=None,
    )


def test_inline_create_makes_one_exact_canonical_request(tmp_path: Path) -> None:
    transport = StubTransport(
        [
            TencentHttpResponse(
                200,
                {
                    "Response": {
                        "Data": {"TaskId": TASK_ID},
                        "RequestId": "request-create",
                    }
                },
            )
        ]
    )
    audio = prepared_audio(tmp_path)

    task = TencentRecordingClient(transport).create(
        audio,
        context(),
        submission(),
    )

    assert task == TencentTaskRef(TASK_ID, "request-create", SIGNED_AT)
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call.url == TENCENT_ASR_ENDPOINT
    assert json.loads(call.body) == {
        "ChannelNum": 1,
        "Data": "YXVkaW8=",
        "DataLen": 5,
        "EngineModelType": "16k_zh_en_2.0",
        "ResTextFormat": 3,
        "SentenceMaxLength": 20,
        "SourceType": 1,
    }
    assert call.body == (
        b'{"ChannelNum":1,"Data":"YXVkaW8=","DataLen":5,'
        b'"EngineModelType":"16k_zh_en_2.0","ResTextFormat":3,'
        b'"SentenceMaxLength":20,"SourceType":1}'
    )
    assert call.headers["Host"] == "asr.tencentcloudapi.com"
    assert call.headers["X-TC-Action"] == "CreateRecTask"
    assert call.headers["X-TC-Timestamp"] == "1700000000"
    assert call.headers["Authorization"].startswith(
        "TC3-HMAC-SHA256 Credential=AKID-example/2023-11-14/asr/tc3_request"
    )
    assert "secret-key" not in repr(call)


def test_cos_create_uses_only_the_ephemeral_signed_url(tmp_path: Path) -> None:
    locator = CosLocator(
        bucket="private-audio-1250000000",
        region="ap-guangzhou",
        object_key=(
            "vtnote-runtime/11111111-1111-4111-8111-111111111111/"
            "ed8910887ecb144f.ogg"
        ),
    )
    transport = StubTransport(
        [
            TencentHttpResponse(
                200,
                {
                    "Response": {
                        "Data": {"TaskId": "123"},
                        "RequestId": "request-url",
                    }
                },
            )
        ]
    )

    TencentRecordingClient(transport).create(
        prepared_audio(tmp_path),
        context(signed_url=TestSignedUrl()),
        submission(locator=locator),
    )

    payload = json.loads(transport.calls[0].body)
    assert payload["SourceType"] == 0
    assert payload["Url"].endswith("q-signature=hidden")
    assert "Data" not in payload
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        (False, CloudAsrOutcomeKind.FALLBACK_ALLOWED),
        (True, CloudAsrOutcomeKind.UNKNOWN),
    ],
)
def test_create_never_retries_and_distinguishes_send_boundary(
    tmp_path: Path,
    sent: bool,
    expected: CloudAsrOutcomeKind,
) -> None:
    transport = StubTransport(
        [TencentTransportFailure("provider_transport_failed", sent=sent)]
    )

    with pytest.raises(CloudAsrRequestError) as caught:
        TencentRecordingClient(transport).create(
            prepared_audio(tmp_path),
            context(),
            submission(),
        )

    assert caught.value.outcome.kind == expected
    assert caught.value.outcome.safe_code == "provider_transport_failed"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("provider_code", "expected_kind", "expected_safe_code"),
    [
        (
            "AuthFailure.InvalidAuthorization",
            CloudAsrOutcomeKind.STOP,
            "stop_configuration",
        ),
        (
            "FailedOperation.UserHasNoAmount",
            CloudAsrOutcomeKind.STOP,
            "stop_billing_or_quota",
        ),
        (
            "FailedOperation.ErrorRecognize",
            CloudAsrOutcomeKind.FALLBACK_ALLOWED,
            "provider_fallback_allowed",
        ),
        (
            "InternalError.Unknown",
            CloudAsrOutcomeKind.FALLBACK_ALLOWED,
            "provider_fallback_allowed",
        ),
    ],
)
def test_http_200_create_error_uses_closed_error_mapping(
    tmp_path: Path,
    provider_code: str,
    expected_kind: CloudAsrOutcomeKind,
    expected_safe_code: str,
) -> None:
    transport = StubTransport(
        [
            TencentHttpResponse(
                200,
                {
                    "Response": {
                        "Error": {
                            "Code": provider_code,
                            "Message": "credential and URL must not escape",
                        },
                        "RequestId": "request-error",
                    }
                },
            )
        ]
    )

    with pytest.raises(CloudAsrRequestError) as caught:
        TencentRecordingClient(transport).create(
            prepared_audio(tmp_path),
            context(),
            submission(),
        )

    assert caught.value.outcome.kind == expected_kind
    assert caught.value.outcome.safe_code == expected_safe_code
    assert "credential and URL" not in str(caught.value)


def test_query_maps_only_valid_result_detail_to_ordered_sentences() -> None:
    transport = StubTransport(
        [
            TencentHttpResponse(
                200,
                {
                    "Response": {
                        "Data": {
                            "Status": 2,
                            "ResultDetail": [
                                {
                                    "FinalSentence": "第一句",
                                    "StartMs": 0,
                                    "EndMs": 900,
                                },
                                {
                                    "FinalSentence": "Second",
                                    "StartMs": 900,
                                    "EndMs": 1800,
                                },
                            ],
                        },
                        "RequestId": "request-query",
                    }
                },
            )
        ]
    )

    outcome = TencentRecordingClient(transport).query(
        TencentTaskRef(TASK_ID, "request-create", NOW),
        context(),
    )

    assert outcome.kind == CloudAsrOutcomeKind.SUCCESS
    assert [(item.start_ms, item.end_ms, item.text) for item in outcome.sentences] == [
        (0, 900, "第一句"),
        (900, 1800, "Second"),
    ]
    assert outcome.request_id == "request-query"
    assert len(transport.calls) == 1
    assert json.loads(transport.calls[0].body) == {
        "TaskId": 18446744073709551615
    }
    assert transport.calls[0].headers["X-TC-Action"] == "DescribeTaskStatus"


def test_query_missing_timestamps_allows_local_fallback() -> None:
    transport = StubTransport(
        [
            TencentHttpResponse(
                200,
                {
                    "Response": {
                        "Data": {"Status": 2, "ResultDetail": []},
                        "RequestId": "request-query",
                    }
                },
            )
        ]
    )

    outcome = TencentRecordingClient(transport).query(
        TencentTaskRef("123", "request-create", NOW),
        context(),
    )

    assert outcome.kind == CloudAsrOutcomeKind.FALLBACK_ALLOWED
    assert outcome.safe_code == "provider_result_missing_timestamps"


def test_poll_delay_is_deterministic_bounded_and_increases() -> None:
    delays = [
        bounded_poll_delay("123", attempt).total_seconds()
        for attempt in range(6)
    ]

    assert delays == [
        bounded_poll_delay("123", attempt).total_seconds()
        for attempt in range(6)
    ]
    assert 4 <= delays[0] < 5
    assert 8 <= delays[1] < 9
    assert 16 <= delays[2] < 17
    assert all(30 <= delay < 31 for delay in delays[3:])


class QueryClient:
    def __init__(self, outcomes: list[CloudAsrOutcome]) -> None:
        self.outcomes = outcomes
        self.task_ids: list[str] = []

    def query(
        self,
        task: TencentTaskRef,
        request_context: TencentRequestContext,
    ) -> CloudAsrOutcome:
        assert request_context.timestamp == int(NOW.timestamp())
        self.task_ids.append(task.task_id)
        return self.outcomes.pop(0)


class DeleteStager:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.deleted: list[CosLocator] = []

    def delete(self, value: CosLocator) -> None:
        self.deleted.append(value)
        if self.fail:
            raise RuntimeError("signed-url=must-not-persist")


def seed_submissions(tmp_path: Path) -> tuple[Engine, list[str]]:
    engine = initialize_database(tmp_path / "reconcile.db")
    with Session(engine) as session:
        task = TaskRecord(
            id="11111111-1111-4111-8111-111111111111",
            status="running",
            options={},
            pipeline_snapshot_json={},
        )
        item = ItemRecord(
            id="22222222-2222-4222-8222-222222222222",
            task=task,
            position=0,
            source_kind="local_media",
            source_locator=r"D:\media\sample.mp4",
            status="running",
        )
        stages = [
            StageRunRecord(
                id="33333333-3333-4333-8333-333333333331",
                item=item,
                stage="transcribe",
                attempt=1,
                status="canceled",
            ),
            StageRunRecord(
                id="33333333-3333-4333-8333-333333333332",
                item=item,
                stage="transcribe",
                attempt=2,
                status="running",
            ),
        ]
        session.add(task)
        session.flush()
        store = CloudSubmissionStore(session)
        created: list[str] = []
        for index, stage in enumerate(stages):
            value = store.prepare(stage.id, hashlib.sha256(f"a{index}".encode()).hexdigest(), None)
            value = store.mark_sending(value.id)
            value = store.mark_submitted(
                value.id,
                task_id=str(100 + index),
                request_id=f"request-{index}",
                submitted_at=NOW,
            )
            store.schedule_query(
                value.id,
                NOW if index == 0 else NOW.replace(second=1),
            )
            created.append(value.id)
        return engine, created


def reconciler(
    engine: Engine,
    client: QueryClient,
    *,
    stager: DeleteStager | None = None,
) -> TencentSubmissionReconciler:
    return TencentSubmissionReconciler(
        engine=engine,
        client=client,
        request_context=lambda _: TencentRequestContext(
            credentials=TencentCredentialBundle(
                secret_id=SecretStr("AKID-example"),
                secret_key=SecretStr("secret-key"),
            ),
            timestamp=int(NOW.timestamp()),
        ),
        cos_stager=stager,
        worker_id="reconciler-a",
    )


def test_reconciler_queries_one_earliest_due_submission_and_ignores_stage_cancel(
    tmp_path: Path,
) -> None:
    engine, ids = seed_submissions(tmp_path)
    client = QueryClient(
        [
            CloudAsrOutcome(
                CloudAsrOutcomeKind.STOP,
                "provider_pending",
                retryable=True,
            )
        ]
    )
    try:
        result = reconciler(engine, client).reconcile_one_due(NOW)

        assert result == ReconcileResult(ids[0], "query_scheduled")
        assert client.task_ids == ["100"]
        with Session(engine) as session:
            first = session.get(CloudSubmissionRecord, ids[0])
            second = session.get(CloudSubmissionRecord, ids[1])
            assert first is not None and second is not None
            assert first.poll_attempt == 1
            assert first.last_query_at == NOW
            assert first.next_poll_at is not None
            assert first.next_poll_at > NOW
            assert second.poll_attempt == 0
            assert session.get(
                ResourceLeaseRecord,
                f"cloud-submission:{ids[0]}",
            ) is None
    finally:
        engine.dispose()


def test_reconciler_never_reclaims_an_unexpired_lease_even_for_same_worker_id(
    tmp_path: Path,
) -> None:
    engine, ids = seed_submissions(tmp_path)
    client = QueryClient([])
    with Session(engine) as session:
        session.add(
            ResourceLeaseRecord(
                resource_key=f"cloud-submission:{ids[0]}",
                lease_owner="reconciler-a",
                lease_expires_at=NOW.replace(minute=5),
                heartbeat_at=NOW,
            )
        )
        other = session.get(CloudSubmissionRecord, ids[1])
        assert other is not None
        other.state = "failed"
        other.next_poll_at = None
        session.commit()
    try:
        assert reconciler(engine, client).reconcile_one_due(NOW) is None
        assert client.task_ids == []
    finally:
        engine.dispose()


def test_reconciler_marks_success_then_deletes_cos_on_a_separate_claim(
    tmp_path: Path,
) -> None:
    engine, ids = seed_submissions(tmp_path)
    stager = DeleteStager()
    client = QueryClient(
        [
            CloudAsrOutcome(
                CloudAsrOutcomeKind.SUCCESS,
                "provider_success",
                request_id="query-success",
            )
        ]
    )
    with Session(engine) as session:
        row = session.get(CloudSubmissionRecord, ids[0])
        assert row is not None
        value = locator_for_hash(row.audio_sha256)
        row.cos_bucket = value.bucket
        row.cos_region = value.region
        row.cos_object_key = value.object_key
        session.commit()
    runner = reconciler(engine, client, stager=stager)
    try:
        first = runner.reconcile_one_due(NOW)
        second = runner.reconcile_one_due(NOW)

        assert first == ReconcileResult(ids[0], "provider_succeeded")
        assert second == ReconcileResult(ids[0], "cos_deleted")
        assert client.task_ids == ["100"]
        assert stager.deleted == [locator_for_hash(hashlib.sha256(b"a0").hexdigest())]
        with Session(engine) as session:
            row = session.get(CloudSubmissionRecord, ids[0])
            assert row is not None
            assert row.state == "succeeded"
            assert row.remote_terminal_at == NOW
            assert row.provider_request_id == "query-success"
            assert row.cos_object_key is None
            assert row.cleanup_due_at is None
    finally:
        engine.dispose()


def locator_for_hash(audio_hash: str) -> CosLocator:
    return CosLocator(
        bucket="private-audio-1250000000",
        region="ap-guangzhou",
        object_key=(
            "vtnote-runtime/11111111-1111-4111-8111-111111111111/"
            f"{audio_hash[:16]}.ogg"
        ),
    )


def test_reconciler_expires_result_without_a_provider_call(tmp_path: Path) -> None:
    engine, ids = seed_submissions(tmp_path)
    client = QueryClient([])
    with Session(engine) as session:
        row = session.get(CloudSubmissionRecord, ids[0])
        assert row is not None
        row.result_expires_at = NOW
        session.commit()
    try:
        result = reconciler(engine, client).reconcile_one_due(NOW)

        assert result == ReconcileResult(ids[0], "provider_result_expired")
        assert client.task_ids == []
        with Session(engine) as session:
            row = session.get(CloudSubmissionRecord, ids[0])
            assert row is not None
            assert row.state == "failed"
            assert row.safe_error_code == "provider_result_expired"
    finally:
        engine.dispose()


def test_unknown_submission_cos_delete_waits_until_url_expiry_plus_30_minutes(
    tmp_path: Path,
) -> None:
    engine, ids = seed_submissions(tmp_path)
    stager = DeleteStager()
    expiry = NOW.replace(hour=17)
    with Session(engine) as session:
        row = session.get(CloudSubmissionRecord, ids[0])
        assert row is not None
        value = locator_for_hash(row.audio_sha256)
        row.state = "submission_unknown"
        row.provider_task_id = None
        row.next_poll_at = None
        row.cos_bucket = value.bucket
        row.cos_region = value.region
        row.cos_object_key = value.object_key
        row.signed_url_expires_at = expiry
        row.cleanup_due_at = expiry.replace(minute=30)
        other = session.get(CloudSubmissionRecord, ids[1])
        assert other is not None
        other.state = "failed"
        other.next_poll_at = None
        session.commit()
    runner = reconciler(engine, QueryClient([]), stager=stager)
    try:
        assert runner.reconcile_one_due(expiry) is None
        assert stager.deleted == []
        result = runner.reconcile_one_due(expiry.replace(minute=30))
        assert result == ReconcileResult(ids[0], "cos_deleted")
        assert len(stager.deleted) == 1
    finally:
        engine.dispose()


def test_cleanup_failure_is_safe_and_durably_rescheduled(tmp_path: Path) -> None:
    engine, ids = seed_submissions(tmp_path)
    stager = DeleteStager(fail=True)
    with Session(engine) as session:
        row = session.get(CloudSubmissionRecord, ids[0])
        assert row is not None
        value = locator_for_hash(row.audio_sha256)
        row.state = "failed"
        row.next_poll_at = None
        row.cos_bucket = value.bucket
        row.cos_region = value.region
        row.cos_object_key = value.object_key
        row.cleanup_due_at = NOW
        session.commit()
    try:
        result = reconciler(engine, QueryClient([]), stager=stager).reconcile_one_due(NOW)

        assert result == ReconcileResult(ids[0], "cos_cleanup_retry")
        with Session(engine) as session:
            row = session.get(CloudSubmissionRecord, ids[0])
            assert row is not None
            assert row.cos_object_key is not None
            assert row.cleanup_due_at is not None
            assert row.cleanup_due_at > NOW
            assert "signed-url" not in (row.safe_error_code or "")
    finally:
        engine.dispose()


class ProfileClient:
    def __init__(self, outcomes: list[CloudAsrOutcome]) -> None:
        self.outcomes = outcomes
        self.created = 0
        self.queries = 0

    def create(
        self,
        audio: PreparedAudio,
        request_context: TencentRequestContext,
        cloud_submission: CloudSubmission,
    ) -> TencentTaskRef:
        self.created += 1
        assert audio.media_info.duration_ms == 3_000
        assert cloud_submission.state == "sending"
        assert cloud_submission.cos_locator is None
        assert request_context.signed_url is None
        return TencentTaskRef("123", "request-create", NOW)

    def query(
        self,
        task: TencentTaskRef,
        request_context: TencentRequestContext,
    ) -> CloudAsrOutcome:
        self.queries += 1
        assert task.task_id == "123"
        return self.outcomes.pop(0)


def test_connectivity_profile_test_submits_once_and_requires_timestamped_result(
    tmp_path: Path,
) -> None:
    client = ProfileClient(
        [
            CloudAsrOutcome(
                CloudAsrOutcomeKind.STOP,
                "provider_pending",
                retryable=True,
            ),
            CloudAsrOutcome(
                CloudAsrOutcomeKind.SUCCESS,
                "provider_success",
                sentences=(
                    TencentSentence(0, 900, "test"),
                ),
            ),
        ]
    )
    sleeps: list[float] = []
    tester = TencentConnectivityTester(
        client=client,
        sample_resolver=lambda sample_id: (
            prepared_audio(tmp_path)
            if sample_id == "sample-1"
            else pytest.fail("wrong sample")
        ),
        clock=lambda: NOW,
        sleeper=sleeps.append,
    )
    credentials = context().credentials
    profile = SimpleNamespace(
        protocol="tencent_recording_asr",
        base_url=TENCENT_ASR_ENDPOINT,
    )
    request = SimpleNamespace(
        test_kind="provider_profile",
        acknowledge_billable_request=True,
        speech_sample_upload_id="sample-1",
    )

    result = tester.test_profile(
        profile,
        credentials,
        request,
        follow_redirects=False,
    )

    assert result.ok is True
    assert "billable" in (result.message or "")
    assert client.created == 1
    assert client.queries == 2
    assert len(sleeps) == 1


def test_connectivity_profile_test_rejects_sample_outside_two_to_ten_seconds(
    tmp_path: Path,
) -> None:
    selected = prepared_audio(tmp_path)
    too_short = PreparedAudio(
        path=selected.path,
        asset_id=selected.asset_id,
        converted=selected.converted,
        media_info=MediaInfo(
            duration_ms=1_999,
            size_bytes=selected.media_info.size_bytes,
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )
    client = ProfileClient([])
    tester = TencentConnectivityTester(
        client=client,
        sample_resolver=lambda _: too_short,
        clock=lambda: NOW,
        sleeper=lambda _: None,
    )

    result = tester.test_profile(
        SimpleNamespace(protocol="tencent_recording_asr"),
        context().credentials,
        SimpleNamespace(
            test_kind="provider_profile",
            acknowledge_billable_request=True,
            speech_sample_upload_id="sample-1",
        ),
        follow_redirects=False,
    )

    assert result.ok is False
    assert client.created == 0


def test_connectivity_profile_test_rejects_oversize_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = prepared_audio(tmp_path)
    selected.path.write_bytes(b"x" * 4_500_001)
    oversized = PreparedAudio(
        path=selected.path,
        asset_id=selected.asset_id,
        converted=True,
        media_info=MediaInfo(
            duration_ms=3_000,
            size_bytes=4_500_001,
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _: pytest.fail("oversize sample must not be loaded"),
    )
    client = ProfileClient([])
    tester = TencentConnectivityTester(
        client=client,
        sample_resolver=lambda _: oversized,
        clock=lambda: NOW,
    )

    result = tester.test_profile(
        SimpleNamespace(protocol="tencent_recording_asr"),
        context().credentials,
        SimpleNamespace(
            test_kind="provider_profile",
            acknowledge_billable_request=True,
            speech_sample_upload_id="sample-1",
        ),
        follow_redirects=False,
    )

    assert result.ok is False
    assert client.created == 0


def test_connectivity_connection_test_is_static_and_requires_fixed_policy() -> None:
    tester = TencentConnectivityTester(
        client=ProfileClient([]),
        sample_resolver=lambda _: pytest.fail("must not resolve media"),
    )

    valid = tester.test_connection(
        SimpleNamespace(
            protocol="tencent_recording_asr",
            base_url=TENCENT_ASR_ENDPOINT,
        ),
        context().credentials,
        follow_redirects=False,
    )
    invalid = tester.test_connection(
        SimpleNamespace(
            protocol="tencent_recording_asr",
            base_url="https://proxy.example",
        ),
        context().credentials,
        follow_redirects=False,
    )

    assert valid.ok is True
    assert invalid.ok is False


def test_diagnostics_remove_tencent_credentials_base64_and_presigned_url() -> None:
    message = (
        '{"secret_id":"AKID-LEAK","secret_key":"KEY-LEAK",'
        '"Data":"YXVkaW8tTEVBSw==",'
        '"Url":"https://private-audio-1250000000.cos.ap-guangzhou.'
        'myqcloud.com/object.ogg?q-ak=AKID-LEAK&q-signature=SIG-LEAK"}'
    )

    cleaned = sanitize_diagnostic(message)

    assert cleaned is not None
    for marker in (
        "AKID-LEAK",
        "KEY-LEAK",
        "YXVkaW8tTEVBSw==",
        "SIG-LEAK",
        "q-signature",
        "myqcloud.com",
    ):
        assert marker not in cleaned
    assert cleaned.count("[redacted]") >= 4


def test_uploaded_speech_sample_resolver_uses_only_managed_uploaded_item(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_settings(
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
        )
    )
    engine = initialize_database(paths.database)
    item_id = "22222222-2222-4222-8222-222222222222"
    with Session(engine) as session:
        task = TaskRecord(
            id="11111111-1111-4111-8111-111111111111",
            status="completed",
            options={},
            pipeline_snapshot_json={},
        )
        session.add(
            ItemRecord(
                id=item_id,
                task=task,
                position=0,
                source_kind="upload_media",
                source_locator="managed-upload",
                status="completed",
            )
        )
        session.commit()
        source = paths.uploaded_source(item_id, "wav")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"managed sample")
        RuntimeAssetService(session, paths).register_staged(
            item_id=item_id,
            role="uploaded_source",
            relative_path=paths.runtime_relative(source),
        )
    expected = PreparedAudio(
        path=paths.cloud_ogg(item_id),
        asset_id="cloud-asset",
        converted=True,
        media_info=MediaInfo(
            duration_ms=3_000,
            size_bytes=10,
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )
    calls: list[tuple[str, Path]] = []

    def convert(
        selected_item_id: str,
        selected_source: Path,
        _: RuntimeAssetService,
    ) -> PreparedAudio:
        calls.append((selected_item_id, selected_source))
        return expected

    try:
        resolved = UploadedSpeechSampleResolver(
            engine=engine,
            paths=paths,
            converter=convert,
        )(item_id)

        assert resolved == expected
        assert calls == [(item_id, source)]
        with pytest.raises(ValueError, match="speech sample"):
            UploadedSpeechSampleResolver(
                engine=engine,
                paths=paths,
                converter=convert,
            )("33333333-3333-4333-8333-333333333333")
    finally:
        engine.dispose()
