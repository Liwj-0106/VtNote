"""HTTP boundary for local library organization and discovery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from vtnote.library import LibraryService
from vtnote.paths import StoragePaths
from vtnote.tasks import TaskService


LibraryServices = Callable[[], tuple[Session, object, TaskService]]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NameInput(_StrictModel):
    name: str = Field(min_length=1, max_length=128)


class OrganizeInput(_StrictModel):
    task_ids: list[str] = Field(min_length=1, max_length=100)
    collection_ids: list[str] = Field(default_factory=list, max_length=100)
    tag_ids: list[str] = Field(default_factory=list, max_length=100)
    operation: Literal["add", "remove"]


class ExcerptCreateInput(_StrictModel):
    segment_id: str = Field(pattern=r"^seg_\d{6}$")
    note: str | None = Field(default=None, max_length=2_000)


class ExcerptPatchInput(_StrictModel):
    note: str | None = Field(default=None, max_length=2_000)


def register_library_routes(
    app: FastAPI,
    *,
    services: LibraryServices,
    paths: StoragePaths,
) -> None:
    def library() -> tuple[Session, LibraryService]:
        session, _, tasks = services()
        return session, LibraryService(session, paths, tasks)

    @app.get("/api/library/meta")
    def library_metadata():
        session, service = library()
        try:
            return service.metadata()
        finally:
            session.close()

    @app.post("/api/library/collections", status_code=201)
    def create_collection(payload: NameInput):
        session, service = library()
        try:
            return service.create_collection(payload.name)
        finally:
            session.close()

    @app.patch("/api/library/collections/{collection_id}")
    def rename_collection(collection_id: str, payload: NameInput):
        session, service = library()
        try:
            return service.rename_collection(collection_id, payload.name)
        finally:
            session.close()

    @app.delete("/api/library/collections/{collection_id}", status_code=204)
    def delete_collection(collection_id: str):
        session, service = library()
        try:
            service.delete_collection(collection_id)
            return Response(status_code=204)
        finally:
            session.close()

    @app.post("/api/library/tags", status_code=201)
    def create_tag(payload: NameInput):
        session, service = library()
        try:
            return service.create_tag(payload.name)
        finally:
            session.close()

    @app.delete("/api/library/tags/{tag_id}", status_code=204)
    def delete_tag(tag_id: str):
        session, service = library()
        try:
            service.delete_tag(tag_id)
            return Response(status_code=204)
        finally:
            session.close()

    @app.post("/api/library/organize")
    def organize(payload: OrganizeInput):
        session, service = library()
        try:
            return service.organize(**payload.model_dump())
        finally:
            session.close()

    @app.get("/api/library/tasks/{task_id}")
    def task_organization(task_id: str):
        session, service = library()
        try:
            return service.organization_for_task(task_id)
        finally:
            session.close()

    @app.get("/api/library/search")
    def search_library(
        q: str | None = Query(default=None, max_length=256),
        source: str | None = Query(default=None, max_length=32),
        status: str | None = Query(default=None, max_length=32),
        collection_id: str | None = None,
        unclassified: bool = False,
        tag_id: str | None = None,
        excerpts_only: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        session, service = library()
        try:
            return service.search(
                query=q,
                source=source,
                status=status,
                collection_id=collection_id,
                unclassified=unclassified,
                tag_id=tag_id,
                excerpts_only=excerpts_only,
                limit=limit,
            )
        finally:
            session.close()

    @app.get("/api/items/{item_id}/excerpts")
    def list_excerpts(item_id: str):
        session, service = library()
        try:
            return service.list_excerpts(item_id)
        finally:
            session.close()

    @app.post("/api/items/{item_id}/excerpts", status_code=201)
    def create_excerpt(item_id: str, payload: ExcerptCreateInput):
        session, service = library()
        try:
            return service.create_excerpt(item_id, **payload.model_dump())
        finally:
            session.close()

    @app.patch("/api/excerpts/{excerpt_id}")
    def update_excerpt(excerpt_id: str, payload: ExcerptPatchInput):
        session, service = library()
        try:
            return service.update_excerpt(excerpt_id, note=payload.note)
        finally:
            session.close()

    @app.delete("/api/excerpts/{excerpt_id}", status_code=204)
    def delete_excerpt(excerpt_id: str):
        session, service = library()
        try:
            service.delete_excerpt(excerpt_id)
            return Response(status_code=204)
        finally:
            session.close()
