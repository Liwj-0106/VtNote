"""FastAPI application for local configuration and durable task control."""

from __future__ import annotations

import logging
import secrets as token_secrets
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from vtnote.config import Settings
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.exports import ExportFormat
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.secrets import KeyringSecretStore, SecretStore
from vtnote.tasks import InvalidTaskOperation, TaskService
from vtnote.url_security import Resolver, SocketResolver, SourceUrlPolicy, UnsafeSourceUrl


@dataclass(frozen=True, slots=True)
class ConnectivityResult:
    ok: bool
    message: str | None = None


@dataclass(frozen=True, slots=True)
class SubtitleDescriptor:
    language: str
    format: str
    is_manual: bool


@dataclass(frozen=True, slots=True)
class ProbeResult:
    canonical_url: str
    title: str | None
    platform: str
    duration_ms: int | None = None
    subtitles: tuple[SubtitleDescriptor | dict[str, Any], ...] = ()
    redirect_chain: tuple[str, ...] = ()


class ConnectionTester(Protocol):
    def test_connection(
        self, connection: Any, secret: str | None, *, follow_redirects: Literal[False]
    ) -> ConnectivityResult: ...


class ProfileTester(Protocol):
    def test_profile(
        self, profile: Any, secret: str | None, *, follow_redirects: Literal[False]
    ) -> ConnectivityResult: ...


class SourceProbe(Protocol):
    """Trusted Task 3 boundary: disable auto-redirects and validate peers before I/O."""

    def probe(
        self, url: str, validate_redirect: Callable[[str], str]
    ) -> ProbeResult: ...


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConnectionCreate(InputModel):
    name: str = Field(min_length=1, max_length=128)
    protocol: str
    base_url: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, min_length=1)


class ConnectionPatch(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = None
    parameters: dict[str, Any] | None = None
    secret: str | None = Field(default=None, min_length=1)
    clear_secret: bool = False

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in ("name", "base_url", "parameters", "secret", "clear_secret"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value


class ProfileCreate(InputModel):
    name: str = Field(min_length=1, max_length=128)
    purpose: str
    connection_id: str
    model: str = Field(min_length=1)
    context_length: int = Field(default=8192, gt=0)
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


class ProbeInput(InputModel):
    url: str = Field(min_length=1)


class RetryInput(InputModel):
    item_id: str
    stage: str


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _dump_subtitle(value: SubtitleDescriptor | dict[str, Any]) -> dict[str, Any]:
    track = value if isinstance(value, SubtitleDescriptor) else SubtitleDescriptor(**value)
    return {
        "language": track.language,
        "format": track.format,
        "is_manual": track.is_manual,
    }


def create_app(
    *,
    settings: Settings | None = None,
    engine: Engine | None = None,
    secret_store: SecretStore | None = None,
    resolver: Resolver | None = None,
    connection_tester: ConnectionTester | None = None,
    profile_tester: ProfileTester | None = None,
    source_probe: SourceProbe | None = None,
) -> FastAPI:
    selected_settings = settings or Settings()
    paths = StoragePaths.from_settings(selected_settings)
    selected_engine = engine or initialize_database(paths.database)
    selected_secrets = secret_store or KeyringSecretStore()
    source_policy = SourceUrlPolicy(resolver or SocketResolver())
    sessions = sessionmaker(selected_engine, expire_on_commit=False)
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

    async def operation_error(_: Request, error: Exception):
        code = "unsafe_source_url" if isinstance(error, UnsafeSourceUrl) else "invalid_task"
        return _error(400, code, str(error))

    for operation_exception in (InvalidTaskOperation, UnsafeSourceUrl, UnsafePathError):
        app.add_exception_handler(operation_exception, operation_error)

    def services() -> tuple[Session, ConfigurationService, TaskService]:
        session = sessions()
        configuration = ConfigurationService(session, selected_secrets, paths=paths)
        tasks = TaskService(session, configuration, paths, source_policy)
        return session, configuration, tasks

    @app.get("/api/security/csrf")
    def issue_csrf():
        token = token_secrets.token_urlsafe(32)
        response = JSONResponse({"csrf_token": token})
        response.set_cookie(
            "vtnote_csrf", token, httponly=False, samesite="strict", secure=False, path="/"
        )
        return response

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
            if connection_tester is None:
                return _error(501, "adapter_unavailable", "connectivity adapter is not configured")
            view = configuration.get_connection(connection_id)
            secret = configuration.secret_for_connection(connection_id)
            try:
                result = connection_tester.test_connection(view, secret, follow_redirects=False)
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
    def test_profile(profile_id: str):
        session, configuration, _ = services()
        try:
            if profile_tester is None:
                return _error(501, "adapter_unavailable", "connectivity adapter is not configured")
            profile = configuration.get_profile(profile_id)
            secret = configuration.secret_for_connection(profile.connection_id)
            try:
                result = profile_tester.test_profile(profile, secret, follow_redirects=False)
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
        source_policy.validate(payload.url)
        if source_probe is None:
            return _error(501, "adapter_unavailable", "source probe adapter is not configured")
        result = source_probe.probe(payload.url, source_policy.validate)
        redirect_targets = list(result.redirect_chain)
        if not redirect_targets or redirect_targets[-1] != result.canonical_url:
            redirect_targets.append(result.canonical_url)
        source_policy.validate_redirect_chain(payload.url, redirect_targets)
        if result.duration_ms is not None and result.duration_ms < 0:
            raise ValueError("probe returned a negative duration")
        subtitles = [_dump_subtitle(track) for track in result.subtitles]
        return {
            "canonical_url": result.canonical_url,
            "title": result.title,
            "platform": result.platform,
            "duration_ms": result.duration_ms,
            "subtitles": subtitles,
            "redirect_chain": list(result.redirect_chain),
        }

    @app.get("/api/tasks")
    def list_tasks():
        session, _, tasks = services()
        try:
            return [_dump(item) for item in tasks.list_tasks()]
        finally:
            session.close()

    @app.post("/api/tasks", status_code=201)
    def create_task(payload: TaskCreate):
        session, _, tasks = services()
        try:
            sources = [item.model_dump() for item in payload.sources]
            options = payload.model_dump(
                exclude={"sources"}, exclude_unset=True, exclude_none=True
            )
            return _dump(tasks.create_task(sources=sources, options=options))
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
            return _dump(tasks.retry_stage(payload.item_id, payload.stage))
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
            return Response(rendered, media_type=content_types[format])
        finally:
            session.close()

    return app
