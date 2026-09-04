"""Bounded, defense-in-depth sanitization for persisted and public diagnostics."""

from __future__ import annotations

import re
import json
import os
import platform
import sys
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from sqlalchemy import Engine, text

from vtnote.config import Settings


_KEY = (
    r"(?:access[_-]?token|api[_-]?key|authorization|token|"
    r"secret(?:[_-]?(?:id|key))?|custom[_-]?prompt|prompt|"
    r"raw[_-]?response|data|url|path)"
)
_QUOTED_VALUE = re.compile(
    rf"(?i)([\"']?{_KEY}[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_UNQUOTED_VALUE = re.compile(
    rf"(?i)(\b{_KEY}\b\s*[:=]\s*)(?![\"'])([^\s,;}}\]]+)"
)
_WINDOWS_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:\\|\\\\)[^\s,;\"']+"
)


def sanitize_diagnostic(
    message: str | None,
    sensitive_values: Iterable[str | None] = (),
    *,
    max_length: int = 500,
) -> str | None:
    """Return a single-line bounded message with credential-shaped values removed."""

    if message is None:
        return None
    cleaned = str(message).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    for value in sorted(
        {value for value in sensitive_values if value}, key=len, reverse=True
    ):
        cleaned = cleaned.replace(value, "[redacted]")
    cleaned = _QUOTED_VALUE.sub(
        lambda match: f'{match.group(1)}{match.group(2)}[redacted]{match.group(4)}',
        cleaned,
    )
    cleaned = _BEARER_VALUE.sub("Bearer [redacted]", cleaned)
    cleaned = _UNQUOTED_VALUE.sub(r"\1[redacted]", cleaned)
    cleaned = _WINDOWS_PATH.sub("[path]", cleaned)
    return cleaned[:max_length]


def contains_sensitive_value(
    values: tuple[str, ...], sensitive_values: tuple[str, ...]
) -> bool:
    return any(
        sensitive and sensitive in value
        for value in values
        for sensitive in sensitive_values
    )


def build_diagnostic_bundle(
    destination: Path,
    *,
    settings: Settings,
    engine: Engine,
) -> Path:
    """Write a deterministic, metadata-only diagnostic archive."""

    selected = Path(destination)
    if selected.suffix.casefold() != ".zip":
        raise ValueError("diagnostic bundle must be a zip file")
    if selected.exists():
        raise FileExistsError(selected)
    selected.parent.mkdir(parents=True, exist_ok=True)
    rendered = diagnostic_bundle_bytes(settings=settings, engine=engine)
    staging = selected.with_name(f".{selected.name}.{uuid4().hex}.tmp")
    try:
        with staging.open("xb") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, selected)
    finally:
        if staging.exists():
            staging.unlink()
    return selected


def diagnostic_bundle_bytes(*, settings: Settings, engine: Engine) -> bytes:
    try:
        with engine.connect() as connection:
            database_reachable = (
                connection.execute(text("SELECT 1")).scalar_one() == 1
            )
    except Exception:
        database_reachable = False
    payload = {
        "schema_version": 1,
        "application": "vtnote",
        "application_version": "0.1.0",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": platform.system(),
        "database_reachable": database_reachable,
        "data_storage_available": settings.data_root.is_dir(),
        "runtime_storage_available": settings.runtime_cache_root.is_dir(),
    }
    document = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    info = ZipInfo("diagnostics.json", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(info, document)
    return stream.getvalue()
