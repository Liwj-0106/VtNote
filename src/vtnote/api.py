"""FastAPI application for local configuration and durable task control."""

from __future__ import annotations

import logging
import re
import secrets as token_secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from vtnote.config import Settings
from vtnote.chat import BailianProfileTester
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.diagnostics import diagnostic_bundle_bytes
from vtnote.exports import ExportFormat, render_execution_summary
from vtnote.media import CommandRunner, FfmpegBinaries, FfmpegMediaProcessor
from vtnote.model_assets import ModelAssetError, ModelAssetService
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.platform_sources import build_default_platform_registry
from vtnote.readiness import ReadinessInspector
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService
from vtnote.secrets import KeyringSecretStore, SecretStore
from vtnote.sources import (
    REMOTE_SOURCE_KINDS,
    PlatformSourceError,
    SourceAdapter,
    SourceCapabilityError,
    SubtitleTrack,
)
from vtnote.sensitive_text import (
    SensitiveTextMigrationRequired,
    SensitiveTextProtectionError,
    SensitiveTextProtector,
    WindowsDpapiSensitiveTextProtector,
    migrate_sensitive_text,
)
from vtnote.tasks import InvalidTaskOperation, LocalSourceValidator, TaskService
from vtnote.tencent_asr import (
    TencentConnectivityTester,
    TencentRecordingClient,
    UploadedSpeechSampleResolver,
)
from vtnote.uploads import (
    LocalSourceFiles,
    MultipartUploadStager,
    UploadError,
    UploadLimits,
    UploadService,
    UploadTaskContext,
)
from vtnote.url_security import Resolver, SocketResolver, SourceUrlPolicy, UnsafeSourceUrl


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


class ChatDataAuthorizationInput(InputModel):
    acknowledge_chat_data_upload: bool


class ModelInstallInput(InputModel):
    acknowledge_download: bool
    expected_revision: str = Field(min_length=40, max_length=40)


