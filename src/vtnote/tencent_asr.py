"""Tencent Recording ASR adapter with an explicit paid-request boundary."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, Protocol

import httpx
from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session

from vtnote.cloud_submissions import CloudSubmission, CloudSubmissionStore, CosLocator
from vtnote.media import (
    CommandRunner,
    FfmpegBinaries,
    FfmpegMediaProcessor,
    PreparedAudio,
)
from vtnote.models import CloudSubmissionRecord, ResourceLeaseRecord
from vtnote.paths import StoragePaths
from vtnote.provider_credentials import TencentCredentialBundle
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService
from vtnote.tencent_contract import (
    TENCENT_ASR_ENDPOINT,
    TencentResponseError,
    TencentSentence,
    build_create_payload_inline,
    build_create_payload_url,
    build_describe_payload,
    build_tc3_headers,
    classify_tencent_error,
    parse_create_response,
    parse_query_response,
)


class CloudAsrOutcomeKind(str, Enum):
    SUCCESS = "success"
    STOP = "stop"
    FALLBACK_ALLOWED = "fallback_allowed"
    UNKNOWN = "submission_unknown"


@dataclass(frozen=True, slots=True)
class CloudAsrOutcome:
    kind: CloudAsrOutcomeKind
    safe_code: str
    sentences: tuple[TencentSentence, ...] = ()
    provider_status: str | None = None
    request_id: str | None = None
    retryable: bool = False


class CloudAsrRequestError(RuntimeError):
    def __init__(self, outcome: CloudAsrOutcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.safe_code)


@dataclass(frozen=True, slots=True)
class TencentTaskRef:
    task_id: str
    request_id: str
    submitted_at: datetime


class SensitiveUrlLike(Protocol):
    def reveal(self) -> str: ...


@dataclass(frozen=True, slots=True)
class TencentRequestContext:
    credentials: TencentCredentialBundle
    timestamp: int
    signed_url: SensitiveUrlLike | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.credentials, TencentCredentialBundle):
            raise ValueError("Tencent credential bundle is required")
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ValueError("invalid Tencent request timestamp")


@dataclass(frozen=True, slots=True)
class TencentHttpResponse:
    status_code: int
    payload: object


class TencentTransportFailure(RuntimeError):
    def __init__(self, safe_code: str, *, sent: bool) -> None:
        self.safe_code = safe_code
        self.sent = sent
        super().__init__(safe_code)


class TencentTransport(Protocol):
    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> TencentHttpResponse: ...


class HttpxTencentTransport:
    """Perform one fixed-host request without redirects or proxy inheritance."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("invalid Tencent response limit")
        self.client = client or httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
        self.max_response_bytes = max_response_bytes

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> TencentHttpResponse:
        if url != TENCENT_ASR_ENDPOINT:
            raise TencentTransportFailure("provider_endpoint_invalid", sent=False)
        try:
            response = self.client.post(url, headers=headers, content=body)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise TencentTransportFailure(
                "provider_connect_failed",
                sent=False,
            ) from None
        except httpx.RequestError:
            raise TencentTransportFailure(
                "provider_response_lost",
                sent=True,
            ) from None
        if len(response.content) > self.max_response_bytes:
            raise TencentTransportFailure(
                "provider_response_oversize",
                sent=True,
            )
        try:
            payload = response.json()
        except (UnicodeError, json.JSONDecodeError):
            raise TencentTransportFailure(
                "provider_response_invalid",
                sent=True,
            ) from None
        return TencentHttpResponse(response.status_code, payload)


