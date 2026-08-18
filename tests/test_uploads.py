from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from vtnote.api import create_app
from vtnote.artifacts import validate_source_subtitle
from vtnote.config import Settings
from vtnote.configuration import ConfigurationService
from vtnote.database import initialize_database
from vtnote.media import MediaError, MediaInfo
from vtnote.models import (
    ItemRecord,
    RuntimeAssetRecord,
    RuntimeCleanupEventRecord,
)
from vtnote.paths import StoragePaths
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService
from vtnote.secrets import MemorySecretStore
from vtnote.tasks import InvalidTaskOperation, TaskService
from vtnote.uploads import (
    MultipartUploadStager,
    UploadError,
    UploadLimits,
    UploadService,
    UploadTaskContext,
)
from vtnote.url_security import SourceUrlPolicy
from vtnote.worker_store import WorkerStore


BASE_URL = "http://127.0.0.1:8765"
VALID_SRT = b"1\n00:00:00,000 --> 00:00:01,000\nhello\n"


class PublicResolver:
    def resolve(self, host: str) -> list[str]:
        return ["142.250.72.14"]


class FakeLocalValidator:
    def validate_media(self, path: Path) -> MediaInfo:
        if path.read_bytes().startswith(b"invalid"):
            raise MediaError("invalid_media")
        return MediaInfo(
            duration_ms=1_000,
            size_bytes=path.stat().st_size,
            format_name="mp4",
            audio_codec="aac",
            sample_rate=44_100,
            channels=2,
        )

    def validate_subtitle(self, path: Path) -> None:
        validate_source_subtitle(path.suffix.removeprefix("."), path.read_bytes())


def multipart_body(
    parts: list[tuple[str, str | None, str, bytes]], boundary: str = "vtnote-boundary"
) -> tuple[bytes, str]:
    body = bytearray()
    for name, filename, content_type, data in parts:
        body.extend(f"--{boundary}\r\n".encode())
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body.extend((disposition + "\r\n").encode("utf-8"))
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(data)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def upload_body(
    *,
    kind: str,
    filename: str,
    data: bytes,
    extra_parts: list[tuple[str, str | None, str, bytes]] | None = None,
) -> tuple[bytes, str]:
    metadata = json.dumps(
        {"kind": kind, "asr_mode": "local", "notes_enabled": False}
    ).encode()
    return multipart_body(
        [
            ("metadata", None, "application/json", metadata),
            ("file", filename, "application/octet-stream", data),
            *(extra_parts or []),
        ]
    )


def make_client(
    tmp_path: Path, *, limits: UploadLimits | None = None
) -> tuple[TestClient, object, StoragePaths]:
    settings = Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    paths = StoragePaths.from_settings(settings)
    engine = initialize_database(paths.database)
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=MemorySecretStore(),
        resolver=PublicResolver(),
        local_source_validator=FakeLocalValidator(),
        upload_limits=limits,
    )
    return TestClient(app, base_url=BASE_URL), engine, paths


def csrf(client: TestClient) -> dict[str, str]:
    token = client.get("/api/security/csrf").json()["csrf_token"]
    return {"Origin": BASE_URL, "X-CSRF-Token": token}


def post_multipart(
    client: TestClient, body: bytes, content_type: str, headers: dict[str, str]
):
    return client.post(
        "/api/tasks",
        content=body,
        headers={
            **headers,
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        },
    )


def test_large_browser_upload_streams_without_system_temp_and_returns_opaque_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, paths = make_client(tmp_path)
    headers = csrf(client)
    payload = b"media" * (400 * 1024)
    body, content_type = upload_body(
        kind="media", filename="downkyi-export.aac", data=payload
    )
    unusable_temp = tmp_path / "system-temp-is-forbidden"
    monkeypatch.setenv("TEMP", str(unusable_temp))
    monkeypatch.setenv("TMP", str(unusable_temp))
    monkeypatch.setattr(
        tempfile,
        "SpooledTemporaryFile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spooling used")),
    )
    try:
        response = post_multipart(client, body, content_type, headers)

        assert response.status_code == 201
        task = response.json()
        item = task["items"][0]
        assert item["source_kind"] == "uploaded_media"
        assert item["source_display_name"] == "downkyi-export.aac"
        asset_id = str(UUID(item["source_locator"]))
        assert asset_id == item["source_locator"]
        assert str(paths.runtime_cache_root) not in json.dumps(task)
        assert str(paths.data_root) not in json.dumps(task)
        with Session(engine) as session:
            stored_item = session.get(ItemRecord, item["id"])
            asset = session.get(RuntimeAssetRecord, asset_id)
            assert stored_item is not None and asset is not None
            assert stored_item.source_locator == asset.id
            assert asset.state == "active"
            assert asset.size_bytes == len(payload)
            assert asset.sha256 == hashlib.sha256(payload).hexdigest()
            assert paths.runtime_from_relative(asset.relative_path).read_bytes() == payload
            durable_dump = json.dumps(stored_item.task.pipeline_snapshot_json)
            assert str(paths.runtime_cache_root) not in durable_dump
    finally:
        engine.dispose()


