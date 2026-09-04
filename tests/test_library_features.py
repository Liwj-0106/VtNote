from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from sqlalchemy.orm import Session

from vtnote.database import initialize_database
from vtnote.export_files import ExportDirectoryService, ExportFileService
from vtnote.exports import ExportFormat
from vtnote.library import LibraryService
from vtnote.models import ItemRecord, TaskRecord
from vtnote.paths import StoragePaths
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    canonical_transcript_bytes,
)
from vtnote.source_probing import SourceProbeService
from vtnote.sources import SourceProbeResult
from vtnote.url_security import SourceUrlPolicy, extract_supported_source_urls


class _ItemView:
    def __init__(self, item_id: str, title: str) -> None:
        self.id = item_id
        self.title = title
        self.source_display_name = title


class _View:
    def __init__(self, task_id: str, item_id: str, title: str) -> None:
        self.task_id = task_id
        self.items = (_ItemView(item_id, title),)

    def model_dump(self, *, mode: str):
        assert mode == "json"
        return {"id": self.task_id, "items": []}


class _Tasks:
    def __init__(self, paths: StoragePaths, item_id: str, title: str) -> None:
        self.paths = paths
        self.item_id = item_id
        self.title = title

    def get_item_transcript(self, item_id: str) -> Transcript:
        return Transcript.model_validate_json(self.paths.transcript(item_id).read_bytes())

    def list_item_notes(self, item_id: str):
        if item_id != self.item_id:
            return []
        return [{"id": "note-1", "markdown": "# 总结\n\n关键结论。\n"}]

    def get_task(self, task_id: str) -> _View:
        return _View(task_id, self.item_id, self.title)

    def export_item(self, item_id: str, **_: object) -> str:
        return "exported transcript\n"


def _library(tmp_path: Path):
    paths = StoragePaths(tmp_path / "data", tmp_path / "cache")
    engine = initialize_database(paths.database)
    session = Session(engine)
    task = TaskRecord(status="completed", options={}, pipeline_snapshot_json={})
    item = ItemRecord(
        position=0,
        source_kind="youtube",
        source_locator="https://youtu.be/example",
        source_display_name="Agent 课程",
        title="Agent 课程",
        status="completed",
    )
    task.items.append(item)
    session.add(task)
    session.commit()
    transcript = Transcript(
        language="zh-Hans",
        duration_ms=3_000,
        provenance=Provenance(
            method=ProvenanceMethod.LOCAL_ASR,
            provider="faster-whisper",
        ),
        segments=(
            TranscriptSegment(
                id="seg_000001",
                start_ms=0,
                end_ms=3_000,
                text="秋招 Agent 实战经验",
            ),
        ),
    )
    transcript_path = paths.transcript(item.id)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_bytes(canonical_transcript_bytes(transcript))
    tasks = _Tasks(paths, item.id, item.title or item.source_display_name or "result")
    return session, paths, task, item, tasks


def test_library_search_organization_and_timestamp_excerpt(tmp_path: Path) -> None:
    session, paths, task, item, tasks = _library(tmp_path)
    try:
        library = LibraryService(session, paths, tasks)  # type: ignore[arg-type]
        collection = library.create_collection("求职")
        tag = library.create_tag("Agent")
        library.organize(
            task_ids=[task.id],
            collection_ids=[collection["id"]],
            tag_ids=[tag["id"]],
            operation="add",
        )
        excerpt = library.create_excerpt(
            item.id, segment_id="seg_000001", note="重点复习"
        )

        results = library.search(query="秋招", collection_id=collection["id"])
        assert [row["task"]["id"] for row in results] == [task.id]
        assert results[0]["match"]["segment_id"] == "seg_000001"
        assert results[0]["collections"] == [{"id": collection["id"], "name": "求职"}]
        assert results[0]["tags"] == [{"id": tag["id"], "name": "Agent"}]
        assert library.organization_for_task(task.id) == {
            "collections": [{"id": collection["id"], "name": "求职"}],
            "tags": [{"id": tag["id"], "name": "Agent"}],
        }

        note_results = library.search(query="重点复习", excerpts_only=True)
        assert note_results[0]["match"]["kind"] == "excerpt"
        assert library.list_excerpts(item.id)[0]["id"] == excerpt["id"]
    finally:
        session.close()


def test_library_public_status_filters_group_internal_states(tmp_path: Path) -> None:
    session, paths, task, _, tasks = _library(tmp_path)
    try:
        library = LibraryService(session, paths, tasks)  # type: ignore[arg-type]
        for internal_status, public_status in (
            ("completed_with_warnings", "completed"),
            ("canceled", "failed"),
            ("queued", "running"),
        ):
            task.status = internal_status
            session.commit()
            results = library.search(status=public_status)
            assert [row["task"]["id"] for row in results] == [task.id]
    finally:
        session.close()


def test_library_source_filter_recognizes_legacy_platform_urls(tmp_path: Path) -> None:
    session, paths, task, item, tasks = _library(tmp_path)
    try:
        item.source_kind = "url"
        item.source_locator = "https://www.bilibili.com/video/BV1xx411c7mD"
        session.commit()

        results = LibraryService(session, paths, tasks).search(source="bilibili")  # type: ignore[arg-type]

        assert [row["task"]["id"] for row in results] == [task.id]
        assert LibraryService(session, paths, tasks).search(source="youtube") == []  # type: ignore[arg-type]
    finally:
        session.close()


