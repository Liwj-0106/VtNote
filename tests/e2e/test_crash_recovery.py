from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from vtnote.database import initialize_database
from vtnote.models import ItemRecord, StageRunRecord, TaskRecord
from vtnote.worker_store import WorkerStore


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


def test_expired_worker_claim_is_requeued_once_without_creating_a_new_attempt(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "data" / "vtnote.db")
    with Session(engine) as session:
        task = TaskRecord(status="queued", pipeline_snapshot_json={})
        item = ItemRecord(
            task=task,
            position=0,
            source_kind="url",
            source_locator="https://www.bilibili.com/video/BV1offline",
            status="queued",
            artifact_relpath="items/offline",
        )
        item.stage_runs = [
            StageRunRecord(stage="source", attempt=1, status="queued"),
            StageRunRecord(stage="transcribe", attempt=1, status="queued"),
        ]
        session.add(item)
        session.commit()

    store = WorkerStore(engine, process_id=100)
    first = store.claim_next("crashed-worker", NOW, timedelta(seconds=10))
    assert first is not None
    recovered = store.recover_expired(NOW + timedelta(seconds=11))
    assert recovered == (first.stage_run_id,)
    second = store.claim_next(
        "replacement-worker",
        NOW + timedelta(seconds=12),
        timedelta(minutes=2),
    )
    assert second is not None
    assert second.stage_run_id == first.stage_run_id
    assert second.attempt == 1
    assert second.recovery_generation == 1
    engine.dispose()