def test_subtitle_upload_is_sniffed_but_original_bytes_remain_runtime_owned(
    tmp_path: Path,
) -> None:
    client, engine, paths = make_client(tmp_path)
    headers = csrf(client)
    body, content_type = upload_body(
        kind="subtitle", filename="captions.SRT", data=VALID_SRT
    )
    try:
        response = post_multipart(client, body, content_type, headers)
        assert response.status_code == 201
        item = response.json()["items"][0]
        assert item["source_kind"] == "uploaded_subtitle"
        assert item["source_display_name"] == "captions.SRT"
        with Session(engine) as session:
            asset = session.get(RuntimeAssetRecord, item["source_locator"])
            assert asset is not None
            assert paths.runtime_from_relative(asset.relative_path).read_bytes() == VALID_SRT
        assert not paths.source_original(item["id"], "srt").exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("body_factory", "expected_code", "expect_task"),
    [
        (
            lambda: upload_body(kind="media", filename="../escape.mp4", data=b"media"),
            "unsafe_filename",
            True,
        ),
        (
            lambda: multipart_body(
                [
                    ("file", "video.mp4", "application/octet-stream", b"media"),
                    (
                        "metadata",
                        None,
                        "application/json",
                        b'{"kind":"media","notes_enabled":false}',
                    ),
                ]
            ),
            "metadata_must_be_first",
            False,
        ),
        (
            lambda: upload_body(
                kind="media",
                filename="video.mp4",
                data=b"media",
                extra_parts=[("extra", None, "text/plain", b"no")],
            ),
            "unexpected_upload_part",
            True,
        ),
        (
            lambda: upload_body(kind="subtitle", filename="empty.srt", data=b""),
            "empty_upload",
            True,
        ),
        (
            lambda: upload_body(
                kind="subtitle", filename="bad.srt", data=b"not a subtitle"
            ),
            "invalid_subtitle",
            True,
        ),
        (
            lambda: upload_body(
                kind="media", filename="bad.mp4", data=b"invalid media"
            ),
            "invalid_media",
            True,
        ),
    ],
)
def test_malformed_or_invalid_uploads_fail_safely_and_remain_visible(
    tmp_path: Path,
    body_factory,
    expected_code: str,
    expect_task: bool,
) -> None:
    client, engine, _ = make_client(tmp_path)
    headers = csrf(client)
    body, content_type = body_factory()
    try:
        response = post_multipart(client, body, content_type, headers)
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == expected_code
        tasks = client.get("/api/tasks").json()
        assert len(tasks) == int(expect_task)
        if expect_task:
            assert tasks[0]["status"] == "failed"
            assert error["details"]["task_id"] == tasks[0]["id"]
    finally:
        engine.dispose()


def test_over_limit_upload_is_registered_then_moved_to_recoverable_trash(
    tmp_path: Path,
) -> None:
    limits = UploadLimits(max_media_bytes=8, max_subtitle_bytes=8)
    client, engine, paths = make_client(tmp_path, limits=limits)
    headers = csrf(client)
    body, content_type = upload_body(
        kind="media", filename="large.mp4", data=b"0123456789ABCDEF"
    )
    try:
        response = post_multipart(client, body, content_type, headers)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "upload_size_limit"
        with Session(engine) as session:
            assets = session.scalars(select(RuntimeAssetRecord)).all()
            assert len(assets) == 1
            asset = assets[0]
            assert asset.state == "trash"
            assert 0 < asset.size_bytes <= 8
            assert paths.runtime_from_relative(asset.relative_path).is_file()
            assert any(
                event.action == "trash" and event.outcome == "succeeded"
                for event in session.scalars(select(RuntimeCleanupEventRecord)).all()
            )
    finally:
        engine.dispose()


