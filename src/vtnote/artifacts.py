"""Atomic writes for compact application-owned text artifacts.

All public writes are checked against configured roots and reject existing
symlink/reparse-point ancestors immediately before the final filesystem call.
This narrows accidental path substitution; it is not a sandbox against a
privileged same-machine process racing the check and write.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from vtnote.paths import StoragePaths
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    Translation,
    canonical_transcript_bytes,
    canonical_translation_bytes,
)
from vtnote.subtitles import parse_ass, parse_srt, parse_vtt


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


def _ensure_immutable(paths: StoragePaths, destination: Path, data: bytes) -> Path:
    try:
        return _atomic_write(paths, destination, data, immutable=True)
    except ArtifactExistsError:
        paths.assert_durable_destination(destination)
        if destination.is_file() and destination.read_bytes() == data:
            return destination
        raise


def validate_source_subtitle(extension: str, data: bytes) -> None:
    """Validate source bytes without writing a durable artifact."""

    if not parse_source_subtitle(extension, data):
        raise ValueError("source subtitle contains no cues")


def parse_source_subtitle(
    extension: str,
    data: bytes,
) -> tuple[TranscriptSegment, ...]:
    """Parse validated source bytes into canonical, chronological segments."""

    normalized = extension.casefold()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("source subtitle must be UTF-8 text") from error
    if normalized == "srt":
        cues = parse_srt(text)
    elif normalized == "vtt":
        cues = parse_vtt(text)
    elif normalized == "ass":
        cues = parse_ass(text)
    elif normalized == "json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("source subtitle JSON is invalid") from error
        cues = _json_segments(payload)
    else:
        raise ValueError("unsupported source subtitle format")
    return tuple(cues)


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _valid_json_cue(cue: object, *, bili: bool) -> bool:
    if not isinstance(cue, dict):
        return False
    if bili:
        start, end, text = cue.get("from"), cue.get("to"), cue.get("content")
    else:
        start, end, text = cue.get("start_ms"), cue.get("end_ms"), cue.get("text")
    return (
        _finite_number(start)
        and _finite_number(end)
        and 0 <= start < end
        and isinstance(text, str)
        and bool(text.strip())
    )


def _validated_json_cues(payload: object) -> list[object]:
    if not isinstance(payload, dict):
        return []
    for key, bili in (("body", True), ("segments", False)):
        cues = payload.get(key)
        if isinstance(cues, list) and cues and all(
            _valid_json_cue(cue, bili=bili) for cue in cues
        ):
            return cues
    return []


def _json_segments(payload: object) -> list[TranscriptSegment]:
    cues = _validated_json_cues(payload)
    if not cues:
        return []
    bili = isinstance(payload, dict) and payload.get("body") is cues
    segments: list[TranscriptSegment] = []
    for index, raw in enumerate(cues, start=1):
        assert isinstance(raw, dict)
        if bili:
            start_ms = round(float(raw["from"]) * 1000)
            end_ms = round(float(raw["to"]) * 1000)
            text = str(raw["content"]).strip()
        else:
            start_ms = round(float(raw["start_ms"]))
            end_ms = round(float(raw["end_ms"]))
            text = str(raw["text"]).strip()
        segments.append(
            TranscriptSegment(
                id=f"seg_{index:06d}",
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        )
    return segments


def transcript_from_source_subtitle(
    extension: str,
    data: bytes,
    *,
    language: str,
    method: ProvenanceMethod,
    provider: str,
    duration_ms: int | None = None,
) -> Transcript:
    """Build the sole canonical transcript representation from source bytes."""

    segments = parse_source_subtitle(extension, data)
    if not segments:
        raise ValueError("source subtitle contains no cues")
    covered_duration = max(segment.end_ms for segment in segments)
    return Transcript(
        language=language,
        duration_ms=max(covered_duration, duration_ms or 0),
        provenance=Provenance(
            method=method,
            provider=provider,
            model=None,
        ),
        segments=segments,
    )


def transcript_from_timed_text(
    cues: tuple[tuple[int, int, str], ...],
    *,
    language: str,
    duration_ms: int,
    method: ProvenanceMethod,
    provider: str,
    model: str,
) -> Transcript:
    """Build a canonical transcript from already validated ASR sentence cues."""

    if not cues:
        raise ValueError("ASR result contains no timed text")
    segments: list[TranscriptSegment] = []
    for index, (start_ms, end_ms, text) in enumerate(cues, start=1):
        segments.append(
            TranscriptSegment(
                id=f"seg_{index:06d}",
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        )
    return Transcript(
        language=language,
        duration_ms=max(duration_ms, max(segment.end_ms for segment in segments)),
        provenance=Provenance(
            method=method,
            provider=provider,
            model=model,
        ),
        segments=tuple(segments),
    )


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

    return ensure_transcript_json(paths, item_id, transcript)


def ensure_transcript_json(
    paths: StoragePaths, item_id: str, transcript: Transcript
) -> Path:
    """Create the immutable transcript or accept identical recovery content."""

    return _ensure_immutable(
        paths, paths.transcript(item_id), canonical_transcript_bytes(transcript)
    )


def ensure_transcription_recovery(
    paths: StoragePaths,
    item_id: str,
    stage_run_id: str,
    transcript: Transcript,
) -> Path:
    """Persist the normalized ASR result before its final publication boundary."""

    return _ensure_immutable(
        paths,
        paths.transcription_recovery(item_id, stage_run_id),
        canonical_transcript_bytes(transcript),
    )


def write_source_original(
    paths: StoragePaths, item_id: str, extension: str, source_bytes: bytes
) -> Path:
    """Persist a validated real source subtitle without ever replacing it."""

    destination = paths.source_original(item_id, extension)
    validate_source_subtitle(extension, source_bytes)
    source_directory = destination.parent
    paths.assert_durable_destination(source_directory)
    if source_directory.is_dir() and any(
        candidate.name.startswith("original.") and candidate != destination
        for candidate in source_directory.iterdir()
    ):
        raise ArtifactExistsError(str(source_directory))
    return _ensure_immutable(paths, destination, source_bytes)


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
