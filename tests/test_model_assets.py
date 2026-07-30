from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.model_assets import (
    HuggingFaceModelTransport,
    ModelDownloadResponse,
    ModelDownloadWorker,
    ModelAssetError,
    ModelAssetService,
    load_local_whisper_manifest,
)
from vtnote.models import ModelInstallRecord
from vtnote.paths import StoragePaths
from vtnote.worker import ModelInstallerLoop, build_model_installer_loop


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT / "assets" / "models" / "large-v3-turbo.manifest.json"
)
REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
NOW = datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)


def make_service(
    tmp_path: Path,
    *,
    free_bytes: int = 4_000_000_000,
) -> tuple[ModelAssetService, object, StoragePaths]:
    paths = StoragePaths.from_settings(
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
        )
    )
    engine = initialize_database(paths.database)
    service = ModelAssetService(
        engine=engine,
        paths=paths,
        manifest_path=MANIFEST_PATH,
        free_bytes=lambda _: free_bytes,
    )
    return service, engine, paths


def test_model_manifest_is_exact_and_self_consistent() -> None:
    manifest = load_local_whisper_manifest(MANIFEST_PATH)
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest.schema_version == 1
    assert manifest.model_name == "large-v3-turbo"
    assert manifest.repo_id == "dropbox-dash/faster-whisper-large-v3-turbo"
    assert manifest.revision == REVISION
    assert [(item.path, item.size, item.sha256) for item in manifest.files] == [
        (
            "config.json",
            2263,
            "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
        ),
        (
            "model.bin",
            1617884929,
            "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
        ),
        (
            "preprocessor_config.json",
            340,
            "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
        ),
        (
            "tokenizer.json",
            2710337,
            "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
        ),
        (
            "vocabulary.json",
            1068114,
            "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
        ),
    ]
    assert manifest.total_bytes == sum(item["size"] for item in raw["files"])
    assert len({item.path for item in manifest.files}) == len(manifest.files)


def test_runtime_rejects_a_tampered_pinned_manifest(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["revision"] = "1" * 40
    candidate = tmp_path / "tampered.json"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelAssetError) as caught:
        load_local_whisper_manifest(candidate)

    assert caught.value.code == "model_manifest_invalid"


def test_install_requires_explicit_ack_and_free_space(tmp_path: Path) -> None:
    service, engine, _ = make_service(tmp_path)
    try:
        with pytest.raises(ModelAssetError) as no_ack:
            service.request_install(
                acknowledge_download=False,
                expected_revision=REVISION,
                now=NOW,
            )
        assert no_ack.value.code == "download_ack_required"

        low_space, low_engine, _ = make_service(
            tmp_path / "low",
            free_bytes=1_000_000,
        )
        try:
            with pytest.raises(ModelAssetError) as insufficient:
                low_space.request_install(
                    acknowledge_download=True,
                    expected_revision=REVISION,
                    now=NOW,
                )
            assert insufficient.value.code == "insufficient_space"
        finally:
            low_engine.dispose()

        queued = service.request_install(
            acknowledge_download=True,
            expected_revision=REVISION,
            now=NOW,
        )
        assert queued.state == "queued"
        assert queued.total_bytes == 1_621_665_983
        assert queued.downloaded_bytes == 0
    finally:
        engine.dispose()


def test_download_is_direct_only_and_d_drive_only(tmp_path: Path) -> None:
    service, engine, paths = make_service(tmp_path)
    try:
        status = service.request_install(
            acknowledge_download=True,
            expected_revision=REVISION,
            now=NOW,
        )

        assert status.staging_path is not None
        assert status.staging_path.is_relative_to(paths.runtime_cache_root)
        assert service.download_url("model.bin") == (
            "https://huggingface.co/dropbox-dash/"
            "faster-whisper-large-v3-turbo/resolve/"
            f"{REVISION}/model.bin"
        )
        with pytest.raises(ModelAssetError):
            service.download_url("../escape")
    finally:
        engine.dispose()


