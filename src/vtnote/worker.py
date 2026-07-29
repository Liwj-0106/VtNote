"""Small orchestration loop for durable stage handlers."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine

from vtnote.diagnostics import sanitize_diagnostic
from vtnote.model_assets import (
    HuggingFaceModelTransport,
    ModelAssetError,
    ModelAssetService,
    ModelDownloadWorker,
)
from vtnote.paths import StoragePaths
from vtnote.platform_transport import PinnedHttpsTransport
from vtnote.url_security import Resolver
from vtnote.worker_store import StageClaim, StageFailure, StageResult, WorkerStore


class StageCancelled(RuntimeError):
    """Raised at a cooperative checkpoint after cancellation is persisted."""


class StageExecutionError(RuntimeError):
    """A handler failure represented by a closed safe machine code."""

    def __init__(self, code: str) -> None:
        if (
            not isinstance(code, str)
            or not code
            or len(code) > 64
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in code)
        ):
            raise ValueError("invalid stage execution error code")
        self.code = code
        super().__init__(code)


class StageHandler(Protocol):
    def run(self, context: StageContext) -> StageResult: ...


class ModelInstaller(Protocol):
    def run_one(self) -> str | None: ...


class ModelInstallerLoop:
    """Supervise durable model work independently from media stage claims."""

    def __init__(
        self,
        *,
        installer: ModelInstaller,
        stop_requested: Callable[[], bool] = lambda: False,
        sleeper: Callable[[float], None] = time.sleep,
        idle_delay: float = 1.0,
    ) -> None:
        if idle_delay <= 0:
            raise ValueError("model installer idle delay must be positive")
        self.installer = installer
        self.stop_requested = stop_requested
        self.sleeper = sleeper
        self.idle_delay = idle_delay

    def run(self) -> None:
        while not self.stop_requested():
            try:
                result = self.installer.run_one()
            except ModelAssetError:
                self.sleeper(self.idle_delay)
                continue
            if result is None:
                self.sleeper(self.idle_delay)


def build_model_installer_loop(
    *,
    engine: Engine,
    paths: StoragePaths,
    manifest_path: Path,
    worker_id: str,
    resolver: Resolver,
    stop_requested: Callable[[], bool] = lambda: False,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] | None = None,
) -> ModelInstallerLoop:
    selected_clock = clock or (lambda: datetime.now(timezone.utc))
    installer = ModelDownloadWorker(
        service=ModelAssetService(
            engine=engine,
            paths=paths,
            manifest_path=manifest_path,
        ),
        transport=HuggingFaceModelTransport(
            PinnedHttpsTransport(resolver=resolver)
        ),
        worker_id=worker_id,
        clock=selected_clock,
    )
    return ModelInstallerLoop(
        installer=installer,
        stop_requested=stop_requested,
        sleeper=sleeper,
    )


@dataclass(frozen=True)
class StageContext:
    store: WorkerStore
    claim: StageClaim
    clock: Callable[[], datetime]

    def checkpoint(self) -> None:
        now = self.clock()
        if self.store.cancel_if_requested(self.claim, now):
            raise StageCancelled()
        if not self.store.heartbeat(self.claim, now):
            raise StageCancelled("stage lease is no longer owned")


class Worker:
    def __init__(
        self,
        *,
        store: WorkerStore,
        worker_id: str,
        handlers: Mapping[str, StageHandler],
        lease_duration: timedelta,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        stop_requested: Callable[[], bool] = lambda: False,
        initial_idle_delay: float = 0.1,
        maximum_idle_delay: float = 2.0,
    ) -> None:
        if not worker_id:
            raise ValueError("worker id is required")
        if lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        if initial_idle_delay <= 0 or maximum_idle_delay < initial_idle_delay:
            raise ValueError("invalid idle delay bounds")
        self.store = store
        self.worker_id = worker_id
        self.handlers = dict(handlers)
        self.lease_duration = lease_duration
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleeper = sleeper
        self.stop_requested = stop_requested
        self.initial_idle_delay = initial_idle_delay
        self.maximum_idle_delay = maximum_idle_delay

    def run(self) -> None:
        self.store.recover_expired(self.clock())
        idle_delay = self.initial_idle_delay
        while not self.stop_requested():
            claim = self.store.claim_next(
                self.worker_id,
                self.clock(),
                self.lease_duration,
            )
            if claim is None:
                self.sleeper(idle_delay)
                idle_delay = min(self.maximum_idle_delay, idle_delay * 2)
                continue
            idle_delay = self.initial_idle_delay
            handler = self.handlers.get(claim.stage)
            if handler is None:
                self.store.fail(
                    claim,
                    StageFailure(
                        error_code="handler_unavailable",
                        error_message=f"no handler registered for {claim.stage}",
                    ),
                    now=self.clock(),
                )
                continue
            context = StageContext(store=self.store, claim=claim, clock=self.clock)
            try:
                result = handler.run(context)
            except StageCancelled:
                continue
            except StageExecutionError as error:
                self.store.fail(
                    claim,
                    StageFailure(
                        error_code=error.code,
                        error_message=error.code,
                    ),
                    now=self.clock(),
                )
            except Exception as error:
                self.store.fail(
                    claim,
                    StageFailure(
                        error_code="handler_failed",
                        error_message=sanitize_diagnostic(str(error))
                        or "handler failed",
                    ),
                    now=self.clock(),
                )
            else:
                self.store.complete(claim, result, now=self.clock())
