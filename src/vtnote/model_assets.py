"""Explicit, durable installation state for the pinned local Whisper model."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from vtnote.models import ModelInstallRecord
from vtnote.paths import StoragePaths


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_FILES = frozenset(
    {
        "config.json",
        "model.bin",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    }
)


class ModelAssetError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelManifest:
    schema_version: int
    model_name: str
    repo_id: str
    revision: str
    files: tuple[ModelFile, ...]
    manifest_sha256: str

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


@dataclass(frozen=True, slots=True)
class ModelInstallStatus:
    model_name: str
    revision: str
    state: str
    total_bytes: int
    downloaded_bytes: int
    completed_files: int
    current_file: str | None
    current_file_bytes: int
    cancel_requested: bool
    error_code: str | None
    staging_path: Path | None
    trash_path: Path | None
    installed_path: Path | None


def load_local_whisper_manifest(path: Path) -> ModelManifest:
    candidate = Path(path)
    try:
        raw_bytes = candidate.read_bytes()
        payload = json.loads(raw_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ModelAssetError("model_manifest_invalid") from None
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {"schema_version", "model_name", "repo_id", "revision", "files"}
        or payload.get("schema_version") != 1
        or payload.get("model_name") != "large-v3-turbo"
        or payload.get("repo_id")
        != "dropbox-dash/faster-whisper-large-v3-turbo"
        or not isinstance(payload.get("revision"), str)
        or _REVISION.fullmatch(payload["revision"]) is None
        or not isinstance(payload.get("files"), list)
    ):
        raise ModelAssetError("model_manifest_invalid")
    files: list[ModelFile] = []
    for value in payload["files"]:
        if (
            not isinstance(value, dict)
            or set(value) != {"path", "size", "sha256"}
            or value.get("path") not in _ALLOWED_FILES
            or isinstance(value.get("size"), bool)
            or not isinstance(value.get("size"), int)
            or value["size"] <= 0
            or not isinstance(value.get("sha256"), str)
            or _SHA256.fullmatch(value["sha256"]) is None
        ):
            raise ModelAssetError("model_manifest_invalid")
        files.append(ModelFile(**value))
    if (
        {item.path for item in files} != _ALLOWED_FILES
        or len(files) != len(_ALLOWED_FILES)
    ):
        raise ModelAssetError("model_manifest_invalid")
    return ModelManifest(
        schema_version=1,
        model_name=payload["model_name"],
        repo_id=payload["repo_id"],
        revision=payload["revision"],
        files=tuple(files),
        manifest_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


class ModelAssetService:
    def __init__(
        self,
        *,
        engine: Engine,
        paths: StoragePaths,
        manifest_path: Path,
        free_bytes: Callable[[Path], int] | None = None,
    ) -> None:
        self.engine = engine
        self.paths = paths
        self.manifest = load_local_whisper_manifest(manifest_path)
        self.free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)

    @property
    def staging_root(self) -> Path:
        return self.paths.runtime(
            "model-installs",
            f"{self.manifest.revision}.staging",
        )

    @property
    def install_root(self) -> Path:
        return self.paths.durable(
            "models",
            self.manifest.model_name,
            self.manifest.revision,
        )

    def _runtime_path(self, relative: str | None) -> Path | None:
        return (
            None
            if relative is None
            else self.paths.runtime_from_relative(relative)
        )

    def _installed_path(self, relative: str | None) -> Path | None:
        if relative is None:
            return None
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(
            part in {"", ".", ".."} for part in pure.parts
        ):
            raise ModelAssetError("model_install_state_invalid")
        return self.paths.assert_durable_destination(
            self.paths.durable(*pure.parts)
        )

    def _status(self, row: ModelInstallRecord | None) -> ModelInstallStatus:
        if row is None:
            return ModelInstallStatus(
                self.manifest.model_name,
                self.manifest.revision,
                "not_installed",
                self.manifest.total_bytes,
                0,
                0,
                None,
                0,
                False,
                None,
                None,
                None,
                None,
            )
        return ModelInstallStatus(
            model_name=row.model_name,
            revision=row.revision,
            state=row.state,
            total_bytes=row.total_bytes,
            downloaded_bytes=row.downloaded_bytes,
            completed_files=row.completed_files,
            current_file=row.current_file,
            current_file_bytes=row.current_file_bytes,
            cancel_requested=row.cancel_requested,
            error_code=row.error_code,
            staging_path=self._runtime_path(row.staging_relpath),
            trash_path=self._runtime_path(row.trash_relpath),
            installed_path=self._installed_path(row.installed_relpath),
        )

    def status(self) -> ModelInstallStatus:
        with Session(self.engine) as session:
            return self._status(
                session.get(ModelInstallRecord, self.manifest.model_name)
            )

    def request_install(
        self,
        *,
        acknowledge_download: bool,
        expected_revision: str,
        now: datetime,
    ) -> ModelInstallStatus:
        if acknowledge_download is not True:
            raise ModelAssetError("download_ack_required")
        if expected_revision != self.manifest.revision:
            raise ModelAssetError("model_revision_mismatch")
        self.paths.ensure_roots()
        required = self.manifest.total_bytes + 256 * 1024 * 1024
        if self.free_bytes(self.paths.runtime_cache_root) < required:
            raise ModelAssetError("insufficient_space")
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if row is None:
                row = ModelInstallRecord(
                    model_name=self.manifest.model_name,
                    revision=self.manifest.revision,
                    manifest_sha256=self.manifest.manifest_sha256,
                    state="queued",
                    total_bytes=self.manifest.total_bytes,
                    downloaded_bytes=0,
                    completed_files=0,
                    current_file_bytes=0,
                    staging_relpath=self.paths.runtime_relative(
                        self.staging_root
                    ),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            elif row.state != "installed":
                row.state = "queued"
                row.cancel_requested = False
                row.error_code = None
            session.commit()
            return self._status(row)

    def download_url(self, file_path: str) -> str:
        if file_path not in {item.path for item in self.manifest.files}:
            raise ModelAssetError("model_file_not_allowed")
        return (
            f"https://huggingface.co/{self.manifest.repo_id}/resolve/"
            f"{self.manifest.revision}/{file_path}"
        )

    def resume_offset(
        self,
        file_path: str,
        *,
        local_size: int,
        stored_etag: str | None,
        response_etag: str | None,
        response_total_size: int,
    ) -> int:
        expected = next(
            (item for item in self.manifest.files if item.path == file_path),
            None,
        )
        if expected is None:
            raise ModelAssetError("model_file_not_allowed")
        if (
            local_size < 0
            or local_size > expected.size
            or response_total_size != expected.size
            or not stored_etag
            or stored_etag != response_etag
        ):
            return 0
        return local_size

    @staticmethod
    def _hash(path: Path) -> tuple[int, str]:
        size = 0
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def publish_verified(self, *, now: datetime) -> ModelInstallStatus:
        for item in self.manifest.files:
            path = self.staging_root / item.path
            if not path.is_file() or self._hash(path) != (
                item.size,
                item.sha256,
            ):
                raise ModelAssetError("model_hash_mismatch")
        self.install_root.parent.mkdir(parents=True, exist_ok=True)
        self.paths.assert_durable_destination(self.install_root.parent)
        if self.install_root.exists():
            raise ModelAssetError("model_publish_conflict")
        os.replace(self.staging_root, self.install_root)
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if row is None:
                raise ModelAssetError("model_install_not_requested")
            row.state = "installed"
            row.downloaded_bytes = row.total_bytes
            row.completed_files = len(self.manifest.files)
            row.current_file = None
            row.current_file_bytes = 0
            row.current_etag = None
            row.staging_relpath = None
            row.installed_relpath = self.install_root.relative_to(
                self.paths.data_root
            ).as_posix()
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.updated_at = now
            session.commit()
            return self._status(row)

    def cancel(self, *, now: datetime) -> ModelInstallStatus:
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if row is None or row.state == "installed":
                raise ModelAssetError("model_install_not_active")
            trash = self.paths.runtime(
                "trash",
                "model-installs",
                str(uuid4()),
            )
            if self.staging_root.exists():
                trash.parent.mkdir(parents=True, exist_ok=True)
                self.paths.assert_runtime_destination(trash.parent)
                os.replace(self.staging_root, trash)
                row.trash_relpath = self.paths.runtime_relative(trash)
            row.state = "canceled"
            row.cancel_requested = True
            row.staging_relpath = None
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.updated_at = now
            session.commit()
            return self._status(row)

    def claim(
        self,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> ModelInstallStatus | None:
        if not worker_id or lease_duration <= timedelta(0):
            raise ValueError("invalid model install lease")
        with Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if (
                row is None
                or row.state not in {"queued", "downloading"}
                or row.cancel_requested
                or (
                    row.lease_expires_at is not None
                    and row.lease_expires_at > now
                )
            ):
                session.rollback()
                return None
            row.state = "downloading"
            row.lease_owner = worker_id
            row.lease_expires_at = now + lease_duration
            row.heartbeat_at = now
            session.commit()
            return self._status(row)

    def record_progress(
        self,
        *,
        worker_id: str,
        file_path: str,
        file_bytes: int,
        etag: str,
        now: datetime,
    ) -> ModelInstallStatus:
        expected = next(
            (item for item in self.manifest.files if item.path == file_path),
            None,
        )
        if (
            expected is None
            or file_bytes < 0
            or file_bytes > expected.size
            or not etag
        ):
            raise ModelAssetError("model_progress_invalid")
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if (
                row is None
                or row.state != "downloading"
                or row.lease_owner != worker_id
                or row.lease_expires_at is None
                or row.lease_expires_at <= now
            ):
                raise ModelAssetError("model_install_lease_lost")
            completed_bytes = sum(
                item.size
                for item in self.manifest.files[: row.completed_files]
            )
            row.current_file = file_path
            row.current_file_bytes = file_bytes
            row.current_etag = etag
            row.downloaded_bytes = completed_bytes + file_bytes
            row.heartbeat_at = now
            row.updated_at = now
            session.commit()
            return self._status(row)

    def require_installed_path(self) -> Path:
        status = self.status()
        if (
            status.state != "installed"
            or status.installed_path is None
            or not status.installed_path.is_dir()
        ):
            raise ModelAssetError("model_not_installed")
        return status.installed_path
