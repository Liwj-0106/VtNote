from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Barrier, Event, Thread

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
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
        assert (
            stored.pipeline_snapshot_json["notes"]["custom_prompt_envelope"]
            is None
        )
        assert task.pipeline_snapshot["notes"]["has_custom_prompt"] is False
        assert "custom_prompt_envelope" not in task.pipeline_snapshot["notes"]
        assert "cloud-secret" not in json.dumps(stored.pipeline_snapshot_json)
        assert "chat-secret" not in json.dumps(stored.pipeline_snapshot_json)

        configuration.update_profile(notes_id, model="changed-after-enqueue")
        reloaded = tasks.get_task(task.id)
        assert reloaded.pipeline_snapshot["notes"]["profile"]["model"] == "notes-model"
        assert "custom_prompt_envelope" not in reloaded.pipeline_snapshot["notes"]
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


def test_cancel_already_user_canceled_task_returns_current_view(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        first = tasks.cancel_task(created.id)
        repeated = tasks.cancel_task(created.id)

        assert repeated == first
        assert repeated.status == "canceled"
        assert len(repeated.items[0].stage_runs) == len(created.items[0].stage_runs)
        assert {run.status for run in repeated.items[0].stage_runs} == {"canceled"}
    finally:
        session.bind.dispose()
        session.close()


def test_cancel_rejects_canceled_task_without_user_reason(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        task = session.get(TaskRecord, created.id)
        assert task is not None
        task.status = "canceled"
        task.terminal_reason_code = None
        session.commit()

        with pytest.raises(InvalidTaskOperation, match="terminal"):
            tasks.cancel_task(created.id)
    finally:
        session.bind.dispose()
        session.close()


def test_cancel_does_not_overwrite_concurrently_completed_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    other_session = Session(session.bind)
    task_loaded = Event()
    allow_cancel = Event()
    outcomes: list[tuple[str, str]] = []
    cancel_thread: Thread | None = None
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        original_load = tasks._load_task

        def pausing_load(task_id: str) -> TaskRecord:
            task = original_load(task_id)
            if not task_loaded.is_set():
                task_loaded.set()
                if not allow_cancel.wait(timeout=5):
                    raise RuntimeError("test timed out waiting to resume cancellation")
            return task

        monkeypatch.setattr(tasks, "_load_task", pausing_load)

        def cancel_in_thread() -> None:
            try:
                view = tasks.cancel_task(created.id)
                outcomes.append(("returned", view.status))
            except InvalidTaskOperation as error:
                outcomes.append(("rejected", str(error)))
            except Exception as error:  # pragma: no cover - surfaced by the assertion below
                outcomes.append(("unexpected", repr(error)))

        cancel_thread = Thread(target=cancel_in_thread)
        cancel_thread.start()
        assert task_loaded.wait(timeout=5)

        completed = other_session.get(TaskRecord, created.id)
        assert completed is not None
        completed.status = "completed"
        for item in completed.items:
            item.status = "completed"
            for stage in item.stage_runs:
                stage.status = "completed"
        other_session.commit()

        allow_cancel.set()
        cancel_thread.join(timeout=5)
        assert not cancel_thread.is_alive()
        assert outcomes == [("rejected", "terminal task cannot be canceled")]

        session.expire_all()
        stored = session.get(TaskRecord, created.id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.terminal_reason_code is None
    finally:
        allow_cancel.set()
        if cancel_thread is not None:
            cancel_thread.join(timeout=5)
        other_session.close()
        session.bind.dispose()
        session.close()


@pytest.mark.parametrize("terminal_status", ["completed", "completed_with_warnings", "failed"])
def test_cancel_rejects_non_cancel_terminal_tasks(
    tmp_path: Path, terminal_status: str,
) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        task = session.get(TaskRecord, created.id)
        assert task is not None
        task.status = terminal_status
        session.commit()

        with pytest.raises(InvalidTaskOperation, match="terminal"):
            tasks.cancel_task(created.id)
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
        retried = tasks.retry_stage(
            item.id,
            "transcribe",
            expected_attempt=1,
            override={"schema_version": 1, "strategy": "same"},
        )
        attempts = [run for run in retried.stage_runs if run.stage == "transcribe"]
        assert [(run.attempt, run.status) for run in attempts] == [(1, "failed"), (2, "queued")]
        assert all(run.attempt == 1 for run in retried.stage_runs if run.stage != "transcribe")

        with pytest.raises(InvalidTaskOperation):
            tasks.retry_stage(
                item.id,
                "source",
                expected_attempt=1,
                override={"schema_version": 1, "strategy": "same"},
            )
    finally:
        session.bind.dispose()
        session.close()


def test_retry_after_user_cancel_clears_terminal_reason(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        canceled = tasks.cancel_task(created.id)
        assert canceled.status == "canceled"

        retried = tasks.retry_stage(
            canceled.items[0].id,
            "source",
            expected_attempt=1,
            override={"schema_version": 1, "strategy": "same"},
        )
        assert retried.status == "queued"
        session.expire_all()
        stored = session.get(TaskRecord, created.id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.terminal_reason_code is None
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
            tasks.retry_stage(
                item.id,
                "transcribe",
                expected_attempt=1,
                override={"schema_version": 1, "strategy": "same"},
            )

        source.status = "running"
        session.commit()
        with pytest.raises(InvalidTaskOperation, match="active"):
            tasks.retry_stage(
                item.id,
                "transcribe",
                expected_attempt=1,
                override={"schema_version": 1, "strategy": "same"},
            )
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
            tasks.retry_stage(
                item.id,
                "transcribe",
                expected_attempt=1,
                override={"schema_version": 1, "strategy": "same"},
            )
    finally:
        session.bind.dispose()
        session.close()


def test_retry_maps_sqlite_busy_race_to_refresh_conflict(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        _, item, _ = _prepare_failed_transcribe(tasks, session)

        def locked(_: Session) -> None:
            raise OperationalError(
                "insert retry",
                {},
                sqlite3.OperationalError("database is locked"),
            )

        event.listen(session, "before_commit", locked, once=True)
        with pytest.raises(InvalidTaskOperation, match="conflicted"):
            tasks.retry_stage(
                item.id,
                "transcribe",
                expected_attempt=1,
                override={"schema_version": 1, "strategy": "same"},
            )
    finally:
        session.bind.dispose()
        session.close()


def test_retry_real_two_session_cas_creates_only_one_attempt(
    tmp_path: Path,
) -> None:
    tasks, _, first_session, paths = make_services(tmp_path)
    engine = first_session.bind
    assert engine is not None
    second_session = Session(engine)
    second_configuration = ConfigurationService(
        second_session, MemorySecretStore(), paths=paths
    )
    second_tasks = TaskService(
        second_session,
        second_configuration,
        paths,
        SourceUrlPolicy(PublicResolver()),
    )
    try:
        _, item, _ = _prepare_failed_transcribe(tasks, first_session)
        barrier = Barrier(2)
        event.listen(
            first_session,
            "before_flush",
            lambda *_: barrier.wait(timeout=5),
            once=True,
        )
        event.listen(
            second_session,
            "before_flush",
            lambda *_: barrier.wait(timeout=5),
            once=True,
        )
        outcomes: list[str] = []
        unexpected: list[BaseException] = []

        def retry(service: TaskService) -> None:
            try:
                service.retry_stage(
                    item.id,
                    "transcribe",
                    expected_attempt=1,
                    override={"schema_version": 1, "strategy": "same"},
                )
            except InvalidTaskOperation as error:
                assert "refresh" in str(error)
                outcomes.append("conflict")
            except BaseException as error:
                unexpected.append(error)
            else:
                outcomes.append("created")

        threads = [Thread(target=retry, args=(service,)) for service in (tasks, second_tasks)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert unexpected == []
        assert sorted(outcomes) == ["conflict", "created"]
        first_session.expire_all()
        stored = first_session.get(ItemRecord, item.id)
        assert stored is not None
        assert [
            run.attempt for run in stored.stage_runs if run.stage == "transcribe"
        ] == [1, 2]
    finally:
        second_session.close()
        first_session.close()
        engine.dispose()


def _prepare_failed_transcribe(
    tasks: TaskService,
    session: Session,
    *,
    unknown: bool = False,
) -> tuple[TaskRecord, ItemRecord, StageRunRecord]:
    created = tasks.create_task(
        sources=[{"kind": "url", "locator": "https://youtu.be/retry"}]
    )
    task = session.get(TaskRecord, created.id)
    assert task is not None
    item = task.items[0]
    source = next(run for run in item.stage_runs if run.stage == "source")
    transcribe = next(run for run in item.stage_runs if run.stage == "transcribe")
    source.status = "completed"
    transcribe.status = "failed"
    if unknown:
        transcribe.external_submission_state = "submission_unknown"
    session.commit()
    return task, item, transcribe


def test_retry_rejects_stale_expected_attempt(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        _, item, _ = _prepare_failed_transcribe(tasks, session)

        with pytest.raises(InvalidTaskOperation, match="refresh"):
            tasks.retry_stage(
                item.id,
                "transcribe",
                expected_attempt=2,
                override={"schema_version": 1, "strategy": "same"},
            )

        session.expire_all()
        stored = session.get(ItemRecord, item.id)
        assert stored is not None
        assert [
            run.attempt for run in stored.stage_runs if run.stage == "transcribe"
        ] == [1]
    finally:
        session.bind.dispose()
        session.close()


def test_unknown_cloud_rejects_same_retry(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        _, item, unknown = _prepare_failed_transcribe(
            tasks, session, unknown=True
        )

        with pytest.raises(InvalidTaskOperation, match="submission_unknown"):
            tasks.retry_stage(
                item.id,
                "transcribe",
                expected_attempt=1,
                override={"schema_version": 1, "strategy": "same"},
            )

        session.refresh(unknown)
        assert unknown.status == "failed"
        assert unknown.external_submission_state == "submission_unknown"
    finally:
        session.bind.dispose()
        session.close()


def test_unknown_cloud_local_retry_snapshots_local_override(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        task, item, unknown = _prepare_failed_transcribe(
            tasks, session, unknown=True
        )
        original_snapshot = json.loads(json.dumps(task.pipeline_snapshot_json))
        override = tasks.build_retry_override(strategy="local")

        retried = tasks.retry_stage(
            item.id,
            "transcribe",
            expected_attempt=1,
            override=override,
        )

        session.expire_all()
        stored = session.get(ItemRecord, item.id)
        assert stored is not None
        attempts = [
            run for run in stored.stage_runs if run.stage == "transcribe"
        ]
        assert attempts[0].id == unknown.id
        assert attempts[0].external_submission_state == "submission_unknown"
        assert attempts[1].retry_override_json == {
            "schema_version": 1,
            "strategy": "local",
            "asr": {"mode": "local", "profile": None},
        }
        assert stored.task.pipeline_snapshot_json == original_snapshot
        assert "retry_override" not in retried.stage_runs[-1].model_dump()
    finally:
        session.bind.dispose()
        session.close()


def test_unknown_cloud_requires_charge_ack_for_cloud_retry(tmp_path: Path) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        cloud_id, _, _ = configure_profiles(configuration)
        _, item, _ = _prepare_failed_transcribe(tasks, session, unknown=True)
        profile = configuration.snapshot_profile(cloud_id)

        with pytest.raises(InvalidTaskOperation, match="possible charge"):
            tasks.build_retry_override(
                strategy="cloud_confirmed",
                cloud_profile_id=cloud_id,
                connection_revision=profile["connection_revision"],
                profile_revision=profile["profile_revision"],
                acknowledge_possible_charge=False,
            )

        confirmed = tasks.build_retry_override(
            strategy="cloud_confirmed",
            cloud_profile_id=cloud_id,
            connection_revision=profile["connection_revision"],
            profile_revision=profile["profile_revision"],
            acknowledge_possible_charge=True,
        )
        with pytest.raises(InvalidTaskOperation, match="possible charge"):
            tasks.retry_stage(
                item.id,
                "transcribe",
                expected_attempt=1,
                override=confirmed,
            )

        assert [
            run.attempt for run in item.stage_runs if run.stage == "transcribe"
        ] == [1]
    finally:
        session.bind.dispose()
        session.close()


def test_cloud_confirmed_requires_current_tested_authorized_revisions(
    tmp_path: Path,
) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        cloud_id, _, _ = configure_profiles(configuration)
        _, item, _ = _prepare_failed_transcribe(tasks, session, unknown=True)
        original = configuration.snapshot_profile(cloud_id)

        with pytest.raises(InvalidTaskOperation, match="revision"):
            tasks.build_retry_override(
                strategy="cloud_confirmed",
                cloud_profile_id=cloud_id,
                connection_revision=original["connection_revision"] + 1,
                profile_revision=original["profile_revision"],
                acknowledge_possible_charge=True,
            )
        with pytest.raises(InvalidTaskOperation, match="revision"):
            tasks.build_retry_override(
                strategy="cloud_confirmed",
                cloud_profile_id=cloud_id,
                connection_revision=original["connection_revision"],
                profile_revision=original["profile_revision"] + 1,
                acknowledge_possible_charge=True,
            )

        configuration.update_profile(cloud_id, model="retry-model")
        changed = configuration.snapshot_profile(cloud_id)
        with pytest.raises(InvalidTaskOperation, match="successful test"):
            tasks.build_retry_override(
                strategy="cloud_confirmed",
                cloud_profile_id=cloud_id,
                connection_revision=changed["connection_revision"],
                profile_revision=changed["profile_revision"],
                acknowledge_possible_charge=True,
            )

        configuration.record_profile_test(cloud_id, ok=True, message="ok")
        with pytest.raises(InvalidTaskOperation, match="upload authorization"):
            tasks.build_retry_override(
                strategy="cloud_confirmed",
                cloud_profile_id=cloud_id,
                connection_revision=changed["connection_revision"],
                profile_revision=changed["profile_revision"],
                acknowledge_possible_charge=True,
            )

        configuration.authorize_cloud_upload(cloud_id)
        override = tasks.build_retry_override(
            strategy="cloud_confirmed",
            cloud_profile_id=cloud_id,
            connection_revision=changed["connection_revision"],
            profile_revision=changed["profile_revision"],
            acknowledge_possible_charge=True,
        )
        retried = tasks.retry_stage(
            item.id,
            "transcribe",
            expected_attempt=1,
            override=override,
            acknowledge_possible_charge=True,
        )
        new_attempt = next(
            run for run in retried.stage_runs
            if run.stage == "transcribe" and run.attempt == 2
        )
        evidence = tasks.record_stage_evidence(
            new_attempt.id, {"model": "retry-model"}
        )
        assert evidence.execution_evidence == {"model": "retry-model"}
        stored_item = session.get(ItemRecord, item.id)
        assert stored_item is not None
        for stale_model in (
            original["model"],
            stored_item.task.pipeline_snapshot_json["local_whisper"]["model"],
        ):
            assert stale_model != "retry-model"
            with pytest.raises(InvalidTaskOperation, match="model"):
                tasks.record_stage_evidence(
                    new_attempt.id, {"model": stale_model}
                )
    finally:
        session.bind.dispose()
        session.close()


def test_retry_override_never_reads_current_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        original_cloud_id, _, _ = configure_profiles(configuration)
        task, item, _ = _prepare_failed_transcribe(tasks, session, unknown=True)
        connection_id = configuration.get_profile(original_cloud_id).connection_id
        selected = configuration.create_profile(
            name="Explicit retry profile",
            purpose="cloud_asr",
            connection_id=connection_id,
            model="explicit-retry-model",
        )
        configuration.record_profile_test(selected.id, ok=True, message="ok")
        configuration.authorize_cloud_upload(selected.id)
        configuration.update_defaults(cloud_asr_profile_id=original_cloud_id)
        selected_snapshot = configuration.snapshot_profile(selected.id)
        original_task_snapshot = json.loads(
            json.dumps(task.pipeline_snapshot_json)
        )

        def fail_if_defaults_are_read():
            raise AssertionError("retry must not read current defaults")

        monkeypatch.setattr(configuration, "get_defaults", fail_if_defaults_are_read)
        override = tasks.build_retry_override(
            strategy="cloud_confirmed",
            cloud_profile_id=selected.id,
            connection_revision=selected_snapshot["connection_revision"],
            profile_revision=selected_snapshot["profile_revision"],
            acknowledge_possible_charge=True,
        )
        tasks.retry_stage(
            item.id,
            "transcribe",
            expected_attempt=1,
            override=override,
            acknowledge_possible_charge=True,
        )

        session.expire_all()
        stored = session.get(ItemRecord, item.id)
        assert stored is not None
        retry = max(
            (run for run in stored.stage_runs if run.stage == "transcribe"),
            key=lambda run: run.attempt,
        )
        assert retry.retry_override_json["asr"]["profile"]["id"] == selected.id
        assert retry.retry_override_json["asr"]["profile"]["model"] == (
            "explicit-retry-model"
        )
        assert stored.task.pipeline_snapshot_json == original_task_snapshot
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


def test_stage_progress_and_execution_evidence_round_trip(tmp_path: Path) -> None:
    tasks, _, session, _ = make_services(tmp_path)
    try:
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        stage_id = next(
            run.id
            for run in created.items[0].stage_runs
            if run.stage == "transcribe"
        )

        progress_view = tasks.record_stage_progress(
            stage_id,
            {
                "current": 3,
                "total": 10,
                "unit": "segments",
                "message_code": "transcribing_segments",
            },
        )
        evidence_view = tasks.record_stage_evidence(
            stage_id,
            {
                "source_method": "local_asr",
                "asr_route": "local",
                "provider": "faster_whisper",
                "model": "large-v3-turbo",
            },
            provider_status_code="waiting",
        )

        assert progress_view.progress == {
            "current": 3,
            "total": 10,
            "unit": "segments",
            "message_code": "transcribing_segments",
        }
        assert evidence_view.execution_evidence == {
            "source_method": "local_asr",
            "asr_route": "local",
            "provider": "faster_whisper",
            "model": "large-v3-turbo",
        }
        assert evidence_view.provider_status_code == "waiting"

        session.expire_all()
        stored = next(
            run
            for run in tasks.get_task(created.id).items[0].stage_runs
            if run.stage == "transcribe"
        )
        assert stored.progress == progress_view.progress
        assert stored.execution_evidence == evidence_view.execution_evidence
        assert stored.provider_status_code == "waiting"
    finally:
        session.bind.dispose()
        session.close()


def test_stage_evidence_rejects_unbounded_or_sensitive_values(tmp_path: Path) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        configuration.create_connection(
            name="Chat",
            protocol="openai_compatible",
            base_url="https://api.example/v1",
            parameters={},
            secret="transcribing_segments",
        )
        configuration.create_connection(
            name="Fallback",
            protocol="openai_compatible",
            base_url="http://127.0.0.1:8001/v1",
            parameters={},
            secret="cloud_rate_limited",
        )
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        stage_id = next(
            run.id
            for run in created.items[0].stage_runs
            if run.stage == "transcribe"
        )

        with pytest.raises(InvalidTaskOperation, match="sensitive"):
            tasks.record_stage_progress(
                stage_id,
                {
                    "current": 1,
                    "total": 2,
                    "unit": "items",
                    "message_code": "transcribing_segments",
                },
            )
        with pytest.raises(InvalidTaskOperation, match="sensitive"):
            tasks.record_stage_evidence(
                stage_id,
                {
                    "provider": "faster_whisper",
                    "model": "large-v3-turbo",
                    "fallback_reason": "cloud_rate_limited",
                },
                provider_status_code="waiting",
            )
        with pytest.raises(InvalidTaskOperation, match="provider status"):
            tasks.record_stage_evidence(
                stage_id,
                {"provider": "tencent_recording_asr"},
                provider_status_code="x" * 129,
            )

        session.expire_all()
        stored = session.get(StageRunRecord, stage_id)
        assert stored is not None
        assert stored.progress_json is None
        assert stored.execution_evidence_json is None
        assert stored.provider_status_code is None
    finally:
        session.bind.dispose()
        session.close()


def test_stage_view_fails_closed_for_invalid_persisted_runtime_fields(
    tmp_path: Path,
) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        configuration.create_connection(
            name="Local test",
            protocol="openai_compatible",
            base_url="http://127.0.0.1:8000/v1",
            parameters={},
            secret="transcribing_segments",
        )
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        stage_id = created.items[0].stage_runs[0].id
        stored = session.get(StageRunRecord, stage_id)
        assert stored is not None
        stored.progress_json = {
            "current": 1,
            "total": 2,
            "unit": "items",
            "message_code": "transcribing_segments",
        }
        stored.execution_evidence_json = {
            "provider_message": "raw provider response with sensitive-token"
        }
        stored.provider_status_code = "AKIDEXAMPLE1234567890"
        session.commit()

        public_stage = tasks.get_task(created.id).items[0].stage_runs[0]
        assert public_stage.progress is None
        assert public_stage.execution_evidence is None
        assert public_stage.provider_status_code is None
        assert "sensitive-token" not in public_stage.model_dump_json()
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

        retried = tasks.retry_stage(
            item.id,
            "notes",
            expected_attempt=1,
            override={"schema_version": 1, "strategy": "same"},
        )
        attempts = [run for run in retried.stage_runs if run.stage == "notes"]
        assert [(run.attempt, run.status) for run in attempts] == [
            (1, "failed"), (2, "queued")
        ]
    finally:
        session.bind.dispose()
        session.close()


@pytest.mark.parametrize(
    ("retry_stage", "parallel_stage"),
    [("notes", "translate"), ("translate", "notes")],
)
def test_translate_and_notes_retry_in_parallel_without_downgrading_running_state(
    tmp_path: Path, retry_stage: str, parallel_stage: str,
) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        connection = configuration.create_connection(
            name="Chat",
            protocol="openai_compatible",
            base_url="https://api.example/v1",
            parameters={},
        )
        translation = configuration.create_profile(
            name="Translation",
            purpose="translation",
            connection_id=connection.id,
            model="translate",
        )
        notes = configuration.create_profile(
            name="Notes",
            purpose="notes",
            connection_id=connection.id,
            model="notes",
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
        task_row = session.get(TaskRecord, created.id)
        assert task_row is not None
        item = task_row.items[0]
        for run in item.stage_runs:
            if run.stage in {"source", "transcribe"}:
                run.status = "completed"
            elif run.stage == retry_stage:
                run.status = "failed"
            elif run.stage == parallel_stage:
                run.status = "running"
        item.status = "running"
        task_row.status = "running"
        session.commit()

        retried = tasks.retry_stage(
            item.id,
            retry_stage,
            expected_attempt=1,
            override={"schema_version": 1, "strategy": "same"},
        )
        assert retried.status == "running"
        assert [
            (run.attempt, run.status)
            for run in retried.stage_runs
            if run.stage == retry_stage
        ] == [(1, "failed"), (2, "queued")]
        assert next(
            run for run in retried.stage_runs if run.stage == parallel_stage
        ).status == "running"
        session.refresh(task_row)
        assert task_row.status == "running"
    finally:
        session.close()
        session.bind.dispose()


@pytest.mark.parametrize(
    ("retry_stage", "active_dependent"),
    [("source", "notes"), ("transcribe", "translate")],
)
def test_upstream_retry_conflicts_with_active_dependent_stage(
    tmp_path: Path, retry_stage: str, active_dependent: str,
) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        connection = configuration.create_connection(
            name="Chat",
            protocol="openai_compatible",
            base_url="https://api.example/v1",
            parameters={},
        )
        translation = configuration.create_profile(
            name="Translation",
            purpose="translation",
            connection_id=connection.id,
            model="translate",
        )
        notes = configuration.create_profile(
            name="Notes",
            purpose="notes",
            connection_id=connection.id,
            model="notes",
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
        for run in item.stage_runs:
            run.status = "completed"
        next(run for run in item.stage_runs if run.stage == retry_stage).status = "failed"
        next(run for run in item.stage_runs if run.stage == active_dependent).status = "running"
        session.commit()

        with pytest.raises(InvalidTaskOperation, match="active"):
            tasks.retry_stage(
                item.id,
                retry_stage,
                expected_attempt=1,
                override={"schema_version": 1, "strategy": "same"},
            )
    finally:
        session.close()
        session.bind.dispose()


def test_same_stage_active_attempt_blocks_duplicate_retry(tmp_path: Path) -> None:
    tasks, configuration, session, _ = make_services(tmp_path)
    try:
        connection = configuration.create_connection(
            name="Chat",
            protocol="openai_compatible",
            base_url="https://api.example/v1",
            parameters={},
        )
        notes = configuration.create_profile(
            name="Notes",
            purpose="notes",
            connection_id=connection.id,
            model="notes",
        )
        configuration.record_profile_test(notes.id, ok=True, message="ok")
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        item = session.get(ItemRecord, created.items[0].id)
        assert item is not None
        for run in item.stage_runs:
            run.status = "completed"
        notes_run = next(run for run in item.stage_runs if run.stage == "notes")
        notes_run.status = "failed"
        item.stage_runs.append(
            StageRunRecord(stage="notes", attempt=2, status="running")
        )
        session.commit()

        with pytest.raises(InvalidTaskOperation, match="active"):
            tasks.retry_stage(
                item.id,
                "notes",
                expected_attempt=2,
                override={"schema_version": 1, "strategy": "same"},
            )
    finally:
        session.close()
        session.bind.dispose()


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
