"""SQLite engine creation and schema bootstrap."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL

from vtnote.models import Base


def _configure_sqlite(connection: sqlite3.Connection, _: Any) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def initialize_database(database_path: Path) -> Engine:
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
    Base.metadata.create_all(engine)
    return engine
