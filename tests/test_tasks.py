from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vtnote.artifacts import write_transcript_json, write_translation_json
from vtnote.config import Settings
from vtnote.configuration import ConfigurationService
from vtnote.database import initialize_database
from vtnote.paths import StoragePaths
from vtnote.models import ItemRecord, ProviderConnectionRecord, StageRunRecord, TaskRecord
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    Translation,
    TranslationEntry,
    transcript_sha256,
)
from vtnote.secrets import MemorySecretStore
from vtnote.tasks import InvalidTaskOperation, TaskService
from vtnote.url_security import SourceUrlPolicy


class PublicResolver:
    def resolve(self, host: str) -> list[str]:
        return ["142.250.72.14"]


def make_services(tmp_path: Path) -> tuple[TaskService, ConfigurationService, Session, StoragePaths]:
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    engine = initialize_database(paths.database)
    session = Session(engine)
    configuration = ConfigurationService(session, MemorySecretStore(), paths=paths)
    tasks = TaskService(session, configuration, paths, SourceUrlPolicy(PublicResolver()))
    return tasks, configuration, session, paths


def assert_no_secret_fields(value: object) -> None:
    if isinstance(value, dict):
        assert "secret" not in value
        assert "credential_ref" not in value
        for child in value.values():
            assert_no_secret_fields(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_secret_fields(child)


def configure_profiles(configuration: ConfigurationService) -> tuple[str, str, str]:
    cloud_connection = configuration.create_connection(
        name="Volc",
        protocol="volc_bigasr_flash",
        base_url="https://openspeech.bytedance.com",
        parameters={},
        secret="cloud-secret",
    )
    cloud = configuration.create_profile(
        name="Flash", purpose="cloud_asr", connection_id=cloud_connection.id, model="bigmodel"
    )
    configuration.record_profile_test(cloud.id, ok=True, message="ok")
    configuration.authorize_cloud_upload(cloud.id)

    chat_connection = configuration.create_connection(
        name="Chat",
        protocol="openai_compatible",
        base_url="https://api.example.com/v1",
        parameters={},
        secret="chat-secret",
    )
    translation = configuration.create_profile(
        name="Translate",
        purpose="translation",
        connection_id=chat_connection.id,
        model="translate-model",
    )
    notes = configuration.create_profile(
        name="Notes", purpose="notes", connection_id=chat_connection.id, model="notes-model"
    )
    configuration.record_profile_test(translation.id, ok=True, message="ok")
    configuration.record_profile_test(notes.id, ok=True, message="ok")
    configuration.update_defaults(
        asr_mode="cloud",
        cloud_asr_profile_id=cloud.id,
        translation_enabled=True,
        translation_profile_id=translation.id,
        notes_enabled=True,
        notes_profile_id=notes.id,
    )
    return cloud.id, translation.id, notes.id


def test_enqueue_creates_durable_rows_and_immutable_redacted_snapshot(tmp_path: Path) -> None:
    tasks, configuration, session, paths = make_services(tmp_path)
    try:
        cloud_id, translation_id, notes_id = configure_profiles(configuration)

        task = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://www.youtube.com/watch?v=abc"}]
        )
        assert task.status == "queued"
        assert len(task.items) == 1
        assert [stage.stage for stage in task.items[0].stage_runs] == [
            "source", "transcribe", "translate", "notes"
        ]
        assert all(stage.status == "queued" for stage in task.items[0].stage_runs)
        assert task.pipeline_snapshot["asr"]["profile"]["id"] == cloud_id
        assert task.pipeline_snapshot["translation"]["profile"]["id"] == translation_id
        assert task.pipeline_snapshot["notes"]["profile"]["id"] == notes_id
        assert task.pipeline_snapshot["local_whisper"] == {
            "model": "large-v3-turbo",
            "device": "auto",
            "compute_type": "int8_float16",
            "vad_filter": True,
            "model_root": str(paths.durable("models", "faster-whisper")),
            "cache_root": str(paths.runtime("models", "faster-whisper")),
        }
        assert_no_secret_fields(task.pipeline_snapshot)
        stored = session.get(TaskRecord, task.id)
        assert stored is not None
        assert stored.pipeline_snapshot_json == task.pipeline_snapshot
        assert "cloud-secret" not in json.dumps(stored.pipeline_snapshot_json)
        assert "chat-secret" not in json.dumps(stored.pipeline_snapshot_json)

        configuration.update_profile(notes_id, model="changed-after-enqueue")
        reloaded = tasks.get_task(task.id)
        assert reloaded.pipeline_snapshot["notes"]["profile"]["model"] == "notes-model"
    finally:
        session.bind.dispose()
        session.close()


