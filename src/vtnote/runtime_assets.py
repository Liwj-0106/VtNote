"""Lifecycle authority for app-owned disposable runtime media."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from vtnote.models import (
    ItemRecord,
    RuntimeAssetRecord,
    RuntimeCleanupEventRecord,
)
from vtnote.paths import StoragePaths, UnsafePathError


TRASH_RETENTION = timedelta(hours=24)
RUNTIME_ASSET_ROLES = frozenset(
    {
        "uploaded_source",
        "downloaded_audio",
        "cloud_audio",
        "local_audio",
        "failed_media",
    }
)
PURGE_BLOCKING_STATUSES = frozenset({"queued", "running", "cancel_requested"})
_RECOVERABLE_AUDIO_EXTENSIONS = (
    "wav",
    "mp3",
    "m4a",
    "flac",
    "ogg",
    "opus",
    "webm",
)


class RuntimeAssetError(ValueError):
    """A runtime-media failure identified only by a safe machine code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"runtime asset operation failed: {code}")


@dataclass(frozen=True, slots=True)
class RuntimeAssetView:
    id: str
    item_id: str
    role: str
    relative_path: str
    state: str
    size_bytes: int
    sha256: str
    purge_after: datetime | None


def _canonical_uuid(value: str, *, code: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError) as error:
        raise RuntimeAssetError(code) from error


