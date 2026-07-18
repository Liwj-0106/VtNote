from __future__ import annotations

import hashlib
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.models import (
    ItemRecord,
    RuntimeAssetRecord,
    RuntimeCleanupEventRecord,
    TaskRecord,
)
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService


ITEM_ID = "11111111-1111-4111-8111-111111111111"
ITEM_B_ID = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


def make_service(
    tmp_path: Path,
) -> tuple[RuntimeAssetService, Session, StoragePaths, ItemRecord]:
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    engine = initialize_database(paths.database)
    session = Session(engine)
    task = TaskRecord(status="completed")
    item = ItemRecord(
        id=ITEM_ID,
        task=task,
        position=0,
        source_kind="url",
        source_locator="https://youtu.be/example",
        status="completed",
    )
    session.add(item)
    session.commit()
    return RuntimeAssetService(session, paths), session, paths, item


def stage_downloaded_audio(paths: StoragePaths, data: bytes = b"owned-audio") -> Path:
    destination = paths.downloaded_audio(ITEM_ID, "webm")
    destination.parent.mkdir(parents=True, exist_ok=True)
    paths.assert_runtime_destination(destination)
    destination.write_bytes(data)
    return destination


def register_downloaded(
    service: RuntimeAssetService, paths: StoragePaths, data: bytes = b"owned-audio"
):
    destination = stage_downloaded_audio(paths, data)
    asset = service.register_staged(
        item_id=ITEM_ID,
        role="downloaded_audio",
        relative_path=paths.runtime_relative(destination),
    )
    return asset, destination


def close_session(session: Session) -> None:
    engine = session.bind
    session.close()
    engine.dispose()


