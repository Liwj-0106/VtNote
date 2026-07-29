"""SQLite engine creation and schema bootstrap."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from vtnote.models import (
    Base,
    DefaultSettingsRecord,
    ItemRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
    StageRunRecord,
    TaskRecord,
)
from vtnote.sensitive_text import (
    SensitiveTextProtector,
    migrate_sensitive_text,
)


_BOOTSTRAP_LOCK = threading.Lock()
_WAL_ATTEMPTS = 5
_ADDITIVE_COLUMNS = {
    "tasks": {
        "terminal_reason_code": "VARCHAR(64)",
    },
    "items": {
        "source_display_name": "TEXT",
    },
    "stage_runs": {
        "external_request_id": "VARCHAR(128)",
        "external_log_id": "VARCHAR(256)",
        "external_submission_state": "VARCHAR(32)",
        "progress_json": "JSON",
        "execution_evidence_json": "JSON",
        "provider_status_code": "VARCHAR(128)",
        "retry_override_json": "JSON",
        "recovered_count": "INTEGER NOT NULL DEFAULT 0",
    },
    "default_settings": {
        "notes_custom_prompt_envelope_json": "JSON",
    },
    "cloud_submissions": {
        "normalized_result_json": "JSON",
    },
}


def _configure_sqlite(connection: sqlite3.Connection, _: Any) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def _initialize_wal(engine: Engine) -> None:
    for attempt in range(_WAL_ATTEMPTS):
        try:
            with engine.connect() as connection:
                mode = connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
            if str(mode).casefold() != "wal":
                raise RuntimeError(f"SQLite refused WAL mode: {mode!r}")
            return
        except OperationalError as error:
            if "locked" not in str(error).casefold() or attempt == _WAL_ATTEMPTS - 1:
                raise
            time.sleep(0.025 * (attempt + 1))


def _apply_additive_schema_upgrades(connection: Connection) -> None:
    """Add new nullable columns to an existing database without rebuilding rows."""

    added_columns: set[tuple[str, str]] = set()
    for table_name, additions in _ADDITIVE_COLUMNS.items():
        columns = {
            str(row[1])
            for row in connection.exec_driver_sql(
                f'PRAGMA table_info("{table_name}")'
            )
        }
        for column_name, declaration in additions.items():
            if column_name not in columns:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" '
                    f'ADD COLUMN "{column_name}" {declaration}'
                )
                added_columns.add((table_name, column_name))
    if ("tasks", "terminal_reason_code") in added_columns:
        connection.exec_driver_sql(
            """UPDATE tasks
            SET terminal_reason_code = 'user_canceled'
            WHERE status = 'canceled' AND terminal_reason_code IS NULL"""
        )


def _initialize_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            Base.metadata.create_all(connection)
            _apply_additive_schema_upgrades(connection)
        except Exception:
            connection.rollback()
            raise
        connection.commit()


def _migrate_default_local_whisper_device(engine: Engine) -> None:
    """Move the mutable default to GPU-only without rewriting task history."""

    with Session(engine) as session:
        defaults = session.get(DefaultSettingsRecord, 1)
        if defaults is None:
            return
        options = defaults.local_whisper_options
        if not isinstance(options, dict) or options.get("device") != "auto":
            return
        migrated = dict(options)
        migrated["device"] = "cuda"
        defaults.local_whisper_options = migrated
        session.commit()


def _contains_legacy_cloud_snapshot(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("protocol") == "volc_bigasr_flash":
            return True
        return any(_contains_legacy_cloud_snapshot(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_legacy_cloud_snapshot(item) for item in value)
    return False


def _migrate_legacy_cloud_provider(engine: Engine) -> None:
    """Retire Volc configuration and stop active snapshots before execution."""

    now = datetime.now(timezone.utc)
    reason = "legacy_provider_requires_reconfiguration"
    with Session(engine) as session:
        legacy_connections = session.scalars(
            select(ProviderConnectionRecord).where(
                ProviderConnectionRecord.protocol == "volc_bigasr_flash"
            )
        ).all()
        if not legacy_connections:
            return
        legacy_connection_ids = {row.id for row in legacy_connections}
        legacy_profiles = session.scalars(
            select(ProcessorProfileRecord).where(
                ProcessorProfileRecord.connection_id.in_(legacy_connection_ids)
            )
        ).all()
        legacy_profile_ids = {row.id for row in legacy_profiles}

        for row in legacy_connections:
            row.archived_at = row.archived_at or now
            row.test_ok = None
            row.tested_revision = None
            row.test_message = None
            row.tested_at = None
        for row in legacy_profiles:
            row.archived_at = row.archived_at or now
            row.test_ok = None
            row.tested_revision = None
            row.tested_connection_revision = None
            row.test_message = None
            row.tested_at = None
            row.upload_authorized_revision = None
            row.upload_authorized_connection_revision = None

        defaults = session.get(DefaultSettingsRecord, 1)
        if (
            defaults is not None
            and defaults.cloud_asr_profile_id in legacy_profile_ids
        ):
            defaults.cloud_asr_profile_id = None
            if defaults.asr_mode == "cloud":
                defaults.asr_mode = "auto"

        active_tasks = session.scalars(
            select(TaskRecord)
            .where(TaskRecord.status.in_(("queued", "running")))
            .options(
                selectinload(TaskRecord.items).selectinload(ItemRecord.stage_runs)
            )
        ).all()
        for task in active_tasks:
            if not _contains_legacy_cloud_snapshot(task.pipeline_snapshot_json):
                continue
            task.status = "failed"
            task.terminal_reason_code = reason
            for item in task.items:
                transcribe_runs = [
                    run
                    for run in item.stage_runs
                    if run.stage == "transcribe"
                    and run.status in {"queued", "running"}
                ]
                if not transcribe_runs:
                    continue
                item.status = "failed"
                for run in transcribe_runs:
                    run.status = "failed"
                    run.error_code = reason
                    run.error_message = (
                        "Cloud ASR provider changed; reconfigure or retry locally"
                    )
                    run.finished_at = now
                    run.lease_owner = None
                    run.lease_expires_at = None
                    run.heartbeat_at = None
        session.commit()


def initialize_database(
    database_path: Path,
    *,
    sensitive_text_protector: SensitiveTextProtector | None = None,
) -> Engine:
    """Create a file-backed SQLite engine, enable durability pragmas, and create tables."""

    path = Path(database_path)
    if not path.is_absolute():
        raise ValueError("database path must be absolute")
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _configure_sqlite)
    try:
        with _BOOTSTRAP_LOCK:
            _initialize_wal(engine)
            _initialize_schema(engine)
            migrate_sensitive_text(engine, sensitive_text_protector)
            _migrate_legacy_cloud_provider(engine)
            _migrate_default_local_whisper_device(engine)
    except Exception:
        engine.dispose()
        raise
    return engine
