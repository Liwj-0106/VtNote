"""Bounded reads and metadata parsing for durable result artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vtnote.application.task_contracts import InvalidTaskOperation


_MAX_RESULT_ARTIFACT_BYTES = 32 * 1024 * 1024
_NOTE_METADATA_KEYS = frozenset(
    {
        "generated_by_ai",
        "template",
        "output_language",
        "requested_model",
        "response_model",
    }
)


def read_result_artifact(path: Path) -> bytes:
    if not path.is_file():
        raise InvalidTaskOperation("result artifact is not available")
    try:
        if path.stat().st_size > _MAX_RESULT_ARTIFACT_BYTES:
            raise InvalidTaskOperation("result artifact exceeds the read limit")
        return path.read_bytes()
    except OSError as error:
        raise InvalidTaskOperation("result artifact could not be read") from error


def parse_note_metadata(markdown: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    lines = markdown.splitlines()
    if not lines or lines[0] != "---":
        return metadata
    for line in lines[1:]:
        if line == "---":
            break
        key, separator, value = line.partition(":")
        normalized_key = key.strip()
        if separator and normalized_key in _NOTE_METADATA_KEYS:
            normalized_value = value.strip()
            metadata[normalized_key] = (
                normalized_value.casefold() == "true"
                if normalized_key == "generated_by_ai"
                else normalized_value
            )
    return metadata
