"""Task and item-result routes for the local HTTP API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from vtnote.application.task_contracts import LocalSourceValidator
from vtnote.exports import ExportFormat
from vtnote.http.contracts import (
    BatchTaskCreate,
    RetryInput,
    TaskCreate,
    TaskDeleteBatch,
    UploadTaskMetadata,
)
from vtnote.http.responses import dump_model as _dump, error_response as _error
from vtnote.paths import StoragePaths
from vtnote.runtime_assets import RuntimeAssetService
from vtnote.tasks import TaskService
from vtnote.uploads import (
    MultipartUploadStager,
    UploadError,
    UploadLimits,
    UploadService,
    UploadTaskContext,
)

TaskServices = Callable[[], tuple[Session, object, TaskService]]


def register_task_routes(
    app: FastAPI,
    *,
    services: TaskServices,
    paths: StoragePaths,
    selected_local_sources: LocalSourceValidator,
    selected_upload_limits: UploadLimits,
) -> None:
    @app.post("/api/tasks/batch", status_code=201)
    def create_batch_tasks(payload: BatchTaskCreate):
        session, _, tasks = services()
        try:
            sources = [item.model_dump() for item in payload.sources]
            options = payload.model_dump(
                exclude={"sources"}, exclude_unset=True, exclude_none=True
            )
            return [
                _dump(task)
                for task in tasks.create_batch_tasks(
                    sources=sources,
                    options=options,
                )
            ]
        finally:
            session.close()

    @app.get("/api/tasks")
    def list_tasks(
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = None,
        status: str | None = Query(default=None, min_length=1, max_length=32),
    ):
        session, _, tasks = services()
        try:
            page, next_cursor = tasks.list_tasks_page(
                limit=limit,
                cursor=cursor,
                status=status,
            )
            headers = (
                {"X-Next-Cursor": next_cursor}
                if next_cursor is not None
                else None
            )
            return JSONResponse(
                [_dump(item) for item in page],
                headers=headers,
            )
        finally:
            session.close()

    @app.post("/api/tasks", status_code=201)
    async def create_task(request: Request):
        session, _, tasks = services()
        try:
            content_type = request.headers.get("content-type", "")
            media_type = content_type.split(";", 1)[0].strip().casefold()
            if media_type == "application/json":
                try:
                    raw_payload = await request.json()
                except ValueError:
                    return _error(
                        422,
                        "validation_error",
                        "request validation failed",
                    )
                try:
                    payload = TaskCreate.model_validate(raw_payload)
                except ValidationError as error:
                    details = [
                        {
                            "location": ["body", *item["loc"]],
                            "type": item["type"],
                        }
                        for item in error.errors()
                    ]
                    return _error(
                        422,
                        "validation_error",
                        "request validation failed",
                        details,
                    )
                sources = [item.model_dump() for item in payload.sources]
                options = payload.model_dump(
                    exclude={"sources"}, exclude_unset=True, exclude_none=True
                )
                return _dump(tasks.create_task(sources=sources, options=options))
            if media_type != "multipart/form-data":
                return _error(
                    415,
                    "unsupported_media_type",
                    "request must be JSON or multipart form data",
                )

            raw_length = request.headers.get("content-length")
            try:
                content_length = int(raw_length) if raw_length is not None else None
            except ValueError:
                return _error(400, "invalid_content_length", "invalid Content-Length")
            uploads = UploadService(
                session=session,
                paths=paths,
                tasks=tasks,
                assets=RuntimeAssetService(session, paths),
                local_sources=selected_local_sources,
            )

            def accept_metadata(
                metadata: dict[str, Any], upload_id: str
            ) -> UploadTaskContext:
                payload = UploadTaskMetadata.model_validate(metadata)
                options = payload.model_dump(
                    exclude={"kind"}, exclude_unset=True, exclude_none=True
                )
                created = tasks.create_upload_task(
                    upload_kind=payload.kind,
                    upload_id=upload_id,
                    options=options,
                )
                return UploadTaskContext(
                    task_id=created.id,
                    item_id=created.items[0].id,
                )

            try:
                state = await MultipartUploadStager(
                    paths, selected_upload_limits
                ).consume(
                    request.stream(),
                    content_type=content_type,
                    content_length=content_length,
                    accept_metadata=accept_metadata,
                )
                return _dump(uploads.complete(state))
            except UploadError as error:
                uploads.fail(error.state, code=error.code)
                details = (
                    {"task_id": error.state.context.task_id}
                    if error.state.context is not None
                    else None
                )
                return _error(
                    error.status_code,
                    error.code,
                    "upload failed",
                    details,
                )
        finally:
            session.close()

    @app.post("/api/tasks/bulk-delete")
    def bulk_delete_tasks(payload: TaskDeleteBatch):
        session, _, tasks = services()
        try:
            deleted = tasks.delete_tasks(payload.task_ids)
            return {"deleted_task_ids": list(deleted)}
        finally:
            session.close()

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: str):
        session, _, tasks = services()
        try:
            tasks.delete_tasks([task_id])
            return Response(status_code=204)
        finally:
            session.close()

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        session, _, tasks = services()
        try:
            return _dump(tasks.get_task(task_id))
        finally:
            session.close()

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        session, _, tasks = services()
        try:
            return _dump(tasks.cancel_task(task_id))
        finally:
            session.close()

    @app.post("/api/tasks/{task_id}/retry", status_code=201)
    def retry_task_stage(task_id: str, payload: RetryInput):
        session, _, tasks = services()
        try:
            task = tasks.get_task(task_id)
            if payload.item_id not in {item.id for item in task.items}:
                raise KeyError(payload.item_id)
            override = tasks.build_retry_override(
                strategy=payload.strategy,
                cloud_profile_id=payload.cloud_profile_id,
                connection_revision=payload.connection_revision,
                profile_revision=payload.profile_revision,
                notes_profile_id=payload.notes_profile_id,
                notes_profile_revision=payload.notes_profile_revision,
                notes_output_language=payload.notes_output_language,
                acknowledge_possible_charge=payload.acknowledge_possible_charge,
            )
            return _dump(
                tasks.retry_stage(
                    payload.item_id,
                    payload.stage,
                    expected_attempt=payload.expected_attempt,
                    override=override,
                    acknowledge_possible_charge=(
                        payload.acknowledge_possible_charge
                    ),
                )
            )
        finally:
            session.close()

    @app.get("/api/items/{item_id}/export")
    def export_item(
        item_id: str,
        variant: Literal["original", "translation"],
        format: ExportFormat,
        language: str | None = None,
    ):
        session, _, tasks = services()
        try:
            rendered = tasks.export_item(
                item_id, variant=variant, export_format=format, language=language
            )
            content_types = {
                ExportFormat.JSON: "application/json",
                ExportFormat.SRT: "application/x-subrip",
                ExportFormat.VTT: "text/vtt",
                ExportFormat.TXT: "text/plain",
                ExportFormat.MARKDOWN: "text/markdown",
            }
            extension = "md" if format is ExportFormat.MARKDOWN else format.value
            filename = f"vtnote-{item_id[:8]}-{variant}.{extension}"
            return Response(
                rendered,
                media_type=content_types[format],
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                },
            )
        finally:
            session.close()

    @app.get("/api/items/{item_id}/transcript")
    def get_item_transcript(item_id: str):
        session, _, tasks = services()
        try:
            return _dump(tasks.get_item_transcript(item_id))
        finally:
            session.close()

    @app.get("/api/items/{item_id}/alignment")
    def get_item_alignment(item_id: str):
        session, _, tasks = services()
        try:
            return _dump(tasks.get_item_alignment(item_id))
        finally:
            session.close()

    @app.get("/api/items/{item_id}/speakers")
    def get_item_speakers(item_id: str):
        session, _, tasks = services()
        try:
            return _dump(tasks.get_item_speakers(item_id))
        finally:
            session.close()

    @app.get("/api/items/{item_id}/translations/{language}")
    def get_item_translation(item_id: str, language: str):
        session, _, tasks = services()
        try:
            return _dump(tasks.get_item_translation(item_id, language))
        finally:
            session.close()

    @app.get("/api/items/{item_id}/notes")
    def list_item_notes(item_id: str):
        session, _, tasks = services()
        try:
            return tasks.list_item_notes(item_id)
        finally:
            session.close()
