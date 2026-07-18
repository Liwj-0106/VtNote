from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vtnote.artifacts import (
    ArtifactExistsError,
    atomic_write_text,
    write_transcript_json,
    write_translation_json,
)
from vtnote.config import Settings
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    Translation,
    TranslationEntry,
    transcript_sha256,
)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path / "data",
        runtime_cache_root=tmp_path / "cache",
    )


def make_transcript(text: str) -> Transcript:
    return Transcript(
        language="en",
        duration_ms=1_000,
        provenance=Provenance(
            method=ProvenanceMethod.PLATFORM_SUBTITLE,
            provider="manual_upload",
            model=None,
        ),
        segments=[TranscriptSegment(id="cue-1", start_ms=0, end_ms=1_000, text=text)],
    )


def test_settings_reject_relative_or_overlapping_storage_roots(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="absolute"):
        Settings(data_root=Path("relative"), runtime_cache_root=tmp_path / "cache")

    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(data_root=tmp_path, runtime_cache_root=tmp_path / "cache")


def test_settings_cannot_bind_outside_loopback(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
            bind_host="0.0.0.0",
        )


def test_storage_paths_reject_escape_and_absolute_components(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))

    with pytest.raises(UnsafePathError):
        paths.durable("..", "outside.txt")
    with pytest.raises(UnsafePathError):
        paths.runtime(tmp_path / "absolute.txt")


def test_storage_paths_create_only_owned_roots(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    paths = StoragePaths.from_settings(settings)

    paths.ensure_roots()

    assert settings.data_root.is_dir()
    assert settings.runtime_cache_root.is_dir()
    assert paths.database == settings.data_root / "vtnote.db"


def test_transcript_write_is_immutable_and_preserves_first_content(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    paths.ensure_roots()
    destination = paths.durable("items", "item-1", "transcript.json")

    write_transcript_json(destination, make_transcript("first"), paths.runtime("staging"))

    with pytest.raises(ArtifactExistsError):
        write_transcript_json(destination, make_transcript("replacement"), paths.runtime("staging"))

    stored = json.loads(destination.read_text(encoding="utf-8"))
    assert stored["segments"][0]["text"] == "first"


def test_atomic_text_write_replaces_generated_artifacts(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    paths.ensure_roots()
    destination = paths.durable("items", "item-1", "export.srt")

    atomic_write_text(destination, "first", paths.runtime("staging"))
    atomic_write_text(destination, "second", paths.runtime("staging"))

    assert destination.read_text(encoding="utf-8") == "second"


def test_translation_artifact_is_bound_to_the_source_hash_and_replaceable(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    paths.ensure_roots()
    source = make_transcript("source")
    destination = paths.durable("items", "item-1", "translation.zh-CN.json")
    first = Translation(
        language="zh-CN",
        source_transcript_sha256=transcript_sha256(source),
        entries=[TranslationEntry(cue_id="cue-1", text="第一版")],
    )
    replacement = first.model_copy(
        update={"entries": (TranslationEntry(cue_id="cue-1", text="第二版"),)}
    )

    write_translation_json(destination, first, paths.runtime("staging"))
    write_translation_json(destination, replacement, paths.runtime("staging"))

    stored = json.loads(destination.read_text(encoding="utf-8"))
    assert stored["source_transcript_sha256"] == transcript_sha256(source)
    assert stored["entries"] == [{"cue_id": "cue-1", "text": "第二版"}]
