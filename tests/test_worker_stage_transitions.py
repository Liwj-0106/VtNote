from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from vtnote.database import initialize_database
from vtnote.models import ItemRecord, StageRunRecord, TaskRecord
from vtnote.worker import StageCancelled, StageContext, Worker
from vtnote.worker_store import StageFailure, StageResult, WorkerStore


NOW = datetime(2026, 7, 29, 7, 0, tzinfo=timezone.utc)


def add_item(
    session: Session,
    task: TaskRecord,
    *,
    item_id: str,
    position: int,
    statuses: dict[str, str],
    source_attempts: tuple[tuple[int, str], ...] = ((1, "queued"),),
) -> dict[str, list[StageRunRecord]]:
    item = ItemRecord(
        id=item_id,
        task=task,
        position=position,
        source_kind="url",
        source_locator=f"https://youtu.be/{item_id}",
        status="queued",
    )
    rows: dict[str, list[StageRunRecord]] = {"source": []}
    for attempt, status in source_attempts:
        rows["source"].append(
            StageRunRecord(
                item=item,
                stage="source",
                attempt=attempt,
                status=status,
                finished_at=NOW if status in {"completed", "skipped"} else None,
            )
        )
    for stage in ("transcribe", "translate", "notes"):
        status = statuses.get(stage, "queued")
        rows[stage] = [
            StageRunRecord(
                item=item,
                stage=stage,
                attempt=1,
                status=status,
                finished_at=NOW if status in {"completed", "skipped"} else None,
            )
        ]
    session.add(item)
    return rows


def seed_task(
    engine: Engine,
    *,
    task_id: str = "11111111-1111-4111-8111-111111111111",
    item_id: str = "22222222-2222-4222-8222-222222222222",
    task_status: str = "queued",
    source_status: str = "queued",
    transcribe_status: str = "queued",
    translate_status: str = "queued",
    notes_status: str = "queued",
    source_attempts: tuple[tuple[int, str], ...] | None = None,
    created_at: datetime = NOW,
) -> dict[str, list[str]]:
    with Session(engine) as session:
        task = TaskRecord(
            id=task_id,
            status=task_status,
            options={},
            pipeline_snapshot_json={},
            created_at=created_at,
        )
        rows = add_item(
            session,
            task,
            item_id=item_id,
            position=0,
            statuses={
                "transcribe": transcribe_status,
                "translate": translate_status,
                "notes": notes_status,
            },
            source_attempts=source_attempts or ((1, source_status),),
        )
        session.add(task)
        session.flush()
        result = {
            stage: [row.id for row in stage_rows]
            for stage, stage_rows in rows.items()
        }
        session.commit()
        return result


@pytest.mark.parametrize("source_status", ["completed", "skipped"])
def test_claim_requires_all_stage_dependencies_successful_or_skipped(
    tmp_path: Path,
    source_status: str,
) -> None:
    engine = initialize_database(tmp_path / f"dependency-{source_status}.db")
    ids = seed_task(engine, source_status=source_status)
    store = WorkerStore(engine, process_id=1)
    try:
        claim = store.claim_next("worker", NOW, timedelta(seconds=30))
        assert claim is not None
        assert claim.stage == "transcribe"
        assert claim.stage_run_id == ids["transcribe"][0]
    finally:
        engine.dispose()


@pytest.mark.parametrize("source_status", ["failed", "canceled"])
def test_claim_does_not_take_failed_or_canceled_upstream(
    tmp_path: Path,
    source_status: str,
) -> None:
    engine = initialize_database(tmp_path / f"blocked-{source_status}.db")
    seed_task(engine, source_status=source_status)
    store = WorkerStore(engine, process_id=1)
    try:
        assert store.claim_next("worker", NOW, timedelta(seconds=30)) is None
    finally:
        engine.dispose()


def test_claim_allows_parallel_translate_and_notes(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "parallel-branches.db")
    seed_task(
        engine,
        source_status="completed",
        transcribe_status="completed",
    )
    store = WorkerStore(engine, process_id=1)
    try:
        first = store.claim_next("worker-a", NOW, timedelta(seconds=30))
        second = store.claim_next("worker-b", NOW, timedelta(seconds=30))
        assert first is not None and second is not None
        assert (first.stage, second.stage) == ("translate", "notes")
    finally:
        engine.dispose()


def test_claim_ignores_canceled_task_and_superseded_attempt(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "ignored-work.db")
    canceled_ids = seed_task(
        engine,
        task_id="11111111-1111-4111-8111-111111111111",
        item_id="22222222-2222-4222-8222-222222222222",
        task_status="canceled",
    )
    current_ids = seed_task(
        engine,
        task_id="33333333-3333-4333-8333-333333333333",
        item_id="44444444-4444-4444-8444-444444444444",
        source_attempts=((1, "queued"), (2, "queued")),
        created_at=NOW + timedelta(seconds=1),
    )
    store = WorkerStore(engine, process_id=1)
    try:
        claim = store.claim_next("worker", NOW, timedelta(seconds=30))
        assert claim is not None
        assert claim.stage_run_id == current_ids["source"][1]
        assert claim.attempt == 2
        assert claim.stage_run_id not in canceled_ids["source"]
    finally:
        engine.dispose()


