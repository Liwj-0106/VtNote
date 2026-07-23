"""Durable task control with the supported diagnostic persistence boundary.

Workers must use ``record_stage_failure`` and ``record_stage_warning`` for diagnostic
writes. Direct mutation of diagnostic ORM fields is internal and unsupported.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.diagnostics import sanitize_diagnostic
from vtnote.exports import ExportFormat, render_export_from_json
from vtnote.models import ItemRecord, RuntimeAssetRecord, StageRunRecord, TaskRecord
from vtnote.paths import StoragePaths
from vtnote.pipeline import (
    ACTIVE_STAGE_STATUSES as _ACTIVE,
    RETRY_ACTIVE_CONFLICTS as _RETRY_ACTIVE_CONFLICTS,
    RETRYABLE_STAGE_STATUSES as _RETRYABLE,
    STAGE_DEPENDENCIES as _STAGE_DEPENDENCIES,
    STAGE_ORDER as _STAGE_ORDER,
    SUCCESSFUL_STAGE_STATUSES as _SUCCESSFUL_PREREQUISITE,
    TERMINAL_STATUSES as _TERMINAL,
)
from vtnote.url_security import SourceUrlPolicy


class InvalidTaskOperation(ValueError):
    pass


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


class TaskView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    status: str
    options: dict[str, Any]
    pipeline_snapshot: dict[str, Any]
    items: tuple[ItemView, ...]


_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_TASK_OPTION_KEYS = frozenset(
    {
        "asr_mode",
        "cloud_asr_profile_id",
        "translation_enabled",
        "translation_profile_id",
        "translation_target_language",
        "notes_enabled",
        "notes_profile_id",
        "notes_template",
        "notes_output_language",
        "notes_custom_prompt",
    }
)


class TaskService:
    def __init__(
        self,
        session: Session,
        configuration: ConfigurationService,
        paths: StoragePaths,
        source_urls: SourceUrlPolicy,
        local_source_validator: LocalSourceValidator | None = None,
    ) -> None:
        self.session = session
        self.configuration = configuration
        self.paths = paths
        self.source_urls = source_urls
        self.local_source_validator = local_source_validator

    def _stage_view(
        self, row: StageRunRecord, sensitive_values: tuple[str, ...]
    ) -> StageView:
        return StageView(
            id=row.id,
            stage=row.stage,
            attempt=row.attempt,
            status=row.status,
            error_code=row.error_code,
            error_message=sanitize_diagnostic(row.error_message, sensitive_values),
            warning=sanitize_diagnostic(row.warning, sensitive_values),
        )

    def _load_task(self, task_id: str) -> TaskRecord:
        row = self.session.scalar(
            select(TaskRecord)
            .where(TaskRecord.id == task_id)
            .options(selectinload(TaskRecord.items).selectinload(ItemRecord.stage_runs))
        )
        if row is None:
            raise KeyError(task_id)
        return row

    def _view_item(
        self, row: ItemRecord, sensitive_values: tuple[str, ...] | None = None
    ) -> ItemView:
        redaction_values = (
            sensitive_values
            if sensitive_values is not None
            else self.configuration.diagnostic_sensitive_values()
        )
        source_locator = (
            Path(row.source_locator).name
            if row.source_kind in {"local_media", "local_subtitle"}
            else row.source_locator
        )
        return ItemView(
            id=row.id,
            position=row.position,
            source_kind=row.source_kind,
            source_locator=source_locator,
            source_display_name=row.source_display_name,
            status=row.status,
            title=row.title,
            stage_runs=tuple(
                self._stage_view(run, redaction_values)
                for run in sorted(
                    row.stage_runs,
                    key=lambda run: (_STAGE_ORDER.get(run.stage, 99), run.attempt),
                )
            ),
        )

    def _view(self, row: TaskRecord) -> TaskView:
        sensitive_values = self.configuration.diagnostic_sensitive_values()
        return TaskView(
            id=row.id,
            status=row.status,
            options=copy.deepcopy(row.options),
            pipeline_snapshot=copy.deepcopy(row.pipeline_snapshot_json),
            items=tuple(
                self._view_item(item, sensitive_values) for item in row.items
            ),
        )

    def _profile_snapshot(self, profile_id: str | None, purpose: str) -> dict[str, Any] | None:
        if profile_id is None:
            return None
        profile = self.configuration.get_profile(profile_id)
        if profile.purpose != purpose or not profile.tested or profile.test_ok is not True:
            raise InvalidConfiguration(f"{purpose} profile must have a current successful test")
        return self.configuration.snapshot_profile(profile_id)

    def _pipeline_snapshot(self, options: dict[str, Any]) -> dict[str, Any]:
        defaults = self.configuration.get_defaults()
        asr_mode = options.get("asr_mode", defaults.asr_mode)
        if asr_mode not in {"auto", "cloud", "local"}:
            raise InvalidTaskOperation("invalid ASR mode")
        cloud_profile_id = options.get(
            "cloud_asr_profile_id", defaults.cloud_asr_profile_id
        )
        cloud = None
        cloud_view = None
        if cloud_profile_id is not None:
            try:
                cloud_view = self.configuration.get_profile(cloud_profile_id)
            except KeyError:
                cloud_view = None
            if (
                cloud_view is not None
                and cloud_view.purpose == "cloud_asr"
                and cloud_view.tested
                and cloud_view.test_ok is True
                and cloud_view.upload_authorized
            ):
                cloud = self.configuration.snapshot_profile(cloud_profile_id)
        cloud_authorized = (
            cloud is not None and cloud_view is not None and cloud_view.upload_authorized
        )
        if asr_mode == "cloud":
            if not cloud_authorized:
                raise InvalidConfiguration("cloud ASR requires current test and upload authorization")
        elif asr_mode == "local":
            cloud = None
        elif not cloud_authorized:
            cloud = None
        translation_enabled = options.get(
            "translation_enabled", defaults.translation_enabled
        )
        translation_profile_id = options.get(
            "translation_profile_id", defaults.translation_profile_id
        )
        translation = (
            self._profile_snapshot(translation_profile_id, "translation")
            if translation_enabled
            else None
        )
        notes_enabled = options.get("notes_enabled", defaults.notes_enabled)
        notes_profile_id = options.get("notes_profile_id", defaults.notes_profile_id)
        notes = (
            self._profile_snapshot(notes_profile_id, "notes")
            if notes_enabled
            else None
        )
        target_language = options.get(
            "translation_target_language", defaults.translation_target_language
        )
        output_language = options.get(
            "notes_output_language", defaults.notes_output_language
        )
        notes_template = options.get("notes_template", defaults.notes_template)
        custom_prompt = options.get("notes_custom_prompt", defaults.notes_custom_prompt)
        if not isinstance(target_language, str) or not target_language.strip():
            raise InvalidTaskOperation("translation target language cannot be empty")
        if not isinstance(output_language, str) or not output_language.strip():
            raise InvalidTaskOperation("notes output language cannot be empty")
        if notes_template not in {"summary", "key_points", "custom"}:
            raise InvalidTaskOperation("invalid notes template")
        if notes_template == "custom" and (
            not isinstance(custom_prompt, str) or not custom_prompt.strip()
        ):
            raise InvalidTaskOperation("custom notes template requires a prompt")
        return {
            "schema_version": 1,
            "asr": {"mode": asr_mode, "profile": cloud},
            "translation": {
                "enabled": translation_enabled,
                "profile": translation,
                "target_language": target_language,
            },
            "notes": {
                "enabled": notes_enabled,
                "profile": notes,
                "template": notes_template,
                "output_language": output_language,
                "custom_prompt": custom_prompt if notes_template == "custom" else None,
            },
            "local_whisper": dict(defaults.local_whisper_options),
        }

    def _validate_source(self, source: dict[str, str]) -> tuple[str, str]:
        if set(source) != {"kind", "locator"}:
            raise InvalidTaskOperation("source requires only kind and locator")
        kind = source["kind"]
        locator = source["locator"]
        if kind == "url":
            return kind, self.source_urls.validate(locator)
        if kind not in {"local_media", "local_subtitle"}:
            raise InvalidTaskOperation("unsupported source kind")
        path = Path(locator)
        if (
            not path.is_absolute()
            or str(path).startswith("\\\\")
            or not path.is_file()
        ):
            raise InvalidTaskOperation("local source must be an existing absolute file")
        if self.local_source_validator is not None:
            try:
                if kind == "local_media":
                    self.local_source_validator.validate_media(path)
                else:
                    self.local_source_validator.validate_subtitle(path)
            except (OSError, ValueError):
                label = "media" if kind == "local_media" else "subtitle"
                raise InvalidTaskOperation(f"invalid local {label}") from None
        return kind, str(path)

    def _create_validated_task(
        self,
        *,
        validated: list[tuple[str, str, str | None]],
        selected_options: dict[str, Any],
    ) -> TaskView:
        snapshot = self._pipeline_snapshot(selected_options)
        task = TaskRecord(options=selected_options, pipeline_snapshot_json=snapshot)
        translation_enabled = snapshot["translation"]["enabled"]
        notes_enabled = snapshot["notes"]["enabled"]
        for position, (kind, locator, display_name) in enumerate(validated):
            item = ItemRecord(
                position=position,
                source_kind=kind,
                source_locator=locator,
                source_display_name=display_name,
            )
            stages = ["source", "transcribe"]
            if translation_enabled:
                stages.append("translate")
            if notes_enabled:
                stages.append("notes")
            item.stage_runs = [StageRunRecord(stage=stage, attempt=1) for stage in stages]
            task.items.append(item)
        self.session.add(task)
        self.session.flush()
        for item in task.items:
            item.artifact_relpath = f"items/{item.id}"
        self.session.commit()
        return self._view(self._load_task(task.id))

    def create_task(
        self, *, sources: list[dict[str, str]], options: dict[str, Any] | None = None
    ) -> TaskView:
        if not sources:
            raise InvalidTaskOperation("at least one source is required")
        selected_options = dict(options or {})
        if set(selected_options) - _TASK_OPTION_KEYS:
            raise InvalidTaskOperation("unsupported task option")
        validated = [(*self._validate_source(source), None) for source in sources]
        return self._create_validated_task(
            validated=validated, selected_options=selected_options
        )

    def create_upload_task(
        self, *, upload_kind: str, upload_id: str, options: dict[str, Any] | None = None
    ) -> TaskView:
        source_kinds = {"media": "uploaded_media", "subtitle": "uploaded_subtitle"}
        if upload_kind not in source_kinds:
            raise InvalidTaskOperation("invalid upload kind")
        try:
            locator = str(UUID(upload_id))
        except (ValueError, AttributeError):
            raise InvalidTaskOperation("invalid upload identifier") from None
        selected_options = dict(options or {})
        if set(selected_options) - _TASK_OPTION_KEYS:
            raise InvalidTaskOperation("unsupported task option")
        return self._create_validated_task(
            validated=[(source_kinds[upload_kind], locator, None)],
            selected_options=selected_options,
        )

    def bind_uploaded_asset(
        self, *, item_id: str, asset_id: str, display_name: str
    ) -> TaskView:
        item = self.session.get(ItemRecord, item_id)
        asset = self.session.get(RuntimeAssetRecord, asset_id)
        if item is None or asset is None:
            raise InvalidTaskOperation("upload asset is unavailable")
        if (
            item.source_kind not in {"uploaded_media", "uploaded_subtitle"}
            or asset.item_id != item.id
            or asset.role != "uploaded_source"
            or asset.state != "active"
        ):
            raise InvalidTaskOperation("upload asset does not match its item")
        if not display_name or len(display_name) > 180:
            raise InvalidTaskOperation("invalid upload display name")
        item.source_locator = asset.id
        item.source_display_name = display_name
        self.session.commit()
        return self._view(self._load_task(item.task_id))

    def record_upload_failure(
        self, *, item_id: str, error_code: str, message: str
    ) -> TaskView:
        if _ERROR_CODE.fullmatch(error_code) is None:
            raise InvalidTaskOperation("invalid stage error code")
        item = self.session.scalar(
            select(ItemRecord)
            .where(ItemRecord.id == item_id)
            .options(selectinload(ItemRecord.stage_runs), selectinload(ItemRecord.task))
        )
        if item is None:
            raise KeyError(item_id)
        source_runs = [run for run in item.stage_runs if run.stage == "source"]
        if not source_runs:
            raise InvalidTaskOperation("source stage is unavailable")
        source = max(source_runs, key=lambda run: run.attempt)
        safe_message = sanitize_diagnostic(
            message, self.configuration.diagnostic_sensitive_values()
        )
        source.status = "failed"
        source.error_code = error_code
        source.error_message = safe_message
        for run in item.stage_runs:
            if run is not source and run.status == "queued":
                run.status = "canceled"
        item.status = "failed"
        item.task.status = "failed"
        self.session.commit()
        return self._view(self._load_task(item.task_id))

    def list_tasks(self) -> list[TaskView]:
        ids = self.session.scalars(select(TaskRecord.id).order_by(TaskRecord.created_at.desc())).all()
        return [self._view(self._load_task(task_id)) for task_id in ids]

    def get_task(self, task_id: str) -> TaskView:
        return self._view(self._load_task(task_id))

    def record_stage_failure(
        self, stage_run_id: str, *, error_code: str, message: str
    ) -> StageView:
        if _ERROR_CODE.fullmatch(error_code) is None:
            raise InvalidTaskOperation("invalid stage error code")
        row = self.session.get(StageRunRecord, stage_run_id)
        if row is None:
            raise KeyError(stage_run_id)
        sensitive_values = self.configuration.diagnostic_sensitive_values()
        safe_message = sanitize_diagnostic(message, sensitive_values)
        row.status = "failed"
        row.error_code = error_code
        row.error_message = safe_message
        self.session.commit()
        return self._stage_view(row, sensitive_values)

    def record_stage_warning(self, stage_run_id: str, warning: str) -> StageView:
        row = self.session.get(StageRunRecord, stage_run_id)
        if row is None:
            raise KeyError(stage_run_id)
        sensitive_values = self.configuration.diagnostic_sensitive_values()
        safe_warning = sanitize_diagnostic(warning, sensitive_values)
        row.warning = safe_warning
        self.session.commit()
        return self._stage_view(row, sensitive_values)

    def cancel_task(self, task_id: str) -> TaskView:
        task = self._load_task(task_id)
        if task.status in _TERMINAL:
            raise InvalidTaskOperation("terminal task cannot be canceled")
        if task.status == "cancel_requested":
            return self._view(task)
        running = task.status == "running" or any(
            item.status == "running" or any(run.status == "running" for run in item.stage_runs)
            for item in task.items
        )
        target = "cancel_requested" if running else "canceled"
        task.status = target
        for item in task.items:
            if item.status not in _TERMINAL:
                item.status = target
            for run in item.stage_runs:
                if run.status == "running":
                    run.status = "cancel_requested"
                elif run.status == "queued":
                    run.status = "canceled" if not running else "canceled"
        self.session.commit()
        return self._view(self._load_task(task_id))

    def retry_stage(self, item_id: str, stage: str) -> ItemView:
        item = self.session.scalar(
            select(ItemRecord)
            .where(ItemRecord.id == item_id)
            .options(selectinload(ItemRecord.stage_runs), selectinload(ItemRecord.task))
        )
        if item is None:
            raise KeyError(item_id)
        if item.status == "cancel_requested" or item.task.status == "cancel_requested":
            raise InvalidTaskOperation("cannot retry while cancellation is active")
        if stage not in _STAGE_DEPENDENCIES:
            raise InvalidTaskOperation("unknown pipeline stage")
        if any(
            run.status in _ACTIVE
            and run.stage in _RETRY_ACTIVE_CONFLICTS[stage]
            for run in item.stage_runs
        ):
            raise InvalidTaskOperation(
                "cannot retry while a conflicting stage is active"
            )
        attempts = [run for run in item.stage_runs if run.stage == stage]
        if not attempts or attempts[-1].status not in _RETRYABLE:
            raise InvalidTaskOperation("only a failed or canceled stage can be retried")
        latest_by_stage: dict[str, StageRunRecord] = {}
        for run in item.stage_runs:
            previous = latest_by_stage.get(run.stage)
            if previous is None or run.attempt > previous.attempt:
                latest_by_stage[run.stage] = run
        for prerequisite in _STAGE_DEPENDENCIES[stage]:
            latest = latest_by_stage.get(prerequisite)
            if latest is None or latest.status not in _SUCCESSFUL_PREREQUISITE:
                raise InvalidTaskOperation("stage prerequisite has not succeeded")
        item_has_active_work = any(
            run.status in _ACTIVE for run in item.stage_runs
        )
        task_has_active_work = self.session.scalar(
            select(StageRunRecord.id)
            .join(ItemRecord, StageRunRecord.item_id == ItemRecord.id)
            .where(
                ItemRecord.task_id == item.task_id,
                StageRunRecord.status.in_(_ACTIVE),
            )
            .limit(1)
        ) is not None
        next_attempt = max(run.attempt for run in attempts) + 1
        item.stage_runs.append(StageRunRecord(stage=stage, attempt=next_attempt, status="queued"))
        item.status = "running" if item_has_active_work else "queued"
        item.task.status = "running" if task_has_active_work else "queued"
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidTaskOperation("stage retry conflicted; refresh and retry") from None
        self.session.refresh(item)
        return self._view_item(item)

    def export_item(
        self,
        item_id: str,
        *,
        variant: str,
        export_format: ExportFormat | str,
        language: str | None = None,
    ) -> str:
        if self.session.get(ItemRecord, item_id) is None:
            raise KeyError(item_id)
        transcript_path = self.paths.transcript(item_id)
        if not transcript_path.is_file():
            raise InvalidTaskOperation("transcript artifact is not available")
        translation_json: bytes | None = None
        if variant == "translation":
            if language is None:
                raise InvalidTaskOperation("translation export requires language")
            translation_path = self.paths.translation(item_id, language)
            if not translation_path.is_file():
                raise InvalidTaskOperation("translation artifact is not available")
            translation_json = translation_path.read_bytes()
        elif variant != "original" or language is not None:
            raise InvalidTaskOperation("invalid export variant")
        return render_export_from_json(
            transcript_path.read_bytes(), export_format, translation_json
        )
