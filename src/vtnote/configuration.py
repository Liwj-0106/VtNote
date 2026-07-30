"""Direct configuration services with redacted public views."""

from __future__ import annotations

import uuid
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from vtnote.models import (
    CredentialCleanupRecord,
    DefaultSettingsRecord,
    ItemRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
    TaskRecord,
)
from vtnote.chat import (
    bailian_base_url,
    bailian_chat_endpoint,
    validate_chat_model,
)
from vtnote.diagnostics import sanitize_diagnostic
from vtnote.secrets import SecretStore
from vtnote.paths import StoragePaths
from vtnote.provider_credentials import (
    CredentialBundle,
    CredentialReentryRequired,
    configured_credential_fields,
    parse_credential_bundle,
    serialize_credential_bundle,
)
from vtnote.tencent_contract import (
    TENCENT_ASR_ENDPOINT,
    TENCENT_ASR_MODEL,
    TENCENT_ASR_REGION,
    TENCENT_LANGUAGE_SCOPE,
)
from vtnote.sensitive_text import (
    DEFAULT_PROMPT_PURPOSE,
    SensitiveTextProtector,
    WindowsDpapiSensitiveTextProtector,
    require_sensitive_text_migration,
    validate_protected_text_envelope,
)


class InvalidConfiguration(ValueError):
    pass


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectionView(PublicModel):
    id: str
    name: str
    protocol: str
    base_url: str
    parameters: dict[str, Any]
    revision: int
    has_secret: bool
    configured_fields: dict[str, bool]
    tested: bool
    test_ok: bool | None
    test_message: str | None
    cleanup_pending: bool


class CredentialCleanupStatusView(PublicModel):
    cleanup_pending: bool
    pending_count: int


class ProfileView(PublicModel):
    id: str
    name: str
    purpose: str
    connection_id: str
    protocol: str
    base_url: str
    model: str
    context_length: int
    options: dict[str, Any]
    revision: int
    tested: bool
    test_ok: bool | None
    test_message: str | None
    upload_authorized: bool
    capability_fingerprint: dict[str, Any] | None
    chat_data_authorized: bool


class DefaultsView(PublicModel):
    asr_mode: Literal["auto", "cloud", "local"]
    cloud_asr_profile_id: str | None
    translation_enabled: bool
    translation_profile_id: str | None
    translation_target_language: str
    notes_enabled: bool
    notes_profile_id: str | None
    notes_template: Literal["summary", "key_points", "custom"]
    notes_output_language: str
    has_custom_prompt: bool
    local_whisper_options: dict[str, Any]


_PROTOCOL_PARAMETERS = {
    "volc_bigasr_flash": frozenset(),
    "tencent_recording_asr": frozenset(
        {
            "asr_region",
            "cos_bucket",
            "cos_region",
            "cos_prefix",
            "cos_private",
            "cos_configured",
        }
    ),
    "aliyun_bailian": frozenset({"workspace_id"}),
}
_ACTIVE_PROTOCOLS = frozenset({"tencent_recording_asr", "aliyun_bailian"})
_PURPOSE_PROTOCOL = {
    "cloud_asr": "tencent_recording_asr",
    "translation": "aliyun_bailian",
    "notes": "aliyun_bailian",
}
_PURPOSE_OPTIONS = {
    "cloud_asr": frozenset(
        {"language_scope", "res_text_format", "sentence_max_length"}
    ),
    "translation": frozenset({"temperature", "max_tokens", "enable_thinking"}),
    "notes": frozenset({"temperature", "max_tokens", "enable_thinking"}),
}
_TERMINAL_TASK_STATUSES = frozenset(
    {"canceled", "completed", "completed_with_warnings", "failed"}
)
_RETRYABLE_STAGE_STATUSES = frozenset({"failed", "canceled"})
_ARCHIVED_NAME_PREFIX = "__vtnote_archived__:"
_CHAT_PURPOSES = frozenset({"translation", "notes"})
_MIN_CHAT_CONTEXT_LENGTH = 32_768
_MIN_CHAT_MAX_TOKENS = 1_024


def _reject_reserved_name(value: str) -> None:
    if value.casefold().startswith(_ARCHIVED_NAME_PREFIX):
        raise InvalidConfiguration("configuration name uses a reserved internal prefix")


_COS_BUCKET_RE = re.compile(
    r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]-[1-9][0-9]{4,11}$"
)


def _normalize_connection_parameters(
    protocol: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    if set(parameters) - _PROTOCOL_PARAMETERS[protocol]:
        raise InvalidConfiguration("unsupported or secret connection parameter")
    if protocol == "tencent_recording_asr":
        configured = parameters.get("cos_configured")
        cos_keys = {"cos_bucket", "cos_region", "cos_prefix", "cos_private"}
        supplied_cos_keys = cos_keys.intersection(parameters)
        if supplied_cos_keys:
            if supplied_cos_keys != cos_keys:
                raise InvalidConfiguration("COS configuration must be complete")
            if configured is False:
                raise InvalidConfiguration("COS configuration state conflicts with fields")
            bucket = parameters["cos_bucket"]
            region = parameters["cos_region"]
            prefix = parameters["cos_prefix"]
            private = parameters["cos_private"]
            if not isinstance(bucket, str) or _COS_BUCKET_RE.fullmatch(bucket) is None:
                raise InvalidConfiguration("invalid private COS bucket")
            if region != TENCENT_ASR_REGION:
                raise InvalidConfiguration("COS region must be ap-guangzhou")
            if prefix != "vtnote-runtime":
                raise InvalidConfiguration("COS prefix must be vtnote-runtime")
            if private is not True:
                raise InvalidConfiguration("COS bucket must be private")
            if parameters.get("asr_region", TENCENT_ASR_REGION) != TENCENT_ASR_REGION:
                raise InvalidConfiguration("Tencent ASR region must be ap-guangzhou")
            return {
                "asr_region": TENCENT_ASR_REGION,
                "cos_bucket": bucket,
                "cos_region": region,
                "cos_prefix": prefix,
                "cos_private": True,
                "cos_configured": True,
            }
        if set(parameters) - {"asr_region", "cos_configured"}:
            raise InvalidConfiguration("COS configuration must be complete")
        if parameters.get("asr_region", TENCENT_ASR_REGION) != TENCENT_ASR_REGION:
            raise InvalidConfiguration("Tencent ASR region must be ap-guangzhou")
        if configured is not None and configured is not False:
            raise InvalidConfiguration("COS configuration fields are missing")
        return {
            "asr_region": TENCENT_ASR_REGION,
            "cos_configured": False,
        }
    if protocol == "aliyun_bailian":
        workspace_id = parameters.get("workspace_id")
        try:
            bailian_base_url(workspace_id)
        except ValueError:
            raise InvalidConfiguration("invalid Bailian workspace_id") from None
        return {"workspace_id": workspace_id}
    if any(not isinstance(value, str) or not value.strip() for value in parameters.values()):
        raise InvalidConfiguration("connection parameter values must be non-empty strings")
    return dict(parameters)


def _validate_profile_options(purpose: str, options: dict[str, Any]) -> None:
    if set(options) - _PURPOSE_OPTIONS[purpose]:
        raise InvalidConfiguration("unsupported profile option")
    language = options.get("language")
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise InvalidConfiguration("invalid profile option value")
    temperature = options.get("temperature")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not 0 <= temperature <= 2
    ):
        raise InvalidConfiguration("invalid profile option value")
    max_tokens = options.get("max_tokens")
    if max_tokens is not None and (
        isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0
    ):
        raise InvalidConfiguration("invalid profile option value")
    enable_thinking = options.get("enable_thinking")
    if enable_thinking is not None and not isinstance(enable_thinking, bool):
        raise InvalidConfiguration("invalid profile option value")


