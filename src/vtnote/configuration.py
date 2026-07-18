"""Direct configuration services with redacted public views."""

from __future__ import annotations

import re
import uuid
import ipaddress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vtnote.models import (
    DefaultSettingsRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
)
from vtnote.secrets import SecretStore
from vtnote.paths import StoragePaths


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
    tested: bool
    test_ok: bool | None
    test_message: str | None


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
    notes_custom_prompt: str | None
    local_whisper_options: dict[str, Any]


_PROTOCOL_PARAMETERS = {
    "volc_bigasr_flash": frozenset(),
    "openai_compatible": frozenset({"api_version", "organization"}),
}
_PURPOSE_PROTOCOL = {
    "cloud_asr": "volc_bigasr_flash",
    "translation": "openai_compatible",
    "notes": "openai_compatible",
}
_PURPOSE_OPTIONS = {
    "cloud_asr": frozenset({"language"}),
    "translation": frozenset({"temperature", "max_tokens"}),
    "notes": frozenset({"temperature", "max_tokens"}),
}


def _validate_connection_parameters(protocol: str, parameters: dict[str, Any]) -> None:
    if set(parameters) - _PROTOCOL_PARAMETERS[protocol]:
        raise InvalidConfiguration("unsupported or secret connection parameter")
    if any(not isinstance(value, str) or not value.strip() for value in parameters.values()):
        raise InvalidConfiguration("connection parameter values must be non-empty strings")


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


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _clean_base_url(value: str) -> str:
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
    if parts.scheme == "http" and not _is_loopback_host(host):
        raise InvalidConfiguration("provider base URL must use HTTPS or loopback HTTP")
    if parts.scheme not in {"http", "https"}:
        raise InvalidConfiguration("invalid provider base URL")
    if parts.scheme == "http" and _is_loopback_host(host):
        pass
    elif parts.scheme != "https":
        raise InvalidConfiguration("invalid provider base URL")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _clean_message(
    message: str | None, sensitive_values: tuple[str | None, ...] = ()
) -> str | None:
    if message is None:
        return None
    cleaned = re.sub(r"(?i)(api[-_ ]?key|authorization|token|secret)\s*[:=]\s*\S+", r"\1=[redacted]", message)
    for value in sensitive_values:
        if value:
            cleaned = cleaned.replace(value, "[redacted]")
    return cleaned[:500]


