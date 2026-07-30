from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Lock, Thread

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from vtnote.database import initialize_database
from vtnote.models import (
    ItemRecord,
    ResourceLeaseRecord,
    StageRunRecord,
    TaskRecord,
    WorkerHeartbeatRecord,
)
from vtnote.worker_store import (
    StageFailure,
    StageResult,
    WorkerStore,
)


NOW = datetime(2026, 7, 29, 6, 0, tzinfo=timezone.utc)


def seed_pipeline(
    engine: Engine,
    *,
    task_id: str = "11111111-1111-4111-8111-111111111111",
    item_id: str = "22222222-2222-4222-8222-222222222222",
    statuses: dict[str, str] | None = None,
) -> dict[str, str]:
    selected = {
        "source": "queued",
        "transcribe": "queued",
        "translate": "queued",
        "notes": "queued",
        **(statuses or {}),
    }
    with Session(engine) as session:
        task = TaskRecord(
            id=task_id,
            status="queued",
            options={},
            pipeline_snapshot_json={},
            created_at=NOW,
        )
        item = ItemRecord(
            id=item_id,
            task=task,
            position=0,
            source_kind="url",
            source_locator="https://youtu.be/worker",
            status="queued",
        )
        rows = {
            stage: StageRunRecord(
                item=item,
                stage=stage,
                attempt=1,
                status=status,
                finished_at=NOW if status in {"completed", "skipped"} else None,
            )
            for stage, status in selected.items()
        }
        session.add(task)
        session.flush()
        ids = {stage: row.id for stage, row in rows.items()}
        session.commit()
        return ids


