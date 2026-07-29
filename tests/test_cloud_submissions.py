from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from vtnote.cloud_submissions import (
    CloudSubmissionError,
    CloudSubmissionStore,
    CosLocator,
)
from vtnote.database import initialize_database
from vtnote.models import ItemRecord, StageRunRecord, TaskRecord


NOW = datetime(2026, 7, 29, 16, 0, tzinfo=timezone.utc)
STAGE_ID = "33333333-3333-4333-8333-333333333333"
AUDIO_HASH = hashlib.sha256(b"audio").hexdigest()


def seeded(tmp_path: Path) -> tuple[object, Session, CloudSubmissionStore]:
    engine = initialize_database((tmp_path / "vtnote.db").absolute())
    session = Session(engine)
    item = ItemRecord(
        id="22222222-2222-4222-8222-222222222222",
        task=TaskRecord(
            id="11111111-1111-4111-8111-111111111111",
            status="running",
            options={},
            pipeline_snapshot_json={},
        ),
        position=0,
        source_kind="local_media",
        source_locator=r"D:\media\sample.mp4",
        status="running",
    )
    item.stage_runs = [
        StageRunRecord(
            id=STAGE_ID,
            stage="transcribe",
            attempt=1,
            status="running",
        )
    ]
    session.add(item)
    session.commit()
    return engine, session, CloudSubmissionStore(session)


def locator() -> CosLocator:
    return CosLocator(
        bucket="private-audio-1250000000",
        region="ap-guangzhou",
        object_key=(
            "vtnote-runtime/11111111-1111-4111-8111-111111111111/"
            f"{AUDIO_HASH[:16]}.ogg"
        ),
    )


def test_prepare_is_attempt_bound_idempotent_and_contains_no_secret_url(
    tmp_path: Path,
) -> None:
    engine, session, store = seeded(tmp_path)
    try:
        created = store.prepare(STAGE_ID, AUDIO_HASH, locator())
        recovered = store.prepare(STAGE_ID, AUDIO_HASH, locator())

        assert recovered.id == created.id
        assert created.state == "prepared"
        assert created.stage_run_id == STAGE_ID
        assert created.audio_sha256 == AUDIO_HASH
        assert created.cos_locator == locator()
        assert "signature" not in repr(created)
        assert "secret" not in repr(created)
        with pytest.raises(CloudSubmissionError) as caught:
            store.prepare(STAGE_ID, hashlib.sha256(b"other").hexdigest(), locator())
        assert caught.value.code == "submission_conflict"
    finally:
        session.close()
        engine.dispose()


def test_sending_submitted_and_query_schedule_are_strict_and_durable(
    tmp_path: Path,
) -> None:
    engine, session, store = seeded(tmp_path)
    try:
        prepared = store.prepare(STAGE_ID, AUDIO_HASH, None)
        sending = store.mark_sending(prepared.id)
        submitted = store.mark_submitted(
            sending.id,
            task_id="18446744073709551615",
            request_id="request-123",
            submitted_at=NOW,
        )
        scheduled = store.schedule_query(
            submitted.id,
            NOW + timedelta(seconds=4),
        )

        assert sending.state == "sending"
        assert submitted.state == "submitted"
        assert submitted.provider_task_id == "18446744073709551615"
        assert submitted.provider_request_id == "request-123"
        assert submitted.result_expires_at == NOW + timedelta(hours=24)
        assert scheduled.next_poll_at == NOW + timedelta(seconds=4)
        assert scheduled.poll_attempt == 0

        session.expire_all()
        loaded = store.get(prepared.id)
        assert loaded.state == "submitted"
        assert loaded.next_poll_at == NOW + timedelta(seconds=4)
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "task_id",
    ["", "-1", "1.5", "18446744073709551616", "not-a-number"],
)
def test_provider_task_id_is_strict_uint64_text(
    tmp_path: Path,
    task_id: str,
) -> None:
    engine, session, store = seeded(tmp_path)
    try:
        submission = store.mark_sending(
            store.prepare(STAGE_ID, AUDIO_HASH, None).id
        )
        with pytest.raises(CloudSubmissionError) as caught:
            store.mark_submitted(
                submission.id,
                task_id=task_id,
                request_id="request",
                submitted_at=NOW,
            )
        assert caught.value.code == "invalid_provider_task_id"
    finally:
        session.close()
        engine.dispose()


def test_possible_send_without_task_id_becomes_unknown_and_never_submitted(
    tmp_path: Path,
) -> None:
    engine, session, store = seeded(tmp_path)
    try:
        sending = store.mark_sending(
            store.prepare(STAGE_ID, AUDIO_HASH, locator()).id
        )
        unknown = store.mark_unknown(
            sending.id,
            "create_response_lost",
            marked_at=NOW,
        )

        assert unknown.state == "submission_unknown"
        assert unknown.safe_error_code == "create_response_lost"
        assert unknown.provider_task_id is None
        assert unknown.next_poll_at is None
        assert unknown.cleanup_due_at is None
        with pytest.raises(CloudSubmissionError) as caught:
            store.mark_sending(unknown.id)
        assert caught.value.code == "invalid_submission_transition"
    finally:
        session.close()
        engine.dispose()


def test_schema_has_due_indexes_and_no_signed_url_or_raw_payload_columns(
    tmp_path: Path,
) -> None:
    engine, session, _ = seeded(tmp_path)
    try:
        inspector = inspect(engine)
        columns = {
            column["name"]
            for column in inspector.get_columns("cloud_submissions")
        }
        indexes = {index["name"] for index in inspector.get_indexes("cloud_submissions")}

        assert {
            "stage_run_id",
            "provider_task_id",
            "provider_request_id",
            "audio_sha256",
            "cos_bucket",
            "cos_region",
            "cos_object_key",
            "state",
            "next_poll_at",
            "poll_attempt",
            "last_query_at",
            "signed_url_expires_at",
            "cleanup_due_at",
            "remote_terminal_at",
            "result_expires_at",
        } <= columns
        assert not any(
            marker in column
            for column in columns
            for marker in ("presigned", "signed_url_value", "payload", "base64", "secret")
        )
        assert "ix_cloud_submissions_due_query" in indexes
        assert "ix_cloud_submissions_due_cleanup" in indexes
    finally:
        session.close()
        engine.dispose()
