"""FastAPI application for local configuration and durable task control."""

from __future__ import annotations

import logging
import importlib.util
import re
import secrets as token_secrets
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from vtnote.application.task_contracts import MAX_BATCH_SOURCES
from vtnote.config import Settings
from vtnote.chat import BailianProfileTester
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.diagnostics import diagnostic_bundle_bytes
from vtnote.exports import render_execution_summary
from vtnote.http.contracts import (
    AsrConnectionVerifyInput,
    ChatConnectionVerifyInput,
    ChatDataAuthorizationInput,
    ConnectionCreate,
    ConnectionPatch,
    ConnectionTester,
    ConnectivityResult,
    DefaultsPatch,
    NotesPromptView,
    ProbeInput,
    ProfileCreate,
    ProfilePatch,
    ProfileTester,
    ProfileTestInput,
    UploadTaskMetadata,
)
from vtnote.http.responses import (
    dump_model as _dump,
    error_response as _error,
)
from vtnote.http.export_routes import register_export_routes
from vtnote.http.library_routes import register_library_routes
from vtnote.http.model_asset_routes import register_model_asset_routes
from vtnote.http.source_routes import register_source_routes
from vtnote.http.task_routes import register_task_routes
from vtnote.folder_picker import DirectoryPicker, pick_directory
from vtnote.media import (
    MEDIA_EXTENSIONS,
    CommandRunner,
    FfmpegBinaries,
    FfmpegMediaProcessor,
    MediaError,
)
from vtnote.model_assets import (
    ModelAssetService,
    load_sensevoice_manifest,
    load_silero_vad_manifest,
)
from vtnote.notes import DEFAULT_NOTES_PROMPT
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.platform_sources import build_default_platform_registry
from vtnote.project_resources import bundled_asset, frontend_dist_path
from vtnote.provider_chat import (
    CHAT_PROTOCOLS,
    MODERN_CHAT_PROTOCOLS,
    ProviderProfileTester,
)
from vtnote.readiness import ReadinessInspector
from vtnote.youtube_runtime import inspect_youtube_runtime
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService
from vtnote.secrets import KeyringSecretStore, SecretStore
from vtnote.sources import (
    PlatformSourceError,
    SourceAdapter,
    SourceCapabilityError,
)
from vtnote.source_probing import SourceProbeService
from vtnote.sensitive_text import (
    SensitiveTextMigrationRequired,
    SensitiveTextProtectionError,
    SensitiveTextProtector,
    WindowsDpapiSensitiveTextProtector,
    migrate_sensitive_text,
)
from vtnote.tasks import (
    InvalidTaskOperation,
    LocalSourceValidator,
    TaskDeletionError,
    TaskService,
)
from vtnote.tencent_asr import (
    BUILTIN_ASR_TEST_SAMPLE_ID,
    BuiltinSpeechSampleResolver,
    SpeechSampleResolver,
    TencentConnectivityTester,
    TencentRecordingClient,
    UploadedSpeechSampleResolver,
)
from vtnote.tencent_contract import TENCENT_ASR_MODEL, TENCENT_LANGUAGE_SCOPE
from vtnote.tokenhub_chat import (
    TOKENHUB_DEFAULT_CONTEXT_LENGTH,
    TOKENHUB_DEFAULT_MODEL,
    TokenHubProfileTester,
)
from vtnote.uploads import (
    LocalSourceFiles,
    MultipartUploadStager,
    UploadError,
    UploadLimits,
    UploadService,
    UploadTaskContext,
)
from vtnote.url_security import (
    Resolver,
    SocketResolver,
    SourceUrlPolicy,
    UnsafeSourceUrl,
)


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
    directory_picker: DirectoryPicker | None = None,
) -> FastAPI:
    selected_settings = settings or Settings()
    paths = StoragePaths.from_settings(selected_settings)
    model_paths = StoragePaths.managed_assets_from_settings(selected_settings)
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
    source_policy = SourceUrlPolicy(
        selected_resolver,
        resolve_dns=selected_settings.platform_proxy_url is None,
    )
    selected_upload_limits = upload_limits or UploadLimits()
    selected_frontend_dist = (
        Path(frontend_dist)
        if frontend_dist is not None
        else frontend_dist_path()
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
    source_probe_service = SourceProbeService(source_policy, selected_source_probe)
    uploaded_speech_samples = UploadedSpeechSampleResolver(
        engine=selected_engine,
        paths=paths,
    )
    built_in_speech_samples = BuiltinSpeechSampleResolver(
        bundled_asset("test-audio", "tencent-asr-check.wav")
    )
    default_tencent_tester = TencentConnectivityTester(
        client=TencentRecordingClient(),
        sample_resolver=SpeechSampleResolver(
            builtin=built_in_speech_samples,
            uploaded=uploaded_speech_samples,
        ),
    )
    selected_connection_tester = (
        connection_tester or default_tencent_tester
    )
    default_bailian_tester = BailianProfileTester()
    default_tokenhub_tester = TokenHubProfileTester()
    default_provider_tester = ProviderProfileTester()
    model_assets = ModelAssetService(
        engine=selected_engine,
        paths=model_paths,
        manifest_path=bundled_asset(
            "models", "large-v3-turbo.manifest.json"
        ),
    )
    sensevoice_assets = ModelAssetService(
        engine=selected_engine,
        paths=model_paths,
        manifest_path=bundled_asset(
            "models", "sensevoice-small-int8.manifest.json"
        ),
        manifest_loader=load_sensevoice_manifest,
    )
    silero_vad_assets = ModelAssetService(
        engine=selected_engine,
        paths=model_paths,
        manifest_path=bundled_asset("models", "silero-vad.manifest.json"),
        manifest_loader=load_silero_vad_manifest,
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
        status = 404 if error.code in {"asset_not_found", "audio_not_found"} else 409
        if error.code in {"invalid_asset_id", "invalid_item_id"}:
            status = 400
        return _error(
            status,
            error.code,
            "runtime asset operation failed",
        )

    @app.exception_handler(TaskDeletionError)
    async def task_deletion_error(_: Request, error: TaskDeletionError):
        status = {
            "invalid_task_count": 400,
            "invalid_task_id": 400,
            "duplicate_task_ids": 400,
            "task_not_terminal": 409,
            "task_delete_active_lease": 409,
            "task_remote_cleanup_pending": 409,
            "task_delete_pending_changes": 409,
            "task_delete_database_busy": 409,
            "task_delete_staging_conflict": 409,
            "task_delete_cross_device": 409,
            "task_delete_asset_state_invalid": 409,
            "task_delete_filesystem_error": 500,
            "task_delete_recovery_failed": 500,
        }.get(error.code, 409)
        message = {
            "task_not_terminal": "processing tasks cannot be deleted",
            "task_delete_active_lease": "task work is still being released",
            "task_remote_cleanup_pending": "cloud cleanup must finish before deletion",
        }.get(error.code, "task deletion failed")
        return _error(status, error.code, message)

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
        message = {
            "removed": "platform content is unavailable",
            "temporary": "platform is temporarily unavailable",
            "auth_required": (
                "platform requires authentication or fresh verification cookies"
            ),
            "region_restricted": "platform content is unavailable in this region",
            "unsupported": "platform URL is unsupported",
            "adapter_drift": "platform adapter needs an update",
            "invalid_content": "platform returned invalid content",
        }[error.code]
        return _error(status, error.code, message)

    def services() -> tuple[Session, ConfigurationService, TaskService]:
        session = sessions()
        configuration = ConfigurationService(
            session,
            selected_secrets,
            paths=paths,
            model_paths=model_paths,
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

    def cleanup_profile_test_sample(item_id: str) -> None:
        cleanup_session = sessions()
        try:
            cleanup_assets = RuntimeAssetService(cleanup_session, paths)
            for role in ("uploaded_source", "cloud_audio", "cloud_audio_inline"):
                active = cleanup_assets.active_for_role(item_id=item_id, role=role)
                if active is not None:
                    cleanup_assets.trash(active.id)
        except (OSError, RuntimeAssetError):
            logging.getLogger("vtnote").warning(
                "profile test sample cleanup will be retried by retention maintenance"
            )
        finally:
            cleanup_session.close()

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "vtnote", "version": "0.1.0"}

    @app.get("/api/readiness")
    def readiness():
        def sensevoice_state() -> str:
            states = {
                sensevoice_assets.status().state,
                silero_vad_assets.status().state,
            }
            if states == {"installed"}:
                return (
                    "installed"
                    if importlib.util.find_spec("sherpa_onnx") is not None
                    else "runtime_unavailable"
                )
            if "failed" in states:
                return "failed"
            if states & {"queued", "downloading", "verifying"}:
                return "installing"
            return "not_installed"

        report = ReadinessInspector(
            engine=selected_engine,
            paths=paths,
            model_probe=lambda: model_assets.status().state,
            local_asr_engine_probes={
                "sensevoice_sherpa_onnx": sensevoice_state,
            },
            youtube_probe=lambda: inspect_youtube_runtime(
                selected_settings
            ).youtube_ready,
        ).inspect()
        payload = report.model_dump(mode="json")
        payload["limits"] = {
            "max_task_sources": 1,
            "max_batch_sources": MAX_BATCH_SOURCES,
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

    register_model_asset_routes(
        app,
        local_whisper=model_assets,
        sensevoice=sensevoice_assets,
        silero_vad=silero_vad_assets,
    )

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
    def delete_connection(
        connection_id: str,
        cascade_profiles: bool = Query(default=False),
    ):
        session, configuration, _ = services()
        try:
            configuration.delete_connection(
                connection_id,
                cascade_profiles=cascade_profiles,
            )
            return Response(status_code=204)
        finally:
            session.close()

    @app.post("/api/connections/{connection_id}/test")
    def test_connection(connection_id: str):
        session, configuration, _ = services()
        try:
            view = configuration.get_connection(connection_id)
            if view.protocol in CHAT_PROTOCOLS:
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

    @app.post("/api/connections/{connection_id}/verify-asr")
    def verify_asr_connection(
        connection_id: str,
        payload: AsrConnectionVerifyInput,
    ):
        if not payload.acknowledge_billable_request:
            return _error(
                400,
                "billable_test_ack_required",
                "ASR verification may create a small provider charge",
            )
        if not payload.authorize_task_audio_upload:
            return _error(
                400,
                "audio_upload_consent_required",
                "ASR verification requires explicit task audio authorization",
            )
        session, configuration, _ = services()
        try:
            connection = configuration.get_connection(connection_id)
            if connection.protocol != "tencent_recording_asr":
                return _error(
                    400,
                    "invalid_asr_connection",
                    "connection does not support Tencent ASR verification",
                )
            profile = next(
                (
                    candidate
                    for candidate in configuration.list_profiles()
                    if candidate.connection_id == connection_id
                    and candidate.purpose == "cloud_asr"
                ),
                None,
            )
            if profile is None:
                profile = configuration.create_profile(
                    name=f"{connection.name} ASR",
                    purpose="cloud_asr",
                    connection_id=connection_id,
                    model=TENCENT_ASR_MODEL,
                    context_length=32768,
                    options={
                        "language_scope": TENCENT_LANGUAGE_SCOPE,
                        "res_text_format": 3,
                        "sentence_max_length": 20,
                    },
                )
            credentials = configuration.credential_bundle_for_connection(
                connection_id
            )
            test_input = ProfileTestInput(
                test_kind="provider_profile",
                acknowledge_billable_request=True,
                speech_sample_upload_id=BUILTIN_ASR_TEST_SAMPLE_ID,
            )
            selected_tester = profile_tester or default_tencent_tester
            try:
                result = selected_tester.test_profile(
                    profile,
                    credentials,
                    test_input,
                    follow_redirects=False,
                )
            except Exception:
                result = ConnectivityResult(False, "ASR verification failed")
            logger.info(
                "tencent_asr_verification ok=%s result=%s",
                result.ok,
                result.message or "none",
            )
            connection = configuration.record_connection_test(
                connection_id,
                ok=result.ok,
                message=result.message,
            )
            profile = configuration.record_profile_test(
                profile.id,
                ok=result.ok,
                message=result.message,
            )
            if result.ok:
                profile = configuration.authorize_cloud_upload(profile.id)
                configuration.update_defaults(
                    asr_mode="auto",
                    cloud_asr_profile_id=profile.id,
                )
            return {
                "connection": _dump(connection),
                "profile": _dump(profile),
            }
        finally:
            session.close()

    @app.post("/api/connections/{connection_id}/verify-chat")
    def verify_chat_connection(
        connection_id: str,
        payload: ChatConnectionVerifyInput,
    ):
        if not payload.acknowledge_billable_request:
            return _error(
                400,
                "billable_test_ack_required",
                "AI model verification may consume provider quota",
            )
        if not payload.authorize_chat_data_upload:
            return _error(
                400,
                "chat_data_consent_required",
                "AI notes require explicit subtitle text authorization",
            )
        session, configuration, _ = services()
        try:
            connection = configuration.get_connection(connection_id)
            if connection.protocol != "tencent_tokenhub":
                return _error(
                    400,
                    "invalid_chat_connection",
                    "connection does not support TokenHub verification",
                )
            profile = next(
                (
                    candidate
                    for candidate in configuration.list_profiles()
                    if candidate.connection_id == connection_id
                    and candidate.purpose == "notes"
                ),
                None,
            )
            if profile is None:
                profile = configuration.create_profile(
                    name=f"{connection.name} GLM-5.1",
                    purpose="notes",
                    connection_id=connection_id,
                    model=TOKENHUB_DEFAULT_MODEL,
                    context_length=TOKENHUB_DEFAULT_CONTEXT_LENGTH,
                    options={
                        "temperature": 0.2,
                        "max_tokens": 4096,
                        "enable_thinking": False,
                    },
                )
            credentials = configuration.credential_bundle_for_connection(
                connection_id
            )
            test_input = ProfileTestInput(
                test_kind="profile_capability_tested",
                acknowledge_billable_request=True,
            )
            selected_tester = profile_tester or default_tokenhub_tester
            try:
                result = selected_tester.test_profile(
                    profile,
                    credentials,
                    test_input,
                    follow_redirects=False,
                )
            except Exception:
                result = ConnectivityResult(False, "AI model verification failed")
            logger.info(
                "tokenhub_verification ok=%s result=%s",
                result.ok,
                result.message or "none",
            )
            connection = configuration.record_connection_test(
                connection_id,
                ok=result.ok,
                message=result.message,
            )
            profile = configuration.record_profile_test(
                profile.id,
                ok=result.ok,
                message=result.message,
            )
            if result.ok:
                profile = configuration.authorize_chat_data(profile.id)
                configuration.update_defaults(
                    notes_enabled=True,
                    notes_profile_id=profile.id,
                )
            return {
                "connection": _dump(connection),
                "profile": _dump(profile),
            }
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
        session, configuration, tasks = services()
        sample_to_cleanup: str | None = None
        try:
            profile = configuration.get_profile(profile_id)
            if profile.protocol in CHAT_PROTOCOLS:
                if payload.test_kind == "connection_policy_validated":
                    return _dump(profile)
                if payload.test_kind != "profile_capability_tested":
                    return _error(
                        400,
                        "invalid_profile_test_kind",
                        "chat profile requires a capability test",
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
                not in {
                    "tencent_recording_asr",
                    *CHAT_PROTOCOLS,
                }
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
            if (
                profile.purpose == "cloud_asr"
                and payload.test_kind == "provider_profile"
                and payload.speech_sample_upload_id is not None
            ):
                try:
                    tasks.require_profile_test_sample(payload.speech_sample_upload_id)
                except (InvalidTaskOperation, KeyError):
                    return _error(
                        400,
                        "speech_test_sample_unavailable",
                        "speech test sample is unavailable",
                    )
                sample_to_cleanup = payload.speech_sample_upload_id
            if payload.test_kind == "cos_sentinel" and profile.purpose != "cloud_asr":
                return _error(
                    400,
                    "invalid_profile_test_kind",
                    "COS sentinel test requires a cloud ASR profile",
                )
            credentials = configuration.credential_bundle_for_connection(
                profile.connection_id
            )
            selected_profile_tester = profile_tester or (
                default_bailian_tester
                if profile.protocol == "aliyun_bailian"
                else (
                    default_tokenhub_tester
                    if profile.protocol == "tencent_tokenhub"
                    else (
                        default_provider_tester
                        if profile.protocol in MODERN_CHAT_PROTOCOLS
                        else default_tencent_tester
                    )
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
            if sample_to_cleanup is not None:
                cleanup_profile_test_sample(sample_to_cleanup)

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

    @app.post("/api/defaults/notes-prompt/reveal")
    def reveal_default_notes_prompt():
        session, configuration, _ = services()
        try:
            defaults = configuration.get_defaults()
            custom_prompt = (
                configuration.resolve_default_custom_prompt()
                if defaults.has_custom_prompt
                else None
            )
            payload = NotesPromptView(
                prompt=custom_prompt or DEFAULT_NOTES_PROMPT,
                is_custom=(
                    defaults.notes_template == "custom"
                    and custom_prompt is not None
                ),
            )
            return JSONResponse(
                payload.model_dump(mode="json"),
                headers={
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                },
            )
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
        return source_probe_service.probe(payload.url)

    @app.post("/api/test-samples", status_code=201)
    async def upload_profile_test_sample(request: Request):
        session, _, tasks = services()
        try:
            content_type = request.headers.get("content-type", "")
            if content_type.split(";", 1)[0].strip().casefold() != "multipart/form-data":
                return _error(
                    415,
                    "unsupported_media_type",
                    "speech sample must be a multipart media upload",
                )
            raw_length = request.headers.get("content-length")
            try:
                content_length = int(raw_length) if raw_length is not None else None
            except ValueError:
                return _error(400, "invalid_content_length", "invalid Content-Length")
            sample_limits = UploadLimits(max_media_bytes=32 * 1024 * 1024)
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
                if payload.model_dump(exclude_none=True) != {"kind": "media"}:
                    raise ValueError("test sample accepts media only")
                created = tasks.create_upload_task(
                    upload_kind="media",
                    upload_id=upload_id,
                    options={
                        "asr_mode": "local",
                        "translation_enabled": False,
                        "notes_enabled": False,
                    },
                )
                return UploadTaskContext(
                    task_id=created.id,
                    item_id=created.items[0].id,
                )

            try:
                state = await MultipartUploadStager(paths, sample_limits).consume(
                    request.stream(),
                    content_type=content_type,
                    content_length=content_length,
                    accept_metadata=accept_metadata,
                )
                if state.incoming_path is None:
                    raise UploadError(
                        "upload_file_missing",
                        status_code=400,
                        state=state,
                    )
                media_info = selected_local_sources.validate_media(state.incoming_path)
                if not 2_000 <= media_info.duration_ms <= 10_000:
                    raise UploadError(
                        "speech_sample_duration",
                        status_code=400,
                        state=state,
                    )
                created = uploads.complete(state)
                sample = tasks.finalize_profile_test_sample(created.id)
                return {
                    "id": sample.id,
                    "duration_ms": media_info.duration_ms,
                    "size_bytes": media_info.size_bytes,
                    "available_for_minutes": 60,
                }
            except UploadError as error:
                uploads.fail(error.state, code=error.code)
                if error.state.context is not None:
                    tasks.finalize_profile_test_sample(
                        error.state.context.task_id
                    )
                return _error(
                    error.status_code,
                    error.code,
                    "speech sample upload failed",
                )
            except (MediaError, OSError, ValueError):
                if "state" in locals():
                    uploads.fail(state, code="invalid_media")
                    if state.context is not None:
                        tasks.finalize_profile_test_sample(
                            state.context.task_id
                        )
                return _error(
                    400,
                    "invalid_media",
                    "speech sample is not valid media",
                )
        finally:
            session.close()

    register_task_routes(
        app,
        services=services,
        paths=paths,
        selected_local_sources=selected_local_sources,
        selected_upload_limits=selected_upload_limits,
    )
    register_source_routes(app, service=source_probe_service)
    register_library_routes(app, services=services, paths=paths)
    register_export_routes(
        app,
        services=services,
        paths=paths,
        directory_picker=directory_picker or pick_directory,
    )

    def item_audio_source(
        assets: RuntimeAssetService, item_id: str
    ) -> Path | None:
        for role in (
            "downloaded_audio",
            "uploaded_source",
            "cloud_audio_inline",
            "cloud_audio",
            "local_audio",
        ):
            view = assets.active_for_role(item_id=item_id, role=role)
            if view is None:
                continue
            resolved = assets.resolve(view.id)
            if resolved.suffix.removeprefix(".").casefold() in MEDIA_EXTENSIONS:
                return resolved
        return None

    @app.get("/api/items/{item_id}/outcomes")
    def get_item_outcomes(item_id: str):
        session, _, tasks = services()
        try:
            outcomes = tasks.item_outcomes(item_id)
            audio = item_audio_source(RuntimeAssetService(session, paths), item_id)
            return {"audio": audio is not None, **outcomes}
        finally:
            session.close()

    @app.get("/api/items/{item_id}/audio")
    def download_item_audio(
        item_id: str,
        format: Literal["m4a", "mp3"] = "m4a",
        inline: bool = False,
    ):
        session, _, tasks = services()
        try:
            tasks.item_outcomes(item_id)
            assets = RuntimeAssetService(session, paths)
            source = item_audio_source(assets, item_id)
            if source is None:
                raise RuntimeAssetError("audio_not_found")
            prepared = FfmpegMediaProcessor(
                runner=CommandRunner(),
                binaries=FfmpegBinaries.discover(),
                paths=paths,
                assets=assets,
            ).export_audio(item_id, source, format)
            return FileResponse(
                prepared.path,
                media_type="audio/mp4" if format == "m4a" else "audio/mpeg",
                filename=(
                    None if inline else f"vtnote-{item_id[:8]}-audio.{format}"
                ),
                headers={"Cache-Control": "private, max-age=0"},
            )
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

        frontend_favicon = selected_frontend_dist / "favicon.svg"
        if frontend_favicon.is_file():
            @app.api_route(
                "/favicon.svg",
                methods=["GET", "HEAD"],
                include_in_schema=False,
            )
            def serve_favicon():
                return FileResponse(frontend_favicon, media_type="image/svg+xml")

        task_detail_route = re.compile(
            r"^tasks/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        )
        spa_routes = {
            "",
            "tasks",
            "settings",
            "settings/export",
            "settings/models",
            "settings/setup",
            "settings/connections",
            "settings/ai-connections",
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
