"""Bounded, defense-in-depth sanitization for persisted and public diagnostics."""

from __future__ import annotations

import re
from collections.abc import Iterable


_KEY = (
    r"(?:access[_-]?token|api[_-]?key|authorization|token|"
    r"secret(?:[_-]?(?:id|key))?|data|url)"
)
_QUOTED_VALUE = re.compile(
    rf"(?i)([\"']?{_KEY}[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_UNQUOTED_VALUE = re.compile(
    rf"(?i)(\b{_KEY}\b\s*[:=]\s*)(?![\"'])([^\s,;}}\]]+)"
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
    return cleaned[:max_length]
