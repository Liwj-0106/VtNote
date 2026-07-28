from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path

from sqlalchemy import MetaData, create_engine, inspect
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from vtnote.database import initialize_database
from vtnote.models import (
    Base,
    ItemRecord,
    ResourceLeaseRecord,
    RuntimeAssetRecord,
    RuntimeCleanupEventRecord,
    StageRunRecord,
    TaskRecord,
    WorkerHeartbeatRecord,
)


_TASK3_TABLES = {
    "runtime_assets",
    "runtime_cleanup_events",
    "resource_leases",
    "worker_heartbeats",
}
_TASK3_COLUMNS = {
    "tasks": {"terminal_reason_code"},
    "items": {"source_display_name"},
    "stage_runs": {
        "external_request_id",
        "external_log_id",
        "external_submission_state",
        "recovered_count",
        "progress_json",
        "execution_evidence_json",
        "provider_status_code",
        "retry_override_json",
    },
}


def create_task2_schema(database_path: Path):
    """Create the complete schema from the accepted Task 2 model shape."""

    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in _TASK3_TABLES:
            table.to_metadata(metadata)
    for table_name, column_names in _TASK3_COLUMNS.items():
        table = metadata.tables[table_name]
        for column_name in column_names:
            if column_name in table.c:
                table._columns.remove(table.c[column_name])
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(database_path)))
    metadata.create_all(engine)
    return engine


def initialize_database_in_child(database_path: str) -> tuple[str, ...]:
    engine = initialize_database(Path(database_path))
    try:
        return tuple(sorted(inspect(engine).get_table_names()))
    finally:
        engine.dispose()


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
            "runtime_assets", "runtime_cleanup_events", "resource_leases",
            "worker_heartbeats",
        } <= set(inspect(engine).get_table_names())
        assert {
            "uq_runtime_assets_relative_path",
            "uq_runtime_assets_original_relative_path",
        } <= {
            constraint["name"]
            for constraint in inspect(engine).get_unique_constraints("runtime_assets")
        }
    finally:
        engine.dispose()


def test_runtime_foundation_fields_round_trip(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "vtnote.db")
    try:
        with Session(engine) as session:
            task = TaskRecord()
            item = ItemRecord(
                task=task,
                position=0,
                source_kind="local_media",
                source_locator="D:/media/talk.mp4",
                source_display_name="talk.mp4",
            )
            stage = StageRunRecord(
                item=item,
                stage="transcribe",
                external_request_id="request-1",
                external_log_id="log-1",
                external_submission_state="submitted",
                recovered_count=2,
            )
            session.add(stage)
            session.commit()
            stage_id = stage.id
            item_id = item.id
            session.expunge_all()

            stored_item = session.get(ItemRecord, item_id)
            stored_stage = session.get(StageRunRecord, stage_id)
            assert stored_item is not None
            assert stored_item.source_display_name == "talk.mp4"
            assert stored_stage is not None
            assert (
                stored_stage.external_request_id,
                stored_stage.external_log_id,
                stored_stage.external_submission_state,
                stored_stage.recovered_count,
            ) == ("request-1", "log-1", "submitted", 2)
    finally:
        engine.dispose()


def test_additive_upgrade_adds_stage_evidence_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "stage-evidence-upgrade.db"
    legacy_engine = create_task2_schema(database_path)
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql(
            """INSERT INTO tasks VALUES (
            '11111111-1111-4111-8111-111111111111', 'canceled', '{}', '{}',
            '2026-07-18 00:00:00', '2026-07-18 00:00:00')"""
        )
    legacy_engine.dispose()

    engine = initialize_database(database_path)
    try:
        inspector = inspect(engine)
        assert "terminal_reason_code" in {
            column["name"] for column in inspector.get_columns("tasks")
        }
        assert {
            "progress_json",
            "execution_evidence_json",
            "provider_status_code",
        } <= {column["name"] for column in inspector.get_columns("stage_runs")}
        with Session(engine) as session:
            task = session.get(TaskRecord, "11111111-1111-4111-8111-111111111111")
            assert task is not None
            assert task.terminal_reason_code == "user_canceled"
    finally:
        engine.dispose()


