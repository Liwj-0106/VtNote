from __future__ import annotations

import json
from pathlib import Path

import pytest

from vtnote.artifacts import write_transcript_json, write_translation_json
from vtnote.config import Settings
from vtnote.exports import ExportFormat, render_export, render_export_from_json
from vtnote.paths import StoragePaths
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    Translation,
    TranslationEntry,
    transcript_sha256,
)
from vtnote.subtitles import parse_srt


ITEM_ID = "11111111-1111-4111-8111-111111111111"


def make_paths(tmp_path: Path) -> StoragePaths:
    return StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )


def make_source() -> Transcript:
    return Transcript(
        language="en",
        duration_ms=2_500,
        provenance=Provenance(
            method=ProvenanceMethod.PLATFORM_SUBTITLE,
            provider="youtube",
            model=None,
        ),
        segments=[
            TranscriptSegment(id="seg_000001", start_ms=250, end_ms=1_000, text="One"),
            TranscriptSegment(id="seg_000002", start_ms=1_500, end_ms=2_500, text="Two"),
        ],
    )


def make_translation(source: Transcript) -> Translation:
    return Translation(
        language="zh-Hans",
        source_transcript_sha256=transcript_sha256(source),
        entries=[
            TranslationEntry(cue_id="seg_000001", text="一"),
            TranslationEntry(cue_id="seg_000002", text="二"),
        ],
    )


def test_exports_regenerate_deterministically_from_immutable_json_artifacts(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    source = make_source()
    translation = make_translation(source)
    write_transcript_json(paths, ITEM_ID, source)
    write_translation_json(paths, ITEM_ID, translation, source)
    source_json = paths.transcript(ITEM_ID).read_bytes()
    translation_json = paths.translation(ITEM_ID, translation.language).read_bytes()

    for export_format in ExportFormat:
        first = render_export_from_json(source_json, export_format, translation_json)
        second = render_export_from_json(source_json, export_format, translation_json)
        assert second == first

    translated_srt = parse_srt(
        render_export_from_json(source_json, ExportFormat.SRT, translation_json)
    )
    translated_cues = [
        (segment.id, segment.start_ms, segment.end_ms, segment.text)
        for segment in translated_srt
    ]
    assert translated_cues == [
        ("seg_000001", 250, 1_000, "一"),
        ("seg_000002", 1_500, 2_500, "二"),
    ]

    translated_json = json.loads(
        render_export_from_json(source_json, ExportFormat.JSON, translation_json)
    )
    assert translated_json["source_transcript_sha256"] == transcript_sha256(source)
    assert translated_json["segments"] == [
        {"id": "seg_000001", "start_ms": 250, "end_ms": 1_000, "text": "一"},
        {"id": "seg_000002", "start_ms": 1_500, "end_ms": 2_500, "text": "二"},
    ]

    durable_files = {path for path in paths.data_root.rglob("*") if path.is_file()}
    assert durable_files == {
        paths.transcript(ITEM_ID),
        paths.translation(ITEM_ID, translation.language),
    }


def test_export_dispatcher_rejects_translation_for_another_source() -> None:
    source = make_source()
    wrong_translation = make_translation(source).model_copy(
        update={"source_transcript_sha256": "0" * 64}
    )

    with pytest.raises(ValueError, match="hash"):
        render_export(source, ExportFormat.VTT, wrong_translation)


def test_source_json_export_is_the_canonical_transcript_json() -> None:
    source = make_source()

    exported = json.loads(render_export(source, ExportFormat.JSON))

    assert exported["provenance"] == {
        "method": "platform_subtitle",
        "provider": "youtube",
        "model": None,
    }
    assert [segment["id"] for segment in exported["segments"]] == [
        "seg_000001",
        "seg_000002",
    ]