class ConfigurationService:
    def __init__(
        self,
        session: Session,
        secrets: SecretStore,
        *,
        paths: StoragePaths | None = None,
    ) -> None:
        self.session = session
        self.secrets = secrets
        data_root = paths.data_root if paths else Path(r"D:\Workspace\Project\VtNote-data")
        cache_root = (
            paths.runtime_cache_root
            if paths
            else Path(r"D:\Workspace\Codex\cache\VtNote-runtime")
        )
        self.local_whisper_defaults = {
            "model": "large-v3-turbo",
            "device": "auto",
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

    def _connection(self, connection_id: str) -> ProviderConnectionRecord:
        row = self.session.get(ProviderConnectionRecord, connection_id)
        if row is None:
            raise KeyError(connection_id)
        return row

    def _profile(self, profile_id: str) -> ProcessorProfileRecord:
        row = self.session.get(ProcessorProfileRecord, profile_id)
        if row is None:
            raise KeyError(profile_id)
        return row

    def _connection_view(self, row: ProviderConnectionRecord) -> ConnectionView:
        current = row.tested_revision == row.revision
        return ConnectionView(
            id=row.id,
            name=row.name,
            protocol=row.protocol,
            base_url=row.base_url,
            parameters=dict(row.parameters),
            revision=row.revision,
            has_secret=self.secrets.get(row.credential_ref) is not None,
            tested=current,
            test_ok=row.test_ok if current else None,
            test_message=row.test_message if current else None,
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
        )

    def create_connection(
        self,
        *,
        name: str,
        protocol: str,
        base_url: str,
        parameters: dict[str, Any],
        secret: str | None = None,
    ) -> ConnectionView:
        if protocol not in _PROTOCOL_PARAMETERS:
            raise InvalidConfiguration("unsupported provider protocol")
        _validate_connection_parameters(protocol, parameters)
        cleaned_name = name.strip()
        if not cleaned_name:
            raise InvalidConfiguration("connection name cannot be empty")
        row = ProviderConnectionRecord(
            name=cleaned_name,
            protocol=protocol,
            base_url=_clean_base_url(base_url),
            parameters=dict(parameters),
            credential_ref=f"connection:{uuid.uuid4()}",
        )
        self.session.add(row)
        try:
            self.session.flush()
            if secret is not None:
                self.secrets.set(row.credential_ref, secret)
            self.session.commit()
        except Exception as error:
            self.session.rollback()
            if secret is not None:
                self.secrets.delete(row.credential_ref)
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
        clear_secret: bool = False,
    ) -> ConnectionView:
        if secret is not None and clear_secret:
            raise InvalidConfiguration("cannot replace and clear a secret together")
        row = self._connection(connection_id)
        if parameters is not None:
            _validate_connection_parameters(row.protocol, parameters)
        old_secret = self.secrets.get(row.credential_ref)
        if name is not None:
            cleaned_name = name.strip()
            if not cleaned_name:
                raise InvalidConfiguration("connection name cannot be empty")
            row.name = cleaned_name
        if base_url is not None:
            row.base_url = _clean_base_url(base_url)
        if parameters is not None:
            row.parameters = dict(parameters)
        row.revision += 1
        row.test_ok = None
        row.tested_revision = None
        row.test_message = None
        try:
            if secret is not None:
                self.secrets.set(row.credential_ref, secret)
            elif clear_secret:
                self.secrets.delete(row.credential_ref)
            self.session.commit()
        except Exception as error:
            self.session.rollback()
            if old_secret is None:
                self.secrets.delete(row.credential_ref)
            else:
                self.secrets.set(row.credential_ref, old_secret)
            if isinstance(error, IntegrityError):
                raise InvalidConfiguration("connection name already exists") from None
            raise
        return self._connection_view(row)

    def list_connections(self) -> list[ConnectionView]:
        rows = self.session.scalars(select(ProviderConnectionRecord).order_by(ProviderConnectionRecord.created_at)).all()
        return [self._connection_view(row) for row in rows]

    def get_connection(self, connection_id: str) -> ConnectionView:
        return self._connection_view(self._connection(connection_id))

    def delete_connection(self, connection_id: str) -> None:
        row = self._connection(connection_id)
        reference = row.credential_ref
        old_secret = self.secrets.get(reference)
        self.secrets.delete(reference)
        self.session.delete(row)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            if old_secret is not None:
                self.secrets.set(reference, old_secret)
            raise

    def record_connection_test(self, connection_id: str, *, ok: bool, message: str | None) -> ConnectionView:
        row = self._connection(connection_id)
        row.test_ok = ok
        row.tested_revision = row.revision
        row.test_message = _clean_message(
            message, (self.secrets.get(row.credential_ref), row.credential_ref)
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
        context_length: int = 8192,
        options: dict[str, Any] | None = None,
    ) -> ProfileView:
        connection = self._connection(connection_id)
        if _PURPOSE_PROTOCOL.get(purpose) != connection.protocol:
            raise InvalidConfiguration("profile purpose is incompatible with connection protocol")
        cleaned_name = name.strip()
        cleaned_model = model.strip()
        if not cleaned_name or not cleaned_model:
            raise InvalidConfiguration("profile name and model cannot be empty")
        if isinstance(context_length, bool) or context_length <= 0:
            raise InvalidConfiguration("context_length must be a positive integer")
        selected_options = dict(options or {})
        _validate_profile_options(purpose, selected_options)
        row = ProcessorProfileRecord(
            name=cleaned_name,
            purpose=purpose,
            connection=connection,
            model=cleaned_model,
            context_length=context_length,
            options=selected_options,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidConfiguration("profile name already exists") from None
        return self._profile_view(row)

    def update_profile(self, profile_id: str, **changes: Any) -> ProfileView:
        row = self._profile(profile_id)
        allowed = {"name", "connection_id", "model", "context_length", "options"}
        if set(changes) - allowed:
            raise InvalidConfiguration("unsupported profile change")
        if "options" in changes:
            _validate_profile_options(row.purpose, changes["options"])
        if "context_length" in changes and (
            isinstance(changes["context_length"], bool) or changes["context_length"] <= 0
        ):
            raise InvalidConfiguration("context_length must be a positive integer")
        for string_field in ("name", "model"):
            if string_field in changes:
                cleaned = changes[string_field].strip()
                if not cleaned:
                    raise InvalidConfiguration(f"profile {string_field} cannot be empty")
                changes[string_field] = cleaned
        if "connection_id" in changes:
            connection = self._connection(changes["connection_id"])
            if _PURPOSE_PROTOCOL[row.purpose] != connection.protocol:
                raise InvalidConfiguration(
                    "profile purpose is incompatible with connection protocol"
                )
        for name, value in changes.items():
            setattr(row, name, dict(value) if name == "options" else value)
        row.revision += 1
        row.test_ok = None
        row.tested_revision = None
        row.tested_connection_revision = None
        row.upload_authorized_revision = None
        row.upload_authorized_connection_revision = None
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise InvalidConfiguration("profile name already exists") from None
        return self._profile_view(row)

    def list_profiles(self) -> list[ProfileView]:
        rows = self.session.scalars(select(ProcessorProfileRecord).order_by(ProcessorProfileRecord.created_at)).all()
        return [self._profile_view(row) for row in rows]

    def get_profile(self, profile_id: str) -> ProfileView:
        return self._profile_view(self._profile(profile_id))

    def delete_profile(self, profile_id: str) -> None:
        self.session.delete(self._profile(profile_id))
        self.session.commit()

    def record_profile_test(self, profile_id: str, *, ok: bool, message: str | None) -> ProfileView:
        row = self._profile(profile_id)
        row.test_ok = ok
        row.tested_revision = row.revision
        row.tested_connection_revision = row.connection.revision
        row.test_message = _clean_message(
            message,
            (
                self.secrets.get(row.connection.credential_ref),
                row.connection.credential_ref,
            ),
        )
        row.tested_at = datetime.now(timezone.utc)
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
        row = self.session.get(ProcessorProfileRecord, profile_id)
        if row is None or row.purpose != purpose:
            return False
        view = self._profile_view(row)
        return view.tested and view.test_ok is True

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
            notes_custom_prompt=row.notes_custom_prompt,
            local_whisper_options=dict(row.local_whisper_options),
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
        if asr_mode == "cloud":
            if not self._valid_profile(cloud_id, "cloud_asr"):
                raise InvalidConfiguration("cloud ASR profile must have a current successful test")
            cloud_profile = self._profile(cloud_id)
            if not self._profile_view(cloud_profile).upload_authorized:
                raise InvalidConfiguration("cloud ASR profile requires current upload authorization")
        notes_id = changes.get("notes_profile_id", row.notes_profile_id)
        if changes.get("notes_enabled", row.notes_enabled) and not self._valid_profile(notes_id, "notes"):
            raise InvalidConfiguration("notes require a current tested notes profile")
        translation_id = changes.get("translation_profile_id", row.translation_profile_id)
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
        custom_prompt = changes.get("notes_custom_prompt", row.notes_custom_prompt)
        if notes_template == "custom" and (
            not isinstance(custom_prompt, str) or not custom_prompt.strip()
        ):
            raise InvalidConfiguration("custom notes template requires a prompt")
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
        self.session.commit()
        return self.get_defaults()

    def secret_for_connection(self, connection_id: str) -> str | None:
        """Return a secret only to an injected execution adapter, never to an API schema."""

        return self.secrets.get(self._connection(connection_id).credential_ref)

    def snapshot_profile(self, profile_id: str) -> dict[str, Any]:
        row = self._profile(profile_id)
        connection = row.connection
        snapshot = {
            "id": row.id,
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
            "has_secret": self.secrets.get(connection.credential_ref) is not None,
        }
        if connection.protocol == "volc_bigasr_flash":
            snapshot["resource"] = "volc.bigasr.auc_turbo"
        return snapshot
