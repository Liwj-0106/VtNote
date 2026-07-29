"""Bounded structured logging with defense-in-depth redaction."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from vtnote.diagnostics import sanitize_diagnostic


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = sanitize_diagnostic(record.getMessage(), max_length=2_000)
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": sanitize_diagnostic(record.name, max_length=128),
            "message": message,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def configure_logging(
    log_directory: Path,
    *,
    process_name: str,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> Path:
    if (
        not process_name
        or not process_name.replace("-", "").replace("_", "").isalnum()
        or max_bytes <= 0
        or backup_count < 0
    ):
        raise ValueError("invalid logging configuration")
    directory = Path(log_directory)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / f"{process_name}.log"
    logger = logging.getLogger("vtnote")
    for handler in tuple(logger.handlers):
        if getattr(handler, "_vtnote_managed", False):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(SafeJsonFormatter())
    setattr(handler, "_vtnote_managed", True)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return log_path