class DefaultsPatch(InputModel):
    asr_mode: Literal["auto", "cloud", "local"] | None = None
    cloud_asr_profile_id: str | None = None
    translation_enabled: bool | None = None
    translation_profile_id: str | None = None
    translation_target_language: str | None = Field(default=None, min_length=1)
    notes_enabled: bool | None = None
    notes_profile_id: str | None = None
    notes_template: Literal["summary", "key_points", "custom"] | None = None
    notes_output_language: str | None = Field(default=None, min_length=1)
    notes_custom_prompt: str | None = None
    local_whisper_options: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in (
                "asr_mode",
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


class SourceInput(InputModel):
    kind: Literal["url", "local_media", "local_subtitle"]
    locator: str = Field(min_length=1)


class TaskCreate(InputModel):
    sources: list[SourceInput]
    asr_mode: Literal["auto", "cloud", "local"] | None = None
    cloud_asr_profile_id: str | None = None
    translation_enabled: bool | None = None
    translation_profile_id: str | None = None
    translation_target_language: str | None = Field(default=None, min_length=1)
    notes_enabled: bool | None = None
    notes_profile_id: str | None = None
    notes_template: Literal["summary", "key_points", "custom"] | None = None
    notes_output_language: str | None = Field(default=None, min_length=1)
    notes_custom_prompt: str | None = None


class UploadTaskMetadata(InputModel):
    """The first multipart part; the second and final part is always ``file``."""

    kind: Literal["media", "subtitle"]
    asr_mode: Literal["auto", "cloud", "local"] | None = None
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
    url: str = Field(min_length=1)


class RetryInput(InputModel):
    item_id: str
    stage: str
    expected_attempt: int = Field(gt=0, strict=True)
    strategy: Literal["same", "local", "cloud_confirmed"] = "same"
    cloud_profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    connection_revision: int | None = Field(default=None, gt=0, strict=True)
    profile_revision: int | None = Field(default=None, gt=0, strict=True)
    acknowledge_possible_charge: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_strategy_fields(self) -> RetryInput:
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
        return self


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _dump_subtitle(track: SubtitleTrack) -> dict[str, Any]:
    return {
        "id": track.id,
        "language": track.language,
        "format": track.format,
        "kind": track.kind,
        "ui_label": track.ui_label,
        "is_translated": track.is_translated,
        "is_live_chat": track.is_live_chat,
    }


def create_app(
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    secret_store: SecretStore | None = None,
    resolver: Resolver | None = None,
    connection_tester: ConnectionTester | None = None,
    profile_tester: ProfileTester | None = None,
    source_probe: SourceAdapter | None = None,
    local_source_validator: LocalSourceValidator | None = None,
    upload_limits: UploadLimits | None = None,
    sensitive_text_protector: SensitiveTextProtector | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    selected_settings = settings or Settings()
    paths = StoragePaths.from_settings(selected_settings)
    selected_protector = (
        sensitive_text_protector
        or WindowsDpapiSensitiveTextProtector()
    )
    selected_engine = engine or initialize_database(
        paths.database,
        sensitive_text_protector=selected_protector,
    )
    if engine is not None:
        migrate_sensitive_text(selected_engine, selected_protector)
    selected_secrets = secret_store or KeyringSecretStore()
    selected_resolver = resolver or SocketResolver()
    source_policy = SourceUrlPolicy(selected_resolver)
    selected_upload_limits = upload_limits or UploadLimits()
    selected_frontend_dist = (
        Path(frontend_dist)
        if frontend_dist is not None
        else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    )
    frontend_index = selected_frontend_dist / "index.html"
    frontend_available = frontend_index.is_file()
    selected_local_sources = local_source_validator or LocalSourceFiles(
        FfmpegMediaProcessor(
            runner=CommandRunner(),
            binaries=FfmpegBinaries.discover(),
        ),
        max_subtitle_bytes=selected_upload_limits.max_subtitle_bytes,
    )
    sessions = sessionmaker(selected_engine, expire_on_commit=False)
    selected_source_probe = source_probe or build_default_platform_registry(
        settings=selected_settings,
        resolver=selected_resolver,
        session_factory=sessions,
    )
    default_tencent_tester = TencentConnectivityTester(
        client=TencentRecordingClient(),
        sample_resolver=UploadedSpeechSampleResolver(
            engine=selected_engine,
            paths=paths,
        ),
    )
    selected_connection_tester = (
        connection_tester or default_tencent_tester
    )
    default_bailian_tester = BailianProfileTester()
    model_assets = ModelAssetService(
        engine=selected_engine,
        paths=paths,
        manifest_path=(
            Path(__file__).resolve().parents[2]
            / "assets"
            / "models"
            / "large-v3-turbo.manifest.json"
        ),
    )
    expected_host = f"{selected_settings.bind_host}:{selected_settings.bind_port}"
    expected_origin = f"http://{expected_host}"

    docs_enabled = selected_settings.enable_dev_docs
    app = FastAPI(
        title="VtNote",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    logger = logging.getLogger("vtnote.api")

    def model_status_payload(status: Any) -> dict[str, Any]:
        return {
            "model_name": status.model_name,
            "revision": status.revision,
            "state": status.state,
            "total_bytes": status.total_bytes,
            "downloaded_bytes": status.downloaded_bytes,
            "completed_files": status.completed_files,
            "current_file": status.current_file,
            "current_file_bytes": status.current_file_bytes,
            "cancel_requested": status.cancel_requested,
            "error_code": status.error_code,
        }

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        try:
            if request.headers.get("host") != expected_host:
                return _error(403, "forbidden_host", "request Host is not allowed")
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                if request.headers.get("origin") != expected_origin:
                    return _error(403, "forbidden_origin", "request Origin is not allowed")
                cookie = request.cookies.get("vtnote_csrf")
                header = request.headers.get("x-csrf-token")
                if not cookie or not header:
                    return _error(403, "csrf_failed", "CSRF validation failed")
                try:
                    cookie.encode("ascii")
                    header.encode("ascii")
                except UnicodeEncodeError:
                    return _error(403, "csrf_failed", "CSRF validation failed")
                if not token_secrets.compare_digest(cookie, header):
                    return _error(403, "csrf_failed", "CSRF validation failed")
            return await call_next(request)
        except Exception:
            logger.error("Unhandled request failure")
            return _error(500, "internal_error", "internal server error")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError):
        details = [
            {"location": list(item["loc"]), "type": item["type"]}
            for item in error.errors()
        ]
        return _error(422, "validation_error", "request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, error: StarletteHTTPException):
        message = error.detail if isinstance(error.detail, str) else "request failed"
        return _error(error.status_code, "http_error", message)

    @app.exception_handler(KeyError)
    async def missing_error(_: Request, __: KeyError):
        return _error(404, "not_found", "requested resource was not found")

    @app.exception_handler(InvalidConfiguration)
    async def configuration_error(_: Request, error: InvalidConfiguration):
        return _error(400, "invalid_configuration", str(error))

    @app.exception_handler(SensitiveTextMigrationRequired)
    async def sensitive_migration_error(_: Request, __: Exception):
        return _error(
            503,
            "sensitive_snapshot_migration_required",
            "sensitive text migration must be completed before execution",
        )

    @app.exception_handler(SensitiveTextProtectionError)
    async def sensitive_protection_error(_: Request, __: Exception):
        return _error(
            503,
            "sensitive_text_protection_unavailable",
            "sensitive text protection is unavailable",
        )

    @app.exception_handler(RuntimeAssetError)
    async def runtime_asset_error(_: Request, error: RuntimeAssetError):
        status = 404 if error.code == "asset_not_found" else 409
        if error.code in {"invalid_asset_id", "invalid_item_id"}:
            status = 400
        return _error(
            status,
            error.code,
            "runtime asset operation failed",
        )

    async def operation_error(_: Request, error: Exception):
        code = "unsafe_source_url" if isinstance(error, UnsafeSourceUrl) else "invalid_task"
        return _error(400, code, str(error))

    for operation_exception in (InvalidTaskOperation, UnsafeSourceUrl, UnsafePathError):
        app.add_exception_handler(operation_exception, operation_error)

    @app.exception_handler(SourceCapabilityError)
    async def source_capability_error(_: Request, error: SourceCapabilityError):
        return _error(
            503,
            error.code,
            "source capability is unavailable",
        )

    @app.exception_handler(PlatformSourceError)
    async def platform_source_error(_: Request, error: PlatformSourceError):
        status = {
            "removed": 404,
            "temporary": 503,
            "auth_required": 403,
            "region_restricted": 451,
            "unsupported": 400,
            "adapter_drift": 502,
            "invalid_content": 422,
        }[error.code]
        return _error(status, error.code, "platform source request failed")

    def services() -> tuple[Session, ConfigurationService, TaskService]:
        session = sessions()
        configuration = ConfigurationService(
            session,
            selected_secrets,
            paths=paths,
            sensitive_text_protector=selected_protector,
        )
        tasks = TaskService(
            session,
            configuration,
            paths,
            source_policy,
            local_source_validator=selected_local_sources,
        )
        return session, configuration, tasks

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "vtnote", "version": "0.1.0"}

    @app.get("/api/readiness")
    def readiness():
        report = ReadinessInspector(
            engine=selected_engine,
            paths=paths,
            model_probe=lambda: model_assets.status().state,
        ).inspect()
        payload = report.model_dump(mode="json")
        payload["limits"] = {
            "max_task_sources": 1,
            "max_media_bytes": selected_upload_limits.max_media_bytes,
            "max_subtitle_bytes": selected_upload_limits.max_subtitle_bytes,
        }
        payload["ui"] = {"available": frontend_available}
        return payload

    @app.get("/api/security/csrf")
    def issue_csrf():
        token = token_secrets.token_urlsafe(32)
        response = JSONResponse({"csrf_token": token})
        response.set_cookie(
            "vtnote_csrf", token, httponly=False, samesite="strict", secure=False, path="/"
        )
        return response

    @app.get("/api/assets/local-whisper")
    def get_local_whisper_asset():
        return model_status_payload(model_assets.status())

    @app.post("/api/assets/local-whisper/install", status_code=202)
    def install_local_whisper_asset(payload: ModelInstallInput):
        try:
            status = model_assets.request_install(
                acknowledge_download=payload.acknowledge_download,
                expected_revision=payload.expected_revision,
                now=datetime.now(timezone.utc),
            )
        except ModelAssetError as error:
            return _error(400, error.code, "model installation request rejected")
        return model_status_payload(status)

    @app.post("/api/assets/local-whisper/cancel")
    def cancel_local_whisper_asset():
        try:
            status = model_assets.cancel(now=datetime.now(timezone.utc))
        except ModelAssetError as error:
            return _error(400, error.code, "model installation cancel rejected")
        return model_status_payload(status)

    @app.get("/api/connections")
    def list_connections():
        session, configuration, _ = services()
        try:
            return [_dump(item) for item in configuration.list_connections()]
        finally:
            session.close()

    @app.get("/api/credential-cleanup")
    def credential_cleanup_status():
        session, configuration, _ = services()
        try:
            return _dump(configuration.credential_cleanup_status())
        finally:
            session.close()

    @app.post("/api/credential-cleanup/retry")
    def retry_credential_cleanup():
        session, configuration, _ = services()
        try:
            return _dump(configuration.retry_credential_cleanup())
        finally:
            session.close()

    @app.post("/api/connections", status_code=201)
    def create_connection(payload: ConnectionCreate):
        session, configuration, _ = services()
        try:
            return _dump(configuration.create_connection(**payload.model_dump()))
        finally:
            session.close()

    @app.get("/api/connections/{connection_id}")
    def get_connection(connection_id: str):
        session, configuration, _ = services()
        try:
            return _dump(configuration.get_connection(connection_id))
        finally:
            session.close()

    @app.patch("/api/connections/{connection_id}")
    def patch_connection(connection_id: str, payload: ConnectionPatch):
        session, configuration, _ = services()
        try:
            changes = payload.model_dump(exclude_unset=True)
            return _dump(configuration.update_connection(connection_id, **changes))
        finally:
            session.close()

    @app.delete("/api/connections/{connection_id}", status_code=204)
    def delete_connection(connection_id: str):
        session, configuration, _ = services()
        try:
            configuration.delete_connection(connection_id)
            return Response(status_code=204)
        finally:
            session.close()

    @app.post("/api/connections/{connection_id}/test")
    def test_connection(connection_id: str):
        session, configuration, _ = services()
        try:
            view = configuration.get_connection(connection_id)
            if view.protocol == "aliyun_bailian":
                return _dump(
                    configuration.record_connection_test(
                        connection_id,
                        ok=True,
                        message="Connection policy validated",
                    )
                )
            if (
                connection_tester is None
                and view.protocol != "tencent_recording_asr"
            ):
                return _error(
                    501,
                    "adapter_unavailable",
                    "connectivity adapter is not configured",
                )
            credentials = configuration.credential_bundle_for_connection(
                connection_id
            )
            try:
                result = selected_connection_tester.test_connection(
                    view,
                    credentials,
                    follow_redirects=False,
                )
            except Exception:
                result = ConnectivityResult(False, "Connectivity test failed")
            safe_message = (
                "Connection test succeeded" if result.ok else "Connection test failed"
            )
            return _dump(
                configuration.record_connection_test(
                    connection_id, ok=result.ok, message=safe_message
                )
            )
        finally:
            session.close()

    @app.get("/api/profiles")
    def list_profiles():
        session, configuration, _ = services()
        try:
            return [_dump(item) for item in configuration.list_profiles()]
        finally:
            session.close()

    @app.post("/api/profiles", status_code=201)
    def create_profile(payload: ProfileCreate):
        session, configuration, _ = services()
        try:
            return _dump(configuration.create_profile(**payload.model_dump()))
        finally:
            session.close()

    @app.get("/api/profiles/{profile_id}")
    def get_profile(profile_id: str):
        session, configuration, _ = services()
        try:
            return _dump(configuration.get_profile(profile_id))
        finally:
            session.close()

    @app.patch("/api/profiles/{profile_id}")
    def patch_profile(profile_id: str, payload: ProfilePatch):
        session, configuration, _ = services()
        try:
            return _dump(
                configuration.update_profile(profile_id, **payload.model_dump(exclude_unset=True))
            )
        finally:
            session.close()

    @app.delete("/api/profiles/{profile_id}", status_code=204)
    def delete_profile(profile_id: str):
        session, configuration, _ = services()
        try:
            configuration.delete_profile(profile_id)
            return Response(status_code=204)
        finally:
            session.close()

    @app.post("/api/profiles/{profile_id}/test")
    def test_profile(profile_id: str, payload: ProfileTestInput):
        session, configuration, _ = services()
        try:
            profile = configuration.get_profile(profile_id)
            if profile.protocol == "aliyun_bailian":
                if payload.test_kind == "connection_policy_validated":
                    return _dump(profile)
                if payload.test_kind != "profile_capability_tested":
                    return _error(
                        400,
                        "invalid_profile_test_kind",
                        "Bailian profile requires a capability test",
                    )
            elif payload.test_kind in {
                "connection_policy_validated",
                "profile_capability_tested",
            }:
                return _error(
                    400,
                    "invalid_profile_test_kind",
                    "profile test kind does not match provider",
                )
            if (
                profile_tester is None
                and profile.protocol
                not in {"tencent_recording_asr", "aliyun_bailian"}
            ):
                return _error(
                    501,
                    "adapter_unavailable",
                    "connectivity adapter is not configured",
                )
            if (
                payload.test_kind in {
                    "provider_profile",
                    "profile_capability_tested",
                }
                and not payload.acknowledge_billable_request
            ):
                return _error(
                    400,
                    "billable_test_ack_required",
                    "billable provider test requires explicit acknowledgement",
                )
            if (
                profile.purpose == "cloud_asr"
                and payload.test_kind == "provider_profile"
                and payload.speech_sample_upload_id is None
            ):
                return _error(
                    400,
                    "speech_test_sample_required",
                    "provider test requires an uploaded speech sample",
                )
            if payload.test_kind == "cos_sentinel" and profile.purpose != "cloud_asr":
                return _error(
                    400,
                    "invalid_profile_test_kind",
                    "COS sentinel test requires a cloud ASR profile",
                )
            credentials = configuration.credential_bundle_for_connection(
                profile.connection_id
            )
            selected_profile_tester = (
                profile_tester
                or (
                    default_bailian_tester
                    if profile.protocol == "aliyun_bailian"
                    else default_tencent_tester
                )
            )
            try:
                result = selected_profile_tester.test_profile(
                    profile,
                    credentials,
                    payload,
                    follow_redirects=False,
                )
            except Exception:
                result = ConnectivityResult(False, "Connectivity test failed")
            safe_message = "Profile test succeeded" if result.ok else "Profile test failed"
            return _dump(configuration.record_profile_test(
                profile_id, ok=result.ok, message=safe_message
            ))
        finally:
            session.close()

    @app.post("/api/profiles/{profile_id}/authorize-upload")
    def authorize_upload(profile_id: str):
        session, configuration, _ = services()
        try:
            return _dump(configuration.authorize_cloud_upload(profile_id))
        finally:
            session.close()

    @app.post("/api/profiles/{profile_id}/authorize-chat-data")
    def authorize_chat_data(
        profile_id: str,
        payload: ChatDataAuthorizationInput,
    ):
        if not payload.acknowledge_chat_data_upload:
            return _error(
                400,
                "chat_data_consent_required",
                "chat data upload requires explicit acknowledgement",
            )
        session, configuration, _ = services()
        try:
            response = _dump(configuration.authorize_chat_data(profile_id))
            response["chat_data_scope"] = {
                "subtitle_cues": True,
                "title_and_metadata": True,
                "target_or_output_language": True,
                "custom_prompt": True,
                "audio": False,
            }
            return response
        finally:
            session.close()

    @app.post("/api/profiles/{profile_id}/revoke-chat-data")
    def revoke_chat_data(profile_id: str):
        session, configuration, _ = services()
        try:
            return _dump(configuration.revoke_chat_data(profile_id))
        finally:
            session.close()

    @app.get("/api/defaults")
    def get_defaults():
        session, configuration, _ = services()
        try:
            return _dump(configuration.get_defaults())
        finally:
            session.close()

    @app.patch("/api/defaults")
    def patch_defaults(payload: DefaultsPatch):
        session, configuration, _ = services()
        try:
            nullable_profile_ids = {
                "cloud_asr_profile_id", "translation_profile_id", "notes_profile_id"
            }
            nullable_fields = nullable_profile_ids | {"notes_custom_prompt"}
            changes = {
                key: value for key, value in payload.model_dump(exclude_unset=True).items()
                if value is not None or key in nullable_fields
            }
            return _dump(configuration.update_defaults(**changes))
        finally:
            session.close()

    @app.post("/api/sources/probe")
    def probe_source(payload: ProbeInput):
        canonical_source = source_policy.validate(payload.url)
        result = selected_source_probe.probe(canonical_source)
        if result.source_kind not in REMOTE_SOURCE_KINDS or result.canonical_url is None:
            raise ValueError("URL probe returned a non-remote source")
        redirect_targets = list(result.redirect_trace)
        if not redirect_targets or redirect_targets[-1] != result.canonical_url:
            redirect_targets.append(result.canonical_url)
        source_policy.validate_redirect_chain(payload.url, redirect_targets)
        return {
            "source_kind": result.source_kind,
            "canonical_url": result.canonical_url,
            "title": result.title,
            "duration_ms": result.duration_ms,
            "subtitle_tracks": [
                _dump_subtitle(track) for track in result.subtitle_tracks
            ],
        }

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

    @app.get("/api/items/{item_id}/execution-summary")
    def get_item_execution_summary(
        item_id: str,
        format: Literal["json", "markdown"] = "json",
    ):
        session, _, tasks = services()
        try:
            payload = tasks.item_execution_summary(item_id)
            if format == "json":
                return payload
            return Response(
                render_execution_summary(payload, "markdown"),
                media_type="text/markdown",
            )
        finally:
            session.close()

    @app.get("/api/storage")
    def storage_summary():
        session = sessions()
        try:
            summary = RuntimeAssetService(session, paths).storage_summary()
            return {
                "data_root": str(paths.data_root),
                "runtime_cache_root": str(paths.runtime_cache_root),
                "retention_hours": 24,
                **summary,
            }
        finally:
            session.close()

    @app.get("/api/diagnostics")
    def download_diagnostics():
        return Response(
            diagnostic_bundle_bytes(
                settings=selected_settings,
                engine=selected_engine,
            ),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    'attachment; filename="vtnote-diagnostics.zip"'
                )
            },
        )

    @app.get("/api/storage/trash")
    def list_storage_trash():
        session = sessions()
        try:
            assets = RuntimeAssetService(session, paths).list_trashed()
            return [
                {
                    "id": asset.id,
                    "item_id": asset.item_id,
                    "role": asset.role,
                    "state": asset.state,
                    "size_bytes": asset.size_bytes,
                    "purge_after": (
                        asset.purge_after.isoformat()
                        if asset.purge_after is not None
                        else None
                    ),
                }
                for asset in assets
            ]
        finally:
            session.close()

    @app.post("/api/storage/trash/{asset_id}/restore")
    def restore_storage_asset(asset_id: str):
        session = sessions()
        try:
            asset = RuntimeAssetService(session, paths).restore(asset_id)
            return {
                "id": asset.id,
                "item_id": asset.item_id,
                "role": asset.role,
                "state": asset.state,
                "size_bytes": asset.size_bytes,
                "purge_after": None,
            }
        finally:
            session.close()

    if frontend_available:
        @app.api_route(
            "/api/{unknown_api_path:path}",
            methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            include_in_schema=False,
        )
        def unknown_api_route(unknown_api_path: str):
            del unknown_api_path
            raise StarletteHTTPException(status_code=404, detail="Not Found")

        frontend_assets = selected_frontend_dist / "assets"
        if frontend_assets.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=frontend_assets, check_dir=True),
                name="frontend-assets",
            )

        task_detail_route = re.compile(
            r"^tasks/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        )
        spa_routes = {
            "",
            "tasks",
            "settings",
            "settings/setup",
            "settings/connections",
            "settings/storage",
        }

        @app.api_route(
            "/{frontend_path:path}",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        def serve_frontend(frontend_path: str):
            normalized = frontend_path.strip("/")
            if normalized not in spa_routes and task_detail_route.fullmatch(
                normalized
            ) is None:
                raise StarletteHTTPException(status_code=404, detail="Not Found")
            return FileResponse(frontend_index, media_type="text/html")

    return app