def _normalize_profile_contract(
    purpose: str,
    protocol: str,
    model: str,
    options: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    cleaned_model = model.strip()
    if purpose == "cloud_asr":
        if protocol == "volc_bigasr_flash":
            if set(options) - {"language"}:
                raise InvalidConfiguration("unsupported profile option")
            language = options.get("language")
            if language is not None and (
                not isinstance(language, str) or not language.strip()
            ):
                raise InvalidConfiguration("invalid profile option value")
            return cleaned_model, dict(options)
        if protocol != "tencent_recording_asr":
            raise InvalidConfiguration(
                "profile purpose is incompatible with connection protocol"
            )
        if cleaned_model != TENCENT_ASR_MODEL:
            raise InvalidConfiguration("Tencent ASR model must be 16k_zh_en_2.0")
        if set(options) - _PURPOSE_OPTIONS[purpose]:
            raise InvalidConfiguration("unsupported profile option")
        if options.get("language_scope", TENCENT_LANGUAGE_SCOPE) != TENCENT_LANGUAGE_SCOPE:
            raise InvalidConfiguration("Tencent ASR language scope is fixed")
        if options.get("res_text_format", 3) != 3:
            raise InvalidConfiguration("Tencent ASR result format is fixed")
        if options.get("sentence_max_length", 20) != 20:
            raise InvalidConfiguration("Tencent ASR sentence length is fixed")
        return cleaned_model, {
            "language_scope": TENCENT_LANGUAGE_SCOPE,
            "res_text_format": 3,
            "sentence_max_length": 20,
        }
    if protocol != "aliyun_bailian":
        raise InvalidConfiguration(
            "profile purpose is incompatible with connection protocol"
        )
    _validate_profile_options(purpose, options)
    try:
        cleaned_model = validate_chat_model(cleaned_model)
    except ValueError:
        raise InvalidConfiguration("invalid Bailian model") from None
    normalized = {
        "temperature": options.get("temperature", 0.2),
        "max_tokens": options.get("max_tokens", 4096),
        "enable_thinking": options.get("enable_thinking", False),
    }
    if normalized["max_tokens"] < _MIN_CHAT_MAX_TOKENS:
        raise InvalidConfiguration(
            f"max_tokens must be at least {_MIN_CHAT_MAX_TOKENS}"
        )
    return cleaned_model, normalized


def _purpose_protocol_is_compatible(purpose: str, protocol: str) -> bool:
    return _PURPOSE_PROTOCOL.get(purpose) == protocol


def _validate_profile_capacity(
    purpose: str,
    context_length: int,
    options: dict[str, Any],
) -> None:
    if purpose not in _CHAT_PURPOSES:
        return
    if context_length < _MIN_CHAT_CONTEXT_LENGTH:
        raise InvalidConfiguration(
            f"chat context_length must be at least {_MIN_CHAT_CONTEXT_LENGTH}"
        )
    max_tokens = options.get("max_tokens")
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens < _MIN_CHAT_MAX_TOKENS
        or max_tokens >= context_length
    ):
        raise InvalidConfiguration(
            f"max_tokens must be at least {_MIN_CHAT_MAX_TOKENS} "
            "and smaller than context_length"
        )


def _validate_local_whisper_options(options: dict[str, Any]) -> None:
    if set(options) - {
        "model", "device", "compute_type", "vad_filter", "model_root", "cache_root"
    }:
        raise InvalidConfiguration("unsupported local Whisper option")
    string_keys = {"model", "device", "compute_type", "model_root", "cache_root"}
    if any(
        key in string_keys and (not isinstance(value, str) or not value.strip())
        for key, value in options.items()
    ) or ("vad_filter" in options and not isinstance(options["vad_filter"], bool)):
        raise InvalidConfiguration("local Whisper options must be non-empty strings")
    if any(
        options.get(name) != expected
        for name, expected in {
            "model": "large-v3-turbo",
            "device": "cuda",
            "compute_type": "int8_float16",
            "vad_filter": True,
        }.items()
    ):
        raise InvalidConfiguration(
            "local Whisper is GPU-only with fixed model and runtime settings"
        )


def _clean_base_url(
    value: str | None,
    protocol: str,
    parameters: dict[str, Any] | None = None,
) -> str:
    if protocol == "aliyun_bailian":
        try:
            expected = bailian_base_url((parameters or {}).get("workspace_id"))
        except ValueError:
            raise InvalidConfiguration("invalid Bailian workspace_id") from None
        if value is not None and value != expected:
            raise InvalidConfiguration("Bailian endpoint is fixed to Beijing workspace")
        return expected
    if not isinstance(value, str):
        raise InvalidConfiguration("provider base URL is required")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _ = parts.port
    except ValueError as error:
        raise InvalidConfiguration("invalid provider base URL") from error
    if parts.username or parts.password or parts.query or parts.fragment:
        raise InvalidConfiguration("invalid provider base URL")
    if not host:
        raise InvalidConfiguration("invalid provider base URL")
    if parts.scheme == "http":
        raise InvalidConfiguration("cloud provider base URL must use HTTPS")
    if parts.scheme not in {"http", "https"}:
        raise InvalidConfiguration("invalid provider base URL")
    if parts.scheme != "https":
        raise InvalidConfiguration("invalid provider base URL")
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    if protocol == "tencent_recording_asr" and cleaned != TENCENT_ASR_ENDPOINT:
        raise InvalidConfiguration("Tencent ASR endpoint is fixed")
    return cleaned


def _clean_message(
    message: str | None, sensitive_values: tuple[str | None, ...] = ()
) -> str | None:
    return sanitize_diagnostic(message, sensitive_values)


