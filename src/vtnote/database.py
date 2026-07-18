"""SQLite engine creation and schema bootstrap."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from vtnote.models import Base


_BOOTSTRAP_LOCK = threading.Lock()
_WAL_ATTEMPTS = 5


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
    try:
        with _BOOTSTRAP_LOCK:
            _initialize_wal(engine)
            Base.metadata.create_all(engine)
    except Exception:
        engine.dispose()
        raise
    return engine