def test_collection_metadata_counts_rename_and_unclassified_filter(tmp_path: Path) -> None:
    session, paths, task, _, tasks = _library(tmp_path)
    try:
        library = LibraryService(session, paths, tasks)  # type: ignore[arg-type]
        initial = library.metadata()
        assert initial["total_count"] == 1
        assert initial["unclassified_count"] == 1

        collection = library.create_collection("租房")
        library.organize(
            task_ids=[task.id],
            collection_ids=[collection["id"]],
            tag_ids=[],
            operation="add",
        )
        organized = library.metadata()
        assert organized["unclassified_count"] == 0
        assert organized["collections"][0]["task_count"] == 1
        assert library.search(unclassified=True) == []

        renamed = library.rename_collection(collection["id"], "居住指南")
        assert renamed["name"] == "居住指南"

        library.organize(
            task_ids=[task.id],
            collection_ids=[collection["id"]],
            tag_ids=[],
            operation="remove",
        )
        assert [row["task"]["id"] for row in library.search(unclassified=True)] == [
            task.id
        ]
    finally:
        session.close()


def test_export_directory_and_collision_safe_transcript_save(tmp_path: Path) -> None:
    session, paths, _, item, tasks = _library(tmp_path)
    try:
        destination = tmp_path / "exports"
        destination.mkdir()
        settings = ExportDirectoryService(session)
        assert settings.get()["default_directory"].endswith("VtNote\\exports")
        view = settings.update(str(destination))
        assert view["directory"] == str(destination.resolve())
        service = ExportFileService(
            session=session,
            paths=paths,
            tasks=tasks,  # type: ignore[arg-type]
        )
        first = service.save(
            item.id,
            items=["transcript"],
            audio_format="m4a",
            transcript_format="txt",
            note_format="markdown",
        )
        second = service.save(
            item.id,
            items=["transcript"],
            audio_format="m4a",
            transcript_format="txt",
            note_format="markdown",
        )
        assert first["files"][0]["filename"] == "Agent 课程-transcript.txt"
        assert second["files"][0]["filename"] == "Agent 课程-transcript (2).txt"
        advanced = service.save_text_export(
            item.id,
            variant="original",
            export_format=ExportFormat.MARKDOWN,
        )
        assert advanced["directory"] == str(destination.resolve())
        assert advanced["files"][0]["filename"] == "Agent 课程-transcript.md"
    finally:
        session.close()


def test_batch_export_supports_markdown_and_zip_variants(tmp_path: Path) -> None:
    session, paths, task, item, tasks = _library(tmp_path)
    try:
        destination = tmp_path / "exports"
        destination.mkdir()
        ExportDirectoryService(session).update(str(destination))
        service = ExportFileService(
            session=session,
            paths=paths,
            tasks=tasks,  # type: ignore[arg-type]
        )

        markdown = service.save_batch([task.id], mode="summary_markdown")
        assert markdown["files"] == [
            {"kind": "notes", "filename": "Agent 课程-notes.md"}
        ]
        assert (destination / "Agent 课程-notes.md").read_text(encoding="utf-8") == (
            "# 总结\n\n关键结论。\n"
        )

        original = service.save_batch([task.id], mode="original_markdown")
        assert original["files"] == [
            {"kind": "transcript", "filename": "Agent 课程-transcript.md"}
        ]
        assert (destination / "Agent 课程-transcript.md").read_text(
            encoding="utf-8"
        ) == "exported transcript\n"

        notes_zip = service.save_batch([task.id], mode="zip_notes")
        with ZipFile(destination / notes_zip["files"][0]["filename"]) as archive:
            assert archive.namelist() == ["Agent 课程-notes.md"]

        full_zip = service.save_batch([task.id], mode="zip_all")
        with ZipFile(destination / full_zip["files"][0]["filename"]) as archive:
            assert archive.namelist() == [
                "Agent 课程-notes.md",
                "Agent 课程-transcript.md",
            ]
    finally:
        session.close()


class _Resolver:
    def resolve(self, host: str) -> list[str]:
        raise AssertionError("proxy-mode test must not resolve DNS")


class _SourceAdapter:
    def probe(self, canonical_source: str) -> SourceProbeResult:
        source_kind = "youtube" if "youtu" in canonical_source else "douyin"
        return SourceProbeResult(
            source_kind=source_kind,
            canonical_url=canonical_source,
            title=f"{source_kind} item",
            duration_ms=1_000,
            author="公开作者",
            published_at="2024-01-01",
            thumbnail_url="https://i.ytimg.com/vi/public/hqdefault.jpg",
            description="公开视频简介",
        )


def test_source_probe_exposes_public_video_metadata() -> None:
    service = SourceProbeService(
        SourceUrlPolicy(_Resolver(), resolve_dns=False),
        _SourceAdapter(),  # type: ignore[arg-type]
    )

    result = service.probe("https://youtu.be/abc")

    assert result["author"] == "公开作者"
    assert result["published_at"] == "2024-01-01"
    assert result["thumbnail_url"] == "https://i.ytimg.com/vi/public/hqdefault.jpg"
    assert result["description"] == "公开视频简介"


def test_share_text_batch_preserves_order_and_marks_duplicates() -> None:
    source_text = (
        "第一个 https://youtu.be/abc 然后 https://v.douyin.com/xyz/ "
        "重复 https://youtu.be/abc"
    )
    assert extract_supported_source_urls(source_text) == [
        "https://youtu.be/abc",
        "https://v.douyin.com/xyz/",
        "https://youtu.be/abc",
    ]
    service = SourceProbeService(
        SourceUrlPolicy(_Resolver(), resolve_dns=False),
        _SourceAdapter(),  # type: ignore[arg-type]
    )
    result = service.probe_batch(source_text)
    assert [row["status"] for row in result["results"]] == [
        "ready",
        "ready",
        "duplicate",
    ]
    assert len(result["valid_sources"]) == 2
