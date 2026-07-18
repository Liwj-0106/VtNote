"""Atomic writes for compact application-owned text artifacts.

All public writes are checked against configured roots and reject existing
symlink/reparse-point ancestors immediately before the final filesystem call.
This narrows accidental path substitution; it is not a sandbox against a
privileged same-machine process racing the check and write.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from vtnote.paths import StoragePaths
from vtnote.schemas import (
    Transcript,
    Translation,
    canonical_transcript_bytes,
    canonical_translation_bytes,
)


class ArtifactExistsError(FileExistsError):
    """Raised when code tries to replace an immutable artifact."""


class AtomicWriteError(OSError):
    """Raised when an atomic rename/link cannot be guaranteed."""


def _staged_file(data: bytes, staging_dir: Path) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="vtnote-",
        suffix=".tmp",
        dir=staging_dir,
        delete=False,
    ) as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _atomic_write(
    paths: StoragePaths, destination: Path, data: bytes, *, immutable: bool
) -> Path:
    destination = Path(destination)
    paths.ensure_roots()
    paths.assert_durable_destination(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    paths.assert_durable_destination(destination)

    staging_dir = paths.runtime("staging")
    paths.assert_runtime_destination(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    paths.assert_runtime_destination(staging_dir)
    if os.stat(destination.parent).st_dev != os.stat(staging_dir).st_dev:
        raise AtomicWriteError("staging and destination must be on the same filesystem")

    staged = _staged_file(data, staging_dir)
    try:
        paths.assert_runtime_destination(staged)
        paths.assert_durable_destination(destination)
        if immutable:
            try:
                os.link(staged, destination)
            except FileExistsError as error:
                raise ArtifactExistsError(str(destination)) from error
        else:
            os.replace(staged, destination)
        return destination
    finally:
        if staged.exists():
            staged.unlink()


def write_note_markdown(
    paths: StoragePaths, item_id: str, note_id: str, markdown: str
) -> Path:
    """Atomically create or replace a note at its typed durable path."""

    return _atomic_write(
        paths,
        paths.note(item_id, note_id),
        markdown.encode("utf-8"),
        immutable=False,
    )


def write_transcript_json(
    paths: StoragePaths, item_id: str, transcript: Transcript
) -> Path:
    """Write the source transcript once; an existing target is never replaced."""

    return _atomic_write(
        paths,
        paths.transcript(item_id),
        canonical_transcript_bytes(transcript),
        immutable=True,
    )


def write_translation_json(
    paths: StoragePaths,
    item_id: str,
    translation: Translation,
    source_transcript: Transcript,
) -> Path:
    """Atomically create or replace a generated translation artifact."""

    translation.validate_against(source_transcript)
    return _atomic_write(
        paths,
        paths.translation(item_id, translation.language),
        canonical_translation_bytes(translation),
        immutable=False,
    )
