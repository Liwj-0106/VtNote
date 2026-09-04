"""Export-directory settings and server-side output routes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from vtnote.export_files import ExportDirectoryService, ExportFileService
from vtnote.exports import ExportFormat
from vtnote.folder_picker import DirectoryPicker
from vtnote.paths import StoragePaths
from vtnote.tasks import InvalidTaskOperation, TaskService


ExportServices = Callable[[], tuple[Session, object, TaskService]]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportSettingsPatch(_StrictModel):
    directory: str | None = Field(default=None, max_length=2_048)
    use_default: bool = False

    @model_validator(mode="after")
    def validate_choice(self):
        if self.use_default == (self.directory is not None):
            raise ValueError("choose a directory or restore the default")
        return self


class ExportFilesInput(_StrictModel):
    items: list[Literal["audio", "transcript", "notes"]] = Field(
        min_length=1, max_length=3
    )
    audio_format: Literal["m4a", "mp3"] = "m4a"
    transcript_format: Literal["srt", "txt"] = "srt"
    note_format: Literal["markdown", "txt"] = "markdown"


class ExportTextFileInput(_StrictModel):
    variant: Literal["original", "translation"] = "original"
    format: ExportFormat
    language: str | None = Field(default=None, min_length=1, max_length=64)


class BatchExportInput(_StrictModel):
    task_ids: list[str] = Field(min_length=1, max_length=100)
    mode: Literal[
        "summary_markdown",
        "original_markdown",
        "zip_all",
        "zip_notes",
    ]


def register_export_routes(
    app: FastAPI,
    *,
    services: ExportServices,
    paths: StoragePaths,
    directory_picker: DirectoryPicker,
) -> None:
    @app.get("/api/export-settings")
    def get_export_settings():
        session, configuration, _ = services()
        try:
            configuration.get_defaults()
            return ExportDirectoryService(session).get()
        finally:
            session.close()

    @app.patch("/api/export-settings")
    def update_export_settings(payload: ExportSettingsPatch):
        session, configuration, _ = services()
        try:
            configuration.get_defaults()
            return ExportDirectoryService(session).update(**payload.model_dump())
        finally:
            session.close()

    @app.post("/api/system/pick-directory")
    def select_export_directory():
        session, configuration, _ = services()
        try:
            configuration.get_defaults()
            settings = ExportDirectoryService(session).get()
            try:
                selected = directory_picker(Path(str(settings["directory"])))
            except RuntimeError:
                raise InvalidTaskOperation("native directory picker is unavailable") from None
            return {"canceled": selected is None, "directory": str(selected) if selected else None}
        finally:
            session.close()

    @app.post("/api/items/{item_id}/export-files")
    def save_export_files(item_id: str, payload: ExportFilesInput):
        session, _, tasks = services()
        try:
            return ExportFileService(session=session, paths=paths, tasks=tasks).save(
                item_id, **payload.model_dump()
            )
        finally:
            session.close()

    @app.post("/api/items/{item_id}/export-text-file")
    def save_text_export(item_id: str, payload: ExportTextFileInput):
        session, _, tasks = services()
        try:
            return ExportFileService(session=session, paths=paths, tasks=tasks).save_text_export(
                item_id,
                variant=payload.variant,
                export_format=payload.format,
                language=payload.language,
            )
        finally:
            session.close()

    @app.post("/api/tasks/bulk-export")
    def save_batch_export(payload: BatchExportInput):
        session, _, tasks = services()
        try:
            return ExportFileService(
                session=session,
                paths=paths,
                tasks=tasks,
            ).save_batch(payload.task_ids, mode=payload.mode)
        finally:
            session.close()