def test_download_resumes_only_matching_etag_revision_and_size(
    tmp_path: Path,
) -> None:
    service, engine, _ = make_service(tmp_path)
    try:
        service.request_install(
            acknowledge_download=True,
            expected_revision=REVISION,
            now=NOW,
        )
        assert service.resume_offset(
            "model.bin",
            local_size=1024,
            stored_etag='"etag-1"',
            response_etag='"etag-1"',
            response_total_size=1_617_884_929,
        ) == 1024
        assert service.resume_offset(
            "model.bin",
            local_size=1024,
            stored_etag='"etag-1"',
            response_etag='"etag-2"',
            response_total_size=1_617_884_929,
        ) == 0
        assert service.resume_offset(
            "model.bin",
            local_size=1024,
            stored_etag='"etag-1"',
            response_etag='"etag-1"',
            response_total_size=123,
        ) == 0
    finally:
        engine.dispose()


def test_each_file_hash_is_checked_before_atomic_publish(tmp_path: Path) -> None:
    service, engine, paths = make_service(tmp_path)
    try:
        service.request_install(
            acknowledge_download=True,
            expected_revision=REVISION,
            now=NOW,
        )
        staging = service.staging_root
        staging.mkdir(parents=True, exist_ok=True)
        for item in service.manifest.files:
            (staging / item.path).write_bytes(b"wrong")

        with pytest.raises(ModelAssetError) as caught:
            service.publish_verified(now=NOW)

        assert caught.value.code == "model_hash_mismatch"
        assert not service.install_root.exists()
    finally:
        engine.dispose()


def test_hash_failure_and_cancel_move_staging_to_recoverable_trash(
    tmp_path: Path,
) -> None:
    service, engine, paths = make_service(tmp_path)
    try:
        service.request_install(
            acknowledge_download=True,
            expected_revision=REVISION,
            now=NOW,
        )
        service.staging_root.mkdir(parents=True, exist_ok=True)
        (service.staging_root / "partial.bin").write_bytes(b"partial")

        canceled = service.cancel(now=NOW)

        assert canceled.state == "canceled"
        assert not service.staging_root.exists()
        assert canceled.trash_path is not None
        assert canceled.trash_path.is_relative_to(paths.runtime_cache_root)
        assert (canceled.trash_path / "partial.bin").read_bytes() == b"partial"
    finally:
        engine.dispose()


def test_install_progress_and_lease_survive_worker_restart(
    tmp_path: Path,
) -> None:
    first, engine, _ = make_service(tmp_path)
    try:
        first.request_install(
            acknowledge_download=True,
            expected_revision=REVISION,
            now=NOW,
        )
        claimed = first.claim("installer-a", NOW, timedelta(minutes=2))
        assert claimed is not None
        first.record_progress(
            worker_id="installer-a",
            file_path="model.bin",
            file_bytes=4096,
            etag='"etag-1"',
            now=NOW + timedelta(seconds=1),
        )

        restarted = ModelAssetService(
            engine=engine,
            paths=first.paths,
            manifest_path=MANIFEST_PATH,
            free_bytes=lambda _: 4_000_000_000,
        )
        status = restarted.status()
        assert status.state == "downloading"
        assert status.downloaded_bytes == 4096
        assert status.current_file == "model.bin"
        assert restarted.claim(
            "installer-b",
            NOW + timedelta(seconds=30),
            timedelta(minutes=2),
        ) is None
        assert restarted.claim(
            "installer-b",
            NOW + timedelta(minutes=2, seconds=1),
            timedelta(minutes=2),
        ) is not None
    finally:
        engine.dispose()


def test_runtime_never_downloads_an_uninstalled_model(tmp_path: Path) -> None:
    service, engine, _ = make_service(tmp_path)
    try:
        with pytest.raises(ModelAssetError) as caught:
            service.require_installed_path()
        assert caught.value.code == "model_not_installed"
        with Session(engine) as session:
            assert session.get(ModelInstallRecord, "large-v3-turbo") is None
    finally:
        engine.dispose()


TINY_FILES = {
    "config.json": b"config-data",
    "model.bin": b"model-binary-data",
    "preprocessor_config.json": b"preprocess",
    "tokenizer.json": b"tokenizer",
    "vocabulary.json": b"vocabulary",
}


