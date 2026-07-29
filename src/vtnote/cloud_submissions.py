"""Durable, transition-checked Tencent cloud submission state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vtnote.models import CloudSubmissionRecord, StageRunRecord


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{1,57}[a-z0-9]-[1-9][0-9]{4,11}$")
_OBJECT = re.compile(
    r"^vtnote-runtime/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"[0-9a-f]{16}\.ogg$"
)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_UINT64_MAX = (1 << 64) - 1


class CloudSubmissionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise CloudSubmissionError("invalid_submission_timestamp")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class CosLocator:
    bucket: str
    region: str
    object_key: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.bucket, str)
            or _BUCKET.fullmatch(self.bucket) is None
            or self.region != "ap-guangzhou"
            or not isinstance(self.object_key, str)
            or (match := _OBJECT.fullmatch(self.object_key)) is None
        ):
            raise ValueError("invalid private COS locator")
        UUID(match.group(1))


@dataclass(frozen=True, slots=True)
class CloudSubmission:
    id: str
    stage_run_id: str
    provider: str
    provider_task_id: str | None
    provider_request_id: str | None
    audio_sha256: str
    cos_locator: CosLocator | None
    state: str
    safe_error_code: str | None
    next_poll_at: datetime | None
    poll_attempt: int
    last_query_at: datetime | None
    signed_url_expires_at: datetime | None
    cleanup_due_at: datetime | None
    remote_terminal_at: datetime | None
    submitted_at: datetime | None
    result_expires_at: datetime | None


class CloudSubmissionStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _view(row: CloudSubmissionRecord) -> CloudSubmission:
        locator = None
        if (
            row.cos_bucket is not None
            and row.cos_region is not None
            and row.cos_object_key is not None
        ):
            locator = CosLocator(
                bucket=row.cos_bucket,
                region=row.cos_region,
                object_key=row.cos_object_key,
            )
        return CloudSubmission(
            id=row.id,
            stage_run_id=row.stage_run_id,
            provider=row.provider,
            provider_task_id=row.provider_task_id,
            provider_request_id=row.provider_request_id,
            audio_sha256=row.audio_sha256,
            cos_locator=locator,
            state=row.state,
            safe_error_code=row.safe_error_code,
            next_poll_at=row.next_poll_at,
            poll_attempt=row.poll_attempt,
            last_query_at=row.last_query_at,
            signed_url_expires_at=row.signed_url_expires_at,
            cleanup_due_at=row.cleanup_due_at,
            remote_terminal_at=row.remote_terminal_at,
            submitted_at=row.submitted_at,
            result_expires_at=row.result_expires_at,
        )

    def _load(self, submission_id: str) -> CloudSubmissionRecord:
        try:
            canonical = str(UUID(str(submission_id)))
        except (ValueError, AttributeError):
            raise CloudSubmissionError("invalid_submission_id") from None
        row = self.session.get(CloudSubmissionRecord, canonical)
        if row is None:
            raise CloudSubmissionError("submission_not_found")
        return row

    def get(self, submission_id: str) -> CloudSubmission:
        return self._view(self._load(submission_id))

    def prepare(
        self,
        stage_run_id: str,
        audio_sha256: str,
        cos_locator: CosLocator | None,
    ) -> CloudSubmission:
        try:
            canonical_stage = str(UUID(str(stage_run_id)))
        except (ValueError, AttributeError):
            raise CloudSubmissionError("invalid_stage_run_id") from None
        stage = self.session.get(StageRunRecord, canonical_stage)
        if stage is None or stage.stage != "transcribe":
            raise CloudSubmissionError("invalid_stage_run_id")
        if not isinstance(audio_sha256, str) or _SHA256.fullmatch(audio_sha256) is None:
            raise CloudSubmissionError("invalid_audio_hash")
        if cos_locator is not None and not isinstance(cos_locator, CosLocator):
            raise CloudSubmissionError("invalid_cos_locator")
        existing = self.session.scalar(
            select(CloudSubmissionRecord).where(
                CloudSubmissionRecord.stage_run_id == canonical_stage
            )
        )
        selected = (
            None
            if cos_locator is None
            else (
                cos_locator.bucket,
                cos_locator.region,
                cos_locator.object_key,
            )
        )
        if existing is not None:
            current = (
                None
                if existing.cos_bucket is None
                else (
                    existing.cos_bucket,
                    existing.cos_region,
                    existing.cos_object_key,
                )
            )
            if existing.audio_sha256 != audio_sha256 or current != selected:
                raise CloudSubmissionError("submission_conflict")
            return self._view(existing)
        row = CloudSubmissionRecord(
            stage_run_id=canonical_stage,
            provider="tencent_recording_asr",
            audio_sha256=audio_sha256,
            cos_bucket=cos_locator.bucket if cos_locator else None,
            cos_region=cos_locator.region if cos_locator else None,
            cos_object_key=cos_locator.object_key if cos_locator else None,
            state="prepared",
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise CloudSubmissionError("submission_conflict") from None
        return self._view(row)

    def mark_sending(
        self,
        submission_id: str,
        *,
        signed_url_expires_at: datetime | None = None,
    ) -> CloudSubmission:
        row = self._load(submission_id)
        if row.state != "prepared":
            raise CloudSubmissionError("invalid_submission_transition")
        if signed_url_expires_at is not None:
            if row.cos_object_key is None:
                raise CloudSubmissionError("invalid_submission_timestamp")
            row.signed_url_expires_at = _utc(signed_url_expires_at)
        row.state = "sending"
        row.safe_error_code = None
        self.session.commit()
        return self._view(row)

    def mark_submitted(
        self,
        submission_id: str,
        task_id: str,
        request_id: str,
        submitted_at: datetime,
    ) -> CloudSubmission:
        row = self._load(submission_id)
        if row.state != "sending":
            raise CloudSubmissionError("invalid_submission_transition")
        if (
            not isinstance(task_id, str)
            or not task_id.isdigit()
            or int(task_id) <= 0
            or int(task_id) > _UINT64_MAX
        ):
            raise CloudSubmissionError("invalid_provider_task_id")
        if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
            raise CloudSubmissionError("invalid_provider_request_id")
        timestamp = _utc(submitted_at)
        row.state = "submitted"
        row.provider_task_id = task_id
        row.provider_request_id = request_id
        row.provider_submitted_date = timestamp.strftime("%Y-%m-%d")
        row.submitted_at = timestamp
        row.result_expires_at = timestamp + timedelta(hours=24)
        row.safe_error_code = None
        self.session.commit()
        return self._view(row)

    def mark_unknown(
        self,
        submission_id: str,
        safe_code: str,
        *,
        marked_at: datetime | None = None,
    ) -> CloudSubmission:
        row = self._load(submission_id)
        if row.state != "sending":
            raise CloudSubmissionError("invalid_submission_transition")
        if not isinstance(safe_code, str) or _SAFE_CODE.fullmatch(safe_code) is None:
            raise CloudSubmissionError("invalid_submission_error_code")
        if marked_at is not None:
            _utc(marked_at)
        row.state = "submission_unknown"
        row.safe_error_code = safe_code
        row.next_poll_at = None
        if row.signed_url_expires_at is not None:
            row.cleanup_due_at = (
                row.signed_url_expires_at + timedelta(minutes=30)
            )
        self.session.commit()
        return self._view(row)

    def schedule_cancel_cleanup(
        self,
        submission_id: str,
        canceled_at: datetime,
    ) -> CloudSubmission:
        row = self._load(submission_id)
        timestamp = _utc(canceled_at)
        if row.state != "prepared" or row.cos_object_key is None:
            raise CloudSubmissionError("invalid_submission_transition")
        row.state = "canceled"
        row.cleanup_due_at = timestamp
        row.next_poll_at = None
        self.session.commit()
        return self._view(row)

    def schedule_query(
        self,
        submission_id: str,
        next_poll_at: datetime,
    ) -> CloudSubmission:
        row = self._load(submission_id)
        if row.state != "submitted" or row.provider_task_id is None:
            raise CloudSubmissionError("invalid_submission_transition")
        timestamp = _utc(next_poll_at)
        if row.submitted_at is not None and timestamp < row.submitted_at:
            raise CloudSubmissionError("invalid_submission_timestamp")
        row.next_poll_at = timestamp
        self.session.commit()
        return self._view(row)