def make_directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) != 1314:
            raise
        result = subprocess.run(
            [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                "mklink",
                "/J",
                str(link),
                str(target),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_register_resolve_hash_and_idempotency(tmp_path: Path) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        asset, destination = register_downloaded(service, paths)

        assert asset.state == "active"
        assert asset.relative_path == f"items/{ITEM_ID}/audio/downloaded.webm"
        assert asset.size_bytes == len(b"owned-audio")
        assert asset.sha256 == hashlib.sha256(b"owned-audio").hexdigest()
        assert service.resolve(asset.id) == destination

        recovered = service.register_staged(
            item_id=ITEM_ID,
            role="downloaded_audio",
            relative_path=asset.relative_path,
        )
        assert recovered.id == asset.id
        assert session.scalar(select(func.count()).select_from(RuntimeAssetRecord)) == 1

        destination.write_bytes(b"tampered")
        with pytest.raises(RuntimeAssetError) as caught:
            service.resolve(asset.id)
        assert caught.value.code == "integrity_mismatch"
    finally:
        close_session(session)


def test_item_deletion_cannot_orphan_a_registered_runtime_file(tmp_path: Path) -> None:
    service, session, paths, item = make_service(tmp_path)
    try:
        asset, destination = register_downloaded(service, paths)

        session.delete(item.task)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        assert destination.read_bytes() == b"owned-audio"
        assert session.get(RuntimeAssetRecord, asset.id) is not None
    finally:
        close_session(session)


def test_trashed_asset_reserves_its_canonical_original_path(tmp_path: Path) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        asset, active_path = register_downloaded(service, paths)
        service.trash(asset.id, now=NOW)
        active_path.parent.mkdir(parents=True, exist_ok=True)
        active_path.write_bytes(b"replacement")

        with pytest.raises(RuntimeAssetError) as caught:
            service.register_staged(
                item_id=ITEM_ID,
                role="downloaded_audio",
                relative_path=paths.runtime_relative(active_path),
            )
        assert caught.value.code == "path_conflict"
        assert session.scalar(select(func.count()).select_from(RuntimeAssetRecord)) == 1
        stored = session.get(RuntimeAssetRecord, asset.id)
        assert stored is not None
        assert stored.state == "trash"
    finally:
        close_session(session)


@pytest.mark.parametrize(
    ("role", "relative_path", "code"),
    [
        ("unknown", f"items/{ITEM_ID}/audio/downloaded.webm", "invalid_role"),
        ("cloud_audio", f"items/{ITEM_ID}/audio/downloaded.webm", "role_path_mismatch"),
        ("downloaded_audio", f"items/{ITEM_ID}/audio/downloaded.exe", "role_path_mismatch"),
        ("downloaded_audio", "../outside.webm", "invalid_relative_path"),
    ],
)
def test_register_rejects_invalid_role_or_relative_path(
    tmp_path: Path, role: str, relative_path: str, code: str
) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        stage_downloaded_audio(paths)
        with pytest.raises(RuntimeAssetError) as caught:
            service.register_staged(
                item_id=ITEM_ID, role=role, relative_path=relative_path
            )
        assert caught.value.code == code
        assert session.scalar(select(func.count()).select_from(RuntimeAssetRecord)) == 0
    finally:
        close_session(session)


def test_trash_and_restore_are_idempotent_and_audited(tmp_path: Path) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        asset, active_path = register_downloaded(service, paths)

        trashed = service.trash(asset.id, now=NOW)
        trash_path = paths.runtime_from_relative(trashed.relative_path)
        assert trashed.state == "trash"
        assert trashed.purge_after == NOW + timedelta(hours=24)
        assert trash_path.read_bytes() == b"owned-audio"
        assert not active_path.exists()

        repeated = service.trash(asset.id, now=NOW + timedelta(hours=1))
        assert repeated.purge_after == trashed.purge_after
        assert service.resolve(asset.id) == trash_path

        restored = service.restore(asset.id)
        assert restored.state == "active"
        assert restored.purge_after is None
        assert active_path.read_bytes() == b"owned-audio"
        assert not trash_path.exists()
        assert service.restore(asset.id).state == "active"

        events = session.scalars(
            select(RuntimeCleanupEventRecord)
            .where(RuntimeCleanupEventRecord.asset_id == asset.id)
            .order_by(RuntimeCleanupEventRecord.created_at, RuntimeCleanupEventRecord.id)
        ).all()
        assert {(event.action, event.outcome, event.code) for event in events} == {
            ("trash", "succeeded", "moved"),
            ("trash", "succeeded", "already_trashed"),
            ("restore", "succeeded", "moved"),
            ("restore", "succeeded", "already_active"),
        }
    finally:
        close_session(session)


def test_purge_refuses_early_then_removes_only_due_registered_trash(
    tmp_path: Path,
) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        asset, _ = register_downloaded(service, paths)
        trashed = service.trash(asset.id, now=NOW)
        trash_path = paths.runtime_from_relative(trashed.relative_path)

        with pytest.raises(RuntimeAssetError) as caught:
            service.purge(asset.id, now=NOW + timedelta(hours=23))
        assert caught.value.code == "not_due"
        assert trash_path.exists()

        assert service.purge(asset.id, now=NOW + timedelta(hours=24)) is True
        assert not trash_path.exists()
        assert session.get(RuntimeAssetRecord, asset.id) is None
        events = session.scalars(
            select(RuntimeCleanupEventRecord).where(
                RuntimeCleanupEventRecord.asset_id == asset.id
            )
        ).all()
        assert {(event.outcome, event.code) for event in events if event.action == "purge"} == {
            ("failed", "not_due"),
            ("succeeded", "purged"),
        }
    finally:
        close_session(session)


@pytest.mark.parametrize(
    ("owner", "status"),
    [
        ("item", "queued"),
        ("item", "running"),
        ("item", "cancel_requested"),
        ("task", "queued"),
        ("task", "running"),
        ("task", "cancel_requested"),
    ],
)
def test_purge_refuses_active_item_or_task(
    tmp_path: Path, owner: str, status: str
) -> None:
    service, session, paths, item = make_service(tmp_path)
    try:
        asset, _ = register_downloaded(service, paths)
        trashed = service.trash(asset.id, now=NOW)
        if owner == "item":
            item.status = status
        else:
            item.task.status = status
        session.commit()

        with pytest.raises(RuntimeAssetError) as caught:
            service.purge(asset.id, now=NOW + timedelta(hours=24))
        assert caught.value.code == "active_work"
        assert paths.runtime_from_relative(trashed.relative_path).exists()
        event = session.scalars(
            select(RuntimeCleanupEventRecord)
            .where(
                RuntimeCleanupEventRecord.asset_id == asset.id,
                RuntimeCleanupEventRecord.action == "purge",
            )
            .order_by(RuntimeCleanupEventRecord.created_at.desc())
        ).first()
        assert event is not None
        assert (event.outcome, event.code) == ("failed", "active_work")
    finally:
        close_session(session)


def test_cleanup_recovers_filesystem_database_mismatches(tmp_path: Path) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        asset, active_path = register_downloaded(service, paths)
        trash_path = paths.trash_asset(asset.id, "webm")
        trash_path.parent.mkdir(parents=True, exist_ok=True)

        # Crash after the filesystem move but before the active -> trash DB update.
        os.replace(active_path, trash_path)
        trashed = service.trash(asset.id, now=NOW)
        assert trashed.state == "trash"
        assert paths.runtime_from_relative(trashed.relative_path) == trash_path

        # Crash after the reverse filesystem move but before the trash -> active DB update.
        os.replace(trash_path, active_path)
        restored = service.restore(asset.id)
        assert restored.state == "active"
        assert paths.runtime_from_relative(restored.relative_path) == active_path

        # Crash after permanent unlink but before the trash row is deleted.
        trashed = service.trash(asset.id, now=NOW)
        paths.runtime_from_relative(trashed.relative_path).unlink()
        assert service.purge(asset.id, now=NOW + timedelta(hours=24)) is True
        assert session.get(RuntimeAssetRecord, asset.id) is None
    finally:
        close_session(session)


def test_arbitrary_caller_path_is_never_a_purge_target(tmp_path: Path) -> None:
    service, session, _, _ = make_service(tmp_path)
    outside = tmp_path / "user-original.mp4"
    outside.write_bytes(b"user-owned")
    try:
        with pytest.raises(RuntimeAssetError) as caught:
            service.purge(str(outside), now=NOW + timedelta(days=2))
        assert caught.value.code == "invalid_asset_id"
        assert outside.read_bytes() == b"user-owned"
    finally:
        close_session(session)


def test_trash_reparse_refusal_is_recorded_with_a_safe_code(tmp_path: Path) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        asset, active_path = register_downloaded(service, paths)
        outside = tmp_path / "outside-audio"
        os.replace(active_path.parent, outside)
        make_directory_link(active_path.parent, outside)

        with pytest.raises(RuntimeAssetError) as caught:
            service.trash(asset.id, now=NOW)
        assert caught.value.code == "invalid_relative_path"
        event = session.scalars(
            select(RuntimeCleanupEventRecord).where(
                RuntimeCleanupEventRecord.asset_id == asset.id,
                RuntimeCleanupEventRecord.action == "trash",
            )
        ).first()
        assert event is not None
        assert (event.outcome, event.code) == ("failed", "invalid_relative_path")
        assert (outside / "downloaded.webm").read_bytes() == b"owned-audio"
    finally:
        close_session(session)


def test_cross_item_audio_junction_cannot_alias_a_registered_asset(
    tmp_path: Path,
) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        item_b_audio = paths.downloaded_audio(ITEM_B_ID, "webm")
        item_b_audio.parent.mkdir(parents=True, exist_ok=True)
        item_b_audio.write_bytes(b"item-b-audio")
        item_a_audio_directory = paths.runtime(
            "items", ITEM_ID, "audio"
        )
        item_a_audio_directory.parent.mkdir(parents=True, exist_ok=True)
        make_directory_link(item_a_audio_directory, item_b_audio.parent)
        alias_relative = f"items/{ITEM_ID}/audio/downloaded.webm"

        with pytest.raises(UnsafePathError):
            paths.runtime_from_relative(alias_relative)
        with pytest.raises(RuntimeAssetError) as caught:
            service.register_staged(
                item_id=ITEM_ID,
                role="downloaded_audio",
                relative_path=alias_relative,
            )
        assert caught.value.code == "invalid_relative_path"
        assert item_b_audio.read_bytes() == b"item-b-audio"
        assert session.scalar(select(func.count()).select_from(RuntimeAssetRecord)) == 0
    finally:
        close_session(session)


def test_purge_holds_writer_reservation_through_file_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, setup_session, paths, _ = make_service(tmp_path)
    asset, _ = register_downloaded(service, paths)
    trashed = service.trash(asset.id, now=NOW)
    trash_path = paths.runtime_from_relative(trashed.relative_path)
    engine = setup_session.bind
    setup_session.close()

    unlink_entered = Event()
    allow_unlink = Event()
    update_started = Event()
    update_executed = Event()
    original_unlink = Path.unlink

    def blocking_unlink(path: Path, *args, **kwargs):
        if path == trash_path:
            unlink_entered.set()
            assert allow_unlink.wait(5)
        return original_unlink(path, *args, **kwargs)

    def before_cursor_execute(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if statement.lstrip().upper().startswith("UPDATE ITEMS"):
            update_started.set()

    def after_cursor_execute(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if statement.lstrip().upper().startswith("UPDATE ITEMS"):
            update_executed.set()

    def run_purge() -> bool:
        with Session(engine) as purge_session:
            return RuntimeAssetService(purge_session, paths).purge(
                asset.id, now=NOW + timedelta(hours=24)
            )

    def queue_item() -> None:
        assert unlink_entered.wait(5)
        with Session(engine) as writer_session:
            item = writer_session.get(ItemRecord, ITEM_ID)
            assert item is not None
            item.status = "queued"
            writer_session.commit()

    monkeypatch.setattr(Path, "unlink", blocking_unlink)
    sqlalchemy_event.listen(engine, "before_cursor_execute", before_cursor_execute)
    sqlalchemy_event.listen(engine, "after_cursor_execute", after_cursor_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            purge_future = pool.submit(run_purge)
            assert unlink_entered.wait(5)
            writer_future = pool.submit(queue_item)
            assert update_started.wait(5)
            assert not update_executed.wait(0.5)
            allow_unlink.set()
            assert purge_future.result(timeout=5) is True
            writer_future.result(timeout=5)
    finally:
        allow_unlink.set()
        sqlalchemy_event.remove(engine, "before_cursor_execute", before_cursor_execute)
        sqlalchemy_event.remove(engine, "after_cursor_execute", after_cursor_execute)
        engine.dispose()


def test_purge_refuses_when_another_session_commits_active_state_first(
    tmp_path: Path,
) -> None:
    service, setup_session, paths, _ = make_service(tmp_path)
    asset, _ = register_downloaded(service, paths)
    trashed = service.trash(asset.id, now=NOW)
    trash_path = paths.runtime_from_relative(trashed.relative_path)
    engine = setup_session.bind
    setup_session.close()
    try:
        with Session(engine, expire_on_commit=False) as purge_session:
            stale_item = purge_session.get(ItemRecord, ITEM_ID)
            assert stale_item is not None
            assert stale_item.status == "completed"
            with Session(engine) as writer_session:
                item = writer_session.get(ItemRecord, ITEM_ID)
                assert item is not None
                item.status = "running"
                writer_session.commit()

            with pytest.raises(RuntimeAssetError) as caught:
                RuntimeAssetService(purge_session, paths).purge(
                    asset.id, now=NOW + timedelta(hours=24)
                )
            assert caught.value.code == "active_work"
        assert trash_path.exists()
    finally:
        engine.dispose()


def test_purge_due_processes_only_assets_whose_retention_has_elapsed(
    tmp_path: Path,
) -> None:
    service, session, paths, _ = make_service(tmp_path)
    try:
        due, _ = register_downloaded(service, paths)
        cloud_path = paths.cloud_ogg(ITEM_ID)
        cloud_path.parent.mkdir(parents=True, exist_ok=True)
        cloud_path.write_bytes(b"cloud-audio")
        later = service.register_staged(
            item_id=ITEM_ID,
            role="cloud_audio",
            relative_path=paths.runtime_relative(cloud_path),
        )
        service.trash(due.id, now=NOW)
        service.trash(later.id, now=NOW + timedelta(hours=1))

        purged = service.purge_due(now=NOW + timedelta(hours=24))

        assert purged == (due.id,)
        assert session.get(RuntimeAssetRecord, due.id) is None
        assert session.get(RuntimeAssetRecord, later.id) is not None
    finally:
        close_session(session)
