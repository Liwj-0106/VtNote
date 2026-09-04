"""Durable task control with the supported diagnostic persistence boundary.

Workers must use ``record_stage_failure`` and ``record_stage_warning`` for diagnostic
writes. Direct mutation of diagnostic ORM fields is internal and unsupported.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload

from vtnote.application.task_contracts import (
    MAX_BATCH_SOURCES,
    InvalidTaskOperation,
    ItemView,
    LocalSourceValidator,
    RetryOverrideSnapshot,
    StageView,
    TaskDeletionError,
    TaskView,
)
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.local_asr_contract import (
    LOCAL_ASR_ENGINES,
    build_local_asr_snapshot,
)
from vtnote.diagnostics import contains_sensitive_value, sanitize_diagnostic
from vtnote.exports import ExportFormat, render_export_from_json
from vtnote.models import (
    ItemRecord,
    RuntimeAssetRecord,
    StageRunRecord,
    TaskRecord,
)
from vtnote.paths import StoragePaths
from vtnote.result_artifacts import parse_note_metadata, read_result_artifact
from vtnote.retry_policy import bounded_retry_override, is_sqlite_retry_conflict
from vtnote.stage_models import allowed_stage_models
from vtnote.task_deletion import TaskDeletionService
from vtnote.schemas import (
    SpeakerMap,
    Transcript,
    TranscriptAlignment,
    Translation,
    transcript_sha256,
)
from vtnote.pipeline import (
    ACTIVE_STAGE_STATUSES as _ACTIVE,
    RETRY_ACTIVE_CONFLICTS as _RETRY_ACTIVE_CONFLICTS,
    RETRYABLE_STAGE_STATUSES as _RETRYABLE,
    STAGE_DEPENDENCIES as _STAGE_DEPENDENCIES,
    STAGE_ORDER as _STAGE_ORDER,
    SUCCESSFUL_STAGE_STATUSES as _SUCCESSFUL_PREREQUISITE,
    TERMINAL_STATUSES as _TERMINAL,
    validate_execution_evidence,
    validate_provider_status_code,
    validate_stage_progress,
)
from vtnote.url_security import SourceUrlPolicy
from vtnote.sensitive_text import (
    require_sensitive_text_migration,
    task_prompt_purpose,
    validate_protected_text_envelope,
)


_PROFILE_TEST_SAMPLE_REASON = "profile_test_sample"
_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_TASK_OPTION_KEYS = frozenset(
    {
        "output_type",
        "audio_export_enabled",
        "asr_mode",
        "local_asr_engine",
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
_LANGUAGE_TAG = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,63})$")


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

    _bounded_retry_override = staticmethod(bounded_retry_override)
    _is_sqlite_retry_conflict = staticmethod(is_sqlite_retry_conflict)

    def _reserve_retry_transaction(self) -> None:
        if self.session.new or self.session.dirty or self.session.deleted:
            raise InvalidTaskOperation("stage retry requires a clean session")
        if self.session.in_transaction():
            connection = self.session.connection()
            driver_connection = connection.connection.driver_connection
            if driver_connection.in_transaction:
                raise InvalidTaskOperation("stage retry requires a clean session")
            self.session.rollback()
        connection = self.session.connection()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as error:
            self.session.rollback()
            if self._is_sqlite_retry_conflict(error):
                raise InvalidTaskOperation(
                    "stage retry conflicted; refresh and retry"
                ) from None
            raise
        self.session.expire_all()

    def build_retry_override(
        self,
        *,
        strategy: Literal["same", "local", "cloud_confirmed"] = "same",
        cloud_profile_id: str | None = None,
        connection_revision: int | None = None,
        profile_revision: int | None = None,
        notes_profile_id: str | None = None,
        notes_profile_revision: int | None = None,
        notes_output_language: str | None = None,
        acknowledge_possible_charge: bool = False,
    ) -> RetryOverrideSnapshot:
        """Build one exact internal retry snapshot without consulting defaults."""

        if type(acknowledge_possible_charge) is not bool:
            raise InvalidTaskOperation("invalid possible-charge acknowledgement")
        if strategy not in {"same", "local", "cloud_confirmed"}:
            raise InvalidTaskOperation("invalid retry strategy")
        has_notes_override = any(
            value is not None
            for value in (
                notes_profile_id,
                notes_profile_revision,
                notes_output_language,
            )
        )
        if has_notes_override and strategy != "same":
            raise InvalidTaskOperation(
                "notes retry fields require the same strategy"
            )
        if (notes_profile_id is None) != (notes_profile_revision is None):
            raise InvalidTaskOperation(
                "notes retry profile id and revision must be provided together"
            )
        if strategy != "cloud_confirmed":
            if (
                cloud_profile_id is not None
                or connection_revision is not None
                or profile_revision is not None
            ):
                raise InvalidTaskOperation(
                    "cloud retry fields are valid only for cloud_confirmed"
                )
            if strategy == "same":
                payload: dict[str, Any] = {
                    "schema_version": 1,
                    "strategy": "same",
                }
                notes_override: dict[str, Any] = {}
                if notes_profile_id is not None:
                    assert notes_profile_revision is not None
                    try:
                        notes_override["profile"] = (
                            self.configuration.snapshot_current_notes_retry_profile(
                                notes_profile_id,
                                profile_revision=notes_profile_revision,
                            )
                        )
                    except InvalidConfiguration as error:
                        raise InvalidTaskOperation(str(error)) from error
                if notes_output_language is not None:
                    normalized_language = notes_output_language.strip()
                    if _LANGUAGE_TAG.fullmatch(normalized_language) is None:
                        raise InvalidTaskOperation(
                            "invalid notes output language"
                        )
                    notes_override["output_language"] = normalized_language
                if notes_override:
                    payload["notes"] = notes_override
                if acknowledge_possible_charge:
                    payload["charge_acknowledged"] = True
                return self._bounded_retry_override(
                    payload
                )
            if acknowledge_possible_charge:
                raise InvalidTaskOperation(
                    "possible-charge acknowledgement is invalid for local retry"
                )
            return self._bounded_retry_override(
                {
                    "schema_version": 1,
                    "strategy": "local",
                    "asr": {"mode": "local", "profile": None},
                }
            )
        if acknowledge_possible_charge is not True:
            raise InvalidTaskOperation(
                "cloud retry requires acknowledging the possible charge"
            )
        if (
            not isinstance(cloud_profile_id, str)
            or not cloud_profile_id
            or type(connection_revision) is not int
            or connection_revision <= 0
            or type(profile_revision) is not int
            or profile_revision <= 0
        ):
            raise InvalidTaskOperation(
                "cloud retry requires a profile and positive revisions"
            )
        try:
            profile = self.configuration.snapshot_current_cloud_asr_retry_profile(
                cloud_profile_id,
                connection_revision=connection_revision,
                profile_revision=profile_revision,
            )
        except InvalidConfiguration as error:
            raise InvalidTaskOperation(str(error)) from error
        return self._bounded_retry_override(
            {
                "schema_version": 1,
                "strategy": "cloud_confirmed",
                "asr": {"mode": "cloud", "profile": profile},
            }
        )

    def _validate_retry_override(
        self, override: RetryOverrideSnapshot | Mapping[str, object]
    ) -> RetryOverrideSnapshot:
        if not isinstance(override, Mapping):
            raise InvalidTaskOperation("invalid retry override")
        strategy = override.get("strategy")
        schema_version = override.get("schema_version")
        if type(schema_version) is not int or schema_version != 1:
            raise InvalidTaskOperation("invalid retry override schema")
        if strategy == "same":
            if not set(override).issubset(
                {"schema_version", "strategy", "charge_acknowledged", "notes"}
            ) or (
                "charge_acknowledged" in override
                and override.get("charge_acknowledged") is not True
            ):
                raise InvalidTaskOperation("invalid same retry override")
            normalized: dict[str, Any] = {
                "schema_version": 1,
                "strategy": "same",
            }
            if override.get("charge_acknowledged") is True:
                normalized["charge_acknowledged"] = True
            if "notes" in override:
                notes = override.get("notes")
                if (
                    not isinstance(notes, Mapping)
                    or not notes
                    or not set(notes).issubset({"profile", "output_language"})
                ):
                    raise InvalidTaskOperation("invalid notes retry override")
                normalized_notes: dict[str, Any] = {}
                if "profile" in notes:
                    supplied_profile = notes.get("profile")
                    if not isinstance(supplied_profile, Mapping):
                        raise InvalidTaskOperation(
                            "invalid notes retry profile snapshot"
                        )
                    supplied_profile_dict = dict(supplied_profile)
                    profile_id = supplied_profile_dict.get("id")
                    profile_revision = supplied_profile_dict.get(
                        "profile_revision"
                    )
                    if (
                        not isinstance(profile_id, str)
                        or type(profile_revision) is not int
                    ):
                        raise InvalidTaskOperation(
                            "invalid notes retry profile snapshot"
                        )
                    try:
                        current_profile = (
                            self.configuration.snapshot_current_notes_retry_profile(
                                profile_id,
                                profile_revision=profile_revision,
                            )
                        )
                    except InvalidConfiguration as error:
                        raise InvalidTaskOperation(str(error)) from error
                    if supplied_profile_dict != current_profile:
                        raise InvalidTaskOperation(
                            "notes retry profile changed; refresh and retry"
                        )
                    normalized_notes["profile"] = current_profile
                if "output_language" in notes:
                    output_language = notes.get("output_language")
                    if (
                        not isinstance(output_language, str)
                        or _LANGUAGE_TAG.fullmatch(output_language) is None
                    ):
                        raise InvalidTaskOperation(
                            "invalid notes retry output language"
                        )
                    normalized_notes["output_language"] = output_language
                normalized["notes"] = normalized_notes
        elif strategy == "local":
            if set(override) != {"schema_version", "strategy", "asr"}:
                raise InvalidTaskOperation("invalid local retry override")
            asr = override.get("asr")
            if (
                not isinstance(asr, Mapping)
                or set(asr) != {"mode", "profile"}
                or asr.get("mode") != "local"
                or asr.get("profile") is not None
            ):
                raise InvalidTaskOperation("invalid local retry override")
            normalized = {
                "schema_version": 1,
                "strategy": "local",
                "asr": {"mode": "local", "profile": None},
            }
        elif strategy == "cloud_confirmed":
            if set(override) != {"schema_version", "strategy", "asr"}:
                raise InvalidTaskOperation("invalid cloud retry override")
            asr = override.get("asr")
            if (
                not isinstance(asr, Mapping)
                or set(asr) != {"mode", "profile"}
                or asr.get("mode") != "cloud"
                or not isinstance(asr.get("profile"), Mapping)
            ):
                raise InvalidTaskOperation("invalid cloud retry override")
            supplied_profile = dict(cast(Mapping[str, Any], asr["profile"]))
            profile_id = supplied_profile.get("id")
            connection_revision = supplied_profile.get("connection_revision")
            profile_revision = supplied_profile.get("profile_revision")
            if (
                not isinstance(profile_id, str)
                or type(connection_revision) is not int
                or type(profile_revision) is not int
            ):
                raise InvalidTaskOperation("invalid cloud retry profile snapshot")
            try:
                current_profile = (
                    self.configuration.snapshot_current_cloud_asr_retry_profile(
                        profile_id,
                        connection_revision=connection_revision,
                        profile_revision=profile_revision,
                    )
                )
            except InvalidConfiguration as error:
                raise InvalidTaskOperation(str(error)) from error
            if supplied_profile != current_profile:
                raise InvalidTaskOperation(
                    "cloud retry profile changed; refresh and retry"
                )
            normalized = {
                "schema_version": 1,
                "strategy": "cloud_confirmed",
                "asr": {"mode": "cloud", "profile": current_profile},
            }
        else:
            raise InvalidTaskOperation("invalid retry strategy")
        return self._bounded_retry_override(normalized)

    _allowed_stage_models = staticmethod(allowed_stage_models)

    _contains_sensitive_value = staticmethod(contains_sensitive_value)

    def _stage_view(
        self, row: StageRunRecord, sensitive_values: tuple[str, ...]
    ) -> StageView:
        progress = None
        if row.progress_json is not None:
            try:
                normalized_progress = validate_stage_progress(row.progress_json)
            except ValueError:
                pass
            else:
                if not self._contains_sensitive_value(
                    (normalized_progress["message_code"],), sensitive_values
                ):
                    progress = dict(normalized_progress)
        execution_evidence = None
        if row.execution_evidence_json is not None:
            try:
                normalized = validate_execution_evidence(
                    row.execution_evidence_json,
                    allowed_models=self._allowed_stage_models(row),
                )
            except ValueError:
                pass
            else:
                if not self._contains_sensitive_value(
                    tuple(normalized.values()), sensitive_values
                ):
                    execution_evidence = dict(normalized)
        provider_status_code = None
        try:
            normalized_status = validate_provider_status_code(
                row.provider_status_code
            )
        except ValueError:
            pass
        else:
            if normalized_status is None or not self._contains_sensitive_value(
                (normalized_status,), sensitive_values
            ):
                provider_status_code = normalized_status
        return StageView(
            id=row.id,
            stage=row.stage,
            attempt=row.attempt,
            status=row.status,
            error_code=row.error_code,
            error_message=sanitize_diagnostic(row.error_message, sensitive_values),
            warning=sanitize_diagnostic(row.warning, sensitive_values),
            progress=progress,
            execution_evidence=execution_evidence,
            provider_status_code=provider_status_code,
            external_submission_state=row.external_submission_state,
            started_at=row.started_at,
            finished_at=row.finished_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
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
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _view(self, row: TaskRecord) -> TaskView:
        sensitive_values = self.configuration.diagnostic_sensitive_values()
        options = copy.deepcopy(row.options)
        options.pop("notes_custom_prompt", None)
        snapshot = copy.deepcopy(row.pipeline_snapshot_json)
        notes_value = snapshot.get("notes")
        if isinstance(notes_value, dict):
            notes = dict(notes_value)
            has_custom_prompt = bool(
                notes.pop("custom_prompt_envelope", None)
                or notes.pop("custom_prompt", None)
            )
            notes["has_custom_prompt"] = has_custom_prompt
            snapshot["notes"] = notes
        return TaskView(
            id=row.id,
            status=row.status,
            options=options,
            pipeline_snapshot=snapshot,
            items=tuple(
                self._view_item(item, sensitive_values) for item in row.items
            ),
            terminal_reason_code=row.terminal_reason_code,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _profile_snapshot(self, profile_id: str | None, purpose: str) -> dict[str, Any] | None:
        if profile_id is None:
            return None
        profile = self.configuration.get_profile(profile_id)
        if profile.purpose != purpose or not profile.tested or profile.test_ok is not True:
            raise InvalidConfiguration(f"{purpose} profile must have a current successful test")
        if purpose in {"translation", "notes"} and not profile.chat_data_authorized:
            raise InvalidConfiguration(
                f"{purpose} profile requires current chat data authorization"
            )
        return self.configuration.snapshot_profile(profile_id)

    def _pipeline_snapshot(
        self, options: dict[str, Any], task_id: str
    ) -> dict[str, Any]:
        defaults = self.configuration.get_defaults()
        output_type = options.get("output_type")
        if output_type not in {None, "audio", "transcript", "notes"}:
            raise InvalidTaskOperation("invalid output type")
        audio_export_enabled = options.get(
            "audio_export_enabled", output_type == "audio"
        )
        if not isinstance(audio_export_enabled, bool):
            raise InvalidTaskOperation("invalid audio export option")
        asr_mode = options.get("asr_mode", defaults.asr_mode)
        if asr_mode not in {"auto", "cloud", "local"}:
            raise InvalidTaskOperation("invalid ASR mode")
        local_asr_engine = options.get(
            "local_asr_engine", defaults.local_asr_engine
        )
        if local_asr_engine not in LOCAL_ASR_ENGINES:
            raise InvalidTaskOperation("invalid local ASR engine")
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
        translation_enabled = (
            False
            if output_type is not None
            else options.get("translation_enabled", defaults.translation_enabled)
        )
        translation_profile_id = options.get(
            "translation_profile_id", defaults.translation_profile_id
        )
        translation = (
            self._profile_snapshot(translation_profile_id, "translation")
            if translation_enabled
            else None
        )
        notes_enabled = (
            output_type == "notes"
            if output_type is not None
            else options.get("notes_enabled", defaults.notes_enabled)
        )
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
        if "notes_custom_prompt" in options:
            custom_prompt = options["notes_custom_prompt"]
        elif defaults.has_custom_prompt:
            custom_prompt = self.configuration.resolve_default_custom_prompt()
        else:
            custom_prompt = None
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
        custom_prompt_envelope = None
        if notes_template == "custom":
            custom_prompt_envelope = (
                self.configuration.sensitive_text_protector.protect(
                    task_prompt_purpose(task_id), custom_prompt
                ).model_dump(mode="json")
            )
        snapshot = {
            "schema_version": 1,
            "asr": {"mode": asr_mode, "profile": cloud},
            "local_asr": build_local_asr_snapshot(local_asr_engine),
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
                "custom_prompt_envelope": custom_prompt_envelope,
            },
        }
        snapshot["local_whisper"] = dict(defaults.local_whisper_options)
        if output_type is not None:
            snapshot["output_type"] = output_type
        snapshot["audio_export_enabled"] = audio_export_enabled
        return snapshot

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

    def _build_validated_task(
        self,
        *,
        validated: list[tuple[str, str, str | None]],
        selected_options: dict[str, Any],
    ) -> TaskRecord:
        task_id = str(uuid4())
        snapshot = self._pipeline_snapshot(selected_options, task_id)
        stored_options = dict(selected_options)
        stored_options.pop("notes_custom_prompt", None)
        task = TaskRecord(
            id=task_id,
            options=stored_options,
            pipeline_snapshot_json=snapshot,
        )
        translation_enabled = snapshot["translation"]["enabled"]
        notes_enabled = snapshot["notes"]["enabled"]
        output_type = snapshot.get("output_type")
        for position, (kind, locator, display_name) in enumerate(validated):
            item = ItemRecord(
                position=position,
                source_kind=kind,
                source_locator=locator,
                source_display_name=display_name,
            )
            if output_type == "audio":
                stages = ["source"]
            elif output_type == "transcript":
                stages = ["source", "transcribe"]
            elif output_type == "notes":
                stages = ["source", "transcribe", "notes"]
            else:
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
        return task

    def _create_validated_task(
        self,
        *,
        validated: list[tuple[str, str, str | None]],
        selected_options: dict[str, Any],
    ) -> TaskView:
        task = self._build_validated_task(
            validated=validated,
            selected_options=selected_options,
        )
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

    def create_batch_tasks(
        self,
        *,
        sources: list[dict[str, str]],
        options: dict[str, Any] | None = None,
    ) -> tuple[TaskView, ...]:
        if not sources:
            raise InvalidTaskOperation("at least one source is required")
        if len(sources) > MAX_BATCH_SOURCES:
            raise InvalidTaskOperation(
                f"batch cannot exceed {MAX_BATCH_SOURCES} sources"
            )
        selected_options = dict(options or {})
        if set(selected_options) - _TASK_OPTION_KEYS:
            raise InvalidTaskOperation("unsupported task option")
        validated = [(*self._validate_source(source), None) for source in sources]
        locators = [item[1] for item in validated]
        if len(locators) != len(set(locators)):
            raise InvalidTaskOperation("batch sources must be unique")
        try:
            task_ids = [
                self._build_validated_task(
                    validated=[source],
                    selected_options=selected_options,
                ).id
                for source in validated
            ]
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return tuple(self._view(self._load_task(task_id)) for task_id in task_ids)

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
        ids = self.session.scalars(
            select(TaskRecord.id).where(
                (TaskRecord.terminal_reason_code.is_(None))
                | (TaskRecord.terminal_reason_code != _PROFILE_TEST_SAMPLE_REASON)
            ).order_by(
                TaskRecord.created_at.desc(),
                TaskRecord.id.desc(),
            )
        ).all()
        return [self._view(self._load_task(task_id)) for task_id in ids]

    def list_tasks_page(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        status: str | None = None,
    ) -> tuple[list[TaskView], str | None]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidTaskOperation("task page limit must be between 1 and 100")
        query = select(TaskRecord.id).where(
            (TaskRecord.terminal_reason_code.is_(None))
            | (TaskRecord.terminal_reason_code != _PROFILE_TEST_SAMPLE_REASON)
        )
        if status is not None:
            if not status or len(status) > 32:
                raise InvalidTaskOperation("invalid task status filter")
            query = query.where(TaskRecord.status == status)
        if cursor is not None:
            marker = self.session.get(TaskRecord, cursor)
            if marker is None:
                raise InvalidTaskOperation("invalid task cursor")
            query = query.where(
                (TaskRecord.created_at < marker.created_at)
                | (
                    (TaskRecord.created_at == marker.created_at)
                    & (TaskRecord.id < marker.id)
                )
            )
        ids = list(
            self.session.scalars(
                query.order_by(
                    TaskRecord.created_at.desc(),
                    TaskRecord.id.desc(),
                ).limit(limit + 1)
            ).all()
        )
        next_cursor = ids[limit - 1] if len(ids) > limit else None
        return (
            [self._view(self._load_task(task_id)) for task_id in ids[:limit]],
            next_cursor,
        )

    def get_task(self, task_id: str) -> TaskView:
        return self._view(self._load_task(task_id))

    def delete_tasks(self, task_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return TaskDeletionService(self.session, self.paths).delete_tasks(task_ids)

    def finalize_profile_test_sample(self, task_id: str) -> ItemView:
        """Hide a validated short sample from the work queue and task history."""

        task = self._load_task(task_id)
        if len(task.items) != 1 or task.items[0].source_kind != "uploaded_media":
            raise InvalidTaskOperation("invalid profile test sample")
        if task.status not in _TERMINAL:
            self.cancel_task(task_id)
            task = self._load_task(task_id)
        task.terminal_reason_code = _PROFILE_TEST_SAMPLE_REASON
        self.session.commit()
        return self._view_item(task.items[0])

    def require_profile_test_sample(self, item_id: str) -> ItemRecord:
        item = self._require_item(item_id)
        if (
            item.task.terminal_reason_code != _PROFILE_TEST_SAMPLE_REASON
            or item.status != "canceled"
            or item.source_kind != "uploaded_media"
        ):
            raise InvalidTaskOperation("profile test sample is unavailable")
        return item

    def _require_item(self, item_id: str) -> ItemRecord:
        item = self.session.scalar(
            select(ItemRecord)
            .where(ItemRecord.id == item_id)
            .options(
                selectinload(ItemRecord.stage_runs),
                selectinload(ItemRecord.task),
            )
        )
        if item is None:
            raise KeyError(item_id)
        return item

    _read_result = staticmethod(read_result_artifact)

    def get_item_transcript(self, item_id: str) -> Transcript:
        self._require_item(item_id)
        try:
            return Transcript.model_validate_json(
                self._read_result(self.paths.transcript(item_id))
            )
        except ValidationError as error:
            raise InvalidTaskOperation("transcript artifact is invalid") from error

    def get_item_alignment(self, item_id: str) -> TranscriptAlignment:
        transcript = self.get_item_transcript(item_id)
        try:
            alignment = TranscriptAlignment.model_validate_json(
                self._read_result(self.paths.transcript_alignment(item_id))
            )
        except ValidationError as error:
            raise InvalidTaskOperation("alignment artifact is invalid") from error
        if alignment.source_transcript_sha256 != transcript_sha256(transcript):
            raise InvalidTaskOperation("alignment artifact does not match transcript")
        return alignment

    def get_item_speakers(self, item_id: str) -> SpeakerMap:
        transcript = self.get_item_transcript(item_id)
        try:
            speakers = SpeakerMap.model_validate_json(
                self._read_result(self.paths.speaker_map(item_id))
            )
        except ValidationError as error:
            raise InvalidTaskOperation("speaker artifact is invalid") from error
        if speakers.source_transcript_sha256 != transcript_sha256(transcript):
            raise InvalidTaskOperation("speaker artifact does not match transcript")
        return speakers

    def get_item_translation(self, item_id: str, language: str) -> Translation:
        transcript = self.get_item_transcript(item_id)
        try:
            translation = Translation.model_validate_json(
                self._read_result(self.paths.translation(item_id, language))
            )
            return translation.validate_against(transcript)
        except ValidationError as error:
            raise InvalidTaskOperation("translation artifact is invalid") from error
        except ValueError as error:
            raise InvalidTaskOperation("translation artifact does not match transcript") from error

    _note_metadata = staticmethod(parse_note_metadata)

    def list_item_notes(self, item_id: str) -> list[dict[str, Any]]:
        self._require_item(item_id)
        notes_root = self.paths.durable("items", str(UUID(item_id)), "notes")
        if not notes_root.is_dir():
            return []
        notes: list[dict[str, Any]] = []
        for path in sorted(notes_root.glob("*.md"), key=lambda item: item.name):
            try:
                note_id = str(UUID(path.stem))
            except ValueError:
                continue
            owned_path = self.paths.note(item_id, note_id)
            if path != owned_path:
                continue
            try:
                markdown = self._read_result(owned_path).decode("utf-8")
            except UnicodeDecodeError as error:
                raise InvalidTaskOperation("note artifact is not UTF-8") from error
            notes.append(
                {
                    "id": note_id,
                    "markdown": markdown,
                    **self._note_metadata(markdown),
                }
            )
        return notes

    def item_outcomes(self, item_id: str) -> dict[str, bool]:
        self._require_item(item_id)
        return {
            "transcript": self.paths.transcript(item_id).is_file(),
            "notes": bool(self.list_item_notes(item_id)),
        }

    def item_execution_summary(self, item_id: str) -> dict[str, Any]:
        item = self._require_item(item_id)
        sensitive_values = self.configuration.diagnostic_sensitive_values()
        view = self._view_item(item, sensitive_values)
        return {
            "schema_version": 1,
            "task_id": item.task_id,
            "task_status": item.task.status,
            "item_id": view.id,
            "item_status": view.status,
            "title": view.title,
            "source_kind": view.source_kind,
            "stages": [
                {
                    "stage": stage.stage,
                    "attempt": stage.attempt,
                    "status": stage.status,
                    "error_code": stage.error_code,
                    "warning": stage.warning,
                    "progress": stage.progress,
                    "execution_evidence": stage.execution_evidence,
                    "provider_status_code": stage.provider_status_code,
                    "external_submission_state": stage.external_submission_state,
                    "started_at": (
                        stage.started_at.isoformat()
                        if stage.started_at is not None
                        else None
                    ),
                    "finished_at": (
                        stage.finished_at.isoformat()
                        if stage.finished_at is not None
                        else None
                    ),
                }
                for stage in view.stage_runs
            ],
        }

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

    def record_stage_progress(
        self, stage_run_id: str, progress: dict[str, object],
    ) -> StageView:
        try:
            normalized = validate_stage_progress(progress)
        except ValueError as error:
            raise InvalidTaskOperation(str(error)) from error
        row = self.session.get(StageRunRecord, stage_run_id)
        if row is None:
            raise KeyError(stage_run_id)
        sensitive_values = self.configuration.diagnostic_sensitive_values()
        if self._contains_sensitive_value(
            (normalized["message_code"],), sensitive_values
        ):
            raise InvalidTaskOperation("stage progress contains sensitive value")
        row.progress_json = dict(normalized)
        self.session.commit()
        return self._stage_view(row, sensitive_values)

    def record_stage_evidence(
        self,
        stage_run_id: str,
        evidence: dict[str, object],
        *,
        provider_status_code: str | None = None,
    ) -> StageView:
        row = self.session.get(StageRunRecord, stage_run_id)
        if row is None:
            raise KeyError(stage_run_id)
        try:
            normalized = validate_execution_evidence(
                evidence,
                allowed_models=self._allowed_stage_models(row),
            )
            safe_provider_status = validate_provider_status_code(provider_status_code)
        except ValueError as error:
            raise InvalidTaskOperation(str(error)) from error
        sensitive_values = self.configuration.diagnostic_sensitive_values()
        evidence_values = tuple(normalized.values())
        if safe_provider_status is not None:
            evidence_values += (safe_provider_status,)
        if self._contains_sensitive_value(evidence_values, sensitive_values):
            raise InvalidTaskOperation("stage evidence contains sensitive value")
        row.execution_evidence_json = dict(normalized)
        row.provider_status_code = safe_provider_status
        self.session.commit()
        return self._stage_view(row, sensitive_values)

    def cancel_task(self, task_id: str) -> TaskView:
        task = self._load_task(task_id)
        if task.status == "canceled" and task.terminal_reason_code == "user_canceled":
            return self._view(task)
        if task.status in _TERMINAL:
            raise InvalidTaskOperation("terminal task cannot be canceled")
        if task.status == "cancel_requested":
            return self._view(task)
        running = any(
            run.status == "running"
            for item in task.items
            for run in item.stage_runs
        )
        target = "cancel_requested" if running else "canceled"
        expected_status = task.status
        task_update = self.session.execute(
            update(TaskRecord)
            .where(
                TaskRecord.id == task_id,
                TaskRecord.status == expected_status,
            )
            .values(
                status=target,
                terminal_reason_code="user_canceled",
            )
            .execution_options(synchronize_session=False)
        )
        if task_update.rowcount != 1:
            self.session.rollback()
            self.session.expire_all()
            current = self._load_task(task_id)
            if (
                current.status == "canceled"
                and current.terminal_reason_code == "user_canceled"
            ):
                return self._view(current)
            if current.status in _TERMINAL:
                raise InvalidTaskOperation("terminal task cannot be canceled")
            if current.status == "cancel_requested":
                return self._view(current)
            raise InvalidTaskOperation(
                "task cancellation conflicted; refresh and retry"
            )
        item_ids = select(ItemRecord.id).where(ItemRecord.task_id == task_id)
        self.session.execute(
            update(ItemRecord)
            .where(
                ItemRecord.task_id == task_id,
                ItemRecord.status.not_in(_TERMINAL),
            )
            .values(status=target)
            .execution_options(synchronize_session=False)
        )
        self.session.execute(
            update(StageRunRecord)
            .where(
                StageRunRecord.item_id.in_(item_ids),
                StageRunRecord.status == "running",
            )
            .values(status="cancel_requested")
            .execution_options(synchronize_session=False)
        )
        self.session.execute(
            update(StageRunRecord)
            .where(
                StageRunRecord.item_id.in_(item_ids),
                StageRunRecord.status == "queued",
            )
            .values(status="canceled")
            .execution_options(synchronize_session=False)
        )
        self.session.execute(
            update(StageRunRecord)
            .where(
                StageRunRecord.item_id.in_(item_ids),
                StageRunRecord.status == "waiting_external",
            )
            .values(
                status="canceled",
                finished_at=datetime.now(timezone.utc),
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        self.session.commit()
        self.session.expire_all()
        return self._view(self._load_task(task_id))

    def retry_stage(
        self,
        item_id: str,
        stage: str,
        expected_attempt: int,
        override: RetryOverrideSnapshot,
        *,
        acknowledge_possible_charge: bool = False,
    ) -> ItemView:
        if type(expected_attempt) is not int or expected_attempt <= 0:
            raise InvalidTaskOperation("expected_attempt must be a positive integer")
        if type(acknowledge_possible_charge) is not bool:
            raise InvalidTaskOperation("invalid possible-charge acknowledgement")
        self._reserve_retry_transaction()
        try:
            normalized_override = self._validate_retry_override(override)
            strategy = normalized_override["strategy"]
            if strategy == "cloud_confirmed":
                if acknowledge_possible_charge is not True:
                    raise InvalidTaskOperation(
                        "cloud retry requires acknowledging the possible charge"
                    )
            elif strategy == "local" and acknowledge_possible_charge:
                raise InvalidTaskOperation(
                    "possible-charge acknowledgement is invalid for local retry"
                )
            if strategy in {"local", "cloud_confirmed"} and stage != "transcribe":
                raise InvalidTaskOperation(
                    "ASR retry strategies are valid only for transcribe"
                )
            item = self.session.scalar(
                select(ItemRecord)
                .where(ItemRecord.id == item_id)
                .options(
                    selectinload(ItemRecord.stage_runs),
                    selectinload(ItemRecord.task),
                )
            )
            if item is None:
                raise KeyError(item_id)
            if (
                item.status == "cancel_requested"
                or item.task.status == "cancel_requested"
            ):
                raise InvalidTaskOperation("cannot retry while cancellation is active")
            if stage not in _STAGE_DEPENDENCIES:
                raise InvalidTaskOperation("unknown pipeline stage")
            if "notes" in normalized_override and stage != "notes":
                raise InvalidTaskOperation(
                    "notes retry override is valid only for notes"
                )
            if any(
                run.status in _ACTIVE
                and run.stage in _RETRY_ACTIVE_CONFLICTS[stage]
                for run in item.stage_runs
            ):
                raise InvalidTaskOperation(
                    "cannot retry while a conflicting stage is active"
                )
            attempts = [run for run in item.stage_runs if run.stage == stage]
            latest_attempt = (
                max(attempts, key=lambda run: run.attempt) if attempts else None
            )
            if latest_attempt is None:
                raise InvalidTaskOperation(
                    "only a failed or canceled stage can be retried"
                )
            if latest_attempt.attempt != expected_attempt:
                raise InvalidTaskOperation(
                    "stage retry conflicted; refresh and retry"
                )
            regenerating_completed_notes = (
                stage == "notes" and latest_attempt.status == "completed"
            )
            if (
                latest_attempt.status not in _RETRYABLE
                and not regenerating_completed_notes
            ):
                raise InvalidTaskOperation(
                    "only a failed or canceled stage can be retried"
                )
            submission_unknown = (
                latest_attempt.external_submission_state == "submission_unknown"
            )
            if stage == "transcribe":
                if submission_unknown and strategy == "same":
                    raise InvalidTaskOperation(
                        "submission_unknown requires an explicit local or "
                        "cloud_confirmed retry"
                    )
                if strategy == "cloud_confirmed" and not submission_unknown:
                    raise InvalidTaskOperation(
                        "cloud_confirmed is valid only for submission_unknown"
                    )
                if strategy == "same" and acknowledge_possible_charge:
                    raise InvalidTaskOperation(
                        "possible-charge acknowledgement is not valid for this retry"
                    )
            elif stage in {"translate", "notes"}:
                if strategy != "same":
                    raise InvalidTaskOperation(
                        "AI stages can only retry the immutable task snapshot"
                    )
                charge_acknowledged = (
                    normalized_override.get("charge_acknowledged") is True
                    and acknowledge_possible_charge is True
                )
                if submission_unknown and not charge_acknowledged:
                    raise InvalidTaskOperation(
                        "AI submission has an unknown outcome; acknowledge the "
                        "possible charge before retrying"
                    )
                if not submission_unknown and (
                    acknowledge_possible_charge
                    or normalized_override.get("charge_acknowledged") is True
                ):
                    raise InvalidTaskOperation(
                        "possible-charge acknowledgement is not valid for this retry"
                    )
            latest_by_stage: dict[str, StageRunRecord] = {}
            for run in item.stage_runs:
                previous = latest_by_stage.get(run.stage)
                if previous is None or run.attempt > previous.attempt:
                    latest_by_stage[run.stage] = run
            for prerequisite in _STAGE_DEPENDENCIES[stage]:
                latest = latest_by_stage.get(prerequisite)
                if latest is None or latest.status not in _SUCCESSFUL_PREREQUISITE:
                    raise InvalidTaskOperation(
                        "stage prerequisite has not succeeded"
                    )
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
            retry = StageRunRecord(
                stage=stage,
                attempt=latest_attempt.attempt + 1,
                status="queued",
                retry_override_json=dict(normalized_override),
            )
            item.stage_runs.append(retry)
            self.session.flush()
            item.status = "running" if item_has_active_work else "queued"
            item.task.status = "running" if task_has_active_work else "queued"
            item.task.terminal_reason_code = None
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidTaskOperation("stage retry conflicted; refresh and retry") from None
        except OperationalError as error:
            self.session.rollback()
            if self._is_sqlite_retry_conflict(error):
                raise InvalidTaskOperation(
                    "stage retry conflicted; refresh and retry"
                ) from None
            raise
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(item)
        return self._view_item(item)

    def resolve_notes_custom_prompt(
        self, item_id: str, *, attempt: int
    ) -> str | None:
        """Resolve the immutable prompt only inside a concrete notes attempt."""

        require_sensitive_text_migration(self.session)
        item = self.session.scalar(
            select(ItemRecord)
            .where(ItemRecord.id == item_id)
            .options(
                selectinload(ItemRecord.stage_runs),
                selectinload(ItemRecord.task),
            )
        )
        if item is None:
            raise KeyError(item_id)
        if not any(
            run.stage == "notes" and run.attempt == attempt
            for run in item.stage_runs
        ):
            raise InvalidTaskOperation("notes attempt does not exist")
        notes = item.task.pipeline_snapshot_json.get("notes")
        if not isinstance(notes, dict):
            return None
        raw_envelope = notes.get("custom_prompt_envelope")
        if raw_envelope is None:
            return None
        envelope = validate_protected_text_envelope(raw_envelope)
        return self.configuration.sensitive_text_protector.unprotect(
            task_prompt_purpose(item.task_id), envelope
        )

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