def tiny_service(
    tmp_path: Path,
) -> tuple[ModelAssetService, object, StoragePaths]:
    manifest_path = tmp_path / "tiny-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_name": "large-v3-turbo",
                "repo_id": "dropbox-dash/faster-whisper-large-v3-turbo",
                "revision": REVISION,
                "files": [
                    {
                        "path": name,
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                    for name, content in TINY_FILES.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    paths = StoragePaths.from_settings(
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
        )
    )
    engine = initialize_database(paths.database)
    return (
        ModelAssetService(
            engine=engine,
            paths=paths,
            manifest_path=manifest_path,
            free_bytes=lambda _: 1_000_000_000,
            allow_test_manifest=True,
        ),
        engine,
        paths,
    )


class FakeDownloadResponse:
    def __init__(
        self,
        *,
        status_code: int,
        headers: dict[str, str],
        chunks: list[bytes],
    ) -> None:
        self.status_code = status_code
        self.headers = headers
        self.chunks = chunks
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.chunks)

    def close(self) -> None:
        self.closed = True


class FakeModelTransport:
    def __init__(self, responses: list[FakeDownloadResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], int]] = []

    def get(
        self,
        *,
        url: str,
        headers: dict[str, str],
        maximum_bytes: int,
    ) -> FakeDownloadResponse:
        self.calls.append((url, headers, maximum_bytes))
        return self.responses.pop(0)


def response(
    content: bytes,
    *,
    status: int = 200,
    etag: str = '"etag-1"',
    offset: int = 0,
) -> FakeDownloadResponse:
    headers = {
        "Content-Length": str(len(content) - offset),
        "ETag": etag,
    }
    if status == 206:
        headers["Content-Range"] = (
            f"bytes {offset}-{len(content) - 1}/{len(content)}"
        )
    return FakeDownloadResponse(
        status_code=status,
        headers=headers,
        chunks=[content[offset:]],
    )


def test_worker_resumes_only_a_matching_etag_range_and_completes_one_file(
    tmp_path: Path,
) -> None:
    service, engine, _ = tiny_service(tmp_path)
    first_name, content = next(iter(TINY_FILES.items()))
    service.request_install(
        acknowledge_download=True,
        expected_revision=REVISION,
        now=NOW,
    )
    service.staging_root.mkdir(parents=True, exist_ok=True)
    partial = service.staging_root / f"{first_name}.part"
    partial.write_bytes(content[:4])
    with Session(engine) as session:
        row = session.get(ModelInstallRecord, "large-v3-turbo")
        assert row is not None
        row.current_file = first_name
        row.current_file_bytes = 4
        row.current_etag = '"etag-1"'
        session.commit()
    transport = FakeModelTransport(
        [response(content, status=206, offset=4)]
    )
    try:
        result = ModelDownloadWorker(
            service=service,
            transport=transport,
            worker_id="model-worker",
            clock=lambda: NOW,
        ).run_one()

        assert result == "file_completed"
        assert transport.calls[0][1] == {
            "Range": "bytes=4-",
            "If-Range": '"etag-1"',
        }
        assert (service.staging_root / first_name).read_bytes() == content
        assert not partial.exists()
        status = service.status()
        assert status.completed_files == 1
        assert status.downloaded_bytes == len(content)
        assert status.state == "queued"
    finally:
        engine.dispose()


def test_worker_restarts_from_zero_when_provider_ignores_range(
    tmp_path: Path,
) -> None:
    service, engine, _ = tiny_service(tmp_path)
    first_name, content = next(iter(TINY_FILES.items()))
    service.request_install(
        acknowledge_download=True,
        expected_revision=REVISION,
        now=NOW,
    )
    service.staging_root.mkdir(parents=True, exist_ok=True)
    (service.staging_root / f"{first_name}.part").write_bytes(b"stale")
    with Session(engine) as session:
        row = session.get(ModelInstallRecord, "large-v3-turbo")
        assert row is not None
        row.current_file = first_name
        row.current_file_bytes = 5
        row.current_etag = '"old-etag"'
        session.commit()
    transport = FakeModelTransport(
        [response(content, status=200, etag='"new-etag"')]
    )
    try:
        result = ModelDownloadWorker(
            service=service,
            transport=transport,
            worker_id="model-worker",
            clock=lambda: NOW,
        ).run_one()

        assert result == "file_completed"
        assert (service.staging_root / first_name).read_bytes() == content
        assert len(transport.calls) == 1
    finally:
        engine.dispose()


