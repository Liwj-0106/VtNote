"""Local-first library organization, excerpts, and full-text discovery."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from vtnote.models import (
    ItemRecord,
    LibraryCollectionRecord,
    LibraryCollectionTaskRecord,
    LibraryExcerptRecord,
    LibrarySearchDocumentRecord,
    LibrarySearchStateRecord,
    LibraryTagRecord,
    LibraryTaskTagRecord,
    TaskRecord,
)
from vtnote.paths import StoragePaths
from vtnote.schemas import transcript_sha256
from vtnote.tasks import InvalidTaskOperation, TaskService


_HIDDEN_TASK_REASON = "profile_test_sample"
_MAX_SEARCH_RESULTS = 100

_LEGACY_SOURCE_PATTERNS = {
    "bilibili": (
        "https://www.bilibili.com/%",
        "https://bilibili.com/%",
        "https://space.bilibili.com/%",
        "https://b23.tv/%",
    ),
    "douyin": (
        "https://www.douyin.com/%",
        "https://douyin.com/%",
        "https://v.douyin.com/%",
    ),
    "youtube": (
        "https://www.youtube.com/%",
        "https://youtube.com/%",
        "https://youtu.be/%",
    ),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_name(value: str) -> str:
    name = " ".join(value.strip().split())
    if not name or len(name) > 128:
        raise InvalidTaskOperation("library name must contain 1 to 128 characters")
    return name


def _snippet(content: str, query: str, *, width: int = 150) -> str:
    normalized = content.replace("\r", " ").replace("\n", " ").strip()
    if len(normalized) <= width:
        return normalized
    index = normalized.casefold().find(query.casefold())
    if index < 0:
        return normalized[: width - 1].rstrip() + "…"
    start = max(0, index - width // 3)
    end = min(len(normalized), start + width)
    return ("…" if start else "") + normalized[start:end].strip() + (
        "…" if end < len(normalized) else ""
    )


class LibraryService:
    def __init__(
        self,
        session: Session,
        paths: StoragePaths,
        tasks: TaskService,
    ) -> None:
        self.session = session
        self.paths = paths
        self.tasks = tasks

    @staticmethod
    def _entity_view(row: LibraryCollectionRecord | LibraryTagRecord) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def metadata(self) -> dict[str, Any]:
        collections = self.session.scalars(
            select(LibraryCollectionRecord).order_by(
                LibraryCollectionRecord.name.collate("NOCASE")
            )
        ).all()
        tags = self.session.scalars(
            select(LibraryTagRecord).order_by(LibraryTagRecord.name.collate("NOCASE"))
        ).all()
        visible_task_filter = (
            (TaskRecord.terminal_reason_code.is_(None))
            | (TaskRecord.terminal_reason_code != _HIDDEN_TASK_REASON)
        )
        total_count = self.session.scalar(
            select(func.count(TaskRecord.id)).where(visible_task_filter)
        ) or 0
        unclassified_count = self.session.scalar(
            select(func.count(TaskRecord.id)).where(
                visible_task_filter,
                ~TaskRecord.id.in_(select(LibraryCollectionTaskRecord.task_id)),
            )
        ) or 0
        collection_counts = dict(
            self.session.execute(
                select(
                    LibraryCollectionTaskRecord.collection_id,
                    func.count(LibraryCollectionTaskRecord.task_id),
                )
                .join(
                    TaskRecord,
                    TaskRecord.id == LibraryCollectionTaskRecord.task_id,
                )
                .where(visible_task_filter)
                .group_by(LibraryCollectionTaskRecord.collection_id)
            ).all()
        )
        return {
            "collections": [
                {
                    **self._entity_view(row),
                    "task_count": int(collection_counts.get(row.id, 0)),
                }
                for row in collections
            ],
            "tags": [self._entity_view(row) for row in tags],
            "total_count": int(total_count),
            "unclassified_count": int(unclassified_count),
        }

    def create_collection(self, name: str) -> dict[str, Any]:
        return self._create_entity(LibraryCollectionRecord, name)

    def rename_collection(self, entity_id: str, name: str) -> dict[str, Any]:
        row = self.session.get(LibraryCollectionRecord, entity_id)
        if row is None:
            raise KeyError(entity_id)
        row.name = _normalized_name(name)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidTaskOperation("library name already exists") from None
        return self._entity_view(row)

    def create_tag(self, name: str) -> dict[str, Any]:
        return self._create_entity(LibraryTagRecord, name)

    def _create_entity(self, model: type[Any], name: str) -> dict[str, Any]:
        row = model(name=_normalized_name(name))
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidTaskOperation("library name already exists") from None
        return self._entity_view(row)

    def delete_collection(self, entity_id: str) -> None:
        self._delete_entity(LibraryCollectionRecord, entity_id)

    def delete_tag(self, entity_id: str) -> None:
        self._delete_entity(LibraryTagRecord, entity_id)

    def _delete_entity(self, model: type[Any], entity_id: str) -> None:
        row = self.session.get(model, entity_id)
        if row is None:
            raise KeyError(entity_id)
        self.session.delete(row)
        self.session.commit()

    def organize(
        self,
        *,
        task_ids: list[str],
        collection_ids: list[str],
        tag_ids: list[str],
        operation: Literal["add", "remove"],
    ) -> dict[str, Any]:
        if not task_ids or len(task_ids) > 100 or len(set(task_ids)) != len(task_ids):
            raise InvalidTaskOperation("organization requires 1 to 100 unique tasks")
        existing_tasks = set(
            self.session.scalars(
                select(TaskRecord.id).where(TaskRecord.id.in_(task_ids))
            ).all()
        )
        if existing_tasks != set(task_ids):
            raise KeyError("task")
        self._require_entities(LibraryCollectionRecord, collection_ids)
        self._require_entities(LibraryTagRecord, tag_ids)
        if operation == "add":
            for task_id in task_ids:
                for collection_id in collection_ids:
                    self.session.merge(
                        LibraryCollectionTaskRecord(
                            collection_id=collection_id, task_id=task_id
                        )
                    )
                for tag_id in tag_ids:
                    self.session.merge(LibraryTaskTagRecord(tag_id=tag_id, task_id=task_id))
        elif operation == "remove":
            if collection_ids:
                self.session.execute(
                    delete(LibraryCollectionTaskRecord).where(
                        LibraryCollectionTaskRecord.task_id.in_(task_ids),
                        LibraryCollectionTaskRecord.collection_id.in_(collection_ids),
                    )
                )
            if tag_ids:
                self.session.execute(
                    delete(LibraryTaskTagRecord).where(
                        LibraryTaskTagRecord.task_id.in_(task_ids),
                        LibraryTaskTagRecord.tag_id.in_(tag_ids),
                    )
                )
        else:
            raise InvalidTaskOperation("invalid organization operation")
        self.session.commit()
        return {"task_ids": task_ids, "operation": operation}

    def organization_for_task(self, task_id: str) -> dict[str, Any]:
        if self.session.get(TaskRecord, task_id) is None:
            raise KeyError(task_id)
        return self._organization_for_tasks([task_id])[task_id]

    def _require_entities(self, model: type[Any], entity_ids: list[str]) -> None:
        if len(entity_ids) > 100 or len(set(entity_ids)) != len(entity_ids):
            raise InvalidTaskOperation("organization IDs must be unique")
        if not entity_ids:
            return
        existing = set(
            self.session.scalars(select(model.id).where(model.id.in_(entity_ids))).all()
        )
        if existing != set(entity_ids):
            raise KeyError("library entity")

    @staticmethod
    def _excerpt_view(row: LibraryExcerptRecord) -> dict[str, Any]:
        return {
            "id": row.id,
            "item_id": row.item_id,
            "segment_id": row.segment_id,
            "start_ms": row.start_ms,
            "end_ms": row.end_ms,
            "text": row.text_snapshot,
            "note": row.note,
            "stale": False,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def list_excerpts(self, item_id: str) -> list[dict[str, Any]]:
        transcript = self.tasks.get_item_transcript(item_id)
        current_hash = transcript_sha256(transcript)
        rows = self.session.scalars(
            select(LibraryExcerptRecord)
            .where(LibraryExcerptRecord.item_id == item_id)
            .order_by(LibraryExcerptRecord.start_ms, LibraryExcerptRecord.created_at)
        ).all()
        result = []
        for row in rows:
            view = self._excerpt_view(row)
            view["stale"] = row.transcript_sha256 != current_hash
            result.append(view)
        return result

    def create_excerpt(
        self, item_id: str, *, segment_id: str, note: str | None = None
    ) -> dict[str, Any]:
        transcript = self.tasks.get_item_transcript(item_id)
        segment = next((row for row in transcript.segments if row.id == segment_id), None)
        if segment is None:
            raise InvalidTaskOperation("transcript segment does not exist")
        existing = self.session.scalar(
            select(LibraryExcerptRecord).where(
                LibraryExcerptRecord.item_id == item_id,
                LibraryExcerptRecord.segment_id == segment_id,
            )
        )
        if existing is not None:
            return self._excerpt_view(existing)
        normalized_note = self._normalize_note(note)
        row = LibraryExcerptRecord(
            item_id=item_id,
            segment_id=segment.id,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text_snapshot=segment.text,
            note=normalized_note,
            transcript_sha256=transcript_sha256(transcript),
        )
        self.session.add(row)
        self._invalidate_index(item_id)
        self.session.commit()
        return self._excerpt_view(row)

    def update_excerpt(self, excerpt_id: str, *, note: str | None) -> dict[str, Any]:
        row = self.session.get(LibraryExcerptRecord, excerpt_id)
        if row is None:
            raise KeyError(excerpt_id)
        row.note = self._normalize_note(note)
        self._invalidate_index(row.item_id)
        self.session.commit()
        return self._excerpt_view(row)

    def delete_excerpt(self, excerpt_id: str) -> None:
        row = self.session.get(LibraryExcerptRecord, excerpt_id)
        if row is None:
            raise KeyError(excerpt_id)
        item_id = row.item_id
        self.session.delete(row)
        self._invalidate_index(item_id)
        self.session.commit()

    @staticmethod
    def _normalize_note(note: str | None) -> str | None:
        if note is None:
            return None
        normalized = note.strip()
        if len(normalized) > 2_000:
            raise InvalidTaskOperation("excerpt note exceeds 2000 characters")
        return normalized or None

    def _invalidate_index(self, item_id: str) -> None:
        state = self.session.get(LibrarySearchStateRecord, item_id)
        if state is not None:
            self.session.delete(state)

    def _item_fingerprint(self, item: ItemRecord) -> str:
        evidence = [item.updated_at.isoformat()]
        transcript = self.paths.transcript(item.id)
        if transcript.is_file():
            metadata = transcript.stat()
            evidence.append(f"transcript:{metadata.st_mtime_ns}:{metadata.st_size}")
        notes_root = self.paths.durable_item_root(item.id) / "notes"
        if notes_root.is_dir():
            for path in sorted(notes_root.glob("*.md"), key=lambda value: value.name):
                metadata = path.stat()
                evidence.append(f"note:{path.name}:{metadata.st_mtime_ns}:{metadata.st_size}")
        excerpts = self.session.scalars(
            select(LibraryExcerptRecord).where(LibraryExcerptRecord.item_id == item.id)
        ).all()
        evidence.extend(f"excerpt:{row.id}:{row.updated_at.isoformat()}" for row in excerpts)
        return hashlib.sha256("\n".join(evidence).encode("utf-8")).hexdigest()

    def _index_item(self, item: ItemRecord) -> None:
        fingerprint = self._item_fingerprint(item)
        state = self.session.get(LibrarySearchStateRecord, item.id)
        if state is not None and state.fingerprint == fingerprint:
            return
        existing_ids = list(
            self.session.scalars(
                select(LibrarySearchDocumentRecord.id).where(
                    LibrarySearchDocumentRecord.item_id == item.id
                )
            ).all()
        )
        if existing_ids:
            for document_id in existing_ids:
                self.session.execute(
                    text("DELETE FROM library_search_fts WHERE document_id = :id"),
                    {"id": document_id},
                )
            self.session.execute(
                delete(LibrarySearchDocumentRecord).where(
                    LibrarySearchDocumentRecord.id.in_(existing_ids)
                )
            )
        documents: list[LibrarySearchDocumentRecord] = []
        for kind, content in (
            ("title", item.title or item.source_display_name or ""),
            ("source", item.source_locator),
        ):
            if content.strip():
                documents.append(
                    LibrarySearchDocumentRecord(
                        task_id=item.task_id,
                        item_id=item.id,
                        kind=kind,
                        content=content,
                    )
                )
        transcript_path = self.paths.transcript(item.id)
        if transcript_path.is_file():
            try:
                transcript = self.tasks.get_item_transcript(item.id)
            except InvalidTaskOperation:
                transcript = None
            if transcript is not None:
                documents.extend(
                    LibrarySearchDocumentRecord(
                        task_id=item.task_id,
                        item_id=item.id,
                        kind="transcript",
                        segment_id=segment.id,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        content=segment.text,
                    )
                    for segment in transcript.segments
                )
        for note in self.tasks.list_item_notes(item.id):
            markdown = note.get("markdown")
            if isinstance(markdown, str) and markdown.strip():
                documents.append(
                    LibrarySearchDocumentRecord(
                        task_id=item.task_id,
                        item_id=item.id,
                        kind="note",
                        content=markdown,
                    )
                )
        excerpts = self.session.scalars(
            select(LibraryExcerptRecord).where(LibraryExcerptRecord.item_id == item.id)
        ).all()
        for excerpt in excerpts:
            content = "\n".join(
                value for value in (excerpt.text_snapshot, excerpt.note) if value
            )
            documents.append(
                LibrarySearchDocumentRecord(
                    task_id=item.task_id,
                    item_id=item.id,
                    kind="excerpt",
                    segment_id=excerpt.segment_id,
                    start_ms=excerpt.start_ms,
                    end_ms=excerpt.end_ms,
                    content=content,
                )
            )
        self.session.add_all(documents)
        self.session.flush()
        if documents:
            self.session.execute(
                text(
                    "INSERT INTO library_search_fts(document_id, content) "
                    "VALUES (:document_id, :content)"
                ),
                [{"document_id": row.id, "content": row.content} for row in documents],
            )
        if state is None:
            self.session.add(
                LibrarySearchStateRecord(item_id=item.id, fingerprint=fingerprint)
            )
        else:
            state.fingerprint = fingerprint
            state.indexed_at = _utcnow()

    def refresh_index(self) -> None:
        self.session.execute(
            text(
                "DELETE FROM library_search_fts WHERE document_id NOT IN "
                "(SELECT id FROM library_search_documents)"
            )
        )
        items = self.session.scalars(
            select(ItemRecord)
            .join(TaskRecord)
            .where(
                (TaskRecord.terminal_reason_code.is_(None))
                | (TaskRecord.terminal_reason_code != _HIDDEN_TASK_REASON)
            )
        ).all()
        for item in items:
            self._index_item(item)
        self.session.commit()

    def search(
        self,
        *,
        query: str | None = None,
        source: str | None = None,
        status: str | None = None,
        collection_id: str | None = None,
        unclassified: bool = False,
        tag_id: str | None = None,
        excerpts_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= _MAX_SEARCH_RESULTS:
            raise InvalidTaskOperation("invalid library search limit")
        normalized_query = (query or "").strip()
        if len(normalized_query) > 256:
            raise InvalidTaskOperation("library query exceeds 256 characters")
        self.refresh_index()
        candidate = select(TaskRecord.id).where(
            (TaskRecord.terminal_reason_code.is_(None))
            | (TaskRecord.terminal_reason_code != _HIDDEN_TASK_REASON)
        )
        if status:
            public_statuses = {
                "completed": ("completed", "completed_with_warnings"),
                "failed": ("failed", "canceled"),
                "running": (
                    "queued",
                    "running",
                    "waiting_external",
                    "cancel_requested",
                ),
            }
            candidate = candidate.where(
                TaskRecord.status.in_(public_statuses.get(status, (status,)))
            )
        if source:
            source_match = ItemRecord.source_kind == source
            legacy_patterns = _LEGACY_SOURCE_PATTERNS.get(source, ())
            if legacy_patterns:
                normalized_locator = func.lower(ItemRecord.source_locator)
                source_match = or_(
                    source_match,
                    and_(
                        ItemRecord.source_kind == "url",
                        or_(
                            *(normalized_locator.like(pattern) for pattern in legacy_patterns)
                        ),
                    ),
                )
            candidate = candidate.where(
                TaskRecord.id.in_(
                    select(ItemRecord.task_id).where(source_match)
                )
            )
        if collection_id:
            candidate = candidate.where(
                TaskRecord.id.in_(
                    select(LibraryCollectionTaskRecord.task_id).where(
                        LibraryCollectionTaskRecord.collection_id == collection_id
                    )
                )
            )
        if unclassified:
            candidate = candidate.where(
                ~TaskRecord.id.in_(select(LibraryCollectionTaskRecord.task_id))
            )
        if tag_id:
            candidate = candidate.where(
                TaskRecord.id.in_(
                    select(LibraryTaskTagRecord.task_id).where(
                        LibraryTaskTagRecord.tag_id == tag_id
                    )
                )
            )
        if excerpts_only:
            candidate = candidate.where(
                TaskRecord.id.in_(
                    select(ItemRecord.task_id)
                    .join(LibraryExcerptRecord, LibraryExcerptRecord.item_id == ItemRecord.id)
                )
            )
        candidate_ids = set(self.session.scalars(candidate).all())
        matches: dict[str, dict[str, Any]] = {}
        if normalized_query and candidate_ids:
            rows = None
            if len(normalized_query) >= 3:
                try:
                    phrase = '"' + normalized_query.replace('"', '""') + '"'
                    rows = self.session.execute(
                        text(
                            """SELECT d.task_id, d.item_id, d.kind, d.segment_id,
                            d.start_ms, d.end_ms, d.content
                            FROM library_search_fts AS f
                            JOIN library_search_documents AS d ON d.id = f.document_id
                            WHERE library_search_fts MATCH :query
                            ORDER BY bm25(library_search_fts)
                            LIMIT 1000"""
                        ),
                        {"query": phrase},
                    ).mappings()
                except OperationalError:
                    rows = None
            if rows is None:
                rows = self.session.execute(
                    select(
                        LibrarySearchDocumentRecord.task_id,
                        LibrarySearchDocumentRecord.item_id,
                        LibrarySearchDocumentRecord.kind,
                        LibrarySearchDocumentRecord.segment_id,
                        LibrarySearchDocumentRecord.start_ms,
                        LibrarySearchDocumentRecord.end_ms,
                        LibrarySearchDocumentRecord.content,
                    ).where(
                        func.lower(LibrarySearchDocumentRecord.content).like(
                            f"%{normalized_query.casefold()}%"
                        )
                    )
                ).mappings()
            for row in rows:
                task_id = str(row["task_id"])
                if task_id not in candidate_ids or task_id in matches:
                    continue
                matches[task_id] = {
                    "kind": row["kind"],
                    "item_id": row["item_id"],
                    "segment_id": row["segment_id"],
                    "start_ms": row["start_ms"],
                    "end_ms": row["end_ms"],
                    "snippet": _snippet(str(row["content"]), normalized_query),
                }
            candidate_ids &= set(matches)
        ordered_ids = self.session.scalars(
            select(TaskRecord.id)
            .where(TaskRecord.id.in_(candidate_ids))
            .order_by(TaskRecord.updated_at.desc(), TaskRecord.id.desc())
            .limit(limit)
        ).all()
        organization = self._organization_for_tasks(list(ordered_ids))
        return [
            {
                "task": self.tasks.get_task(task_id).model_dump(mode="json"),
                "match": matches.get(task_id),
                **organization.get(task_id, {"collections": [], "tags": []}),
            }
            for task_id in ordered_ids
        ]

    def _organization_for_tasks(self, task_ids: list[str]) -> dict[str, dict[str, Any]]:
        result = {
            task_id: {"collections": [], "tags": []} for task_id in task_ids
        }
        if not task_ids:
            return result
        collection_rows = self.session.execute(
            select(
                LibraryCollectionTaskRecord.task_id,
                LibraryCollectionRecord.id,
                LibraryCollectionRecord.name,
            )
            .join(
                LibraryCollectionRecord,
                LibraryCollectionRecord.id == LibraryCollectionTaskRecord.collection_id,
            )
            .where(LibraryCollectionTaskRecord.task_id.in_(task_ids))
        ).all()
        for task_id, entity_id, name in collection_rows:
            result[task_id]["collections"].append({"id": entity_id, "name": name})
        tag_rows = self.session.execute(
            select(
                LibraryTaskTagRecord.task_id,
                LibraryTagRecord.id,
                LibraryTagRecord.name,
            )
            .join(LibraryTagRecord, LibraryTagRecord.id == LibraryTaskTagRecord.tag_id)
            .where(LibraryTaskTagRecord.task_id.in_(task_ids))
        ).all()
        for task_id, entity_id, name in tag_rows:
            result[task_id]["tags"].append({"id": entity_id, "name": name})
        return result
