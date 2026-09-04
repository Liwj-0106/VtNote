"""Atomic task deletion and app-owned artifact cleanup."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from vtnote.application.task_contracts import MAX_BATCH_SOURCES, TaskDeletionError
from vtnote.models import (
    CloudSubmissionRecord,
    ItemRecord,
    RuntimeAssetRecord,
    StageRunRecord,
    TaskRecord,
)
from vtnote.paths import StoragePaths
from vtnote.pipeline import TERMINAL_STATUSES

_REMOTE_DELETE_BLOCKING_STATES = frozenset(
    {"sending", "submitted", "submission_unknown"}
)
_DELETE_LOGGER = logging.getLogger("vtnote.task_deletion")


class TaskDeletionService:
    def __init__(self, session: Session, paths: StoragePaths) -> None:
        self.session = session
        self.paths = paths

    @staticmethod
    def _canonical_delete_ids(task_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        if not 1 <= len(task_ids) <= MAX_BATCH_SOURCES:
            raise TaskDeletionError("invalid_task_count")
        canonical: list[str] = []
        for task_id in task_ids:
            try:
                canonical.append(str(UUID(str(task_id))))
            except (ValueError, AttributeError) as error:
                raise TaskDeletionError("invalid_task_id") from error
        if len(set(canonical)) != len(canonical):
            raise TaskDeletionError("duplicate_task_ids")
        return tuple(canonical)

    def _reserve_delete_transaction(self) -> None:
        if self.session.new or self.session.dirty or self.session.deleted:
            raise TaskDeletionError("task_delete_pending_changes")
        if self.session.in_transaction():
            self.session.rollback()
        try:
            self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as error:
            self.session.rollback()
            raise TaskDeletionError("task_delete_database_busy") from error
        self.session.expire_all()

    def _stage_delete_path(
        self,
        source: Path,
        destination: Path,
        staged: list[tuple[Path, Path]],
    ) -> None:
        if not source.exists():
            return
        self.paths.assert_runtime_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.paths.assert_runtime_destination(destination.parent)
        if destination.exists():
            raise TaskDeletionError("task_delete_staging_conflict")
        try:
            if os.stat(source).st_dev != os.stat(destination.parent).st_dev:
                raise TaskDeletionError("task_delete_cross_device")
            os.replace(source, destination)
        except TaskDeletionError:
            raise
        except OSError as error:
            raise TaskDeletionError("task_delete_filesystem_error") from error
        staged.append((source, destination))

    @staticmethod
    def _restore_delete_stage(staged: list[tuple[Path, Path]]) -> bool:
        restored = True
        for source, destination in reversed(staged):
            if not destination.exists():
                continue
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                if source.exists():
                    restored = False
                    continue
                os.replace(destination, source)
            except OSError:
                restored = False
        return restored

    def _discard_delete_stage(self, staging_root: Path) -> None:
        if not staging_root.exists():
            return
        try:
            self.paths.assert_runtime_destination(staging_root)
            shutil.rmtree(staging_root)
            parent = staging_root.parent
            if parent.exists():
                parent.rmdir()
        except OSError:
            _DELETE_LOGGER.warning(
                "Task deletion completed but its internal cache staging needs cleanup"
            )

    def delete_tasks(self, task_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        """Atomically remove terminal tasks and their app-owned local artifacts."""

        canonical_ids = self._canonical_delete_ids(task_ids)
        staging_root = self.paths.task_deletion_staging(uuid4())
        staged: list[tuple[Path, Path]] = []
        self._reserve_delete_transaction()
        try:
            tasks = list(
                self.session.scalars(
                    select(TaskRecord)
                    .where(TaskRecord.id.in_(canonical_ids))
                    .options(
                        selectinload(TaskRecord.items).selectinload(
                            ItemRecord.stage_runs
                        )
                    )
                ).all()
            )
            tasks_by_id = {task.id: task for task in tasks}
            if set(tasks_by_id) != set(canonical_ids):
                missing_id = next(
                    task_id for task_id in canonical_ids if task_id not in tasks_by_id
                )
                raise KeyError(missing_id)
            if any(task.status not in TERMINAL_STATUSES for task in tasks):
                raise TaskDeletionError("task_not_terminal")
            if any(
                run.lease_owner is not None
                for task in tasks
                for item in task.items
                for run in item.stage_runs
            ):
                raise TaskDeletionError("task_delete_active_lease")

            pending_remote = self.session.scalar(
                select(CloudSubmissionRecord.id)
                .join(
                    StageRunRecord,
                    StageRunRecord.id == CloudSubmissionRecord.stage_run_id,
                )
                .join(ItemRecord, ItemRecord.id == StageRunRecord.item_id)
                .where(
                    ItemRecord.task_id.in_(canonical_ids),
                    or_(
                        CloudSubmissionRecord.cos_object_key.is_not(None),
                        CloudSubmissionRecord.state.in_(
                            _REMOTE_DELETE_BLOCKING_STATES
                        ),
                    ),
                )
                .limit(1)
            )
            if pending_remote is not None:
                raise TaskDeletionError("task_remote_cleanup_pending")

            item_ids = tuple(item.id for task in tasks for item in task.items)
            assets = list(
                self.session.scalars(
                    select(RuntimeAssetRecord).where(
                        RuntimeAssetRecord.item_id.in_(item_ids)
                    )
                ).all()
            )

            runtime_roots = {
                item_id: self.paths.runtime_item_root(item_id)
                for item_id in item_ids
            }
            for asset in assets:
                current = self.paths.runtime_from_relative(asset.relative_path)
                if asset.state == "active":
                    if asset.relative_path != asset.original_relative_path or not current.is_relative_to(
                        runtime_roots[asset.item_id]
                    ):
                        raise TaskDeletionError("task_delete_asset_state_invalid")
                elif asset.state == "trash":
                    extension = PurePosixPath(
                        asset.original_relative_path
                    ).suffix.removeprefix(".")
                    if current != self.paths.trash_asset(asset.id, extension):
                        raise TaskDeletionError("task_delete_asset_state_invalid")
                else:
                    raise TaskDeletionError("task_delete_asset_state_invalid")

            for item_id in item_ids:
                durable_root = self.paths.assert_durable_destination(
                    self.paths.durable_item_root(item_id)
                )
                runtime_root = self.paths.assert_runtime_destination(
                    runtime_roots[item_id]
                )
                self._stage_delete_path(
                    durable_root,
                    staging_root / "durable" / item_id,
                    staged,
                )
                self._stage_delete_path(
                    runtime_root,
                    staging_root / "runtime" / item_id,
                    staged,
                )

            for asset in assets:
                if asset.state != "trash":
                    continue
                current = self.paths.runtime_from_relative(asset.relative_path)
                trash_root = self.paths.assert_runtime_destination(current.parent)
                self._stage_delete_path(
                    trash_root,
                    staging_root / "trash" / asset.id,
                    staged,
                )

            if item_ids:
                self.session.execute(
                    delete(RuntimeAssetRecord).where(
                        RuntimeAssetRecord.item_id.in_(item_ids)
                    )
                )
            self.session.execute(
                delete(TaskRecord).where(TaskRecord.id.in_(canonical_ids))
            )
            self.session.commit()
        except Exception:
            if self.session.in_transaction():
                self.session.rollback()
            if not self._restore_delete_stage(staged):
                _DELETE_LOGGER.error("Task deletion rollback could not restore all files")
                raise TaskDeletionError("task_delete_recovery_failed") from None
            self._discard_delete_stage(staging_root)
            raise

        self._discard_delete_stage(staging_root)
        return canonical_ids
