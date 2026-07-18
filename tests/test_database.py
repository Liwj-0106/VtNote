from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from vtnote.database import initialize_database


def test_sqlite_initialization_creates_tables_and_enables_wal(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "vtnote.db"

    engine = initialize_database(database_path)
    try:
        with engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()

        assert journal_mode.lower() == "wal"
        assert foreign_keys == 1
        assert {"tasks", "items", "stage_runs"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
