"""Loopback-only launcher and small API/worker supervisor."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import uvicorn
from sqlalchemy.orm import Session, sessionmaker

from vtnote.ai_stages import build_ai_stage_handlers
from vtnote.api import create_app
from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.local_asr import FasterWhisperTranscriber
from vtnote.logging_setup import configure_logging
from vtnote.maintenance import MaintenanceLoop, build_maintenance_service
from vtnote.media import CommandRunner, FfmpegBinaries, FfmpegMediaProcessor
from vtnote.model_assets import ModelAssetService
from vtnote.paths import StoragePaths
from vtnote.platform_sources import (
    FfmpegMediaValidator,
    build_default_platform_registry,
)
from vtnote.runtime_assets import RuntimeAssetService
from vtnote.secrets import KeyringSecretStore
from vtnote.sensitive_text import WindowsDpapiSensitiveTextProtector
from vtnote.source_stage import SourceStageHandler
from vtnote.tencent_asr import TencentRecordingClient
from vtnote.transcribe_stage import (
    SnapshotTencentCredentialResolver,
    TranscribeStageHandler,
    build_snapshot_cos_stager,
)
from vtnote.url_security import SocketResolver
from vtnote.worker import Worker, build_model_installer_loop
from vtnote.worker_store import WorkerStore
from vtnote.youtube_runtime import configure_managed_runtime_environment


class LauncherError(RuntimeError):
    """A startup or supervision failure represented by a safe diagnostic code."""


class ChildProcess(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float) -> int: ...

    def kill(self) -> None: ...


def child_commands(
    *,
    python_executable: str = sys.executable,
) -> dict[str, tuple[str, ...]]:
    return {
        "api": (python_executable, "-m", "vtnote", "api"),
        "worker": (python_executable, "-m", "vtnote", "worker"),
    }


def ensure_port_available(settings: Settings) -> None:
    if settings.bind_host != "127.0.0.1":
        raise LauncherError("non_loopback_bind_rejected")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        listener.bind((settings.bind_host, settings.bind_port))
    except OSError:
        raise LauncherError("port_unavailable") from None
    finally:
        listener.close()


def stop_children(
    children: Mapping[str, ChildProcess],
    *,
    timeout_seconds: float = 5.0,
    waiter: Callable[[ChildProcess, float], int] | None = None,
) -> None:
    selected_waiter = waiter or (lambda child, timeout: child.wait(timeout=timeout))
    running = [child for child in children.values() if child.poll() is None]
    for child in running:
        child.terminate()
    for child in running:
        try:
            selected_waiter(child, timeout_seconds)
        except (subprocess.TimeoutExpired, TimeoutError):
            child.kill()
            try:
                selected_waiter(child, timeout_seconds)
            except (subprocess.TimeoutExpired, TimeoutError):
                pass


def _spawn(command: Sequence[str]) -> subprocess.Popen[bytes]:
    kwargs: dict[str, object] = {
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(tuple(command), **kwargs)


def supervise(
    settings: Settings,
    *,
    process_factory: Callable[[Sequence[str]], ChildProcess] = _spawn,
    sleeper: Callable[[float], None] = time.sleep,
    stop_requested: Callable[[], bool] = lambda: False,
    maximum_restarts: int = 2,
) -> int:
    if maximum_restarts < 0:
        raise ValueError("maximum_restarts must not be negative")
    ensure_port_available(settings)
    commands = child_commands()
    children: dict[str, ChildProcess] = {}
    restarts = {role: 0 for role in commands}
    try:
        for role, command in commands.items():
            children[role] = process_factory(command)
        while not stop_requested():
            for role, child in tuple(children.items()):
                exit_code = child.poll()
                if exit_code is None:
                    continue
                if restarts[role] >= maximum_restarts:
                    raise LauncherError(f"{role}_restart_limit")
                restarts[role] += 1
                children[role] = process_factory(commands[role])
            sleeper(0.25)
    except KeyboardInterrupt:
        return 0
    except LauncherError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        stop_children(children)
    return 0


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def run_api(settings: Settings) -> int:
    production_settings = settings.model_copy(update={"enable_dev_docs": False})
    paths = StoragePaths.from_settings(production_settings)
    paths.ensure_roots()
    configure_managed_runtime_environment(production_settings)
    configure_logging(paths.runtime("logs"), process_name="api")
    uvicorn.run(
        create_app(settings=production_settings),
        host=production_settings.bind_host,
        port=production_settings.bind_port,
        access_log=False,
        log_config=None,
    )
    return 0


def _manifest_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "assets"
        / "models"
        / "large-v3-turbo.manifest.json"
    )


def run_worker(settings: Settings) -> int:
    paths = StoragePaths.from_settings(settings)
    paths.ensure_roots()
    configure_managed_runtime_environment(settings)
    configure_logging(paths.runtime("logs"), process_name="worker")
    protector = WindowsDpapiSensitiveTextProtector()
    engine = initialize_database(
        paths.database,
        sensitive_text_protector=protector,
    )
    secrets = KeyringSecretStore()
    resolver = SocketResolver()
    sessions = sessionmaker(engine, expire_on_commit=False)
    media_validator = FfmpegMediaProcessor(
        runner=CommandRunner(),
        binaries=FfmpegBinaries.discover(),
    )
    platform_source = build_default_platform_registry(
        settings=settings,
        resolver=resolver,
        session_factory=sessions,
    )
    source_handler = SourceStageHandler(
        paths=paths,
        platform_source=platform_source,
        local_sources=FfmpegMediaValidator(media_validator),
        preferred_languages=("zh-Hans", "zh", "zh-CN", "en"),
    )

    asset_session = Session(engine, expire_on_commit=False)
    assets = RuntimeAssetService(asset_session, paths)
    media = FfmpegMediaProcessor(
        runner=CommandRunner(),
        binaries=FfmpegBinaries.discover(),
        paths=paths,
        assets=assets,
    )
    model_assets = ModelAssetService(
        engine=engine,
        paths=paths,
        manifest_path=_manifest_path(),
    )
    local_transcriber = FasterWhisperTranscriber(
        assets=model_assets,
        expected_model_root=paths.durable("models", "faster-whisper"),
        expected_cache_root=paths.runtime("models", "faster-whisper"),
    )
    transcribe_handler = TranscribeStageHandler(
        paths=paths,
        media=media,
        local_transcriber=local_transcriber,
        cloud_client=TencentRecordingClient(),
        credential_resolver=SnapshotTencentCredentialResolver(
            engine=engine,
            secrets=secrets,
        ),
        cos_stager_resolver=build_snapshot_cos_stager,
    )
    handlers = {
        "source": source_handler,
        "transcribe": transcribe_handler,
        **build_ai_stage_handlers(
            engine=engine,
            paths=paths,
            secrets=secrets,
            sensitive_text_protector=protector,
        ),
    }

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker_id = f"worker-{uuid4()}"
    worker = Worker(
        store=WorkerStore(engine),
        worker_id=worker_id,
        handlers=handlers,
        lease_duration=timedelta(minutes=2),
        stop_requested=stop_event.is_set,
    )
    installer = build_model_installer_loop(
        engine=engine,
        paths=paths,
        manifest_path=_manifest_path(),
        worker_id=f"model-{uuid4()}",
        resolver=resolver,
        stop_requested=stop_event.is_set,
    )
    installer_thread = threading.Thread(
        target=installer.run,
        name="vtnote-model-installer",
        daemon=True,
    )
    maintenance_service, maintenance_session = build_maintenance_service(
        engine=engine,
        paths=paths,
        secrets=secrets,
        worker_id=f"maintenance-{uuid4()}",
    )
    maintenance = MaintenanceLoop(
        service=maintenance_service,
        stop_requested=stop_event.is_set,
    )
    maintenance_thread = threading.Thread(
        target=maintenance.run,
        name="vtnote-maintenance",
        daemon=True,
    )
    installer_thread.start()
    maintenance_thread.start()
    try:
        worker.run()
    finally:
        stop_event.set()
        installer_thread.join(timeout=5.0)
        maintenance_thread.join(timeout=5.0)
        maintenance_session.close()
        asset_session.close()
        engine.dispose()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vtnote")
    parser.add_argument(
        "role",
        choices=("supervisor", "api", "worker"),
        nargs="?",
        default="supervisor",
    )
    args = parser.parse_args(argv)
    settings = Settings()
    if args.role == "api":
        return run_api(settings)
    if args.role == "worker":
        return run_worker(settings)

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    paths = StoragePaths.from_settings(settings)
    paths.ensure_roots()
    configure_logging(paths.runtime("logs"), process_name="supervisor")
    try:
        return supervise(settings, stop_requested=stop_event.is_set)
    except LauncherError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