def test_empty_upload_uses_only_typed_zero_byte_cleanup_and_audits_it(tmp_path: Path) -> None:
    client, engine, paths = make_client(tmp_path)
    headers = csrf(client)
    body, content_type = upload_body(
        kind="subtitle", filename="empty.srt", data=b""
    )
    try:
        response = post_multipart(client, body, content_type, headers)
        assert response.status_code == 400
        with Session(engine) as session:
            assert session.scalar(select(RuntimeAssetRecord)) is None
            events = session.scalars(select(RuntimeCleanupEventRecord)).all()
            assert len(events) == 1
            assert events[0].action == "discard"
            assert events[0].code == "zero_byte_incoming"
        assert not any(path.is_file() for path in paths.runtime("incoming").rglob("*"))
    finally:
        engine.dispose()


def test_upload_total_request_limit_uses_largest_payload() -> None:
    limits = UploadLimits(
        max_media_bytes=11,
        max_subtitle_bytes=7,
        max_metadata_bytes=3,
        max_request_overhead_bytes=5,
    )

    assert limits.max_request_bytes == 19


def test_stream_parser_detects_false_content_length_and_disconnect_after_metadata(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    body, content_type = upload_body(
        kind="media", filename="video.mp4", data=b"partial-media"
    )
    data_offset = body.index(b"partial-media")
    first = body[: data_offset + 4]
    rest = body[data_offset + 4 :]
    contexts: list[UploadTaskContext] = []

    def accept(metadata: dict[str, object], upload_id: str) -> UploadTaskContext:
        context = UploadTaskContext(task_id=str(UUID(int=1)), item_id=str(UUID(int=2)))
        contexts.append(context)
        return context

    stager = MultipartUploadStager(paths, UploadLimits())

    async def wrong_length_chunks():
        yield first
        yield rest

    with pytest.raises(UploadError) as wrong_length:
        asyncio.run(
            stager.consume(
                wrong_length_chunks(),
                content_type=content_type,
                content_length=len(body) - 2,
                accept_metadata=accept,
            )
        )
    assert wrong_length.value.code == "content_length_mismatch"
    assert wrong_length.value.state.context is not None

    contexts.clear()

    async def disconnected_chunks():
        yield first
        raise ClientDisconnect()

    with pytest.raises(UploadError) as disconnected:
        asyncio.run(
            stager.consume(
                disconnected_chunks(),
                content_type=content_type,
                content_length=len(body),
                accept_metadata=accept,
            )
        )
    assert disconnected.value.code == "client_disconnected"
    assert disconnected.value.state.context is not None
    assert disconnected.value.state.incoming_path is not None
    assert disconnected.value.state.incoming_path.read_bytes() == b"part"


def make_direct_services(tmp_path: Path):
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    engine = initialize_database(paths.database)
    session = Session(engine)
    configuration = ConfigurationService(session, MemorySecretStore(), paths=paths)
    tasks = TaskService(
        session,
        configuration,
        paths,
        SourceUrlPolicy(PublicResolver()),
        local_source_validator=FakeLocalValidator(),
    )
    uploads = UploadService(
        session=session,
        paths=paths,
        tasks=tasks,
        assets=RuntimeAssetService(session, paths),
        local_sources=FakeLocalValidator(),
    )
    return session, paths, tasks, uploads


def test_upload_completion_recovers_move_and_registration_crash_shapes(tmp_path: Path) -> None:
    session, paths, tasks, uploads = make_direct_services(tmp_path)
    try:
        task = tasks.create_upload_task(
            upload_kind="subtitle",
            upload_id=str(UUID(int=10)),
            options={"notes_enabled": False},
        )
        item_id = task.items[0].id
        incoming = paths.incoming_upload(str(UUID(int=10)), "srt")
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(VALID_SRT)
        state = uploads.staged_state(
            task_id=task.id,
            item_id=item_id,
            upload_id=str(UUID(int=10)),
            upload_kind="subtitle",
            extension="srt",
            display_name="captions.srt",
            incoming_path=incoming,
            file_size=len(VALID_SRT),
        )

        destination = paths.uploaded_source(item_id, "srt")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(incoming, destination)  # crash after move, before DB registration
        completed = uploads.complete(state)
        asset_id = completed.items[0].source_locator
        assert RuntimeAssetService(session, paths).resolve(asset_id) == destination

        # Calling completion after registration/before response is idempotent.
        recovered = uploads.complete(state)
        assert recovered.items[0].source_locator == asset_id
        assert recovered.items[0].source_display_name == "captions.srt"
    finally:
        session.bind.dispose()
        session.close()


def test_worker_waits_until_uploaded_media_registration_commits(tmp_path: Path) -> None:
    session, paths, tasks, uploads = make_direct_services(tmp_path)
    upload_id = str(UUID(int=11))
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    try:
        task = tasks.create_upload_task(
            upload_kind="media",
            upload_id=upload_id,
            options={"output_type": "transcript", "notes_enabled": False},
        )
        item_id = task.items[0].id
        store = WorkerStore(session.bind, process_id=1)

        assert store.claim_next("worker", now, timedelta(seconds=30)) is None

        incoming = paths.incoming_upload(upload_id, "mp4")
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(b"uploaded-media")
        completed = uploads.complete(
            uploads.staged_state(
                task_id=task.id,
                item_id=item_id,
                upload_id=upload_id,
                upload_kind="media",
                extension="mp4",
                display_name="video.mp4",
                incoming_path=incoming,
                file_size=len(b"uploaded-media"),
            )
        )

        assert completed.items[0].source_locator != upload_id
        assert RuntimeAssetService(session, paths).active_for_role(
            item_id=item_id,
            role="uploaded_source",
        ) is not None
        claim = store.claim_next("worker", now, timedelta(seconds=30))
        assert claim is not None
        assert claim.item_id == item_id
        assert claim.stage == "source"
    finally:
        session.bind.dispose()
        session.close()


def test_upload_cleanup_failure_is_persisted_as_a_safe_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, paths, tasks, uploads = make_direct_services(tmp_path)
    try:
        task = tasks.create_upload_task(
            upload_kind="media",
            upload_id=str(UUID(int=20)),
            options={"notes_enabled": False},
        )
        incoming = paths.incoming_upload(str(UUID(int=20)), "mp4")
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(b"partial-media")
        state = uploads.staged_state(
            task_id=task.id,
            item_id=task.items[0].id,
            upload_id=str(UUID(int=20)),
            upload_kind="media",
            extension="mp4",
            display_name="partial.mp4",
            incoming_path=incoming,
            file_size=len(b"partial-media"),
        )

        def fail_trash(asset_id: str):
            raise RuntimeAssetError("filesystem_error")

        monkeypatch.setattr(uploads.assets, "trash", fail_trash)
        failed = uploads.fail(state, code="client_disconnected")

        assert failed is not None and failed.status == "failed"
        events = session.scalars(
            select(RuntimeCleanupEventRecord).where(
                RuntimeCleanupEventRecord.action == "upload_cleanup"
            )
        ).all()
        assert len(events) == 1
        assert events[0].outcome == "failed"
        assert events[0].code == "upload_cleanup_failed"
        assert "filesystem" not in events[0].code
    finally:
        session.bind.dispose()
        session.close()


def test_trusted_local_sources_are_validated_in_place_and_never_modified(tmp_path: Path) -> None:
    session, _, tasks, _ = make_direct_services(tmp_path)
    valid = tmp_path / "user-captions.srt"
    valid.write_bytes(VALID_SRT)
    before = hashlib.sha256(valid.read_bytes()).hexdigest()
    invalid = tmp_path / "bad.srt"
    invalid.write_bytes(b"not subtitles")
    try:
        created = tasks.create_task(
            sources=[{"kind": "local_subtitle", "locator": str(valid)}],
            options={"notes_enabled": False},
        )
        assert created.items[0].source_locator == valid.name
        assert hashlib.sha256(valid.read_bytes()).hexdigest() == before
        with pytest.raises(ValueError, match="invalid local subtitle"):
            tasks.create_task(
                sources=[{"kind": "local_subtitle", "locator": str(invalid)}],
                options={"notes_enabled": False},
            )
    finally:
        session.bind.dispose()
        session.close()


def test_task_service_rejects_unc_sources_before_filesystem_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, tasks, _ = make_direct_services(tmp_path)
    unc = Path(r"\\server\share\video.mp4")
    original_is_file = Path.is_file

    def guarded_is_file(path: Path) -> bool:
        if str(path).startswith("\\\\server\\"):
            raise AssertionError("UNC path was touched")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", guarded_is_file)
    try:
        with pytest.raises(InvalidTaskOperation, match="existing absolute file"):
            tasks.create_task(
                sources=[{"kind": "local_media", "locator": str(unc)}],
                options={"notes_enabled": False},
            )
    finally:
        session.bind.dispose()
        session.close()
