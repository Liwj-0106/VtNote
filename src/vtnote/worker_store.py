"""Short, lease-guarded database transitions for durable workers."""

from __future__ import annotations

import copy
import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, selectinload

from vtnote.diagnostics import sanitize_diagnostic
from vtnote.models import (
    ItemRecord,
    ResourceLeaseRecord,
    StageRunRecord,
    TaskRecord,
    WorkerHeartbeatRecord,
)
from vtnote.pipeline import (
    STAGE_DEPENDENCIES,
    STAGE_ORDER,
    SUCCESSFUL_STAGE_STATUSES,
    aggregate_item_status,
    aggregate_task_status,
    validate_execution_evidence,
    validate_stage_progress,
)
from vtnote.stage_models import allowed_stage_models


_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")
_ELIGIBLE_CONTAINER_STATUSES = frozenset({"queued", "running"})


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in copy.deepcopy(dict(value)).items()}
        )
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class StageClaim:
    stage_run_id: str
    item_id: str
    stage: str
    attempt: int
    worker_id: str
    lease_expires_at: datetime
    recovery_generation: int
    retry_override: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class StageResult:
    status: str = "completed"
    warning: str | None = None
    item_title: str | None = None
    execution_evidence: Mapping[str, str] | None = None
    skip_stages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"completed", "skipped"}:
            raise ValueError("stage result status must be completed or skipped")
        if self.item_title is not None and (
            not isinstance(self.item_title, str)
            or not self.item_title.strip()
            or len(self.item_title) > 4096
        ):
            raise ValueError("invalid stage result item title")
        if (
            not isinstance(self.skip_stages, tuple)
            or len(self.skip_stages) != len(set(self.skip_stages))
            or any(stage not in STAGE_ORDER for stage in self.skip_stages)
        ):
            raise ValueError("invalid stage result skip stages")
        if self.execution_evidence is not None:
            selected_model = self.execution_evidence.get("model")
            normalized = validate_execution_evidence(
                self.execution_evidence,
                allowed_models=(
                    (selected_model,)
                    if isinstance(selected_model, str)
                    else ()
                ),
            )
            object.__setattr__(
                self,
                "execution_evidence",
                MappingProxyType(dict(normalized)),
            )
        if self.item_title is not None:
            object.__setattr__(self, "item_title", self.item_title.strip())


@dataclass(frozen=True)
class StageFailure:
    error_code: str
    error_message: str
    external_submission_state: str | None = None
    warning: str | None = None

    def __post_init__(self) -> None:
        if _ERROR_CODE.fullmatch(self.error_code) is None:
            raise ValueError("invalid stage error code")
        if self.external_submission_state not in {None, "submission_unknown"}:
            raise ValueError("invalid external submission state")


class StageDeferred(RuntimeError):
    """Yield a claimed stage while a durable external request is pending."""

    def __init__(
        self,
        *,
        external_submission_state: str,
        execution_evidence: Mapping[str, str],
        warning: str | None = None,
    ) -> None:
        if external_submission_state not in {"submitted", "waiting"}:
            raise ValueError("invalid external submission state")
        self.external_submission_state = external_submission_state
        selected_model = execution_evidence.get("model")
        self.execution_evidence = MappingProxyType(
            dict(
                validate_execution_evidence(
                    execution_evidence,
                    allowed_models=(
                        (selected_model,)
                        if isinstance(selected_model, str)
                        else ()
                    ),
                )
            )
        )
        self.warning = sanitize_diagnostic(warning)
        super().__init__("stage_waiting_external")


class StageRequeue(RuntimeError):
    """Release a claim back to the durable queue without recording failure."""


