"""Stable task-service contracts independent from task persistence logic."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict


class InvalidTaskOperation(ValueError):
    pass


class TaskDeletionError(ValueError):
    """A safe task-deletion failure exposed as a stable API code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"task deletion failed: {code}")


MAX_BATCH_SOURCES = 100


class RetryOverrideSnapshot(TypedDict, total=False):
    schema_version: Literal[1]
    strategy: Literal["same", "local", "cloud_confirmed"]
    asr: dict[str, Any]
    notes: dict[str, Any]
    charge_acknowledged: bool


class LocalSourceValidator(Protocol):
    def validate_media(self, path: Path) -> Any: ...

    def validate_subtitle(self, path: Path) -> None: ...


class StageView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    stage: str
    attempt: int
    status: str
    error_code: str | None
    error_message: str | None
    warning: str | None
    progress: dict[str, Any] | None
    execution_evidence: dict[str, Any] | None
    provider_status_code: str | None
    external_submission_state: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ItemView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    position: int
    source_kind: str
    source_locator: str
    source_display_name: str | None
    status: str
    title: str | None
    stage_runs: tuple[StageView, ...]
    created_at: datetime
    updated_at: datetime


class TaskView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    status: str
    options: dict[str, Any]
    pipeline_snapshot: dict[str, Any]
    items: tuple[ItemView, ...]
    terminal_reason_code: str | None
    created_at: datetime
    updated_at: datetime