def _measure(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


class RuntimeAssetService:
    """Register, resolve, trash, restore, and purge only managed cache files."""

    def __init__(self, session: Session, paths: StoragePaths) -> None:
        self.session = session
        self.paths = paths

    @staticmethod
    def _view(row: RuntimeAssetRecord) -> RuntimeAssetView:
        return RuntimeAssetView(
            id=row.id,
            item_id=row.item_id,
            role=row.role,
            relative_path=row.relative_path,
            state=row.state,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            purge_after=row.purge_after,
        )

    def _load(self, asset_id: str) -> RuntimeAssetRecord:
        canonical = _canonical_uuid(asset_id, code="invalid_asset_id")
        row = self.session.get(RuntimeAssetRecord, canonical)
        if row is None:
            raise RuntimeAssetError("asset_not_found")
        return row

    def _role_path(self, item_id: str, role: str, relative_path: str) -> Path:
        if role not in RUNTIME_ASSET_ROLES:
            raise RuntimeAssetError("invalid_role")
        try:
            candidate = self.paths.runtime_from_relative(relative_path)
        except UnsafePathError as error:
            raise RuntimeAssetError("invalid_relative_path") from error
        extension = PurePosixPath(relative_path).suffix.removeprefix(".")
        try:
            if role == "uploaded_source":
                expected = self.paths.uploaded_source(item_id, extension)
            elif role == "downloaded_audio":
                expected = self.paths.downloaded_audio(item_id, extension)
            elif role == "cloud_audio":
                expected = self.paths.cloud_ogg(item_id)
            elif role == "local_audio":
                expected = self.paths.local_prepared_audio(item_id)
            else:
                relative = PurePosixPath(relative_path)
                if len(relative.parts) != 5 or relative.parts[:4] != (
                    "items",
                    item_id,
                    "audio",
                    "staging",
                ):
                    raise RuntimeAssetError("role_path_mismatch")
                expected = self.paths.conversion_staging(
                    item_id,
                    relative.stem,
                    extension,
                )
        except UnsafePathError as error:
            raise RuntimeAssetError("role_path_mismatch") from error
        if candidate != expected:
            raise RuntimeAssetError("role_path_mismatch")
        return candidate

    def _active_path(self, row: RuntimeAssetRecord) -> Path:
        return self._role_path(row.item_id, row.role, row.original_relative_path)

    def _trash_path(self, row: RuntimeAssetRecord) -> Path:
        extension = PurePosixPath(row.original_relative_path).suffix.removeprefix(".")
        try:
            return self.paths.trash_asset(row.id, extension)
        except UnsafePathError as error:
            raise RuntimeAssetError("invalid_relative_path") from error

    @staticmethod
    def _event(
        asset_id: str, action: str, outcome: str, code: str
    ) -> RuntimeCleanupEventRecord:
        return RuntimeCleanupEventRecord(
            asset_id=asset_id,
            action=action,
            outcome=outcome,
            code=code,
        )

    def _fail(self, row: RuntimeAssetRecord, action: str, code: str) -> None:
        self.session.add(self._event(row.id, action, "failed", code))
        self.session.commit()
        raise RuntimeAssetError(code)

    def _verify(self, row: RuntimeAssetRecord, path: Path) -> None:
        self.paths.assert_runtime_destination(path)
        if not path.is_file():
            raise RuntimeAssetError("file_missing")
        size, digest = _measure(path)
        if size != row.size_bytes or digest != row.sha256:
            raise RuntimeAssetError("integrity_mismatch")

    def _move(self, source: Path, destination: Path) -> None:
        try:
            self.paths.assert_runtime_destination(source)
            self.paths.assert_runtime_destination(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.paths.assert_runtime_destination(destination)
            if os.stat(source).st_dev != os.stat(destination.parent).st_dev:
                raise RuntimeAssetError("cross_device_move")
            os.replace(source, destination)
        except RuntimeAssetError:
            raise
        except OSError as error:
            raise RuntimeAssetError("filesystem_error") from error

    def _reserve_purge_transaction(self) -> None:
        if self.session.new or self.session.dirty or self.session.deleted:
            raise RuntimeAssetError("pending_session_changes")
        if self.session.in_transaction():
            self.session.rollback()
        connection = self.session.connection()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as error:
            self.session.rollback()
            raise RuntimeAssetError("database_busy") from error
        self.session.expire_all()

    def register_staged(
        self, *, item_id: str, role: str, relative_path: str
    ) -> RuntimeAssetView:
        canonical_item_id = _canonical_uuid(item_id, code="invalid_item_id")
        if self.session.get(ItemRecord, canonical_item_id) is None:
            raise RuntimeAssetError("item_not_found")
        path = self._role_path(canonical_item_id, role, relative_path)
        if not path.is_file():
            raise RuntimeAssetError("file_missing")
        size, digest = _measure(path)
        if size == 0:
            raise RuntimeAssetError("empty_file")

        existing = self.session.scalar(
            select(RuntimeAssetRecord).where(
                or_(
                    RuntimeAssetRecord.relative_path == relative_path,
                    RuntimeAssetRecord.original_relative_path == relative_path,
                )
            )
        )
        if existing is not None:
            if (
                existing.item_id != canonical_item_id
                or existing.role != role
                or existing.state != "active"
                or existing.relative_path != relative_path
                or existing.original_relative_path != relative_path
            ):
                raise RuntimeAssetError("path_conflict")
            self._verify(existing, path)
            return self._view(existing)

        row = RuntimeAssetRecord(
            item_id=canonical_item_id,
            role=role,
            relative_path=relative_path,
            original_relative_path=relative_path,
            state="active",
            size_bytes=size,
            sha256=digest,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            recovered = self.session.scalar(
                select(RuntimeAssetRecord).where(
                    or_(
                        RuntimeAssetRecord.relative_path == relative_path,
                        RuntimeAssetRecord.original_relative_path == relative_path,
                    )
                )
            )
            if recovered is None:
                raise RuntimeAssetError("registration_conflict") from None
            if (
                recovered.item_id != canonical_item_id
                or recovered.role != role
                or recovered.state != "active"
                or recovered.size_bytes != size
                or recovered.sha256 != digest
            ):
                raise RuntimeAssetError("path_conflict") from None
            row = recovered
        return self._view(row)

    def active_for_role(self, *, item_id: str, role: str) -> RuntimeAssetView | None:
        """Return the verified active asset for one typed item role, if present."""

        canonical_item_id = _canonical_uuid(item_id, code="invalid_item_id")
        if role not in RUNTIME_ASSET_ROLES:
            raise RuntimeAssetError("invalid_role")
        rows = self.session.scalars(
            select(RuntimeAssetRecord).where(
                RuntimeAssetRecord.item_id == canonical_item_id,
                RuntimeAssetRecord.role == role,
                RuntimeAssetRecord.state == "active",
            )
        ).all()
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeAssetError("ambiguous_role_assets")
        self.resolve(rows[0].id)
        return self._view(rows[0])

    def recover_downloaded_audio(
        self,
        *,
        item_id: str,
    ) -> RuntimeAssetView | None:
        """Recover exactly one canonical audio handoff after an interrupted stage."""

        active = self.active_for_role(item_id=item_id, role="downloaded_audio")
        if active is not None:
            return active
        restored = self.restore_trashed_for_role(
            item_id=item_id,
            role="downloaded_audio",
        )
        if restored is not None:
            return restored
        candidates = tuple(
            path
            for extension in _RECOVERABLE_AUDIO_EXTENSIONS
            if (path := self.paths.downloaded_audio(item_id, extension)).is_file()
        )
        if not candidates:
            return None
        if len(candidates) != 1:
            raise RuntimeAssetError("ambiguous_downloaded_audio")
        return self.register_staged(
            item_id=item_id,
            role="downloaded_audio",
            relative_path=self.paths.runtime_relative(candidates[0]),
        )

    def discard_empty_conversion_staging(
        self, *, item_id: str, staging_id: str, extension: str
    ) -> None:
        """Remove only one exact typed zero-byte FFmpeg staging file and audit it."""

        canonical_item_id = _canonical_uuid(item_id, code="invalid_item_id")
        canonical_staging_id = _canonical_uuid(
            staging_id, code="invalid_staging_id"
        )
        if self.session.get(ItemRecord, canonical_item_id) is None:
            raise RuntimeAssetError("item_not_found")
        try:
            staging = self.paths.conversion_staging(
                canonical_item_id, canonical_staging_id, extension
            )
            self.paths.assert_runtime_destination(staging)
            if staging.exists():
                if not staging.is_file() or staging.stat().st_size != 0:
                    raise RuntimeAssetError("staging_not_empty")
                staging.unlink()
            event = self._event(
                canonical_staging_id,
                "discard",
                "succeeded",
                "zero_byte_media_staging",
            )
        except RuntimeAssetError as error:
            event = self._event(
                canonical_staging_id,
                "discard",
                "failed",
                "zero_byte_media_discard_failed",
            )
            self.session.add(event)
            self.session.commit()
            raise error
        except (OSError, UnsafePathError):
            self.session.add(
                self._event(
                    canonical_staging_id,
                    "discard",
                    "failed",
                    "zero_byte_media_discard_failed",
                )
            )
            self.session.commit()
            raise RuntimeAssetError("filesystem_error") from None
        self.session.add(event)
        self.session.commit()

    def restore_trashed_for_role(
        self, *, item_id: str, role: str
    ) -> RuntimeAssetView | None:
        """Restore the one retained typed asset for a role, if present."""

        canonical_item_id = _canonical_uuid(item_id, code="invalid_item_id")
        if role not in RUNTIME_ASSET_ROLES:
            raise RuntimeAssetError("invalid_role")
        rows = self.session.scalars(
            select(RuntimeAssetRecord).where(
                RuntimeAssetRecord.item_id == canonical_item_id,
                RuntimeAssetRecord.role == role,
                RuntimeAssetRecord.state == "trash",
            )
        ).all()
        if not rows:
            return None
        if len(rows) != 1:
            raise RuntimeAssetError("ambiguous_role_assets")
        return self.restore(rows[0].id)

    def resolve(self, asset_id: str) -> Path:
        row = self._load(asset_id)
        active_path = self._active_path(row)
        trash_path = self._trash_path(row)
        if row.state == "active" and row.relative_path != row.original_relative_path:
            raise RuntimeAssetError("invalid_asset_state")
        if row.state == "trash" and row.relative_path != self.paths.runtime_relative(trash_path):
            raise RuntimeAssetError("invalid_asset_state")
        if row.state == "active":
            selected = active_path
        elif row.state == "trash":
            selected = trash_path
        else:
            raise RuntimeAssetError("invalid_asset_state")
        self._verify(row, selected)
        return selected

    def get(self, asset_id: str) -> RuntimeAssetView:
        """Return one verified asset without exposing its ORM record."""

        row = self._load(asset_id)
        self.resolve(row.id)
        return self._view(row)

    def list_trashed(self) -> tuple[RuntimeAssetView, ...]:
        """Return verified trash entries without permitting arbitrary path reads."""

        rows = self.session.scalars(
            select(RuntimeAssetRecord)
            .where(RuntimeAssetRecord.state == "trash")
            .order_by(
                RuntimeAssetRecord.purge_after.asc(),
                RuntimeAssetRecord.id.asc(),
            )
        ).all()
        views: list[RuntimeAssetView] = []
        for row in rows:
            self.resolve(row.id)
            views.append(self._view(row))
        return tuple(views)

    def storage_summary(self) -> dict[str, dict[str, int]]:
        """Return aggregate managed-cache counts and sizes by lifecycle state."""

        rows = self.session.execute(
            select(
                RuntimeAssetRecord.state,
                func.count(RuntimeAssetRecord.id),
                func.coalesce(func.sum(RuntimeAssetRecord.size_bytes), 0),
            ).group_by(RuntimeAssetRecord.state)
        ).all()
        totals = {
            "active": {"count": 0, "size_bytes": 0},
            "trash": {"count": 0, "size_bytes": 0},
        }
        for state, count, size_bytes in rows:
            if state in totals:
                totals[state] = {
                    "count": int(count),
                    "size_bytes": int(size_bytes),
                }
        return totals

    def trash(self, asset_id: str, *, now: datetime | None = None) -> RuntimeAssetView:
        row = self._load(asset_id)
        timestamp = now or datetime.now(timezone.utc)
        try:
            active_path = self._active_path(row)
            trash_path = self._trash_path(row)
        except RuntimeAssetError as error:
            self._fail(row, "trash", error.code)
        active_exists = active_path.is_file()
        trash_exists = trash_path.is_file()
        if active_exists and trash_exists:
            self._fail(row, "trash", "ambiguous_copies")
        if not active_exists and not trash_exists:
            self._fail(row, "trash", "file_missing")

        if trash_exists:
            try:
                self._verify(row, trash_path)
            except RuntimeAssetError as error:
                self._fail(row, "trash", error.code)
            code = "already_trashed" if row.state == "trash" else "recovered_move"
        else:
            try:
                self._verify(row, active_path)
                self._move(active_path, trash_path)
            except RuntimeAssetError as error:
                self._fail(row, "trash", error.code)
            code = "moved"

        row.state = "trash"
        row.relative_path = self.paths.runtime_relative(trash_path)
        if row.purge_after is None:
            row.purge_after = timestamp + TRASH_RETENTION
        self.session.add(self._event(row.id, "trash", "succeeded", code))
        self.session.commit()
        return self._view(row)

    def restore(self, asset_id: str) -> RuntimeAssetView:
        row = self._load(asset_id)
        try:
            active_path = self._active_path(row)
            trash_path = self._trash_path(row)
        except RuntimeAssetError as error:
            self._fail(row, "restore", error.code)
        active_exists = active_path.is_file()
        trash_exists = trash_path.is_file()
        if active_exists and trash_exists:
            self._fail(row, "restore", "ambiguous_copies")
        if not active_exists and not trash_exists:
            self._fail(row, "restore", "file_missing")

        if active_exists:
            try:
                self._verify(row, active_path)
            except RuntimeAssetError as error:
                self._fail(row, "restore", error.code)
            code = "already_active" if row.state == "active" else "recovered_move"
        else:
            try:
                self._verify(row, trash_path)
                self._move(trash_path, active_path)
            except RuntimeAssetError as error:
                self._fail(row, "restore", error.code)
            code = "moved"

        row.state = "active"
        row.relative_path = row.original_relative_path
        row.purge_after = None
        self.session.add(self._event(row.id, "restore", "succeeded", code))
        self.session.commit()
        return self._view(row)

    def purge(self, asset_id: str, *, now: datetime | None = None) -> bool:
        _canonical_uuid(asset_id, code="invalid_asset_id")
        self._reserve_purge_transaction()
        try:
            return self._purge_reserved(asset_id, now=now)
        except Exception:
            if self.session.in_transaction():
                self.session.rollback()
            raise

    def _purge_reserved(self, asset_id: str, *, now: datetime | None = None) -> bool:
        row = self._load(asset_id)
        timestamp = now or datetime.now(timezone.utc)
        if row.state != "trash":
            self._fail(row, "purge", "not_trashed")
        if row.purge_after is None or row.purge_after > timestamp:
            self._fail(row, "purge", "not_due")
        if (
            row.item.status in PURGE_BLOCKING_STATUSES
            or row.item.task.status in PURGE_BLOCKING_STATUSES
        ):
            self._fail(row, "purge", "active_work")

        try:
            active_path = self._active_path(row)
            trash_path = self._trash_path(row)
        except RuntimeAssetError as error:
            self._fail(row, "purge", error.code)
        if active_path.exists():
            self._fail(row, "purge", "active_copy_present")
        code = "already_missing"
        if trash_path.exists():
            try:
                self._verify(row, trash_path)
                self.paths.assert_runtime_destination(trash_path)
                trash_path.unlink()
            except RuntimeAssetError as error:
                self._fail(row, "purge", error.code)
            except OSError:
                self._fail(row, "purge", "filesystem_error")
            code = "purged"

        self.session.add(self._event(row.id, "purge", "succeeded", code))
        self.session.delete(row)
        self.session.commit()
        return True

    def purge_due(self, *, now: datetime | None = None) -> tuple[str, ...]:
        timestamp = now or datetime.now(timezone.utc)
        due_ids = tuple(
            self.session.scalars(
                select(RuntimeAssetRecord.id).where(
                    RuntimeAssetRecord.state == "trash",
                    RuntimeAssetRecord.purge_after <= timestamp,
                )
            ).all()
        )
        purged: list[str] = []
        for asset_id in due_ids:
            try:
                self.purge(asset_id, now=timestamp)
            except RuntimeAssetError:
                continue
            purged.append(asset_id)
        return tuple(purged)