def test_worker_discards_mismatched_partial_before_retrying_from_zero(
    tmp_path: Path,
) -> None:
    service, engine, _ = tiny_service(tmp_path)
    first_name, content = next(iter(TINY_FILES.items()))
    service.request_install(
        acknowledge_download=True,
        expected_revision=REVISION,
        now=NOW,
    )
    service.staging_root.mkdir(parents=True, exist_ok=True)
    partial = service.staging_root / f"{first_name}.part"
    partial.write_bytes(content[:4])
    with Session(engine) as session:
        row = session.get(ModelInstallRecord, "large-v3-turbo")
        assert row is not None
        row.current_file = first_name
        row.current_file_bytes = 4
        row.current_etag = '"old-etag"'
        session.commit()
    bad = response(content, status=206, etag='"new-etag"', offset=4)
    good = response(content, status=200, etag='"new-etag"')
    worker = ModelDownloadWorker(
        service=service,
        transport=FakeModelTransport([bad, good]),
        worker_id="model-worker",
        clock=lambda: NOW,
    )
    try:
        with pytest.raises(ModelAssetError) as caught:
            worker.run_one()
        assert caught.value.code == "model_download_invalid"
        assert not partial.exists()
        checkpoint = service.status()
        assert checkpoint.current_file is None
        assert checkpoint.current_file_bytes == 0

        assert worker.run_one() == "file_completed"
        assert (service.staging_root / first_name).read_bytes() == content
    finally:
        engine.dispose()


def test_worker_publishes_only_after_every_manifest_file_is_verified(
    tmp_path: Path,
) -> None:
    service, engine, _ = tiny_service(tmp_path)
    service.request_install(
        acknowledge_download=True,
        expected_revision=REVISION,
        now=NOW,
    )
    transport = FakeModelTransport(
        [response(content) for content in TINY_FILES.values()]
    )
    worker = ModelDownloadWorker(
        service=service,
        transport=transport,
        worker_id="model-worker",
        clock=lambda: NOW,
    )
    try:
        results = [worker.run_one() for _ in TINY_FILES]

        assert results[-1] == "installed"
        assert service.status().state == "installed"
        installed = service.require_installed_path()
        assert {
            path.name: path.read_bytes()
            for path in installed.iterdir()
        } == TINY_FILES
        assert len(transport.calls) == len(TINY_FILES)
    finally:
        engine.dispose()


class RecordingPinnedTransport:
    def __init__(self, selected: FakeDownloadResponse) -> None:
        self.selected = selected
        self.calls = []

    def request(self, request, policy):
        self.calls.append((request, policy))
        return self.selected


def test_huggingface_transport_uses_dns_pinned_public_peer_policy() -> None:
    selected = response(b"data")
    pinned = RecordingPinnedTransport(selected)
    transport = HuggingFaceModelTransport(pinned)

    returned = transport.get(
        url=(
            "https://huggingface.co/dropbox-dash/"
            f"faster-whisper-large-v3-turbo/resolve/{REVISION}/config.json"
        ),
        headers={"Range": "bytes=2-"},
        maximum_bytes=100,
    )

    assert returned is selected
    request, policy = pinned.calls[0]
    assert request.method == "GET"
    assert request.headers == {"Range": "bytes=2-"}
    assert request.max_wire_bytes == 100
    assert policy.platform == "model_assets"
    assert policy.exact_hosts == frozenset({"huggingface.co"})
    assert "hf.co" in policy.allowed_suffixes


def test_pinned_model_response_streams_bounded_chunks_not_binary_lines() -> None:
    class ReadOnlyRaw:
        status = 200
        headers = {"Content-Length": "6", "ETag": '"etag"'}

        def __init__(self) -> None:
            self.remaining = bytearray(b"abcdef")
            self.read_sizes: list[int] = []
            self.closed = False

        def read(self, size: int) -> bytes:
            self.read_sizes.append(size)
            selected = bytes(self.remaining[:2])
            del self.remaining[:2]
            return selected

        def __iter__(self):
            pytest.fail("binary model response must not use line iteration")

        def close(self) -> None:
            self.closed = True

    raw = ReadOnlyRaw()
    pinned = RecordingPinnedTransport(raw)  # type: ignore[arg-type]
    streamed = HuggingFaceModelTransport(pinned).get(
        url=(
            "https://huggingface.co/dropbox-dash/"
            f"faster-whisper-large-v3-turbo/resolve/{REVISION}/config.json"
        ),
        headers={},
        maximum_bytes=7,
    )

    assert list(streamed) == [b"ab", b"cd", b"ef"]
    streamed.close()
    assert all(size == 1024 * 1024 for size in raw.read_sizes)
    assert raw.closed is True


