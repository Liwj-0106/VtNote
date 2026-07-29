from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.model_assets import (
    ModelAssetError,
    ModelAssetService,
    load_local_whisper_manifest,
)
from vtnote.models import ModelInstallRecord
from vtnote.paths import StoragePaths


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
            NOW + timedelta(minutes=2),
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