class WorkerStore:
    """Own independent sessions for atomic claims and bounded state changes."""

    def __init__(self, engine: Engine, *, process_id: int | None = None) -> None:
        self.engine = engine
        self.process_id = os.getpid() if process_id is None else process_id

    @staticmethod
    def _begin_immediate(session: Session) -> None:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    @staticmethod
    def _latest_by_stage(item: ItemRecord) -> dict[str, StageRunRecord]:
        latest: dict[str, StageRunRecord] = {}
        for row in item.stage_runs:
            current = latest.get(row.stage)
            if current is None or row.attempt > current.attempt:
                latest[row.stage] = row
        return latest

    _allowed_stage_models = staticmethod(allowed_stage_models)

    @classmethod
    def _recalculate_item_and_task(
        cls,
        session: Session,
        item: ItemRecord,
    ) -> None:
        latest = cls._latest_by_stage(item)
        statuses = {stage: row.status for stage, row in latest.items()}
        snapshot = item.task.pipeline_snapshot_json
        if (
            isinstance(snapshot, dict)
            and snapshot.get("output_type") == "audio"
            and "transcribe" not in statuses
        ):
            statuses["transcribe"] = "skipped"
        item.status = aggregate_item_status(
            statuses,
            has_warnings=any(row.warning is not None for row in latest.values()),
        )
        task = item.task
        task.status = aggregate_task_status(child.status for child in task.items)

    @staticmethod
    def _record_worker_heartbeat(
        session: Session,
        *,
        worker_id: str,
        process_id: int,
        now: datetime,
    ) -> None:
        heartbeat = session.get(WorkerHeartbeatRecord, worker_id)
        if heartbeat is None:
            session.add(
                WorkerHeartbeatRecord(
                    worker_id=worker_id,
                    process_id=process_id,
                    started_at=now,
                    heartbeat_at=now,
                )
            )
            return
        heartbeat.process_id = process_id
        heartbeat.heartbeat_at = now

    @staticmethod
    def _claim_token(claim: StageClaim) -> str:
        raw = (
            f"{claim.stage_run_id}|{claim.attempt}|{claim.worker_id}|"
            f"{claim.recovery_generation}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _claim_matches(
        row: StageRunRecord | None,
        claim: StageClaim,
        now: datetime,
        *,
        statuses: frozenset[str] = frozenset({"running"}),
        require_active_containers: bool = True,
    ) -> bool:
        return bool(
            row is not None
            and row.status in statuses
            and row.attempt == claim.attempt
            and row.lease_owner == claim.worker_id
            and row.recovered_count == claim.recovery_generation
            and row.lease_expires_at is not None
            and row.lease_expires_at > now
            and (
                not require_active_containers
                or (
                    row.item.status in _ELIGIBLE_CONTAINER_STATUSES
                    and row.item.task.status in _ELIGIBLE_CONTAINER_STATUSES
                )
            )
        )

    @staticmethod
    def _source_input_ready(item: ItemRecord, stage: str) -> bool:
        """Keep upload stages unclaimable until ownership registration commits."""

        if stage != "source" or item.source_kind not in {
            "uploaded_media",
            "uploaded_subtitle",
        }:
            return True
        # Upload completion binds the verified asset locator and display name in one
        # transaction. The initial upload task intentionally has no display name.
        return item.source_display_name is not None

    def claim_next(
        self,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> StageClaim | None:
        if not worker_id or lease_duration <= timedelta(0):
            raise ValueError("worker id and positive lease duration are required")
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            tasks = session.scalars(
                select(TaskRecord)
                .options(
                    selectinload(TaskRecord.items).selectinload(ItemRecord.stage_runs)
                )
            ).all()
            candidates: list[tuple[datetime, int, str, int, StageRunRecord]] = []
            for task in tasks:
                if task.status not in _ELIGIBLE_CONTAINER_STATUSES:
                    continue
                for item in task.items:
                    if item.status not in _ELIGIBLE_CONTAINER_STATUSES:
                        continue
                    latest = self._latest_by_stage(item)
                    for stage, row in latest.items():
                        if row.status != "queued" or stage not in STAGE_ORDER:
                            continue
                        if not self._source_input_ready(item, stage):
                            continue
                        dependencies = STAGE_DEPENDENCIES[stage]
                        if any(
                            dependency not in latest
                            or latest[dependency].status
                            not in SUCCESSFUL_STAGE_STATUSES
                            for dependency in dependencies
                        ):
                            continue
                        candidates.append(
                            (
                                task.created_at,
                                STAGE_ORDER[stage],
                                item.id,
                                row.attempt,
                                row,
                            )
                        )
            if not candidates:
                session.rollback()
                return None
            row = min(candidates, key=lambda candidate: candidate[:4])[4]
            expires_at = now + lease_duration
            row.status = "running"
            row.lease_owner = worker_id
            row.lease_expires_at = expires_at
            row.heartbeat_at = now
            row.started_at = row.started_at or now
            row.finished_at = None
            row.error_code = None
            row.error_message = None
            self._record_worker_heartbeat(
                session,
                worker_id=worker_id,
                process_id=self.process_id,
                now=now,
            )
            self._recalculate_item_and_task(session, row.item)
            claim = StageClaim(
                stage_run_id=row.id,
                item_id=row.item_id,
                stage=row.stage,
                attempt=row.attempt,
                worker_id=worker_id,
                lease_expires_at=expires_at,
                recovery_generation=row.recovered_count,
                retry_override=(
                    _freeze(row.retry_override_json)
                    if row.retry_override_json is not None
                    else None
                ),
            )
            session.commit()
            return claim

    def heartbeat(self, claim: StageClaim, now: datetime) -> bool:
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            row = session.get(StageRunRecord, claim.stage_run_id)
            if not self._claim_matches(row, claim, now):
                session.rollback()
                return False
            assert row is not None
            assert row.lease_expires_at is not None
            previous_heartbeat = row.heartbeat_at or row.started_at or now
            interval = row.lease_expires_at - previous_heartbeat
            if interval <= timedelta(0):
                session.rollback()
                return False
            row.heartbeat_at = now
            row.lease_expires_at = now + interval
            self._record_worker_heartbeat(
                session,
                worker_id=claim.worker_id,
                process_id=self.process_id,
                now=now,
            )
            token = self._claim_token(claim)
            resources = session.scalars(
                select(ResourceLeaseRecord).where(
                    ResourceLeaseRecord.lease_owner == token
                )
            ).all()
            for resource in resources:
                resource_interval = resource.lease_expires_at - resource.heartbeat_at
                if resource.lease_expires_at > now and resource_interval > timedelta(0):
                    resource.heartbeat_at = now
                    resource.lease_expires_at = now + resource_interval
            session.commit()
            return True

    def acquire_resource(
        self,
        claim: StageClaim,
        resource_key: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        if not resource_key or lease_duration <= timedelta(0):
            raise ValueError("resource key and positive lease duration are required")
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            stage = session.get(StageRunRecord, claim.stage_run_id)
            if not self._claim_matches(stage, claim, now):
                session.rollback()
                return False
            resource = session.get(ResourceLeaseRecord, resource_key)
            token = self._claim_token(claim)
            if (
                resource is not None
                and resource.lease_owner != token
                and resource.lease_expires_at > now
            ):
                session.rollback()
                return False
            if resource is None:
                resource = ResourceLeaseRecord(
                    resource_key=resource_key,
                    lease_owner=token,
                    lease_expires_at=now + lease_duration,
                    heartbeat_at=now,
                )
                session.add(resource)
            else:
                resource.lease_owner = token
                resource.lease_expires_at = now + lease_duration
                resource.heartbeat_at = now
            session.commit()
            return True

    def update_progress(
        self,
        claim: StageClaim,
        progress: Mapping[str, object],
        *,
        now: datetime,
    ) -> bool:
        """Persist bounded progress only while the caller still owns the stage."""

        normalized = validate_stage_progress(progress)
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            row = session.get(StageRunRecord, claim.stage_run_id)
            if not self._claim_matches(row, claim, now):
                session.rollback()
                return False
            assert row is not None
            row.progress_json = dict(normalized)
            session.commit()
            return True

    def _release_resources(self, session: Session, claim: StageClaim) -> None:
        token = self._claim_token(claim)
        for resource in session.scalars(
            select(ResourceLeaseRecord).where(
                ResourceLeaseRecord.lease_owner == token
            )
        ).all():
            session.delete(resource)

    def complete(
        self,
        claim: StageClaim,
        result: StageResult,
        *,
        now: datetime,
    ) -> bool:
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            row = session.get(StageRunRecord, claim.stage_run_id)
            if not self._claim_matches(row, claim, now):
                session.rollback()
                return False
            assert row is not None
            row.status = result.status
            row.warning = sanitize_diagnostic(result.warning)
            if result.item_title is not None:
                row.item.title = result.item_title
            if result.execution_evidence is not None:
                row.execution_evidence_json = dict(
                    validate_execution_evidence(
                        result.execution_evidence,
                        allowed_models=self._allowed_stage_models(row),
                    )
                )
            if result.skip_stages:
                if row.stage != "source" or result.skip_stages != ("transcribe",):
                    session.rollback()
                    return False
                latest = self._latest_by_stage(row.item)
                transcribe = latest.get("transcribe")
                if transcribe is None or transcribe.status != "queued":
                    session.rollback()
                    return False
                transcribe.status = "skipped"
                transcribe.finished_at = now
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            self._release_resources(session, claim)
            self._recalculate_item_and_task(session, row.item)
            session.commit()
            return True

    def defer_external(
        self,
        claim: StageClaim,
        deferred: StageDeferred,
        *,
        now: datetime,
    ) -> bool:
        now = _utc(now)
        if not isinstance(deferred, StageDeferred):
            raise TypeError("deferred stage result is required")
        with Session(self.engine) as session:
            self._begin_immediate(session)
            row = session.get(StageRunRecord, claim.stage_run_id)
            if not self._claim_matches(row, claim, now):
                session.rollback()
                return False
            assert row is not None
            row.status = "waiting_external"
            row.warning = deferred.warning
            row.execution_evidence_json = dict(
                validate_execution_evidence(
                    deferred.execution_evidence,
                    allowed_models=self._allowed_stage_models(row),
                )
            )
            row.external_submission_state = deferred.external_submission_state
            row.progress_json = {
                "current": None,
                "total": None,
                "unit": None,
                "message_code": "waiting_cloud_asr",
            }
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            self._release_resources(session, claim)
            self._recalculate_item_and_task(session, row.item)
            session.commit()
            return True

    def requeue(self, claim: StageClaim, *, now: datetime) -> bool:
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            row = session.get(StageRunRecord, claim.stage_run_id)
            if not self._claim_matches(row, claim, now):
                session.rollback()
                return False
            assert row is not None
            row.status = "queued"
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            self._release_resources(session, claim)
            self._recalculate_item_and_task(session, row.item)
            session.commit()
            return True

    def fail(
        self,
        claim: StageClaim,
        failure: StageFailure,
        *,
        now: datetime,
    ) -> bool:
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            row = session.get(StageRunRecord, claim.stage_run_id)
            if not self._claim_matches(row, claim, now):
                session.rollback()
                return False
            assert row is not None
            row.status = "failed"
            row.error_code = failure.error_code
            row.error_message = sanitize_diagnostic(failure.error_message)
            row.external_submission_state = failure.external_submission_state
            row.warning = sanitize_diagnostic(failure.warning)
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            self._release_resources(session, claim)
            self._recalculate_item_and_task(session, row.item)
            session.commit()
            return True

    def cancel_if_requested(self, claim: StageClaim, now: datetime) -> bool:
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            row = session.get(StageRunRecord, claim.stage_run_id)
            if not self._claim_matches(
                row,
                claim,
                now,
                statuses=frozenset({"running", "cancel_requested"}),
                require_active_containers=False,
            ):
                session.rollback()
                return False
            assert row is not None
            if not (
                row.status == "cancel_requested"
                or row.item.status in {"cancel_requested", "canceled"}
                or row.item.task.status in {"cancel_requested", "canceled"}
            ):
                session.rollback()
                return False
            row.status = "canceled"
            row.finished_at = now
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            self._release_resources(session, claim)
            self._recalculate_item_and_task(session, row.item)
            session.commit()
            return True

    def recover_expired(self, now: datetime) -> tuple[str, ...]:
        now = _utc(now)
        with Session(self.engine) as session:
            self._begin_immediate(session)
            rows = session.scalars(
                select(StageRunRecord)
                .options(
                    selectinload(StageRunRecord.item).selectinload(ItemRecord.task)
                )
                .where(
                    StageRunRecord.status.in_(("running", "cancel_requested")),
                    StageRunRecord.lease_expires_at.is_not(None),
                    StageRunRecord.lease_expires_at <= now,
                )
                .order_by(StageRunRecord.id)
            ).all()
            recovered: list[str] = []
            affected_items: dict[str, ItemRecord] = {}
            for row in rows:
                old_claim = StageClaim(
                    stage_run_id=row.id,
                    item_id=row.item_id,
                    stage=row.stage,
                    attempt=row.attempt,
                    worker_id=row.lease_owner or "",
                    lease_expires_at=row.lease_expires_at or now,
                    recovery_generation=row.recovered_count,
                )
                canceled = (
                    row.status == "cancel_requested"
                    or row.item.status in {"cancel_requested", "canceled"}
                    or row.item.task.status in {"cancel_requested", "canceled"}
                )
                row.status = "canceled" if canceled else "queued"
                row.finished_at = now if canceled else None
                row.lease_owner = None
                row.lease_expires_at = None
                row.heartbeat_at = None
                row.recovered_count += 1
                self._release_resources(session, old_claim)
                recovered.append(row.id)
                affected_items[row.item_id] = row.item
            for item in affected_items.values():
                self._recalculate_item_and_task(session, item)
            if recovered:
                session.commit()
            else:
                session.rollback()
            return tuple(recovered)
