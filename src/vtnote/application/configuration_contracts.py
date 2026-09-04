"""Public configuration-service views and stable application errors."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


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
    local_asr_engine: Literal["faster_whisper", "sensevoice_sherpa_onnx"]
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