def test_additive_upgrade_keeps_legacy_retry_override_nullable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "retry-override-upgrade.db"
    legacy_engine = create_task2_schema(database_path)
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql(
            """INSERT INTO tasks (
                id, status, options, pipeline_snapshot_json, created_at, updated_at
            ) VALUES (
                '11111111-1111-4111-8111-111111111111', 'queued', '{}', '{}',
                '2026-07-18 00:00:00', '2026-07-18 00:00:00'
            )"""
        )
        connection.exec_driver_sql(
            """INSERT INTO items (
                id, task_id, position, source_kind, source_locator, status,
                created_at, updated_at
            ) VALUES (
                '22222222-2222-4222-8222-222222222222',
                '11111111-1111-4111-8111-111111111111', 0, 'url',
                'https://youtu.be/legacy', 'queued',
                '2026-07-18 00:00:00', '2026-07-18 00:00:00'
            )"""
        )
        connection.exec_driver_sql(
            """INSERT INTO stage_runs (
                id, item_id, stage, attempt, status, created_at, updated_at
            ) VALUES (
                '33333333-3333-4333-8333-333333333333',
                '22222222-2222-4222-8222-222222222222',
                'transcribe', 1, 'failed',
                '2026-07-18 00:00:00', '2026-07-18 00:00:00'
            )"""
        )
    legacy_engine.dispose()

    engine = initialize_database(database_path)
    try:
        columns = {
            column["name"] for column in inspect(engine).get_columns("stage_runs")
        }
        assert "retry_override_json" in columns
        with Session(engine) as session:
            stage = session.get(
                StageRunRecord, "33333333-3333-4333-8333-333333333333"
            )
            assert stage is not None
            assert stage.retry_override_json is None
    finally:
        engine.dispose()


def test_terminal_reason_backfill_runs_only_when_legacy_column_is_added(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "current-schema.db"
    engine = initialize_database(database_path)
    with Session(engine) as session:
        task = TaskRecord(status="canceled", terminal_reason_code=None)
        session.add(task)
        session.commit()
        task_id = task.id
    engine.dispose()

    reopened = initialize_database(database_path)
    try:
        with Session(reopened) as session:
            stored = session.get(TaskRecord, task_id)
            assert stored is not None
            assert stored.terminal_reason_code is None
    finally:
        reopened.dispose()


def test_additive_upgrade_preserves_task2_rows_and_adds_runtime_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "task2.db"
    legacy_engine = create_task2_schema(database_path)
    task2_tables = set(Base.metadata.tables) - _TASK3_TABLES
    try:
        legacy_inspector = inspect(legacy_engine)
        assert set(legacy_inspector.get_table_names()) == task2_tables
        assert "uq_provider_connections_active_name" in {
            index["name"]
            for index in legacy_inspector.get_indexes("provider_connections")
        }
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql(
                """INSERT INTO tasks VALUES (
                '11111111-1111-4111-8111-111111111111', 'queued', '{}', '{}',
                '2026-07-18 00:00:00', '2026-07-18 00:00:00')"""
            )
            connection.exec_driver_sql(
                """INSERT INTO items VALUES (
                '22222222-2222-4222-8222-222222222222',
                '11111111-1111-4111-8111-111111111111', 0, 'url',
                'https://youtu.be/existing', 'queued', 'Existing', 'items/existing',
                '2026-07-18 00:00:00', '2026-07-18 00:00:00')"""
            )
            connection.exec_driver_sql(
                """INSERT INTO stage_runs VALUES (
                '33333333-3333-4333-8333-333333333333',
                '22222222-2222-4222-8222-222222222222', 'source', 1, 'queued',
                NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                '2026-07-18 00:00:00', '2026-07-18 00:00:00')"""
            )
    finally:
        legacy_engine.dispose()

    engine = initialize_database(database_path)
    try:
        inspector = inspect(engine)
        assert "source_display_name" in {
            column["name"] for column in inspector.get_columns("items")
        }
        assert {
            "external_request_id", "external_log_id", "external_submission_state",
            "recovered_count",
        } <= {column["name"] for column in inspector.get_columns("stage_runs")}
        assert {
            "runtime_assets", "runtime_cleanup_events", "resource_leases",
            "worker_heartbeats",
        } <= set(inspector.get_table_names())
        assert task2_tables <= set(inspector.get_table_names())
        assert "uq_provider_connections_active_name" in {
            index["name"] for index in inspector.get_indexes("provider_connections")
        }

        with Session(engine) as session:
            item = session.get(ItemRecord, "22222222-2222-4222-8222-222222222222")
            stage = session.get(StageRunRecord, "33333333-3333-4333-8333-333333333333")
            assert item is not None
            assert item.title == "Existing"
            assert item.source_display_name is None
            assert stage is not None
            assert stage.stage == "source"
            assert stage.recovered_count == 0
    finally:
        engine.dispose()


def test_additive_upgrade_is_serialized_across_processes(tmp_path: Path) -> None:
    database_path = tmp_path / "process-upgrade.db"
    legacy_engine = create_task2_schema(database_path)
    legacy_engine.dispose()

    context = get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as pool:
        results = list(
            pool.map(initialize_database_in_child, [str(database_path)] * 4)
        )

    assert all(_TASK3_TABLES <= set(table_names) for table_names in results)
    engine = initialize_database(database_path)
    try:
        assert _TASK3_COLUMNS["items"] <= {
            column["name"] for column in inspect(engine).get_columns("items")
        }
        assert _TASK3_COLUMNS["stage_runs"] <= {
            column["name"] for column in inspect(engine).get_columns("stage_runs")
        }
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