def test_atomic_single_claim_across_independent_engines(tmp_path: Path) -> None:
    database_path = tmp_path / "atomic-claim.db"
    first_engine = initialize_database(database_path)
    second_engine = initialize_database(database_path)
    stage_ids = seed_pipeline(first_engine)
    first_store = WorkerStore(first_engine, process_id=1001)
    second_store = WorkerStore(second_engine, process_id=1002)
    barrier = Barrier(2)
    claims: list[object] = []
    errors: list[BaseException] = []
    result_lock = Lock()

    def claim(store: WorkerStore, worker_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            result = store.claim_next(
                worker_id,
                NOW,
                timedelta(seconds=30),
            )
            with result_lock:
                claims.append(result)
        except BaseException as error:
            with result_lock:
                errors.append(error)

    threads = [
        Thread(target=claim, args=(first_store, "worker-a")),
        Thread(target=claim, args=(second_store, "worker-b")),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        created = [claim for claim in claims if claim is not None]
        assert len(created) == 1
        assert created[0].stage_run_id == stage_ids["source"]

        with Session(first_engine) as session:
            row = session.get(StageRunRecord, stage_ids["source"])
            assert row is not None
            assert row.status == "running"
            assert row.lease_owner in {"worker-a", "worker-b"}
    finally:
        first_engine.dispose()
        second_engine.dispose()


def test_heartbeat_requires_current_owner_attempt_and_unexpired_lease(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "heartbeat.db")
    stage_ids = seed_pipeline(engine)
    store = WorkerStore(engine, process_id=4242)
    try:
        claim = store.claim_next("worker-a", NOW, timedelta(seconds=30))
        assert claim is not None

        renewed_at = NOW + timedelta(seconds=5)
        assert store.heartbeat(claim, renewed_at)
        assert not store.heartbeat(
            replace(claim, worker_id="worker-b"),
            renewed_at + timedelta(seconds=1),
        )
        assert not store.heartbeat(
            claim,
            NOW + timedelta(seconds=36),
        )

        with Session(engine) as session:
            row = session.get(StageRunRecord, stage_ids["source"])
            heartbeat = session.get(WorkerHeartbeatRecord, "worker-a")
            assert row is not None
            assert row.heartbeat_at == renewed_at
            assert row.lease_expires_at == NOW + timedelta(seconds=35)
            assert heartbeat is not None
            assert heartbeat.process_id == 4242
            assert heartbeat.heartbeat_at == renewed_at
    finally:
        engine.dispose()


def test_expired_recovery_requeues_once_and_stale_worker_cannot_complete(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "recovery.db")
    stage_ids = seed_pipeline(engine)
    store = WorkerStore(engine, process_id=1)
    try:
        stale = store.claim_next("worker-a", NOW, timedelta(seconds=10))
        assert stale is not None
        recovery_time = NOW + timedelta(seconds=10)

        assert store.recover_expired(recovery_time) == (stage_ids["source"],)
        assert store.recover_expired(recovery_time) == ()

        current = store.claim_next(
            "worker-b",
            recovery_time,
            timedelta(seconds=20),
        )
        assert current is not None
        assert not store.complete(stale, StageResult(), now=recovery_time)

        with Session(engine) as session:
            row = session.get(StageRunRecord, stage_ids["source"])
            assert row is not None
            assert row.status == "running"
            assert row.lease_owner == "worker-b"
            assert row.recovered_count == 1

        assert store.complete(
            current,
            StageResult(),
            now=recovery_time + timedelta(seconds=1),
        )
    finally:
        engine.dispose()


def test_global_resource_lease_is_claim_owned_and_released_on_terminal_paths(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "resource.db")
    seed_pipeline(
        engine,
        statuses={"source": "completed", "transcribe": "completed"},
    )
    store = WorkerStore(engine, process_id=1)
    try:
        translate = store.claim_next("worker-a", NOW, timedelta(seconds=30))
        notes = store.claim_next("worker-b", NOW, timedelta(seconds=30))
        assert translate is not None and translate.stage == "translate"
        assert notes is not None and notes.stage == "notes"

        assert store.acquire_resource(
            translate,
            "gpu",
            NOW,
            timedelta(seconds=30),
        )
        assert not store.acquire_resource(
            notes,
            "gpu",
            NOW,
            timedelta(seconds=30),
        )
        assert store.heartbeat(translate, NOW + timedelta(seconds=5))

        with Session(engine) as session:
            resource = session.get(ResourceLeaseRecord, "gpu")
            assert resource is not None
            assert resource.lease_expires_at == NOW + timedelta(seconds=35)

        assert store.complete(
            translate,
            StageResult(status="skipped"),
            now=NOW + timedelta(seconds=6),
        )
        assert store.acquire_resource(
            notes,
            "gpu",
            NOW + timedelta(seconds=6),
            timedelta(seconds=30),
        )
        assert store.fail(
            notes,
            StageFailure(
                error_code="handler_failed",
                error_message="safe failure",
            ),
            now=NOW + timedelta(seconds=7),
        )
        with Session(engine) as session:
            assert session.get(ResourceLeaseRecord, "gpu") is None
    finally:
        engine.dispose()


def test_expired_cancel_requested_claim_becomes_canceled_not_queued(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "cancel-recovery.db")
    stage_ids = seed_pipeline(engine)
    store = WorkerStore(engine, process_id=1)
    try:
        claim = store.claim_next("worker-a", NOW, timedelta(seconds=10))
        assert claim is not None
        with Session(engine) as session:
            row = session.get(StageRunRecord, claim.stage_run_id)
            assert row is not None
            row.status = "cancel_requested"
            row.item.status = "cancel_requested"
            row.item.task.status = "cancel_requested"
            row.item.task.terminal_reason_code = "user_canceled"
            session.commit()

        assert store.recover_expired(NOW + timedelta(seconds=10)) == (
            stage_ids["source"],
        )
        with Session(engine) as session:
            row = session.get(StageRunRecord, stage_ids["source"])
            assert row is not None
            assert row.status == "canceled"
            assert row.recovered_count == 1
            assert row.item.status == "canceled"
            assert row.item.task.status == "canceled"
    finally:
        engine.dispose()


def test_recovery_releases_resource_after_stage_heartbeat_extended_expiry(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "resource-recovery.db")
    seed_pipeline(engine)
    store = WorkerStore(engine, process_id=1)
    try:
        claim = store.claim_next("worker-a", NOW, timedelta(seconds=10))
        assert claim is not None
        assert store.acquire_resource(
            claim,
            "gpu",
            NOW,
            timedelta(seconds=10),
        )
        assert store.heartbeat(claim, NOW + timedelta(seconds=5))

        assert store.recover_expired(NOW + timedelta(seconds=15)) == (
            claim.stage_run_id,
        )
        with Session(engine) as session:
            assert session.get(ResourceLeaseRecord, "gpu") is None
    finally:
        engine.dispose()


def test_task_cancellation_makes_running_claim_stale_before_stage_fanout(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "task-cancel-race.db")
    seed_pipeline(engine)
    store = WorkerStore(engine, process_id=1)
    try:
        claim = store.claim_next("worker-a", NOW, timedelta(seconds=30))
        assert claim is not None
        with Session(engine) as session:
            row = session.get(StageRunRecord, claim.stage_run_id)
            assert row is not None
            row.item.task.status = "cancel_requested"
            row.item.task.terminal_reason_code = "user_canceled"
            session.commit()

        assert not store.complete(
            claim,
            StageResult(),
            now=NOW + timedelta(seconds=1),
        )
        with Session(engine) as session:
            row = session.get(StageRunRecord, claim.stage_run_id)
            assert row is not None
            assert row.status == "running"
    finally:
        engine.dispose()
