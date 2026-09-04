"""Validated request and response contracts for the local HTTP API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vtnote.application.task_contracts import MAX_BATCH_SOURCES


@dataclass(frozen=True, slots=True)
class ConnectivityResult:
    ok: bool
    message: str | None = None


class ConnectionTester(Protocol):
    def test_connection(
        self, connection: Any, credentials: Any, *, follow_redirects: Literal[False]
    ) -> ConnectivityResult: ...


class ProfileTester(Protocol):
    def test_profile(
        self,
        profile: Any,
        credentials: Any,
        test_input: "ProfileTestInput",
        *,
        follow_redirects: Literal[False],
    ) -> ConnectivityResult: ...


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConnectionCreate(InputModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str
    base_url: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, min_length=1)
    credentials: dict[str, Any] | None = None


class ConnectionPatch(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = None
    parameters: dict[str, Any] | None = None
    secret: str | None = Field(default=None, min_length=1)
    credentials: dict[str, Any] | None = None
    clear_secret: bool = False

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in (
                "name",
                "base_url",
                "parameters",
                "secret",
                "credentials",
                "clear_secret",
            ):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value


class ProfileCreate(InputModel):
    name: str = Field(min_length=1, max_length=128)
    purpose: str
    connection_id: str
    model: str = Field(min_length=1)
    context_length: int = Field(default=32768, gt=0)
    options: dict[str, Any] = Field(default_factory=dict)


class ProfilePatch(InputModel):
    name: str | None = Field(default=None, min_length=1)
    connection_id: str | None = None
    model: str | None = Field(default=None, min_length=1)
    context_length: int | None = Field(default=None, gt=0)
    options: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in ("name", "connection_id", "model", "context_length", "options"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value


class ProfileTestInput(InputModel):
    test_kind: Literal[
        "provider_profile",
        "cos_sentinel",
        "connection_policy_validated",
        "profile_capability_tested",
    ] = "provider_profile"
    acknowledge_billable_request: bool = False
    speech_sample_upload_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )


class AsrConnectionVerifyInput(InputModel):
    acknowledge_billable_request: bool = False
    authorize_task_audio_upload: bool = False


class ChatConnectionVerifyInput(InputModel):
    acknowledge_billable_request: bool = False
    authorize_chat_data_upload: bool = False


class ChatDataAuthorizationInput(InputModel):
    acknowledge_chat_data_upload: bool


class ModelInstallInput(InputModel):
    acknowledge_download: bool
    expected_revision: str = Field(min_length=40, max_length=40)


class DefaultsPatch(InputModel):
    asr_mode: Literal["auto", "cloud", "local"] | None = None
    local_asr_engine: Literal[
        "faster_whisper", "sensevoice_sherpa_onnx"
    ] | None = None
    cloud_asr_profile_id: str | None = None
    translation_enabled: bool | None = None
    translation_profile_id: str | None = None
    translation_target_language: str | None = Field(default=None, min_length=1)
    notes_enabled: bool | None = None
    notes_profile_id: str | None = None
    notes_template: Literal["summary", "key_points", "custom"] | None = None
    notes_output_language: str | None = Field(default=None, min_length=1)
    notes_custom_prompt: str | None = Field(default=None, max_length=8_000)
    local_whisper_options: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in (
                "asr_mode",
                "local_asr_engine",
                "translation_enabled",
                "translation_target_language",
                "notes_enabled",
                "notes_template",
                "notes_output_language",
                "local_whisper_options",
            ):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value


class NotesPromptView(BaseModel):
    prompt: str
    is_custom: bool


class SourceInput(InputModel):
    kind: Literal["url", "local_media", "local_subtitle"]
    locator: str = Field(min_length=1)


class TaskCreate(InputModel):
    sources: list[SourceInput] = Field(max_length=1)
    output_type: Literal["audio", "transcript", "notes"] | None = None
    audio_export_enabled: bool | None = None
    asr_mode: Literal["auto", "cloud", "local"] | None = None
    local_asr_engine: Literal[
        "faster_whisper", "sensevoice_sherpa_onnx"
    ] | None = None
    cloud_asr_profile_id: str | None = None
    translation_enabled: bool | None = None
    translation_profile_id: str | None = None
    translation_target_language: str | None = Field(default=None, min_length=1)
    notes_enabled: bool | None = None
    notes_profile_id: str | None = None
    notes_template: Literal["summary", "key_points", "custom"] | None = None
    notes_output_language: str | None = Field(default=None, min_length=1)
    notes_custom_prompt: str | None = None


class BatchTaskCreate(TaskCreate):
    sources: list[SourceInput] = Field(
        min_length=1,
        max_length=MAX_BATCH_SOURCES,
    )


class TaskDeleteBatch(InputModel):
    task_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_BATCH_SOURCES,
    )


class UploadTaskMetadata(InputModel):
    """The first multipart part; the second and final part is always ``file``."""

    kind: Literal["media", "subtitle"]
    output_type: Literal["audio", "transcript", "notes"] | None = None
    audio_export_enabled: bool | None = None
    asr_mode: Literal["auto", "cloud", "local"] | None = None
    local_asr_engine: Literal[
        "faster_whisper", "sensevoice_sherpa_onnx"
    ] | None = None
    cloud_asr_profile_id: str | None = None
    translation_enabled: bool | None = None
    translation_profile_id: str | None = None
    translation_target_language: str | None = Field(default=None, min_length=1)
    notes_enabled: bool | None = None
    notes_profile_id: str | None = None
    notes_template: Literal["summary", "key_points", "custom"] | None = None
    notes_output_language: str | None = Field(default=None, min_length=1)
    notes_custom_prompt: str | None = None


class ProbeInput(InputModel):
    url: str = Field(min_length=1, max_length=8_192)


class RetryInput(InputModel):
    item_id: str
    stage: str
    expected_attempt: int = Field(gt=0, strict=True)
    strategy: Literal["same", "local", "cloud_confirmed"] = "same"
    cloud_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    connection_revision: int | None = Field(default=None, gt=0, strict=True)
    profile_revision: int | None = Field(default=None, gt=0, strict=True)
    notes_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    notes_profile_revision: int | None = Field(default=None, gt=0, strict=True)
    notes_output_language: str | None = Field(default=None, min_length=1, max_length=35)
    acknowledge_possible_charge: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_strategy_fields(self) -> "RetryInput":
        cloud_fields = {
            "cloud_profile_id",
            "connection_revision",
            "profile_revision",
        }
        if self.strategy == "cloud_confirmed":
            if (
                self.cloud_profile_id is None
                or self.connection_revision is None
                or self.profile_revision is None
                or self.acknowledge_possible_charge is not True
            ):
                raise ValueError(
                    "cloud_confirmed requires a current profile, revisions, "
                    "and possible-charge acknowledgement"
                )
        elif self.model_fields_set & cloud_fields:
            raise ValueError(
                "cloud retry fields are valid only for cloud_confirmed"
            )
        elif self.strategy == "local" and self.acknowledge_possible_charge:
            raise ValueError(
                "possible-charge acknowledgement is invalid for local retry"
            )
        notes_fields = {
            "notes_profile_id",
            "notes_profile_revision",
            "notes_output_language",
        }
        selected_notes_fields = self.model_fields_set & notes_fields
        if selected_notes_fields and self.stage != "notes":
            raise ValueError("notes retry fields are valid only for notes")
        if selected_notes_fields and self.strategy != "same":
            raise ValueError("notes retry fields require the same strategy")
        if (self.notes_profile_id is None) != (self.notes_profile_revision is None):
            raise ValueError(
                "notes retry profile id and revision must be provided together"
            )
        return self
