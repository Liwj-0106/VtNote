"""Atomic writes for compact application-owned text artifacts."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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


def _atomic_write(destination: Path, data: bytes, staging_dir: Path, *, immutable: bool) -> Path:
    destination = Path(destination)
    staging_dir = Path(staging_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    if os.stat(destination.parent).st_dev != os.stat(staging_dir).st_dev:
        raise AtomicWriteError("staging and destination must be on the same filesystem")

    staged = _staged_file(data, staging_dir)
    try:
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


def atomic_write_text(destination: Path, text: str, staging_dir: Path) -> Path:
    return _atomic_write(destination, text.encode("utf-8"), staging_dir, immutable=False)


def write_transcript_json(destination: Path, transcript: Transcript, staging_dir: Path) -> Path:
    """Write the source transcript once; an existing target is never replaced."""

    return _atomic_write(
        destination,
        canonical_transcript_bytes(transcript),
        staging_dir,
        immutable=True,
    )


def write_translation_json(
    destination: Path, translation: Translation, staging_dir: Path
) -> Path:
    """Atomically create or replace a generated translation artifact."""

    return _atomic_write(
        destination,
        canonical_translation_bytes(translation),
        staging_dir,
        immutable=False,
    )
