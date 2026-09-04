"""Shared validation helpers for durable stage retry requests."""

from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any, cast

from sqlalchemy.exc import OperationalError

from vtnote.application.task_contracts import (
    InvalidTaskOperation,
    RetryOverrideSnapshot,
)


_MAX_RETRY_OVERRIDE_BYTES = 16 * 1024


def bounded_retry_override(override: dict[str, Any]) -> RetryOverrideSnapshot:
    try:
        encoded = json.dumps(
            override,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise InvalidTaskOperation("invalid retry override") from None
    if len(encoded) > _MAX_RETRY_OVERRIDE_BYTES:
        raise InvalidTaskOperation("retry override exceeds the storage limit")
    return cast(RetryOverrideSnapshot, copy.deepcopy(override))


def is_sqlite_retry_conflict(error: OperationalError) -> bool:
    original = error.orig
    if not isinstance(original, sqlite3.OperationalError):
        return False
    code = getattr(original, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return True
    return str(original).casefold() in {
        "database is busy",
        "database is locked",
        "database table is locked",
    }
