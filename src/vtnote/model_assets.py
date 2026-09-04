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
from collections.abc import Iterator, Mapping
from typing import Callable, Protocol
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from vtnote.models import ModelInstallRecord
from vtnote.paths import StoragePaths
from vtnote.platform_transport import PinnedHttpsTransport, SourceHttpRequest
from vtnote.url_security import UpstreamHostPolicy


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_LOCAL_WHISPER_ALLOWED_FILES = frozenset(
    {
        "config.json",
        "model.bin",
        "preprocessor_config.json",
        "tokenizer.json",
        "vocabulary.json",
    }
)
_LOCAL_WHISPER_REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
_LOCAL_WHISPER_FILES = {
    "config.json": (
        2263,
        "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
    ),
    "model.bin": (
        1617884929,
        "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
    ),
    "preprocessor_config.json": (
        340,
        "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
    ),
    "tokenizer.json": (
        2710337,
        "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
    ),
    "vocabulary.json": (
        1068114,
        "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
    ),
}


@dataclass(frozen=True, slots=True)
class PinnedModelSpec:
    model_name: str
    repo_id: str
    revision: str
    files: Mapping[str, tuple[int, str]]


LOCAL_WHISPER_SPEC = PinnedModelSpec(
    model_name="large-v3-turbo",
    repo_id="dropbox-dash/faster-whisper-large-v3-turbo",
    revision=_LOCAL_WHISPER_REVISION,
    files=_LOCAL_WHISPER_FILES,
)
SENSEVOICE_SPEC = PinnedModelSpec(
    model_name="sensevoice-small-int8-2024-07-17",
    repo_id=(
        "csukuangfj/"
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    ),
    revision="2365baeacb507f821a0c8120fcee3d484dba7a07",
    files={
        "model.int8.onnx": (
            239_233_841,
            "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        ),
        "tokens.txt": (
            315_894,
            "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc",
        ),
    },
)
SILERO_VAD_SPEC = PinnedModelSpec(
    model_name="silero-vad-v4",
    repo_id="csukuangfj/vad",
    revision="af4fcfc9b8305246b1fe2ebcaf248975673166f1",
    files={
        "silero_vad.onnx": (
            1_807_522,
            "a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28",
        ),
    },
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


@dataclass(frozen=True, slots=True)
class ModelDownloadCheckpoint:
    completed_files: int
    current_file: str | None
    current_file_bytes: int
    current_etag: str | None


class ModelDownloadResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def __iter__(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class ModelTransport(Protocol):
    def get(
        self,
        *,
        url: str,
        headers: dict[str, str],
        maximum_bytes: int,
    ) -> ModelDownloadResponse: ...


class _PinnedModelResponse:
    def __init__(self, response: object) -> None:
        self._response = response
        self.status_code = response.status  # type: ignore[attr-defined]
        self.headers = response.headers  # type: ignore[attr-defined]

    def __iter__(self) -> Iterator[bytes]:
        while True:
            chunk = self._response.read(1024 * 1024)  # type: ignore[attr-defined]
            if not chunk:
                return
            yield chunk

    def close(self) -> None:
        self._response.close()  # type: ignore[attr-defined]


class HuggingFaceModelTransport:
    """Use the shared DNS-pinned transport for fixed-revision model files."""

    def __init__(self, transport: PinnedHttpsTransport) -> None:
        self.transport = transport
        self.policy = UpstreamHostPolicy(
            platform="model_assets",
            stage="extractor_aux",
            exact_hosts=frozenset({"huggingface.co"}),
            allowed_suffixes=frozenset(
                {
                    "huggingface.co",
                    "hf.co",
                    "xethub.hf.co",
                }
            ),
        )

    def get(
        self,
        *,
        url: str,
        headers: dict[str, str],
        maximum_bytes: int,
    ) -> ModelDownloadResponse:
        response = self.transport.request(
            SourceHttpRequest(
                url=url,
                method="GET",
                headers=headers,
                max_wire_bytes=maximum_bytes,
                max_decoded_bytes=maximum_bytes,
            ),
            self.policy,
        )
        if hasattr(response, "status_code"):
            return response  # type: ignore[return-value]
        return _PinnedModelResponse(response)


def load_pinned_model_manifest(
    path: Path,
    *,
    spec: PinnedModelSpec,
    allow_test_file_variants: bool = False,
) -> ModelManifest:
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
        or payload.get("model_name") != spec.model_name
        or payload.get("repo_id") != spec.repo_id
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
            or value.get("path") not in spec.files
            or isinstance(value.get("size"), bool)
            or not isinstance(value.get("size"), int)
            or value["size"] <= 0
            or not isinstance(value.get("sha256"), str)
            or _SHA256.fullmatch(value["sha256"]) is None
        ):
            raise ModelAssetError("model_manifest_invalid")
        files.append(ModelFile(**value))
    if (
        {item.path for item in files} != set(spec.files)
        or len(files) != len(spec.files)
    ):
        raise ModelAssetError("model_manifest_invalid")
    if not allow_test_file_variants and (
        payload["revision"] != spec.revision
        or {
            item.path: (item.size, item.sha256)
            for item in files
        }
        != dict(spec.files)
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


def load_local_whisper_manifest(
    path: Path,
    *,
    allow_test_file_variants: bool = False,
) -> ModelManifest:
    return load_pinned_model_manifest(
        path,
        spec=LOCAL_WHISPER_SPEC,
        allow_test_file_variants=allow_test_file_variants,
    )


def load_sensevoice_manifest(
    path: Path,
    *,
    allow_test_file_variants: bool = False,
) -> ModelManifest:
    return load_pinned_model_manifest(
        path,
        spec=SENSEVOICE_SPEC,
        allow_test_file_variants=allow_test_file_variants,
    )


def load_silero_vad_manifest(
    path: Path,
    *,
    allow_test_file_variants: bool = False,
) -> ModelManifest:
    return load_pinned_model_manifest(
        path,
        spec=SILERO_VAD_SPEC,
        allow_test_file_variants=allow_test_file_variants,
    )


class ModelAssetService:
    def __init__(
        self,
        *,
        engine: Engine,
        paths: StoragePaths,
        manifest_path: Path,
        manifest_loader: Callable[..., ModelManifest] = load_local_whisper_manifest,
        free_bytes: Callable[[Path], int] | None = None,
        allow_test_manifest: bool = False,
    ) -> None:
        self.engine = engine
        self.paths = paths
        self.manifest = manifest_loader(
            manifest_path,
            allow_test_file_variants=allow_test_manifest,
        )
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
            row.cancel_requested = False
            row.error_code = None
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
            if row.state == "downloading" and row.lease_owner is not None:
                row.cancel_requested = True
                row.updated_at = now
                session.commit()
                return self._status(row)
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

    def cancellation_requested(self, worker_id: str) -> bool:
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            return bool(
                row is not None
                and row.lease_owner == worker_id
                and row.cancel_requested
            )

    def finish_cancel(
        self,
        *,
        worker_id: str,
        now: datetime,
    ) -> ModelInstallStatus:
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if (
                row is None
                or row.lease_owner != worker_id
                or not row.cancel_requested
            ):
                raise ModelAssetError("model_install_lease_lost")
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
        lease_duration: timedelta = timedelta(minutes=2),
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
            or lease_duration <= timedelta(0)
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
            row.lease_expires_at = now + lease_duration
            row.updated_at = now
            session.commit()
            return self._status(row)

    def download_checkpoint(
        self,
        *,
        worker_id: str,
        now: datetime,
    ) -> ModelDownloadCheckpoint:
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
            return ModelDownloadCheckpoint(
                completed_files=row.completed_files,
                current_file=row.current_file,
                current_file_bytes=row.current_file_bytes,
                current_etag=row.current_etag,
            )

    def complete_file(
        self,
        *,
        worker_id: str,
        file_path: str,
        now: datetime,
    ) -> ModelInstallStatus:
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            expected = (
                self.manifest.files[row.completed_files]
                if row is not None
                and row.completed_files < len(self.manifest.files)
                else None
            )
            if (
                row is None
                or row.state != "downloading"
                or row.lease_owner != worker_id
                or row.lease_expires_at is None
                or row.lease_expires_at <= now
                or expected is None
                or expected.path != file_path
                or row.current_file_bytes != expected.size
            ):
                raise ModelAssetError("model_install_lease_lost")
            row.completed_files += 1
            row.downloaded_bytes = sum(
                item.size
                for item in self.manifest.files[: row.completed_files]
            )
            row.current_file = None
            row.current_file_bytes = 0
            row.current_etag = None
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.state = (
                "verifying"
                if row.completed_files == len(self.manifest.files)
                else "queued"
            )
            row.updated_at = now
            session.commit()
            return self._status(row)

    def release_for_retry(
        self,
        *,
        worker_id: str,
        safe_code: str,
        now: datetime,
    ) -> ModelInstallStatus:
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if (
                row is None
                or row.state != "downloading"
                or row.lease_owner != worker_id
            ):
                raise ModelAssetError("model_install_lease_lost")
            row.state = "queued"
            row.error_code = safe_code
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.updated_at = now
            session.commit()
            return self._status(row)

    def discard_partial(
        self,
        *,
        worker_id: str,
        partial: Path,
        now: datetime,
    ) -> None:
        expected_parent = self.paths.assert_runtime_destination(
            self.staging_root
        )
        candidate = self.paths.assert_runtime_destination(Path(partial))
        if candidate.parent != expected_parent or not candidate.name.endswith(
            ".part"
        ):
            raise ModelAssetError("model_progress_invalid")
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if row is None or row.lease_owner != worker_id:
                raise ModelAssetError("model_install_lease_lost")
            if candidate.is_file():
                trash = self.paths.runtime(
                    "trash",
                    "model-installs",
                    str(uuid4()),
                )
                trash.mkdir(parents=True, exist_ok=False)
                destination = trash / candidate.name
                self.paths.assert_runtime_destination(destination)
                os.replace(candidate, destination)
                row.trash_relpath = self.paths.runtime_relative(trash)
            row.current_file = None
            row.current_file_bytes = 0
            row.current_etag = None
            row.downloaded_bytes = sum(
                item.size
                for item in self.manifest.files[: row.completed_files]
            )
            row.updated_at = now
            session.commit()

    def fail_and_trash(
        self,
        *,
        worker_id: str,
        safe_code: str,
        now: datetime,
    ) -> ModelInstallStatus:
        with Session(self.engine) as session:
            row = session.get(ModelInstallRecord, self.manifest.model_name)
            if row is None or row.lease_owner != worker_id:
                raise ModelAssetError("model_install_lease_lost")
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
            row.state = "failed"
            row.error_code = safe_code
            row.staging_relpath = None
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
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


class ModelDownloadWorker:
    def __init__(
        self,
        *,
        service: ModelAssetService,
        transport: ModelTransport,
        worker_id: str,
        clock: Callable[[], datetime],
        lease_duration: timedelta = timedelta(minutes=2),
    ) -> None:
        self.service = service
        self.transport = transport
        self.worker_id = worker_id
        self.clock = clock
        self.lease_duration = lease_duration

    @staticmethod
    def _headers(response: ModelDownloadResponse) -> dict[str, str]:
        return {
            str(name).casefold(): str(value).strip()
            for name, value in response.headers.items()
        }

    @staticmethod
    def _content_range(
        value: str | None,
        *,
        offset: int,
        total: int,
    ) -> bool:
        if value is None:
            return False
        match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", value)
        return (
            match is not None
            and int(match.group(1)) == offset
            and int(match.group(2)) == total - 1
            and int(match.group(3)) == total
        )

    def _download(
        self,
        file: ModelFile,
        checkpoint: ModelDownloadCheckpoint,
    ) -> None:
        staging = self.service.staging_root
        staging.mkdir(parents=True, exist_ok=True)
        self.service.paths.assert_runtime_destination(staging)
        partial = staging / f"{file.path}.part"
        final = staging / file.path
        offset = 0
        etag = None
        if (
            checkpoint.current_file == file.path
            and checkpoint.current_etag
            and partial.is_file()
            and partial.stat().st_size == checkpoint.current_file_bytes
        ):
            offset = checkpoint.current_file_bytes
            etag = checkpoint.current_etag
        headers = (
            {"Range": f"bytes={offset}-", "If-Range": etag}
            if offset > 0 and etag is not None
            else {}
        )
        response = self.transport.get(
            url=self.service.download_url(file.path),
            headers=headers,
            maximum_bytes=file.size + 1,
        )
        try:
            response_headers = self._headers(response)
            response_etag = response_headers.get("etag")
            if not response_etag:
                raise ModelAssetError("model_download_invalid")
            if (
                offset > 0
                and response.status_code == 206
                and response_etag == etag
                and self._content_range(
                    response_headers.get("content-range"),
                    offset=offset,
                    total=file.size,
                )
                and response_headers.get("content-length")
                == str(file.size - offset)
            ):
                mode = "ab"
                written = offset
            elif (
                response.status_code == 200
                and response_headers.get("content-length") == str(file.size)
            ):
                mode = "wb"
                written = 0
            else:
                if offset > 0:
                    self.service.discard_partial(
                        worker_id=self.worker_id,
                        partial=partial,
                        now=self.clock(),
                    )
                raise ModelAssetError("model_download_invalid")
            self.service.record_progress(
                worker_id=self.worker_id,
                file_path=file.path,
                file_bytes=written,
                etag=response_etag,
                now=self.clock(),
                lease_duration=self.lease_duration,
            )
            with partial.open(mode) as destination:
                last_checkpoint = written
                for chunk in response:
                    if not isinstance(chunk, bytes) or not chunk:
                        continue
                    if self.service.cancellation_requested(self.worker_id):
                        raise ModelAssetError("model_install_canceled")
                    written += len(chunk)
                    if written > file.size:
                        raise ModelAssetError("model_download_invalid")
                    destination.write(chunk)
                    if written - last_checkpoint >= 8 * 1024 * 1024:
                        self.service.record_progress(
                            worker_id=self.worker_id,
                            file_path=file.path,
                            file_bytes=written,
                            etag=response_etag,
                            now=self.clock(),
                            lease_duration=self.lease_duration,
                        )
                        last_checkpoint = written
                destination.flush()
                os.fsync(destination.fileno())
            self.service.record_progress(
                worker_id=self.worker_id,
                file_path=file.path,
                file_bytes=written,
                etag=response_etag,
                now=self.clock(),
                lease_duration=self.lease_duration,
            )
        finally:
            response.close()
        if written != file.size or self.service._hash(partial) != (
            file.size,
            file.sha256,
        ):
            raise ModelAssetError("model_hash_mismatch")
        os.replace(partial, final)

    def run_one(self) -> str | None:
        now = self.clock()
        claimed = self.service.claim(
            self.worker_id,
            now,
            self.lease_duration,
        )
        if claimed is None:
            return None
        checkpoint = self.service.download_checkpoint(
            worker_id=self.worker_id,
            now=now,
        )
        if self.service.cancellation_requested(self.worker_id):
            self.service.finish_cancel(worker_id=self.worker_id, now=now)
            return "canceled"
        if checkpoint.completed_files >= len(self.service.manifest.files):
            return None
        file = self.service.manifest.files[checkpoint.completed_files]
        try:
            self._download(file, checkpoint)
            status = self.service.complete_file(
                worker_id=self.worker_id,
                file_path=file.path,
                now=self.clock(),
            )
            if status.state == "verifying":
                self.service.publish_verified(now=self.clock())
                return "installed"
            return "file_completed"
        except ModelAssetError as error:
            if error.code == "model_install_canceled":
                self.service.finish_cancel(
                    worker_id=self.worker_id,
                    now=self.clock(),
                )
                return "canceled"
            if error.code == "model_hash_mismatch":
                self.service.fail_and_trash(
                    worker_id=self.worker_id,
                    safe_code=error.code,
                    now=self.clock(),
                )
            else:
                self.service.release_for_retry(
                    worker_id=self.worker_id,
                    safe_code=error.code,
                    now=self.clock(),
                )
            raise
        except Exception:
            self.service.release_for_retry(
                worker_id=self.worker_id,
                safe_code="model_download_failed",
                now=self.clock(),
            )
            raise ModelAssetError("model_download_failed") from None