def test_claim_order_is_created_at_stage_order_item_id_attempt(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "claim-order.db")
    with Session(engine) as session:
        older = TaskRecord(
            id="11111111-1111-4111-8111-111111111111",
            status="queued",
            options={},
            pipeline_snapshot_json={},
            created_at=NOW,
        )
        add_item(
            session,
            older,
            item_id="33333333-3333-4333-8333-333333333333",
            position=0,
            statuses={"transcribe": "queued"},
            source_attempts=((1, "completed"),),
        )
        add_item(
            session,
            older,
            item_id="22222222-2222-4222-8222-222222222222",
            position=1,
            statuses={},
            source_attempts=((1, "queued"),),
        )
        newer = TaskRecord(
            id="44444444-4444-4444-8444-444444444444",
            status="queued",
            options={},
            pipeline_snapshot_json={},
            created_at=NOW + timedelta(seconds=1),
        )
        add_item(
            session,
            newer,
            item_id="55555555-5555-4555-8555-555555555555",
            position=0,
            statuses={},
            source_attempts=((1, "queued"),),
        )
        session.add_all([older, newer])
        session.commit()
    store = WorkerStore(engine, process_id=1)
    try:
        first = store.claim_next("worker-a", NOW, timedelta(seconds=30))
        second = store.claim_next("worker-b", NOW, timedelta(seconds=30))
        assert first is not None and second is not None
        assert first.item_id == "22222222-2222-4222-8222-222222222222"
        assert first.stage == "source"
        assert second.item_id == "33333333-3333-4333-8333-333333333333"
        assert second.stage == "transcribe"
    finally:
        engine.dispose()


def test_terminal_transitions_recalculate_item_and_task_status(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "aggregate.db")
    ids = seed_task(engine)
    store = WorkerStore(engine, process_id=1)
    try:
        source = store.claim_next("worker", NOW, timedelta(seconds=30))
        assert source is not None
        assert store.complete(source, StageResult(), now=NOW + timedelta(seconds=1))

        transcribe = store.claim_next(
            "worker",
            NOW + timedelta(seconds=1),
            timedelta(seconds=30),
        )
        assert transcribe is not None
        assert store.fail(
            transcribe,
            StageFailure(error_code="transcribe_failed", error_message="failed"),
            now=NOW + timedelta(seconds=2),
        )

        with Session(engine) as session:
            row = session.get(StageRunRecord, ids["transcribe"][0])
            assert row is not None
            assert row.status == "failed"
            assert row.item.status == "failed"
            assert row.item.task.status == "failed"
    finally:
        engine.dispose()


class RecordingHandler:
    def __init__(self, action: Callable[[StageContext], StageResult]) -> None:
        self.action = action
        self.contexts: list[StageContext] = []

    def run(self, context: StageContext) -> StageResult:
        self.contexts.append(context)
        return self.action(context)


def test_worker_graceful_stop_and_bounded_idle_backoff(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "graceful-stop.db")
    store = WorkerStore(engine, process_id=1)
    sleeps: list[float] = []
    stopped = False

    def sleep(delay: float) -> None:
        nonlocal stopped
        sleeps.append(delay)
        stopped = True

    worker = Worker(
        store=store,
        worker_id="worker",
        handlers={},
        lease_duration=timedelta(seconds=30),
        clock=lambda: NOW,
        sleeper=sleep,
        stop_requested=lambda: stopped,
        initial_idle_delay=0.1,
        maximum_idle_delay=0.5,
    )
    try:
        worker.run()
        assert sleeps == [0.1]
    finally:
        engine.dispose()


def test_worker_idle_backoff_reaches_but_does_not_exceed_maximum(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "bounded-backoff.db")
    store = WorkerStore(engine, process_id=1)
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)

    worker = Worker(
        store=store,
        worker_id="worker",
        handlers={},
        lease_duration=timedelta(seconds=30),
        clock=lambda: NOW,
        sleeper=sleep,
        stop_requested=lambda: len(sleeps) == 5,
        initial_idle_delay=0.1,
        maximum_idle_delay=0.5,
    )
    try:
        worker.run()
        assert sleeps == [0.1, 0.2, 0.4, 0.5, 0.5]
    finally:
        engine.dispose()


def test_worker_cancellation_checkpoint_persists_canceled_and_stops(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "worker-cancel.db")
    ids = seed_task(engine)
    store = WorkerStore(engine, process_id=1)
    stopped = False

    def cancel_during_handler(context: StageContext) -> StageResult:
        nonlocal stopped
        with Session(engine) as session:
            row = session.get(StageRunRecord, context.claim.stage_run_id)
            assert row is not None
            row.status = "cancel_requested"
            row.item.status = "cancel_requested"
            row.item.task.status = "cancel_requested"
            row.item.task.terminal_reason_code = "user_canceled"
            session.commit()
        stopped = True
        context.checkpoint()
        raise AssertionError("checkpoint must raise")

    handler = RecordingHandler(cancel_during_handler)
    worker = Worker(
        store=store,
        worker_id="worker",
        handlers={"source": handler},
        lease_duration=timedelta(seconds=30),
        clock=lambda: NOW,
        sleeper=lambda _: None,
        stop_requested=lambda: stopped,
    )
    try:
        worker.run()
        assert len(handler.contexts) == 1
        with Session(engine) as session:
            source = session.get(StageRunRecord, ids["source"][0])
            assert source is not None
            assert source.status == "canceled"
            assert source.item.status == "canceled"
            assert source.item.task.status == "canceled"
    finally:
        engine.dispose()


def test_stage_context_exposes_immutable_attempt_override(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "attempt-override.db")
    ids = seed_task(engine)
    with Session(engine) as session:
        row = session.get(StageRunRecord, ids["source"][0])
        assert row is not None
        row.retry_override_json = {"schema_version": 1, "strategy": "same"}
        session.commit()
    store = WorkerStore(engine, process_id=1)
    claim = store.claim_next("worker", NOW, timedelta(seconds=30))
    try:
        assert claim is not None
        assert claim.retry_override == {"schema_version": 1, "strategy": "same"}
        with pytest.raises(TypeError):
            claim.retry_override["strategy"] = "local"
    finally:
        engine.dispose()