def test_enqueue_never_calls_a_worker(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        task = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        assert task.status == "queued"
    finally:
        session.bind.dispose()
        session.close()


def test_auto_mode_omits_cloud_profile_until_upload_is_authorized(tmp_path: Path) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        connection = configuration.create_connection(
            name="Volc", protocol="volc_bigasr_flash",
            base_url="https://openspeech.bytedance.com", parameters={}, secret="key"
        )
        profile = configuration.create_profile(
            name="Flash", purpose="cloud_asr", connection_id=connection.id, model="bigmodel"
        )
        configuration.record_profile_test(profile.id, ok=True, message="ok")
        configuration.update_defaults(asr_mode="auto", cloud_asr_profile_id=profile.id)

        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        assert created.pipeline_snapshot["asr"] == {"mode": "auto", "profile": None}
        assert created.pipeline_snapshot["local_whisper"]["model"] == "large-v3-turbo"
    finally:
        session.bind.dispose()
        session.close()


def test_cancel_queued_task_cancels_items_and_stages(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        canceled = tasks.cancel_task(created.id)
        assert canceled.status == "canceled"
        assert canceled.items[0].status == "canceled"
        assert {run.status for run in canceled.items[0].stage_runs} == {"canceled"}
    finally:
        session.bind.dispose()
        session.close()


def test_cancel_running_task_requests_cooperative_cancel(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        task_row = session.get(TaskRecord, created.id)
        assert task_row is not None
        task_row.status = "running"
        task_row.items[0].status = "running"
        task_row.items[0].stage_runs[0].status = "running"
        session.commit()
        canceled = tasks.cancel_task(created.id)
        assert canceled.status == "cancel_requested"
        assert canceled.items[0].status == "cancel_requested"
        assert canceled.items[0].stage_runs[0].status == "cancel_requested"
        repeated = tasks.cancel_task(created.id)
        assert repeated.status == "cancel_requested"
        assert repeated.items[0].status == "cancel_requested"
        assert repeated.items[0].stage_runs[0].status == "cancel_requested"
    finally:
        session.bind.dispose()
        session.close()


def test_retry_is_stage_only_and_increments_attempt(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        item = created.items[0]
        item_row = session.get(ItemRecord, item.id)
        assert item_row is not None
        next(run for run in item_row.stage_runs if run.stage == "source").status = "completed"
        next(run for run in item_row.stage_runs if run.stage == "transcribe").status = "failed"
        session.commit()
        retried = tasks.retry_stage(item.id, "transcribe")
        attempts = [run for run in retried.stage_runs if run.stage == "transcribe"]
        assert [(run.attempt, run.status) for run in attempts] == [(1, "failed"), (2, "queued")]
        assert all(run.attempt == 1 for run in retried.stage_runs if run.stage != "transcribe")

        with pytest.raises(InvalidTaskOperation):
            tasks.retry_stage(item.id, "source")
    finally:
        session.bind.dispose()
        session.close()


def test_retry_rejects_active_work_and_incomplete_prerequisites(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        item = session.get(ItemRecord, created.items[0].id)
        assert item is not None
        source = next(run for run in item.stage_runs if run.stage == "source")
        transcribe = next(run for run in item.stage_runs if run.stage == "transcribe")
        source.status = "canceled"
        transcribe.status = "failed"
        session.commit()
        with pytest.raises(InvalidTaskOperation, match="prerequisite"):
            tasks.retry_stage(item.id, "transcribe")

        source.status = "running"
        session.commit()
        with pytest.raises(InvalidTaskOperation, match="active"):
            tasks.retry_stage(item.id, "transcribe")
    finally:
        session.bind.dispose()
        session.close()


def test_retry_maps_duplicate_attempt_race_to_invalid_operation(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        item = session.get(ItemRecord, created.items[0].id)
        assert item is not None
        next(run for run in item.stage_runs if run.stage == "source").status = "completed"
        next(run for run in item.stage_runs if run.stage == "transcribe").status = "failed"
        session.commit()

        def conflict(_: Session) -> None:
            raise IntegrityError("insert retry", {}, RuntimeError("duplicate"))

        event.listen(session, "before_commit", conflict, once=True)
        with pytest.raises(InvalidTaskOperation, match="conflicted"):
            tasks.retry_stage(item.id, "transcribe")
    finally:
        session.bind.dispose()
        session.close()


def test_public_task_view_redacts_local_path_and_stage_diagnostics(tmp_path: Path) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        source = tmp_path / "private-folder" / "video.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"media")
        connection = configuration.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example/v1", parameters={}, secret="known-secret"
        )
        stored_connection = session.get(ProviderConnectionRecord, connection.id)
        assert stored_connection is not None
        created = tasks.create_task(
            sources=[{"kind": "local_media", "locator": str(source)}]
        )
        item = session.get(ItemRecord, created.items[0].id)
        assert item is not None
        item.stage_runs[0].error_message = '{"access_token":"issued-token"}'
        item.stage_runs[0].warning = (
            f"secret={stored_connection.credential_ref} known-secret"
        )
        session.commit()

        public = tasks.get_task(created.id)
        assert public.items[0].source_locator == "video.mp4"
        assert "issued-token" not in (public.items[0].stage_runs[0].error_message or "")
        assert "known-secret" not in (public.items[0].stage_runs[0].warning or "")
        assert stored_connection.credential_ref not in (
            public.items[0].stage_runs[0].warning or ""
        )
        session.refresh(item)
        assert item.source_locator == str(source)
    finally:
        session.bind.dispose()
        session.close()


def test_stage_diagnostic_write_boundary_sanitizes_before_database_commit(
    tmp_path: Path,
) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        connection = configuration.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example/v1", parameters={}, secret="database-secret"
        )
        stored_connection = session.get(ProviderConnectionRecord, connection.id)
        assert stored_connection is not None
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        stage_id = created.items[0].stage_runs[0].id
        tasks.record_stage_failure(
            stage_id,
            error_code="source_failed",
            message=(
                '{"access_token":"issued-token"} database-secret '
                f"{stored_connection.credential_ref}"
            ),
        )
        tasks.record_stage_warning(
            stage_id, "Authorization: Bearer warning-token database-secret"
        )

        session.expire_all()
        stored = session.get(StageRunRecord, stage_id)
        assert stored is not None
        persisted = f"{stored.error_message} {stored.warning}"
        assert "issued-token" not in persisted
        assert "warning-token" not in persisted
        assert "database-secret" not in persisted
        assert stored_connection.credential_ref not in persisted
        assert stored.status == "failed"
        assert stored.error_code == "source_failed"
    finally:
        session.bind.dispose()
        session.close()


@pytest.mark.parametrize("translation_status", ["failed", "canceled"])
def test_notes_retry_depends_on_transcription_not_translation(
    tmp_path: Path, translation_status: str,
) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        connection = configuration.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example/v1", parameters={}
        )
        translation = configuration.create_profile(
            name="Translation", purpose="translation",
            connection_id=connection.id, model="translate"
        )
        notes = configuration.create_profile(
            name="Notes", purpose="notes", connection_id=connection.id, model="notes"
        )
        configuration.record_profile_test(translation.id, ok=True, message="ok")
        configuration.record_profile_test(notes.id, ok=True, message="ok")
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}],
            options={
                "translation_enabled": True,
                "translation_profile_id": translation.id,
                "notes_enabled": True,
                "notes_profile_id": notes.id,
            },
        )
        item = session.get(ItemRecord, created.items[0].id)
        assert item is not None
        statuses = {
            "source": "completed", "transcribe": "completed",
            "translate": translation_status, "notes": "failed",
        }
        for run in item.stage_runs:
            run.status = statuses[run.stage]
        session.commit()

        retried = tasks.retry_stage(item.id, "notes")
        attempts = [run for run in retried.stage_runs if run.stage == "notes"]
        assert [(run.attempt, run.status) for run in attempts] == [
            (1, "failed"), (2, "queued")
        ]
    finally:
        session.bind.dispose()
        session.close()


