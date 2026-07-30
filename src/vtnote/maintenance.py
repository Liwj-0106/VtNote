"""Lease-guarded local cleanup and one-at-a-time provider reconciliation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import Engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from vtnote.cloud_submissions import CloudSubmission, CosLocator
from vtnote.models import (
    CloudSubmissionRecord,
    ResourceLeaseRecord,
    StageRunRecord,
)
from vtnote.paths import StoragePaths
from vtnote.runtime_assets import RuntimeAssetService
from vtnote.secrets import SecretStore
from vtnote.tencent_asr import (
    TencentRecordingClient,
    TencentRequestContext,
    TencentSubmissionReconciler,
)
from vtnote.transcribe_stage import (
    SnapshotTencentCredentialResolver,
    build_snapshot_cos_stager,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RuntimeAssets(Protocol):
    def trash_abandoned_profile_test_samples(
        self, *, now: datetime
    ) -> tuple[str, ...]: ...

    def purge_due(self, *, now: datetime) -> tuple[str, ...]: ...


class Reconciler(Protocol):
    def reconcile_one_due(self, now: datetime): ...


class MaintenanceLease:
    RESOURCE_KEY = "maintenance"

    def __init__(
        self,
        engine: Engine,
        *,
        owner: str,
        duration: timedelta,
    ) -> None:
        if not owner or duration <= timedelta(0):
            raise ValueError("invalid maintenance lease")
        self.engine = engine
        self.owner = owner
        self.duration = duration

    def acquire(self, now: datetime) -> bool:
        timestamp = _utc(now)
        with Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(ResourceLeaseRecord, self.RESOURCE_KEY)
            if (
                row is not None
                and row.lease_owner != self.owner
                and row.lease_expires_at > timestamp
            ):
                session.rollback()
                return False
            if row is None:
                row = ResourceLeaseRecord(
                    resource_key=self.RESOURCE_KEY,
                    lease_owner=self.owner,
                    lease_expires_at=timestamp + self.duration,
                    heartbeat_at=timestamp,
                )
                session.add(row)
            else:
                row.lease_owner = self.owner
                row.lease_expires_at = timestamp + self.duration
                row.heartbeat_at = timestamp
            session.commit()
            return True

    def release(self) -> None:
        with Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(ResourceLeaseRecord, self.RESOURCE_KEY)
            if row is not None and row.lease_owner == self.owner:
                session.delete(row)
                session.commit()
            else:
                session.rollback()


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    acquired: bool
    trashed_asset_ids: tuple[str, ...] = ()
    purged_asset_ids: tuple[str, ...] = ()
    external_action: str | None = None


class MaintenanceService:
    def __init__(
        self,
        *,
        lease: MaintenanceLease,
        runtime_assets: RuntimeAssets,
        reconciler: Reconciler | None,
    ) -> None:
        self.lease = lease
        self.runtime_assets = runtime_assets
        self.reconciler = reconciler

    def run_once(self, now: datetime) -> MaintenanceResult:
        timestamp = _utc(now)
        if not self.lease.acquire(timestamp):
            return MaintenanceResult(acquired=False)
        try:
            trashed = self.runtime_assets.trash_abandoned_profile_test_samples(
                now=timestamp
            )
            purged = self.runtime_assets.purge_due(now=timestamp)
            external = (
                self.reconciler.reconcile_one_due(timestamp)
                if self.reconciler is not None
                else None
            )
            return MaintenanceResult(
                acquired=True,
                trashed_asset_ids=trashed,
                purged_asset_ids=purged,
                external_action=(
                    str(external.action)
                    if external is not None
                    and isinstance(getattr(external, "action", None), str)
                    else None
                ),
            )
        finally:
            self.lease.release()


class MaintenanceLoop:
    def __init__(
        self,
        *,
        service: MaintenanceService,
        stop_requested: Callable[[], bool],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = time.sleep,
        interval_seconds: float = 5.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("invalid maintenance interval")
        self.service = service
        self.stop_requested = stop_requested
        self.clock = clock
        self.sleeper = sleeper
        self.interval_seconds = interval_seconds

    def run(self) -> None:
        logger = logging.getLogger("vtnote.maintenance")
        while not self.stop_requested():
            try:
                self.service.run_once(self.clock())
            except Exception:
                logger.error("maintenance_pass_failed")
            self.sleeper(self.interval_seconds)


class SnapshotTencentMaintenanceRuntime:
    """Resolve query/delete credentials from the immutable submission stage."""

    def __init__(self, *, engine: Engine, secrets: SecretStore) -> None:
        self.engine = engine
        self.credentials = SnapshotTencentCredentialResolver(
            engine=engine,
            secrets=secrets,
        )

    def _profile(self, stage_run_id: str):
        with Session(self.engine) as session:
            stage = session.get(StageRunRecord, stage_run_id)
            if stage is None or not isinstance(
                stage.item.task.pipeline_snapshot_json,
                dict,
            ):
                raise ValueError("cloud maintenance snapshot is unavailable")
            snapshot = stage.item.task.pipeline_snapshot_json
            selected = snapshot.get("asr")
            override = stage.retry_override_json
            if (
                isinstance(override, dict)
                and override.get("strategy") == "cloud_confirmed"
            ):
                selected = override.get("asr")
            if not isinstance(selected, dict):
                raise ValueError("cloud maintenance snapshot is invalid")
            profile = selected.get("profile")
            if not isinstance(profile, dict):
                raise ValueError("cloud maintenance profile is unavailable")
            return profile

    def request_context(
        self,
        submission: CloudSubmission,
    ) -> TencentRequestContext:
        profile = self._profile(submission.stage_run_id)
        return TencentRequestContext(
            credentials=self.credentials(profile),
            timestamp=int(datetime.now(timezone.utc).timestamp()),
        )

    def delete(self, locator: CosLocator) -> None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(CloudSubmissionRecord).where(
                    CloudSubmissionRecord.cos_bucket == locator.bucket,
                    CloudSubmissionRecord.cos_region == locator.region,
                    CloudSubmissionRecord.cos_object_key == locator.object_key,
                )
            )
            if row is None:
                raise ValueError("cloud cleanup submission is unavailable")
            profile = self._profile(row.stage_run_id)
        credentials = self.credentials(profile)
        build_snapshot_cos_stager(profile, credentials).delete(locator)


def build_maintenance_service(
    *,
    engine: Engine,
    paths: StoragePaths,
    secrets: SecretStore,
    worker_id: str,
) -> tuple[MaintenanceService, Session]:
    runtime_session = Session(engine, expire_on_commit=False)
    runtime = SnapshotTencentMaintenanceRuntime(
        engine=engine,
        secrets=secrets,
    )
    reconciler = TencentSubmissionReconciler(
        engine=engine,
        client=TencentRecordingClient(),
        request_context=runtime.request_context,
        cos_stager=runtime,
        worker_id=f"{worker_id}-tencent",
    )
    return (
        MaintenanceService(
            lease=MaintenanceLease(
                engine,
                owner=worker_id,
                duration=timedelta(minutes=2),
            ),
            runtime_assets=RuntimeAssetService(runtime_session, paths),
            reconciler=reconciler,
        ),
        runtime_session,
    )