def test_model_installer_loop_keeps_durable_install_work_outside_stage_queue() -> None:
    class SteppedInstaller:
        def __init__(self) -> None:
            self.calls = 0

        def run_one(self) -> str | None:
            self.calls += 1
            return None if self.calls == 1 else "file_completed"

    installer = SteppedInstaller()
    sleeps: list[float] = []
    loop = ModelInstallerLoop(
        installer=installer,
        stop_requested=lambda: installer.calls >= 3,
        sleeper=sleeps.append,
        idle_delay=0.25,
    )

    loop.run()

    assert installer.calls == 3
    assert sleeps == [0.25]


def test_model_installer_loop_survives_one_safe_install_failure() -> None:
    class RecoveringInstaller:
        def __init__(self) -> None:
            self.calls = 0

        def run_one(self) -> str | None:
            self.calls += 1
            if self.calls == 1:
                raise ModelAssetError("model_download_failed")
            return "file_completed"

    installer = RecoveringInstaller()
    sleeps: list[float] = []
    loop = ModelInstallerLoop(
        installer=installer,
        stop_requested=lambda: installer.calls >= 2,
        sleeper=sleeps.append,
        idle_delay=0.25,
    )

    loop.run()

    assert installer.calls == 2
    assert sleeps == [0.25]


def test_active_cancel_is_cooperative_then_moves_partial_to_recoverable_trash(
    tmp_path: Path,
) -> None:
    service, engine, paths = tiny_service(tmp_path)
    service.request_install(
        acknowledge_download=True,
        expected_revision=REVISION,
        now=NOW,
    )
    assert service.claim(
        "model-worker",
        NOW,
        timedelta(minutes=2),
    ) is not None
    service.staging_root.mkdir(parents=True, exist_ok=True)
    (service.staging_root / "model.bin.part").write_bytes(b"partial")
    try:
        requested = service.cancel(now=NOW + timedelta(seconds=1))

        assert requested.state == "downloading"
        assert requested.cancel_requested is True
        assert service.staging_root.exists()
        assert service.cancellation_requested("model-worker") is True

        canceled = service.finish_cancel(
            worker_id="model-worker",
            now=NOW + timedelta(seconds=2),
        )

        assert canceled.state == "canceled"
        assert not service.staging_root.exists()
        assert canceled.trash_path is not None
        assert canceled.trash_path.is_relative_to(paths.runtime_cache_root)
        assert (canceled.trash_path / "model.bin.part").read_bytes() == b"partial"
    finally:
        engine.dispose()


def test_download_progress_renews_the_installer_lease(tmp_path: Path) -> None:
    service, engine, _ = tiny_service(tmp_path)
    first_name = next(iter(TINY_FILES))
    service.request_install(
        acknowledge_download=True,
        expected_revision=REVISION,
        now=NOW,
    )
    service.claim("model-worker", NOW, timedelta(minutes=2))
    try:
        service.record_progress(
            worker_id="model-worker",
            file_path=first_name,
            file_bytes=1,
            etag='"etag"',
            now=NOW + timedelta(minutes=1, seconds=50),
            lease_duration=timedelta(minutes=2),
        )
        renewed = service.record_progress(
            worker_id="model-worker",
            file_path=first_name,
            file_bytes=2,
            etag='"etag"',
            now=NOW + timedelta(minutes=2, seconds=10),
            lease_duration=timedelta(minutes=2),
        )

        assert renewed.current_file_bytes == 2
    finally:
        engine.dispose()


def test_production_model_installer_composition_is_direct_and_durable(
    tmp_path: Path,
) -> None:
    service, engine, paths = make_service(tmp_path)

    class Resolver:
        def resolve(self, host: str) -> list[str]:
            return ["93.184.216.34"]

    try:
        loop = build_model_installer_loop(
            engine=engine,
            paths=paths,
            manifest_path=MANIFEST_PATH,
            worker_id="model-installer",
            resolver=Resolver(),
            stop_requested=lambda: True,
        )

        assert isinstance(loop, ModelInstallerLoop)
        assert isinstance(loop.installer, ModelDownloadWorker)
        assert isinstance(
            loop.installer.transport,
            HuggingFaceModelTransport,
        )
        assert loop.installer.service.paths == service.paths
    finally:
        engine.dispose()