def make_artifacts(paths: StoragePaths, item_id: str) -> None:
    transcript = Transcript(
        language="en",
        duration_ms=1_000,
        provenance=Provenance(method=ProvenanceMethod.PLATFORM_SUBTITLE, provider="youtube"),
        segments=[TranscriptSegment(id="seg_000001", start_ms=0, end_ms=1_000, text="Hello")],
    )
    translation = Translation(
        language="zh-Hans",
        source_transcript_sha256=transcript_sha256(transcript),
        entries=[TranslationEntry(cue_id="seg_000001", text="你好")],
    )
    write_transcript_json(paths, item_id, transcript)
    write_translation_json(paths, item_id, translation, transcript)


def test_original_and_translation_exports_are_generated_on_demand(tmp_path: Path) -> None:
    tasks, _, session, paths = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        item_id = created.items[0].id
        make_artifacts(paths, item_id)

        original = tasks.export_item(item_id, variant="original", export_format="srt")
        translated = tasks.export_item(
            item_id, variant="translation", export_format="json", language="zh-Hans"
        )
        assert "Hello" in original
        assert json.loads(translated)["segments"][0]["text"] == "你好"
        assert {path.name for path in (paths.data_root / "items" / item_id).rglob("*") if path.is_file()} == {
            "transcript.json", "zh-Hans.json"
        }
    finally:
        session.bind.dispose()
        session.close()