def _canonical_body(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _provider_error(
    code: str,
    *,
    phase: Literal["create", "query"],
) -> CloudAsrOutcome:
    action = classify_tencent_error(code, phase=phase)
    if action == "fallback_allowed":
        return CloudAsrOutcome(
            CloudAsrOutcomeKind.FALLBACK_ALLOWED,
            "provider_fallback_allowed",
        )
    if action == "query_retry":
        return CloudAsrOutcome(
            CloudAsrOutcomeKind.STOP,
            "provider_query_retry",
            retryable=True,
        )
    return CloudAsrOutcome(CloudAsrOutcomeKind.STOP, action)


class TencentRecordingClient:
    def __init__(self, transport: TencentTransport | None = None) -> None:
        self.transport = transport

    @staticmethod
    def _headers(
        context: TencentRequestContext,
        action: Literal["CreateRecTask", "DescribeTaskStatus"],
        payload: dict[str, object],
    ) -> dict[str, str]:
        return build_tc3_headers(
            secret_id=context.credentials.secret_id.get_secret_value(),
            secret_key=context.credentials.secret_key.get_secret_value(),
            action=action,
            timestamp=context.timestamp,
            payload=payload,
        )

    @staticmethod
    def _inline_bytes(
        audio: PreparedAudio,
        submission: CloudSubmission,
    ) -> bytes:
        path = Path(audio.path)
        if submission.state != "sending" or not path.is_absolute() or not path.is_file():
            raise ValueError("prepared audio is unavailable")
        if path.stat().st_size > 4_500_000:
            raise ValueError("prepared inline audio does not match submission")
        data = path.read_bytes()
        if (
            not data
            or len(data) > 4_500_000
            or hashlib.sha256(data).hexdigest() != submission.audio_sha256
        ):
            raise ValueError("prepared inline audio does not match submission")
        return data

    def _post(
        self,
        *,
        action: Literal["CreateRecTask", "DescribeTaskStatus"],
        context: TencentRequestContext,
        payload: dict[str, object],
    ) -> TencentHttpResponse:
        if self.transport is None:
            self.transport = HttpxTencentTransport()
        return self.transport.post(
            url=TENCENT_ASR_ENDPOINT,
            headers=self._headers(context, action, payload),
            body=_canonical_body(payload),
        )

    def create(
        self,
        audio: PreparedAudio,
        context: TencentRequestContext,
        submission: CloudSubmission,
    ) -> TencentTaskRef:
        if submission.state != "sending":
            raise ValueError("cloud submission is not sending")
        if submission.cos_locator is None:
            payload = build_create_payload_inline(
                self._inline_bytes(audio, submission)
            )
        else:
            if context.signed_url is None:
                raise ValueError("signed COS URL is required")
            payload = build_create_payload_url(context.signed_url.reveal())
        try:
            response = self._post(
                action="CreateRecTask",
                context=context,
                payload=payload,
            )
        except TencentTransportFailure as error:
            kind = (
                CloudAsrOutcomeKind.UNKNOWN
                if error.sent
                else CloudAsrOutcomeKind.FALLBACK_ALLOWED
            )
            raise CloudAsrRequestError(
                CloudAsrOutcome(kind, error.safe_code)
            ) from None
        if response.status_code in {400, 401, 403, 404}:
            raise CloudAsrRequestError(
                CloudAsrOutcome(
                    CloudAsrOutcomeKind.STOP,
                    "provider_http_configuration",
                )
            )
        if response.status_code == 429:
            raise CloudAsrRequestError(
                CloudAsrOutcome(
                    CloudAsrOutcomeKind.FALLBACK_ALLOWED,
                    "provider_rate_limited",
                )
            )
        if response.status_code != 200:
            raise CloudAsrRequestError(
                CloudAsrOutcome(
                    CloudAsrOutcomeKind.UNKNOWN,
                    "provider_create_response_unknown",
                )
            )
        try:
            result = parse_create_response(response.payload)
        except TencentResponseError as error:
            raise CloudAsrRequestError(
                _provider_error(error.provider_code, phase="create")
            ) from None
        except ValueError:
            raise CloudAsrRequestError(
                CloudAsrOutcome(
                    CloudAsrOutcomeKind.UNKNOWN,
                    "provider_create_response_invalid",
                )
            ) from None
        return TencentTaskRef(
            task_id=result.task_id,
            request_id=result.request_id,
            submitted_at=datetime.fromtimestamp(
                context.timestamp,
                tz=timezone.utc,
            ),
        )

    def query(
        self,
        task: TencentTaskRef,
        context: TencentRequestContext,
    ) -> CloudAsrOutcome:
        payload = build_describe_payload(task.task_id)
        try:
            response = self._post(
                action="DescribeTaskStatus",
                context=context,
                payload=payload,
            )
        except TencentTransportFailure as error:
            return CloudAsrOutcome(
                CloudAsrOutcomeKind.STOP,
                error.safe_code,
                retryable=True,
            )
        if response.status_code in {429, 500, 502, 503, 504}:
            return CloudAsrOutcome(
                CloudAsrOutcomeKind.STOP,
                "provider_query_retry",
                retryable=True,
            )
        if response.status_code != 200:
            return CloudAsrOutcome(
                CloudAsrOutcomeKind.STOP,
                "provider_http_configuration",
            )
        try:
            result = parse_query_response(response.payload)
        except TencentResponseError as error:
            return _provider_error(error.provider_code, phase="query")
        except ValueError as error:
            if str(error) == "provider_result_missing_timestamps":
                return CloudAsrOutcome(
                    CloudAsrOutcomeKind.FALLBACK_ALLOWED,
                    "provider_result_missing_timestamps",
                )
            return CloudAsrOutcome(
                CloudAsrOutcomeKind.STOP,
                "provider_query_response_invalid",
            )
        if result.state in {"waiting", "running"}:
            return CloudAsrOutcome(
                CloudAsrOutcomeKind.STOP,
                "provider_pending",
                provider_status=result.provider_status,
                request_id=result.request_id,
                retryable=True,
            )
        if result.state == "failed":
            return CloudAsrOutcome(
                CloudAsrOutcomeKind.FALLBACK_ALLOWED,
                "provider_transcription_failed",
                provider_status=result.provider_status,
                request_id=result.request_id,
            )
        return CloudAsrOutcome(
            CloudAsrOutcomeKind.SUCCESS,
            "provider_success",
            sentences=result.sentences,
            provider_status=result.provider_status,
            request_id=result.request_id,
        )


def bounded_poll_delay(task_id: str, poll_attempt: int) -> timedelta:
    if type(poll_attempt) is not int or poll_attempt < 0:
        raise ValueError("invalid poll attempt")
    base = min(30, 4 * (2 ** min(poll_attempt, 3)))
    digest = hashlib.sha256(f"{task_id}:{poll_attempt}".encode("ascii")).digest()
    jitter_milliseconds = int.from_bytes(digest[:2], "big") % 1000
    return timedelta(seconds=base, milliseconds=jitter_milliseconds)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    submission_id: str
    action: str
    sentences: tuple[TencentSentence, ...] = ()


class RecordingQueryClient(Protocol):
    def query(
        self,
        task: TencentTaskRef,
        context: TencentRequestContext,
    ) -> CloudAsrOutcome: ...


class CosDeleteClient(Protocol):
    def delete(self, locator: CosLocator) -> None: ...


@dataclass(frozen=True, slots=True)
class _ReconcileClaim:
    submission_id: str
    action: Literal["query", "delete", "expire"]


class TencentSubmissionReconciler:
    """Claim and perform at most one remote query or delete per invocation."""

    def __init__(
        self,
        *,
        engine: Engine,
        client: RecordingQueryClient,
        request_context: Callable[[CloudSubmission], TencentRequestContext],
        cos_stager: CosDeleteClient | None,
        worker_id: str,
        lease_duration: timedelta = timedelta(minutes=2),
    ) -> None:
        if not worker_id or lease_duration <= timedelta(0):
            raise ValueError("invalid reconciler lease")
        self.engine = engine
        self.client = client
        self.request_context = request_context
        self.cos_stager = cos_stager
        self.worker_id = worker_id
        self.lease_duration = lease_duration

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise ValueError("reconcile time is required")
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _begin(session: Session) -> None:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    @staticmethod
    def _due_at(row: CloudSubmissionRecord) -> datetime:
        values = [
            value
            for value in (
                row.cleanup_due_at if row.cos_object_key is not None else None,
                row.result_expires_at if row.state == "submitted" else None,
                row.next_poll_at if row.state == "submitted" else None,
            )
            if value is not None
        ]
        return min(values)

    def _claim_one(self, now: datetime) -> _ReconcileClaim | None:
        with Session(self.engine) as session:
            self._begin(session)
            rows = session.scalars(
                select(CloudSubmissionRecord).where(
                    or_(
                        (
                            (CloudSubmissionRecord.cos_object_key.is_not(None))
                            & (CloudSubmissionRecord.cleanup_due_at.is_not(None))
                            & (CloudSubmissionRecord.cleanup_due_at <= now)
                        ),
                        (
                            (CloudSubmissionRecord.state == "submitted")
                            & (
                                (
                                    CloudSubmissionRecord.next_poll_at.is_not(None)
                                    & (CloudSubmissionRecord.next_poll_at <= now)
                                )
                                | (
                                    CloudSubmissionRecord.result_expires_at.is_not(None)
                                    & (CloudSubmissionRecord.result_expires_at <= now)
                                )
                            )
                        ),
                    )
                )
            ).all()
            rows.sort(key=lambda row: (self._due_at(row), row.created_at, row.id))
            for row in rows:
                key = f"cloud-submission:{row.id}"
                lease = session.get(ResourceLeaseRecord, key)
                if (
                    lease is not None
                    and lease.lease_expires_at > now
                ):
                    continue
                if lease is None:
                    lease = ResourceLeaseRecord(
                        resource_key=key,
                        lease_owner=self.worker_id,
                        lease_expires_at=now + self.lease_duration,
                        heartbeat_at=now,
                    )
                    session.add(lease)
                else:
                    lease.lease_owner = self.worker_id
                    lease.lease_expires_at = now + self.lease_duration
                    lease.heartbeat_at = now
                if (
                    row.cos_object_key is not None
                    and row.cleanup_due_at is not None
                    and row.cleanup_due_at <= now
                ):
                    action: Literal["query", "delete", "expire"] = "delete"
                elif (
                    row.result_expires_at is not None
                    and row.result_expires_at <= now
                ):
                    action = "expire"
                else:
                    action = "query"
                session.commit()
                return _ReconcileClaim(row.id, action)
            session.rollback()
            return None

    def _load_claimed(
        self,
        session: Session,
        submission_id: str,
    ) -> tuple[CloudSubmissionRecord, ResourceLeaseRecord] | None:
        row = session.get(CloudSubmissionRecord, submission_id)
        lease = session.get(
            ResourceLeaseRecord,
            f"cloud-submission:{submission_id}",
        )
        if row is None or lease is None or lease.lease_owner != self.worker_id:
            return None
        return row, lease

    def _release_only(self, submission_id: str) -> None:
        with Session(self.engine) as session:
            self._begin(session)
            loaded = self._load_claimed(session, submission_id)
            if loaded is not None:
                session.delete(loaded[1])
                session.commit()
            else:
                session.rollback()

    def _expire(self, claim: _ReconcileClaim, now: datetime) -> ReconcileResult:
        with Session(self.engine) as session:
            self._begin(session)
            loaded = self._load_claimed(session, claim.submission_id)
            if loaded is None:
                session.rollback()
                return ReconcileResult(claim.submission_id, "claim_lost")
            row, lease = loaded
            row.state = "failed"
            row.safe_error_code = "provider_result_expired"
            row.next_poll_at = None
            row.remote_terminal_at = now
            if row.cos_object_key is not None:
                row.cleanup_due_at = now
            session.delete(lease)
            session.commit()
        return ReconcileResult(claim.submission_id, "provider_result_expired")

    def _delete(self, claim: _ReconcileClaim, now: datetime) -> ReconcileResult:
        with Session(self.engine) as session:
            loaded = self._load_claimed(session, claim.submission_id)
            if loaded is None:
                return ReconcileResult(claim.submission_id, "claim_lost")
            row, _ = loaded
            submission = CloudSubmissionStore._view(row)
        if submission.cos_locator is None:
            self._release_only(claim.submission_id)
            return ReconcileResult(claim.submission_id, "cos_already_deleted")
        if self.cos_stager is None:
            failed = True
        else:
            try:
                self.cos_stager.delete(submission.cos_locator)
            except Exception:
                failed = True
            else:
                failed = False
        with Session(self.engine) as session:
            self._begin(session)
            loaded = self._load_claimed(session, claim.submission_id)
            if loaded is None:
                session.rollback()
                return ReconcileResult(claim.submission_id, "claim_lost")
            row, lease = loaded
            if failed:
                row.cleanup_due_at = now + timedelta(minutes=5)
                action = "cos_cleanup_retry"
            else:
                row.cos_bucket = None
                row.cos_region = None
                row.cos_object_key = None
                row.signed_url_expires_at = None
                row.cleanup_due_at = None
                action = "cos_deleted"
            session.delete(lease)
            session.commit()
        return ReconcileResult(claim.submission_id, action)

    def _query(self, claim: _ReconcileClaim, now: datetime) -> ReconcileResult:
        with Session(self.engine) as session:
            loaded = self._load_claimed(session, claim.submission_id)
            if loaded is None:
                return ReconcileResult(claim.submission_id, "claim_lost")
            row, _ = loaded
            if row.provider_task_id is None or row.submitted_at is None:
                self._release_only(claim.submission_id)
                return ReconcileResult(
                    claim.submission_id,
                    "provider_submission_invalid",
                )
            submission = CloudSubmissionStore._view(row)
            task = TencentTaskRef(
                row.provider_task_id,
                row.provider_request_id or "unknown",
                row.submitted_at,
            )
        try:
            outcome = self.client.query(
                task,
                self.request_context(submission),
            )
        except Exception:
            outcome = CloudAsrOutcome(
                CloudAsrOutcomeKind.STOP,
                "provider_query_failed",
                retryable=True,
            )
        with Session(self.engine) as session:
            self._begin(session)
            loaded = self._load_claimed(session, claim.submission_id)
            if loaded is None:
                session.rollback()
                return ReconcileResult(claim.submission_id, "claim_lost")
            row, lease = loaded
            row.last_query_at = now
            row.poll_attempt += 1
            if outcome.retryable:
                row.next_poll_at = now + bounded_poll_delay(
                    row.provider_task_id or "0",
                    row.poll_attempt - 1,
                )
                action = "query_scheduled"
            else:
                row.next_poll_at = None
                row.remote_terminal_at = now
                row.safe_error_code = (
                    None
                    if outcome.kind == CloudAsrOutcomeKind.SUCCESS
                    else outcome.safe_code
                )
                row.state = (
                    "succeeded"
                    if outcome.kind == CloudAsrOutcomeKind.SUCCESS
                    else "failed"
                )
                if outcome.request_id is not None:
                    row.provider_request_id = outcome.request_id
                if row.cos_object_key is not None:
                    row.cleanup_due_at = now
                action = (
                    "provider_succeeded"
                    if outcome.kind == CloudAsrOutcomeKind.SUCCESS
                    else "provider_failed"
                )
            session.delete(lease)
            session.commit()
        return ReconcileResult(claim.submission_id, action, outcome.sentences)

    def reconcile_one_due(self, now: datetime) -> ReconcileResult | None:
        selected = self._claim_one(self._utc(now))
        if selected is None:
            return None
        if selected.action == "expire":
            return self._expire(selected, self._utc(now))
        if selected.action == "delete":
            return self._delete(selected, self._utc(now))
        return self._query(selected, self._utc(now))


@dataclass(frozen=True, slots=True)
class TencentConnectivityResult:
    ok: bool
    message: str | None = None


class RecordingCreateQueryClient(RecordingQueryClient, Protocol):
    def create(
        self,
        audio: PreparedAudio,
        context: TencentRequestContext,
        submission: CloudSubmission,
    ) -> TencentTaskRef: ...


class CosReadinessTester(Protocol):
    def readiness_test(
        self,
        profile: object,
        credentials: TencentCredentialBundle,
    ) -> None: ...


class TencentConnectivityTester:
    """User-triggered tests; static connection validation never incurs cost."""

    def __init__(
        self,
        *,
        client: RecordingCreateQueryClient,
        sample_resolver: Callable[[str], PreparedAudio],
        cos_tester: CosReadinessTester | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        maximum_queries: int = 20,
    ) -> None:
        if type(maximum_queries) is not int or maximum_queries <= 0:
            raise ValueError("invalid profile test query limit")
        self.client = client
        self.sample_resolver = sample_resolver
        self.cos_tester = cos_tester
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleeper = sleeper
        self.maximum_queries = maximum_queries

    @staticmethod
    def _credentials(value: object) -> TencentCredentialBundle:
        if not isinstance(value, TencentCredentialBundle):
            raise ValueError("complete Tencent credentials are required")
        return value

    def test_connection(
        self,
        connection: object,
        credentials: object,
        *,
        follow_redirects: Literal[False],
    ) -> TencentConnectivityResult:
        if follow_redirects is not False:
            raise ValueError("redirects are forbidden")
        self._credentials(credentials)
        valid = (
            getattr(connection, "protocol", None)
            == "tencent_recording_asr"
            and getattr(connection, "base_url", None)
            == TENCENT_ASR_ENDPOINT
        )
        return TencentConnectivityResult(
            valid,
            "Connection policy validated"
            if valid
            else "Connection policy is invalid",
        )

    def test_profile(
        self,
        profile: object,
        credentials: object,
        test_input: object,
        *,
        follow_redirects: Literal[False],
    ) -> TencentConnectivityResult:
        if follow_redirects is not False:
            raise ValueError("redirects are forbidden")
        bundle = self._credentials(credentials)
        if not getattr(test_input, "acknowledge_billable_request", False):
            raise ValueError("billable test acknowledgement is required")
        if getattr(test_input, "test_kind", None) == "cos_sentinel":
            if self.cos_tester is None:
                return TencentConnectivityResult(
                    False,
                    "COS readiness tester is unavailable",
                )
            self.cos_tester.readiness_test(profile, bundle)
            return TencentConnectivityResult(
                True,
                "COS readiness test succeeded",
            )
        sample_id = getattr(test_input, "speech_sample_upload_id", None)
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError("speech sample is required")
        audio = self.sample_resolver(sample_id)
        if not 2_000 <= audio.media_info.duration_ms <= 10_000:
            return TencentConnectivityResult(
                False,
                "Speech sample must be 2-10 seconds",
            )
        path = Path(audio.path)
        if not path.is_file():
            return TencentConnectivityResult(False, "Speech sample is unavailable")
        if path.stat().st_size > 4_500_000:
            return TencentConnectivityResult(
                False,
                "Speech sample is not inline eligible",
            )
        data = path.read_bytes()
        if not data or len(data) > 4_500_000:
            return TencentConnectivityResult(
                False,
                "Speech sample is not inline eligible",
            )
        now = self.clock()
        submission = CloudSubmission(
            id="00000000-0000-4000-8000-000000000001",
            stage_run_id="00000000-0000-4000-8000-000000000002",
            provider="tencent_recording_asr",
            provider_task_id=None,
            provider_request_id=None,
            audio_sha256=hashlib.sha256(data).hexdigest(),
            cos_locator=None,
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
        request_context = TencentRequestContext(
            credentials=bundle,
            timestamp=int(now.timestamp()),
        )
        try:
            task = self.client.create(audio, request_context, submission)
        except CloudAsrRequestError:
            return TencentConnectivityResult(False, "Profile test failed")
        for attempt in range(self.maximum_queries):
            outcome = self.client.query(
                task,
                TencentRequestContext(
                    credentials=bundle,
                    timestamp=int(self.clock().timestamp()),
                ),
            )
            if (
                outcome.kind == CloudAsrOutcomeKind.SUCCESS
                and outcome.sentences
            ):
                return TencentConnectivityResult(
                    True,
                    "Profile test succeeded; sample upload may be billable",
                )
            if not outcome.retryable:
                return TencentConnectivityResult(False, "Profile test failed")
            if attempt + 1 < self.maximum_queries:
                self.sleeper(
                    bounded_poll_delay(
                        task.task_id,
                        attempt,
                    ).total_seconds()
                )
        return TencentConnectivityResult(False, "Profile test timed out")


class UploadedSpeechSampleResolver:
    """Resolve an uploaded media item and convert it through the normal boundary."""

    def __init__(
        self,
        *,
        engine: Engine,
        paths: StoragePaths,
        converter: Callable[
            [str, Path, RuntimeAssetService],
            PreparedAudio,
        ]
        | None = None,
    ) -> None:
        self.engine = engine
        self.paths = paths
        self.converter = converter or self._convert

    def _convert(
        self,
        item_id: str,
        source: Path,
        assets: RuntimeAssetService,
    ) -> PreparedAudio:
        return FfmpegMediaProcessor(
            runner=CommandRunner(),
            binaries=FfmpegBinaries.discover(),
            paths=self.paths,
            assets=assets,
        ).convert_for_cloud(item_id, source)

    def __call__(self, item_id: str) -> PreparedAudio:
        try:
            with Session(self.engine) as session:
                assets = RuntimeAssetService(session, self.paths)
                uploaded = assets.active_for_role(
                    item_id=item_id,
                    role="uploaded_source",
                )
                if uploaded is None:
                    raise ValueError("speech sample is unavailable")
                source = assets.resolve(uploaded.id)
                return self.converter(item_id, source, assets)
        except RuntimeAssetError:
            raise ValueError("speech sample is unavailable") from None
