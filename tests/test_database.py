from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from vtnote.database import initialize_database
from vtnote.models import ItemRecord, StageRunRecord, TaskRecord


def test_sqlite_initialization_creates_tables_and_enables_wal(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "vtnote.db"

    engine = initialize_database(database_path)
    try:
        with engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()

        assert journal_mode.lower() == "wal"
        assert foreign_keys == 1
        assert busy_timeout == 5_000
        assert {
            "tasks", "items", "stage_runs", "provider_connections",
            "processor_profiles", "default_settings", "credential_cleanup",
        } <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_concurrent_database_initialization_is_serialized_and_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "vtnote.db"

    with ThreadPoolExecutor(max_workers=6) as pool:
        engines = list(pool.map(lambda _: initialize_database(database_path), range(12)))

    try:
        for engine in engines:
            with engine.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        for engine in engines:
            engine.dispose()


def test_timestamps_round_trip_as_aware_utc(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "vtnote.db")
    supplied = datetime(2026, 7, 18, 20, 30, tzinfo=timezone(timedelta(hours=8)))
    try:
        with Session(engine) as session:
            task = TaskRecord()
            item = ItemRecord(
                task=task,
                position=0,
                source_kind="local",
                source_locator="video.mp4",
            )
            stage = StageRunRecord(
                item=item,
                stage="transcribe",
                attempt=1,
                lease_expires_at=supplied,
                heartbeat_at=supplied,
            )
            session.add(stage)
            session.commit()
            stage_id = stage.id
            session.expunge_all()

            loaded = session.get(StageRunRecord, stage_id)

            assert loaded is not None
            assert loaded.lease_expires_at == supplied.astimezone(timezone.utc)
            assert loaded.heartbeat_at == supplied.astimezone(timezone.utc)
            assert loaded.created_at.tzinfo is not None
            assert loaded.created_at.utcoffset() == timedelta(0)
    finally:
        engine.dispose()


def test_task_options_track_in_place_mutations(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "vtnote.db")
    try:
        with Session(engine) as session:
            task = TaskRecord(options={"notes": True})
            session.add(task)
            session.commit()
            task_id = task.id

            task.options["translate"] = True
            session.commit()
            session.expunge_all()

            loaded = session.get(TaskRecord, task_id)
            assert loaded is not None
            assert loaded.options == {"notes": True, "translate": True}
    finally:
        engine.dispose()


def test_relationships_load_in_deterministic_order(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "vtnote.db")
    try:
        with Session(engine) as session:
            task = TaskRecord()
            second = ItemRecord(
                task=task,
                position=2,
                source_kind="local",
                source_locator="second.mp4",
            )
            first = ItemRecord(
                task=task,
                position=1,
                source_kind="local",
                source_locator="first.mp4",
            )
            second.stage_runs.extend(
                [
                    StageRunRecord(stage="translate", attempt=2),
                    StageRunRecord(stage="transcribe", attempt=1),
                    StageRunRecord(stage="translate", attempt=1),
                ]
            )
            session.add_all([second, first])
            session.commit()
            task_id = task.id
            second_id = second.id
            session.expunge_all()

            loaded_task = session.get(TaskRecord, task_id)
            loaded_item = session.get(ItemRecord, second_id)

            assert loaded_task is not None
            assert [item.position for item in loaded_task.items] == [1, 2]
            assert loaded_item is not None
            assert [(run.stage, run.attempt) for run in loaded_item.stage_runs] == [
                ("transcribe", 1),
                ("translate", 1),
                ("translate", 2),
            ]
    finally:
        engine.dispose()