def _chat_capability_fingerprint(
    row: ProcessorProfileRecord,
) -> dict[str, Any] | None:
    connection = row.connection
    if row.purpose not in _CHAT_PURPOSES or connection.protocol != "aliyun_bailian":
        return None
    options = dict(row.options)
    return {
        "schema_version": 1,
        "protocol": "aliyun_bailian",
        "endpoint": bailian_chat_endpoint(connection.parameters["workspace_id"]),
        "connection_revision": connection.revision,
        "profile_revision": row.revision,
        "model": row.model,
        "response_format": "json_object",
        "enable_thinking": options["enable_thinking"],
        "options": {
            "temperature": options["temperature"],
            "max_tokens": options["max_tokens"],
        },
    }


def chat_capability_fingerprint_digest(value: Mapping[str, Any]) -> str:
    """Return the stable non-secret digest bound to one tested chat capability."""

    body = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class ConfigurationService:
    def __init__(
        self,
        session: Session,
        secrets: SecretStore,
        *,
        paths: StoragePaths | None = None,
        sensitive_text_protector: SensitiveTextProtector | None = None,
    ) -> None:
        self.session = session
        self.secrets = secrets
        self.sensitive_text_protector = (
            sensitive_text_protector
            or WindowsDpapiSensitiveTextProtector()
        )
        data_root = paths.data_root if paths else Path(r"D:\Workspace\Project\VtNote-data")
        cache_root = (
            paths.runtime_cache_root
            if paths
            else Path(r"D:\Workspace\Codex\cache\VtNote-runtime")
        )
        self.local_whisper_defaults = {
            "model": "large-v3-turbo",
            "device": "cuda",
            "compute_type": "int8_float16",
            "vad_filter": True,
            "model_root": str(data_root / "models" / "faster-whisper"),
            "cache_root": str(cache_root / "models" / "faster-whisper"),
        }
        self._data_root = data_root.resolve(strict=False)
        self._cache_root = cache_root.resolve(strict=False)

    def _validate_local_roots(self, options: dict[str, Any]) -> None:
        for key, root in (
            ("model_root", self._data_root),
            ("cache_root", self._cache_root),
        ):
            candidate = Path(options[key])
            try:
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(root)
            except (ValueError, OSError):
                raise InvalidConfiguration(
                    "local Whisper paths must remain under the configured D-drive roots"
                ) from None
            if resolved.drive.casefold() != "d:":
                raise InvalidConfiguration(
                    "local Whisper paths must remain under the configured D-drive roots"
                )

    def _connection(
        self, connection_id: str, *, include_archived: bool = False
    ) -> ProviderConnectionRecord:
        row = self.session.get(ProviderConnectionRecord, connection_id)
        if row is None or (row.archived_at is not None and not include_archived):
            raise KeyError(connection_id)
        return row

    def _profile(
        self, profile_id: str, *, include_archived: bool = False
    ) -> ProcessorProfileRecord:
        row = self.session.get(ProcessorProfileRecord, profile_id)
        if row is None or (row.archived_at is not None and not include_archived):
            raise KeyError(profile_id)
        return row

    def _retire_archived_name_conflicts(
        self,
        record_type: type[ProviderConnectionRecord] | type[ProcessorProfileRecord],
        name: str,
        *,
        exclude_id: str | None = None,
    ) -> None:
        statement = select(record_type).where(
            record_type.name == name,
            record_type.archived_at.is_not(None),
        )
        if exclude_id is not None:
            statement = statement.where(record_type.id != exclude_id)
        for archived in self.session.scalars(statement).all():
            archived.name = f"{_ARCHIVED_NAME_PREFIX}{archived.id}"

    def _retained_snapshot_references(self) -> tuple[set[str], set[str]]:
        profile_ids: set[str] = set()
        connection_ids: set[str] = set()
        tasks = self.session.scalars(
            select(TaskRecord).options(
                selectinload(TaskRecord.items).selectinload(ItemRecord.stage_runs)
            )
        ).all()
        for task in tasks:
            retained = task.status not in _TERMINAL_TASK_STATUSES
            if not retained:
                latest_runs: dict[tuple[str, str], Any] = {}
                for item in task.items:
                    for run in item.stage_runs:
                        key = (item.id, run.stage)
                        previous = latest_runs.get(key)
                        if previous is None or run.attempt > previous.attempt:
                            latest_runs[key] = run
                retained = any(
                    run.status in _RETRYABLE_STAGE_STATUSES
                    for run in latest_runs.values()
                )
            if not retained:
                continue
            snapshot = task.pipeline_snapshot_json
            if not isinstance(snapshot, dict):
                continue
            for section_name in ("asr", "translation", "notes"):
                section = snapshot.get(section_name)
                profile = section.get("profile") if isinstance(section, dict) else None
                if not isinstance(profile, dict):
                    continue
                profile_id = profile.get("id")
                connection_id = profile.get("connection_id")
                if isinstance(profile_id, str):
                    profile_ids.add(profile_id)
                if isinstance(connection_id, str):
                    connection_ids.add(connection_id)
            for item in task.items:
                for run in item.stage_runs:
                    override = run.retry_override_json
                    if not isinstance(override, dict):
                        continue
                    asr = override.get("asr")
                    profile = asr.get("profile") if isinstance(asr, dict) else None
                    if not isinstance(profile, dict):
                        continue
                    profile_id = profile.get("id")
                    connection_id = profile.get("connection_id")
                    if isinstance(profile_id, str):
                        profile_ids.add(profile_id)
                    if isinstance(connection_id, str):
                        connection_ids.add(connection_id)
        return profile_ids, connection_ids

    def _queue_credential_cleanup(
        self, credential_ref: str, connection_id: str | None
    ) -> CredentialCleanupRecord:
        row = self.session.get(CredentialCleanupRecord, credential_ref)
        if row is None:
            row = CredentialCleanupRecord(
                credential_ref=credential_ref, connection_id=connection_id
            )
            self.session.add(row)
        elif row.connection_id is None and connection_id is not None:
            row.connection_id = connection_id
        return row

    def _attempt_credential_cleanup(self, credential_ref: str) -> bool:
        row = self.session.get(CredentialCleanupRecord, credential_ref)
        if row is None:
            return True
        row.attempts += 1
        row.last_attempt_at = datetime.now(timezone.utc)
        try:
            self.secrets.delete(credential_ref)
        except Exception:
            try:
                self.session.commit()
            except Exception:
                self.session.rollback()
            return False
        self.session.delete(row)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            return False
        return True

    def _delete_or_queue_orphaned_credential(
        self, credential_ref: str, connection_id: str | None = None
    ) -> None:
        try:
            self.secrets.delete(credential_ref)
        except Exception:
            self._queue_credential_cleanup(credential_ref, connection_id)
            self.session.commit()

    def _connection_view(self, row: ProviderConnectionRecord) -> ConnectionView:
        current = row.tested_revision == row.revision
        stored_secret = self.secrets.get(row.credential_ref)
        if row.protocol in {"tencent_recording_asr", "aliyun_bailian"}:
            configured_fields = configured_credential_fields(
                row.protocol,
                stored_secret,
            )
            has_secret = all(configured_fields.values())
        else:
            configured_fields = {}
            has_secret = stored_secret is not None
        cleanup_pending = self.session.scalar(
            select(CredentialCleanupRecord.credential_ref)
            .where(CredentialCleanupRecord.connection_id == row.id)
            .limit(1)
        ) is not None
        return ConnectionView(
            id=row.id,
            name=row.name,
            protocol=row.protocol,
            base_url=row.base_url,
            parameters=dict(row.parameters),
            revision=row.revision,
            has_secret=has_secret,
            configured_fields=configured_fields,
            tested=current,
            test_ok=row.test_ok if current else None,
            test_message=row.test_message if current else None,
            cleanup_pending=cleanup_pending,
        )

    def _profile_view(self, row: ProcessorProfileRecord) -> ProfileView:
        connection = row.connection
        tested = (
            row.tested_revision == row.revision
            and row.tested_connection_revision == connection.revision
        )
        authorized = (
            row.purpose == "cloud_asr"
            and row.upload_authorized_revision == row.revision
            and row.upload_authorized_connection_revision == connection.revision
        )
        current_fingerprint = _chat_capability_fingerprint(row)
        capability_fingerprint = (
            current_fingerprint
            if current_fingerprint is not None
            and dict(row.capability_fingerprint_json or {}) == current_fingerprint
            and tested
            and row.test_ok is True
            else None
        )
        chat_data_authorized = (
            capability_fingerprint is not None
            and row.chat_data_authorized_fingerprint
            == chat_capability_fingerprint_digest(capability_fingerprint)
        )
        return ProfileView(
            id=row.id,
            name=row.name,
            purpose=row.purpose,
            connection_id=row.connection_id,
            protocol=connection.protocol,
            base_url=connection.base_url,
            model=row.model,
            context_length=row.context_length,
            options=dict(row.options),
            revision=row.revision,
            tested=tested,
            test_ok=row.test_ok if tested else None,
            test_message=row.test_message if tested else None,
            upload_authorized=authorized,
            capability_fingerprint=capability_fingerprint,
            chat_data_authorized=chat_data_authorized,
        )

    def create_connection(
        self,
        *,
        name: str,
        protocol: str,
        base_url: str | None,
        parameters: dict[str, Any],
        secret: str | None = None,
        credentials: dict[str, object] | None = None,
    ) -> ConnectionView:
        if protocol not in _ACTIVE_PROTOCOLS:
            raise InvalidConfiguration("unsupported provider protocol")
        selected_parameters = _normalize_connection_parameters(protocol, parameters)
        if secret is not None and credentials is not None:
            raise InvalidConfiguration("cannot provide two credential formats")
        if protocol in {"tencent_recording_asr", "aliyun_bailian"}:
            if secret is not None:
                raise InvalidConfiguration(
                    "provider credentials must use the structured atomic form"
                )
            try:
                selected_secret = (
                    serialize_credential_bundle(protocol, credentials)
                    if credentials is not None
                    else None
                )
            except ValueError:
                raise InvalidConfiguration("invalid provider credentials") from None
        else:
            if credentials is not None:
                raise InvalidConfiguration("structured credentials are not supported")
            selected_secret = secret
        cleaned_name = name.strip()
        if not cleaned_name:
            raise InvalidConfiguration("connection name cannot be empty")
        _reject_reserved_name(cleaned_name)
        row = ProviderConnectionRecord(
            name=cleaned_name,
            protocol=protocol,
            base_url=_clean_base_url(base_url, protocol, selected_parameters),
            parameters=selected_parameters,
            credential_ref=f"connection:{uuid.uuid4()}",
        )
        try:
            self._retire_archived_name_conflicts(
                ProviderConnectionRecord, cleaned_name
            )
            self.session.flush()
            self.session.add(row)
            self.session.flush()
            if selected_secret is not None:
                self.secrets.set(row.credential_ref, selected_secret)
            self.session.commit()
        except Exception as error:
            self.session.rollback()
            if selected_secret is not None:
                self._delete_or_queue_orphaned_credential(row.credential_ref)
            if isinstance(error, IntegrityError):
                raise InvalidConfiguration("connection name already exists") from None
            raise
        return self._connection_view(row)

    def update_connection(
        self,
        connection_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        parameters: dict[str, Any] | None = None,
        secret: str | None = None,
        credentials: dict[str, object] | None = None,
        clear_secret: bool = False,
    ) -> ConnectionView:
        if secret is not None and credentials is not None:
            raise InvalidConfiguration("cannot provide two credential formats")
        if (secret is not None or credentials is not None) and clear_secret:
            raise InvalidConfiguration("cannot replace and clear a secret together")
        row = self._connection(connection_id)
        if row.protocol in {"tencent_recording_asr", "aliyun_bailian"}:
            if secret is not None:
                raise InvalidConfiguration(
                    "provider credentials must use the structured atomic form"
                )
            try:
                selected_secret = (
                    serialize_credential_bundle(row.protocol, credentials)
                    if credentials is not None
                    else None
                )
            except ValueError:
                raise InvalidConfiguration("invalid provider credentials") from None
        else:
            if credentials is not None:
                raise InvalidConfiguration("structured credentials are not supported")
            selected_secret = secret
        old_reference = row.credential_ref
        old_secret = self.secrets.get(old_reference)
        cleaned_name = row.name
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise InvalidConfiguration("connection name cannot be empty")
            cleaned_name = name.strip()
            _reject_reserved_name(cleaned_name)
        selected_parameters = dict(row.parameters)
        if parameters is not None:
            selected_parameters = _normalize_connection_parameters(
                row.protocol,
                parameters,
            )
        if row.protocol == "aliyun_bailian":
            cleaned_base_url = (
                _clean_base_url(base_url, row.protocol, selected_parameters)
                if base_url is not None or parameters is not None
                else row.base_url
            )
        else:
            cleaned_base_url = (
                _clean_base_url(base_url, row.protocol, selected_parameters)
                if base_url is not None
                else row.base_url
            )
        secret_changed = selected_secret is not None and selected_secret != old_secret
        secret_cleared = clear_secret and old_secret is not None
        execution_changed = (
            cleaned_base_url != row.base_url
            or selected_parameters != dict(row.parameters)
            or secret_changed
            or secret_cleared
        )
        display_changed = cleaned_name != row.name
        if not display_changed and not execution_changed:
            return self._connection_view(row)

        new_reference: str | None = None
        try:
            if display_changed:
                self._retire_archived_name_conflicts(
                    ProviderConnectionRecord, cleaned_name, exclude_id=row.id
                )
                self.session.flush()
            if secret_changed or secret_cleared:
                new_reference = f"connection:{uuid.uuid4()}"
                if secret_changed:
                    assert selected_secret is not None
                    self.secrets.set(new_reference, selected_secret)
                row.credential_ref = new_reference
                self._queue_credential_cleanup(old_reference, row.id)
            row.name = cleaned_name
            row.base_url = cleaned_base_url
            row.parameters = selected_parameters
            if execution_changed:
                row.revision += 1
                row.test_ok = None
                row.tested_revision = None
                row.test_message = None
                for profile in row.profiles:
                    profile.capability_fingerprint_json = None
                    profile.chat_data_authorized_fingerprint = None
            self.session.commit()
        except Exception as error:
            self.session.rollback()
            if new_reference is not None:
                self._delete_or_queue_orphaned_credential(new_reference)
            if isinstance(error, IntegrityError):
                raise InvalidConfiguration("connection name already exists") from None
            raise
        if new_reference is not None:
            self._attempt_credential_cleanup(old_reference)
        return self._connection_view(row)

    def list_connections(self) -> list[ConnectionView]:
        rows = self.session.scalars(
            select(ProviderConnectionRecord)
            .where(ProviderConnectionRecord.archived_at.is_(None))
            .order_by(ProviderConnectionRecord.created_at)
        ).all()
        return [self._connection_view(row) for row in rows]

    def get_connection(self, connection_id: str) -> ConnectionView:
        return self._connection_view(self._connection(connection_id))

    def delete_connection(self, connection_id: str) -> None:
        row = self._connection(connection_id)
        if any(profile.archived_at is None for profile in row.profiles):
            raise InvalidConfiguration("connection has active profiles")
        profile_references, connection_references = (
            self._retained_snapshot_references()
        )
        referenced = row.id in connection_references or any(
            profile.id in profile_references for profile in row.profiles
        )
        cleanup_reference: str | None = None
        if referenced:
            row.archived_at = datetime.now(timezone.utc)
        else:
            cleanup_reference = row.credential_ref
            self._queue_credential_cleanup(cleanup_reference, row.id)
            self.session.delete(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidConfiguration("connection could not be deleted") from None
        if cleanup_reference is not None:
            self._attempt_credential_cleanup(cleanup_reference)

    def record_connection_test(self, connection_id: str, *, ok: bool, message: str | None) -> ConnectionView:
        row = self._connection(connection_id)
        row.test_ok = ok
        row.tested_revision = row.revision
        row.test_message = _clean_message(
            message,
            self.diagnostic_sensitive_values(),
        )
        row.tested_at = datetime.now(timezone.utc)
        self.session.commit()
        return self._connection_view(row)

    def create_profile(
        self,
        *,
        name: str,
        purpose: str,
        connection_id: str,
        model: str,
        context_length: int = 32768,
        options: dict[str, Any] | None = None,
    ) -> ProfileView:
        connection = self._connection(connection_id)
        if not _purpose_protocol_is_compatible(purpose, connection.protocol):
            raise InvalidConfiguration("profile purpose is incompatible with connection protocol")
        cleaned_name = name.strip()
        cleaned_model = model.strip()
        if not cleaned_name or not cleaned_model:
            raise InvalidConfiguration("profile name and model cannot be empty")
        _reject_reserved_name(cleaned_name)
        if isinstance(context_length, bool) or context_length <= 0:
            raise InvalidConfiguration("context_length must be a positive integer")
        selected_options = dict(options or {})
        cleaned_model, selected_options = _normalize_profile_contract(
            purpose,
            connection.protocol,
            cleaned_model,
            selected_options,
        )
        _validate_profile_capacity(purpose, context_length, selected_options)
        try:
            self._retire_archived_name_conflicts(
                ProcessorProfileRecord, cleaned_name
            )
            self.session.flush()
            row = ProcessorProfileRecord(
                name=cleaned_name,
                purpose=purpose,
                connection=connection,
                model=cleaned_model,
                context_length=context_length,
                options=selected_options,
            )
            self.session.add(row)
            self.session.commit()
        except Exception as error:
            self.session.rollback()
            if isinstance(error, IntegrityError):
                raise InvalidConfiguration("profile name already exists") from None
            raise
        return self._profile_view(row)

    def update_profile(self, profile_id: str, **changes: Any) -> ProfileView:
        row = self._profile(profile_id)
        allowed = {"name", "connection_id", "model", "context_length", "options"}
        if set(changes) - allowed:
            raise InvalidConfiguration("unsupported profile change")
        if "options" in changes and changes["options"] is None:
            raise InvalidConfiguration("profile options cannot be null")
        if "context_length" in changes:
            context_length = changes["context_length"]
            if (
                context_length is None
                or isinstance(context_length, bool)
                or not isinstance(context_length, int)
                or context_length <= 0
            ):
                raise InvalidConfiguration("context_length must be a positive integer")
        for string_field in ("name", "model"):
            if string_field in changes:
                value = changes[string_field]
                if not isinstance(value, str):
                    raise InvalidConfiguration(f"profile {string_field} cannot be null")
                cleaned = value.strip()
                if not cleaned:
                    raise InvalidConfiguration(f"profile {string_field} cannot be empty")
                if string_field == "name":
                    _reject_reserved_name(cleaned)
                changes[string_field] = cleaned
        if "connection_id" in changes:
            if changes["connection_id"] is None:
                raise InvalidConfiguration("profile connection_id cannot be null")
            connection = self._connection(changes["connection_id"])
            if not _purpose_protocol_is_compatible(row.purpose, connection.protocol):
                raise InvalidConfiguration(
                    "profile purpose is incompatible with connection protocol"
                )
        else:
            connection = row.connection
        selected_model = changes.get("model", row.model)
        selected_options = dict(changes.get("options", row.options))
        normalized_model, normalized_options = _normalize_profile_contract(
            row.purpose,
            connection.protocol,
            selected_model,
            selected_options,
        )
        selected_context_length = changes.get("context_length", row.context_length)
        _validate_profile_capacity(
            row.purpose,
            selected_context_length,
            normalized_options,
        )
        if "model" in changes or normalized_model != row.model:
            changes["model"] = normalized_model
        if "options" in changes or normalized_options != dict(row.options):
            changes["options"] = normalized_options
        selected = {
            name: dict(value) if name == "options" else value
            for name, value in changes.items()
        }
        changed = {
            name: value for name, value in selected.items()
            if value != getattr(row, name)
        }
        if not changed:
            return self._profile_view(row)
        execution_changed = any(name != "name" for name in changed)
        try:
            if "name" in changed:
                self._retire_archived_name_conflicts(
                    ProcessorProfileRecord, changed["name"], exclude_id=row.id
                )
                self.session.flush()
            for name, value in changed.items():
                setattr(row, name, value)
            if execution_changed:
                row.revision += 1
                row.test_ok = None
                row.tested_revision = None
                row.tested_connection_revision = None
                row.upload_authorized_revision = None
                row.upload_authorized_connection_revision = None
                row.capability_fingerprint_json = None
                row.chat_data_authorized_fingerprint = None
            self.session.commit()
        except Exception as error:
            self.session.rollback()
            if isinstance(error, IntegrityError):
                raise InvalidConfiguration("profile name already exists") from None
            raise
        return self._profile_view(row)

    def list_profiles(self) -> list[ProfileView]:
        rows = self.session.scalars(
            select(ProcessorProfileRecord)
            .where(ProcessorProfileRecord.archived_at.is_(None))
            .order_by(ProcessorProfileRecord.created_at)
        ).all()
        return [self._profile_view(row) for row in rows]

    def get_profile(self, profile_id: str) -> ProfileView:
        return self._profile_view(self._profile(profile_id))

    def delete_profile(self, profile_id: str) -> None:
        row = self._profile(profile_id)
        defaults = self.session.get(DefaultSettingsRecord, 1)
        if defaults is not None:
            if defaults.cloud_asr_profile_id == row.id:
                defaults.cloud_asr_profile_id = None
                if defaults.asr_mode == "cloud":
                    defaults.asr_mode = "auto"
            if defaults.translation_profile_id == row.id:
                defaults.translation_profile_id = None
                defaults.translation_enabled = False
            if defaults.notes_profile_id == row.id:
                defaults.notes_profile_id = None
                defaults.notes_enabled = False
        profile_references, _ = self._retained_snapshot_references()
        if row.id in profile_references:
            row.archived_at = datetime.now(timezone.utc)
        else:
            self.session.delete(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidConfiguration("profile could not be deleted") from None

    def purge_unreferenced_archived(self) -> dict[str, int]:
        """Purge archives after their last retained task snapshot releases them."""

        profile_references, connection_references = (
            self._retained_snapshot_references()
        )
        profiles = self.session.scalars(
            select(ProcessorProfileRecord).where(
                ProcessorProfileRecord.archived_at.is_not(None)
            )
        ).all()
        purged_profiles = 0
        for profile in profiles:
            if profile.id not in profile_references:
                self.session.delete(profile)
                purged_profiles += 1
        self.session.flush()

        connections = self.session.scalars(
            select(ProviderConnectionRecord).where(
                ProviderConnectionRecord.archived_at.is_not(None)
            )
        ).all()
        cleanup_references: list[str] = []
        purged_connections = 0
        for connection in connections:
            if connection.id in connection_references:
                continue
            referenced_profile = self.session.scalar(
                select(ProcessorProfileRecord.id)
                .where(
                    ProcessorProfileRecord.connection_id == connection.id,
                    ProcessorProfileRecord.id.in_(profile_references),
                )
                .limit(1)
            )
            active_profile = self.session.scalar(
                select(ProcessorProfileRecord.id)
                .where(
                    ProcessorProfileRecord.connection_id == connection.id,
                    ProcessorProfileRecord.archived_at.is_(None),
                )
                .limit(1)
            )
            if referenced_profile is not None or active_profile is not None:
                continue
            cleanup_references.append(connection.credential_ref)
            self._queue_credential_cleanup(connection.credential_ref, connection.id)
            self.session.delete(connection)
            purged_connections += 1
        self.session.commit()
        for credential_ref in cleanup_references:
            self._attempt_credential_cleanup(credential_ref)
        return {
            "profiles": purged_profiles,
            "connections": purged_connections,
        }

    def record_profile_test(self, profile_id: str, *, ok: bool, message: str | None) -> ProfileView:
        row = self._profile(profile_id)
        row.test_ok = ok
        row.tested_revision = row.revision
        row.tested_connection_revision = row.connection.revision
        row.test_message = _clean_message(
            message,
            self.diagnostic_sensitive_values(),
        )
        row.tested_at = datetime.now(timezone.utc)
        fingerprint = _chat_capability_fingerprint(row)
        if fingerprint is not None:
            row.capability_fingerprint_json = fingerprint if ok else None
            if not ok:
                row.chat_data_authorized_fingerprint = None
        if (
            row.purpose == "notes"
            and row.connection.protocol != "aliyun_bailian"
            and ok
        ):
            defaults = self.session.get(DefaultSettingsRecord, 1)
            if defaults is None:
                defaults = DefaultSettingsRecord(
                    id=1,
                    local_whisper_options=dict(self.local_whisper_defaults),
                    notes_auto_enable_allowed=True,
                )
                self.session.add(defaults)
            if (
                defaults.notes_auto_enable_allowed
                and defaults.notes_profile_id is None
                and not defaults.notes_enabled
            ):
                defaults.notes_profile_id = row.id
                defaults.notes_enabled = True
                defaults.notes_auto_enable_allowed = False
        self.session.commit()
        return self._profile_view(row)

    def authorize_cloud_upload(self, profile_id: str) -> ProfileView:
        row = self._profile(profile_id)
        view = self._profile_view(row)
        if row.purpose != "cloud_asr" or not view.tested or view.test_ok is not True:
            raise InvalidConfiguration("cloud upload requires a current successful profile test")
        row.upload_authorized_revision = row.revision
        row.upload_authorized_connection_revision = row.connection.revision
        self.session.commit()
        return self._profile_view(row)

    def authorize_chat_data(self, profile_id: str) -> ProfileView:
        row = self._profile(profile_id)
        view = self._profile_view(row)
        if (
            row.purpose not in _CHAT_PURPOSES
            or row.connection.protocol != "aliyun_bailian"
            or not view.tested
            or view.test_ok is not True
            or view.capability_fingerprint is None
        ):
            raise InvalidConfiguration(
                "chat data authorization requires a current successful capability test"
            )
        row.chat_data_authorized_fingerprint = chat_capability_fingerprint_digest(
            view.capability_fingerprint
        )
        if row.purpose == "notes":
            defaults = self.session.get(DefaultSettingsRecord, 1)
            if defaults is None:
                defaults = DefaultSettingsRecord(
                    id=1,
                    local_whisper_options=dict(self.local_whisper_defaults),
                    notes_auto_enable_allowed=True,
                )
                self.session.add(defaults)
            if (
                defaults.notes_auto_enable_allowed
                and defaults.notes_profile_id is None
                and not defaults.notes_enabled
            ):
                defaults.notes_profile_id = row.id
                defaults.notes_enabled = True
                defaults.notes_auto_enable_allowed = False
        self.session.commit()
        return self._profile_view(row)

    def revoke_chat_data(self, profile_id: str) -> ProfileView:
        row = self._profile(profile_id)
        if (
            row.purpose not in _CHAT_PURPOSES
            or row.connection.protocol != "aliyun_bailian"
        ):
            raise InvalidConfiguration("profile does not use domestic chat")
        row.chat_data_authorized_fingerprint = None
        defaults = self.session.get(DefaultSettingsRecord, 1)
        if defaults is not None:
            if defaults.translation_profile_id == row.id:
                defaults.translation_enabled = False
            if defaults.notes_profile_id == row.id:
                defaults.notes_enabled = False
        self.session.commit()
        return self._profile_view(row)

    def _defaults_row(self) -> DefaultSettingsRecord:
        row = self.session.get(DefaultSettingsRecord, 1)
        if row is None:
            row = DefaultSettingsRecord(
                id=1, local_whisper_options=dict(self.local_whisper_defaults)
            )
            self.session.add(row)
            self.session.commit()
        return row

    def _valid_profile(self, profile_id: str | None, purpose: str) -> bool:
        if profile_id is None:
            return False
        try:
            row = self._profile(profile_id)
        except KeyError:
            return False
        if row.purpose != purpose:
            return False
        view = self._profile_view(row)
        if not (view.tested and view.test_ok is True):
            return False
        if purpose in _CHAT_PURPOSES:
            return view.chat_data_authorized
        return True

    def get_defaults(self) -> DefaultsView:
        row = self._defaults_row()
        notes_enabled = row.notes_enabled and self._valid_profile(row.notes_profile_id, "notes")
        translation_enabled = row.translation_enabled and self._valid_profile(row.translation_profile_id, "translation")
        return DefaultsView(
            asr_mode=row.asr_mode,
            cloud_asr_profile_id=row.cloud_asr_profile_id,
            translation_enabled=translation_enabled,
            translation_profile_id=row.translation_profile_id,
            translation_target_language=row.translation_target_language,
            notes_enabled=notes_enabled,
            notes_profile_id=row.notes_profile_id,
            notes_template=row.notes_template,
            notes_output_language=row.notes_output_language,
            has_custom_prompt=bool(
                row.notes_custom_prompt_envelope_json
                or row.notes_custom_prompt
            ),
            local_whisper_options=dict(row.local_whisper_options),
        )

    def resolve_default_custom_prompt(self) -> str | None:
        require_sensitive_text_migration(self.session)
        row = self._defaults_row()
        if row.notes_custom_prompt_envelope_json is None:
            return None
        envelope = validate_protected_text_envelope(
            row.notes_custom_prompt_envelope_json
        )
        return self.sensitive_text_protector.unprotect(
            DEFAULT_PROMPT_PURPOSE, envelope
        )

    def update_defaults(self, **changes: Any) -> DefaultsView:
        row = self._defaults_row()
        allowed = {
            "asr_mode", "cloud_asr_profile_id", "translation_enabled",
            "translation_profile_id", "notes_enabled", "notes_profile_id",
            "translation_target_language", "notes_template", "notes_output_language",
            "notes_custom_prompt", "local_whisper_options",
        }
        if set(changes) - allowed:
            raise InvalidConfiguration("unsupported default setting")
        if changes.get("asr_mode", row.asr_mode) not in {"auto", "cloud", "local"}:
            raise InvalidConfiguration("invalid ASR mode")
        asr_mode = changes.get("asr_mode", row.asr_mode)
        cloud_id = changes.get("cloud_asr_profile_id", row.cloud_asr_profile_id)
        if cloud_id is not None:
            try:
                cloud_reference = self._profile(cloud_id)
            except KeyError:
                raise InvalidConfiguration("cloud ASR profile does not exist") from None
            if cloud_reference.purpose != "cloud_asr":
                raise InvalidConfiguration("cloud ASR profile has the wrong purpose")
        if asr_mode == "cloud":
            if not self._valid_profile(cloud_id, "cloud_asr"):
                raise InvalidConfiguration("cloud ASR profile must have a current successful test")
            cloud_profile = self._profile(cloud_id)
            if not self._profile_view(cloud_profile).upload_authorized:
                raise InvalidConfiguration("cloud ASR profile requires current upload authorization")
        notes_id = changes.get("notes_profile_id", row.notes_profile_id)
        if notes_id is not None:
            try:
                notes_reference = self._profile(notes_id)
            except KeyError:
                raise InvalidConfiguration("notes profile does not exist") from None
            if notes_reference.purpose != "notes":
                raise InvalidConfiguration("notes profile has the wrong purpose")
        if changes.get("notes_enabled", row.notes_enabled) and not self._valid_profile(notes_id, "notes"):
            raise InvalidConfiguration("notes require a current tested notes profile")
        translation_id = changes.get("translation_profile_id", row.translation_profile_id)
        if translation_id is not None:
            try:
                translation_reference = self._profile(translation_id)
            except KeyError:
                raise InvalidConfiguration("translation profile does not exist") from None
            if translation_reference.purpose != "translation":
                raise InvalidConfiguration("translation profile has the wrong purpose")
        if changes.get("translation_enabled", row.translation_enabled) and not self._valid_profile(translation_id, "translation"):
            raise InvalidConfiguration("translation requires a current tested translation profile")
        target_language = changes.get(
            "translation_target_language", row.translation_target_language
        )
        output_language = changes.get("notes_output_language", row.notes_output_language)
        if not isinstance(target_language, str) or not target_language.strip():
            raise InvalidConfiguration("translation target language cannot be empty")
        if not isinstance(output_language, str) or not output_language.strip():
            raise InvalidConfiguration("notes output language cannot be empty")
        notes_template = changes.get("notes_template", row.notes_template)
        if notes_template not in {"summary", "key_points", "custom"}:
            raise InvalidConfiguration("invalid notes template")
        prompt_changed = "notes_custom_prompt" in changes
        custom_prompt = changes.pop("notes_custom_prompt", None)
        has_custom_prompt = (
            custom_prompt is not None
            if prompt_changed
            else bool(
                row.notes_custom_prompt_envelope_json
                or row.notes_custom_prompt
            )
        )
        if notes_template == "custom" and (
            not has_custom_prompt
            or (
                prompt_changed
                and (
                    not isinstance(custom_prompt, str)
                    or not custom_prompt.strip()
                )
            )
        ):
            raise InvalidConfiguration("custom notes template requires a prompt")
        protected_prompt = None
        if prompt_changed and custom_prompt is not None:
            protected_prompt = self.sensitive_text_protector.protect(
                DEFAULT_PROMPT_PURPOSE, custom_prompt
            ).model_dump(mode="json")
        if "local_whisper_options" in changes:
            merged_local_options = {
                **row.local_whisper_options,
                **changes["local_whisper_options"],
            }
            _validate_local_whisper_options(merged_local_options)
            self._validate_local_roots(merged_local_options)
            changes["local_whisper_options"] = merged_local_options
        for name, value in changes.items():
            setattr(row, name, dict(value) if name == "local_whisper_options" else value)
        if prompt_changed:
            row.notes_custom_prompt = None
            row.notes_custom_prompt_envelope_json = protected_prompt
        if "notes_enabled" in changes or "notes_profile_id" in changes:
            row.notes_auto_enable_allowed = False
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidConfiguration("default profile reference is invalid") from None
        return self.get_defaults()

    def secret_for_connection(self, connection_id: str) -> str | None:
        """Return a secret only to an injected execution adapter, never to an API schema."""

        row = self._connection(connection_id, include_archived=True)
        if row.protocol == "openai_compatible":
            raise InvalidConfiguration("legacy_chat_endpoint_blocked")
        return self.secrets.get(row.credential_ref)

    def credential_bundle_for_connection(
        self,
        connection_id: str,
    ) -> CredentialBundle | str | None:
        """Return a typed bundle at the execution boundary without exposing it publicly."""

        row = self._connection(connection_id, include_archived=True)
        if row.protocol == "openai_compatible":
            raise InvalidConfiguration("legacy_chat_endpoint_blocked")
        stored = self.secrets.get(row.credential_ref)
        if stored is None:
            return None
        if row.protocol not in {"tencent_recording_asr", "aliyun_bailian"}:
            return stored
        try:
            return parse_credential_bundle(row.protocol, stored)
        except CredentialReentryRequired:
            raise InvalidConfiguration(
                "stored credentials require complete re-entry"
            ) from None

    def credential_cleanup_status(self) -> CredentialCleanupStatusView:
        pending_count = len(
            self.session.scalars(
                select(CredentialCleanupRecord.credential_ref)
            ).all()
        )
        return CredentialCleanupStatusView(
            cleanup_pending=pending_count > 0,
            pending_count=pending_count,
        )

    def retry_credential_cleanup(self) -> CredentialCleanupStatusView:
        references = self.session.scalars(
            select(CredentialCleanupRecord.credential_ref).order_by(
                CredentialCleanupRecord.created_at
            )
        ).all()
        for credential_ref in references:
            self._attempt_credential_cleanup(credential_ref)
        return self.credential_cleanup_status()

    def diagnostic_sensitive_values(self) -> tuple[str, ...]:
        """Return internal redaction literals without exposing them through public views."""

        values: set[str] = set()
        rows = self.session.scalars(select(ProviderConnectionRecord)).all()
        cleanup_references = self.session.scalars(
            select(CredentialCleanupRecord.credential_ref)
        ).all()
        for credential_ref in [row.credential_ref for row in rows] + list(
            cleanup_references
        ):
            values.add(credential_ref)
            try:
                secret = self.secrets.get(credential_ref)
            except Exception:
                secret = None
            if secret:
                values.add(secret)
                try:
                    payload = json.loads(secret)
                except (TypeError, ValueError):
                    payload = None
                if isinstance(payload, dict):
                    values.update(
                        value
                        for key, value in payload.items()
                        if key != "schema_version"
                        and isinstance(value, str)
                        and value
                    )
        return tuple(sorted(values, key=lambda value: (-len(value), value)))

    def snapshot_profile(
        self, profile_id: str, *, include_archived: bool = False
    ) -> dict[str, Any]:
        row = self._profile(profile_id, include_archived=include_archived)
        connection = row.connection
        if connection.protocol == "openai_compatible":
            raise InvalidConfiguration("legacy_chat_endpoint_blocked")
        stored_secret = self.secrets.get(connection.credential_ref)
        has_secret = stored_secret is not None
        if connection.protocol in {"tencent_recording_asr", "aliyun_bailian"}:
            has_secret = all(
                configured_credential_fields(
                    connection.protocol,
                    stored_secret,
                ).values()
            )
        snapshot = {
            "id": row.id,
            "connection_id": connection.id,
            "name": row.name,
            "purpose": row.purpose,
            "protocol": connection.protocol,
            "base_url": connection.base_url,
            "parameters": dict(connection.parameters),
            "connection_revision": connection.revision,
            "model": row.model,
            "context_length": row.context_length,
            "options": dict(row.options),
            "profile_revision": row.revision,
            "has_secret": has_secret,
        }
        if connection.protocol == "aliyun_bailian":
            capability_fingerprint = _chat_capability_fingerprint(row)
            snapshot["capability_fingerprint"] = (
                dict(capability_fingerprint)
                if capability_fingerprint is not None
                and dict(row.capability_fingerprint_json or {})
                == capability_fingerprint
                and row.test_ok is True
                and row.tested_revision == row.revision
                and row.tested_connection_revision == connection.revision
                else None
            )
            snapshot["chat_data_consent_fingerprint"] = (
                row.chat_data_authorized_fingerprint
            )
        if connection.protocol == "volc_bigasr_flash":
            snapshot["resource"] = "volc.bigasr.auc_turbo"
        return snapshot

    def snapshot_current_cloud_asr_retry_profile(
        self,
        profile_id: str,
        *,
        connection_revision: int,
        profile_revision: int,
    ) -> dict[str, Any]:
        """Snapshot one explicitly selected, currently usable ASR profile."""

        if (
            type(connection_revision) is not int
            or connection_revision <= 0
            or type(profile_revision) is not int
            or profile_revision <= 0
        ):
            raise InvalidConfiguration("cloud ASR retry revisions must be positive")
        try:
            row = self._profile(profile_id)
        except KeyError:
            raise InvalidConfiguration(
                "cloud ASR retry profile is unavailable"
            ) from None
        if row.purpose != "cloud_asr":
            raise InvalidConfiguration("cloud ASR retry profile has the wrong purpose")
        if (
            row.connection.revision != connection_revision
            or row.revision != profile_revision
        ):
            raise InvalidConfiguration(
                "cloud ASR retry profile revision is stale; refresh and retry"
            )
        view = self._profile_view(row)
        if not view.tested or view.test_ok is not True:
            raise InvalidConfiguration(
                "cloud ASR retry requires a current successful test"
            )
        if not view.upload_authorized:
            raise InvalidConfiguration(
                "cloud ASR retry requires current upload authorization"
            )
        return self.snapshot_profile(profile_id)

    def resolve_profile_for_execution(self, profile_id: str) -> dict[str, Any]:
        """Resolve an immutable task reference even after its profile is archived."""

        return self.snapshot_profile(profile_id, include_archived=True)
