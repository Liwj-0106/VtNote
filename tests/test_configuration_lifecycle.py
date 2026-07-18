from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.models import ProviderConnectionRecord, TaskRecord
from vtnote.paths import StoragePaths
from vtnote.secrets import MemorySecretStore
from vtnote.tasks import TaskService
from vtnote.url_security import SourceUrlPolicy


class PublicResolver:
    def resolve(self, host: str) -> list[str]:
        return ["142.250.72.14"]


class RecoveringDeleteSecretStore(MemorySecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_delete = True

    def delete(self, reference: str) -> None:
        if self.fail_delete:
            raise RuntimeError("credential backend delete unavailable")
        super().delete(reference)


def make_services(
    tmp_path: Path, secrets: MemorySecretStore | None = None
) -> tuple[ConfigurationService, TaskService, MemorySecretStore, Session]:
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    engine = initialize_database(paths.database)
    session = Session(engine)
    selected_secrets = secrets or MemorySecretStore()
    configuration = ConfigurationService(session, selected_secrets, paths=paths)
    tasks = TaskService(
        session, configuration, paths, SourceUrlPolicy(PublicResolver())
    )
    return configuration, tasks, selected_secrets, session


def test_secret_rotation_never_exposes_new_secret_with_old_database_config(
    tmp_path: Path,
) -> None:
    configuration, _, secrets, session = make_services(tmp_path)
    connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://old.example/v1", parameters={}, secret="old-key"
    )
    stored = session.get(ProviderConnectionRecord, connection.id)
    assert stored is not None
    old_reference = stored.credential_ref
    observed: list[tuple[str, str, str | None]] = []

    def observe_before_commit(_: Session) -> None:
        with Session(session.bind) as observer:
            row = observer.get(ProviderConnectionRecord, connection.id)
            assert row is not None
            observed.append((row.base_url, row.credential_ref, secrets.get(row.credential_ref)))

    event.listen(session, "before_commit", observe_before_commit, once=True)
    updated = configuration.update_connection(
        connection.id, base_url="https://new.example/v1", secret="new-key"
    )

    assert observed == [("https://old.example/v1", old_reference, "old-key")]
    session.refresh(stored)
    assert stored.credential_ref != old_reference
    assert secrets.get(old_reference) is None
    assert configuration.secret_for_connection(connection.id) == "new-key"
    assert updated.revision == 2
    session.close()
    session.bind.dispose()


def test_clear_secret_rotates_to_a_new_empty_reference(tmp_path: Path) -> None:
    configuration, _, secrets, session = make_services(tmp_path)
    connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}, secret="old-key"
    )
    stored = session.get(ProviderConnectionRecord, connection.id)
    assert stored is not None
    old_reference = stored.credential_ref
    cleared = configuration.update_connection(connection.id, clear_secret=True)
    session.refresh(stored)
    assert stored.credential_ref != old_reference
    assert secrets.get(old_reference) is None
    assert secrets.get(stored.credential_ref) is None
    assert cleared.has_secret is False
    session.close()
    session.bind.dispose()


def test_archiving_keeps_queued_task_profile_and_credential_resolvable(
    tmp_path: Path,
) -> None:
    configuration, tasks, _, session = make_services(tmp_path)
    connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}, secret="task-key"
    )
    profile = configuration.create_profile(
        name="Notes", purpose="notes", connection_id=connection.id, model="notes"
    )
    configuration.record_profile_test(profile.id, ok=True, message="ok")
    task = tasks.create_task(
        sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
    )
    snapshot = task.pipeline_snapshot["notes"]["profile"]
    assert snapshot["connection_id"] == connection.id

    with pytest.raises(InvalidConfiguration, match="active profiles"):
        configuration.delete_connection(connection.id)
    configuration.delete_profile(profile.id)
    configuration.delete_connection(connection.id)

    assert configuration.list_profiles() == []
    assert configuration.list_connections() == []
    with pytest.raises(KeyError):
        configuration.get_profile(profile.id)
    resolved = configuration.resolve_profile_for_execution(profile.id)
    assert resolved["connection_id"] == connection.id
    assert configuration.secret_for_connection(connection.id) == "task-key"
    session.close()
    session.bind.dispose()


def test_archived_names_can_be_reused_and_terminal_tasks_allow_purge(
    tmp_path: Path,
) -> None:
    configuration, tasks, secrets, session = make_services(tmp_path)
    old_connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://old.example/v1", parameters={}, secret="old-task-key"
    )
    old_profile = configuration.create_profile(
        name="Notes", purpose="notes",
        connection_id=old_connection.id, model="old-notes"
    )
    configuration.record_profile_test(old_profile.id, ok=True, message="ok")
    task = tasks.create_task(
        sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
    )
    old_row = session.get(ProviderConnectionRecord, old_connection.id)
    assert old_row is not None
    old_reference = old_row.credential_ref

    configuration.delete_profile(old_profile.id)
    configuration.delete_connection(old_connection.id)
    replacement = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://new.example/v1", parameters={}
    )
    replacement_profile = configuration.create_profile(
        name="Notes", purpose="notes",
        connection_id=replacement.id, model="new-notes"
    )
    assert replacement_profile.name == "Notes"
    assert configuration.resolve_profile_for_execution(old_profile.id)["id"] == old_profile.id

    task_row = session.get(TaskRecord, task.id)
    assert task_row is not None
    task_row.status = "completed"
    session.commit()
    purged = configuration.purge_unreferenced_archived()
    assert purged["profiles"] == 1
    assert purged["connections"] == 1
    with pytest.raises(KeyError):
        configuration.resolve_profile_for_execution(old_profile.id)
    assert secrets.get(old_reference) is None
    session.close()
    session.bind.dispose()


def test_failed_obsolete_credential_delete_is_tracked_and_retryable(
    tmp_path: Path,
) -> None:
    secrets = RecoveringDeleteSecretStore()
    configuration, _, _, session = make_services(tmp_path, secrets)
    connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}, secret="old-secret"
    )
    stored = session.get(ProviderConnectionRecord, connection.id)
    assert stored is not None
    old_reference = stored.credential_ref

    updated = configuration.update_connection(connection.id, secret="new-secret")
    assert updated.cleanup_pending is True
    status = configuration.credential_cleanup_status()
    assert status.cleanup_pending is True
    assert status.pending_count == 1
    assert {
        column["name"]
        for column in inspect(session.bind).get_columns("credential_cleanup")
    } == {
        "credential_ref", "connection_id", "attempts", "last_attempt_at", "created_at"
    }
    assert old_reference in configuration.diagnostic_sensitive_values()
    assert "old-secret" in configuration.diagnostic_sensitive_values()

    secrets.fail_delete = False
    retried = configuration.retry_credential_cleanup()
    assert retried.cleanup_pending is False
    assert retried.pending_count == 0
    assert secrets.get(old_reference) is None
    session.close()
    session.bind.dispose()


def test_unreferenced_delete_hard_purges_and_tracks_failed_credential_cleanup(
    tmp_path: Path,
) -> None:
    secrets = RecoveringDeleteSecretStore()
    configuration, _, _, session = make_services(tmp_path, secrets)
    connection = configuration.create_connection(
        name="Disposable", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}, secret="purge-secret"
    )
    stored = session.get(ProviderConnectionRecord, connection.id)
    assert stored is not None
    reference = stored.credential_ref
    configuration.delete_connection(connection.id)
    with pytest.raises(KeyError):
        configuration.secret_for_connection(connection.id)
    assert configuration.credential_cleanup_status().cleanup_pending is True
    assert reference in configuration.diagnostic_sensitive_values()

    recreated = configuration.create_connection(
        name="Disposable", protocol="openai_compatible",
        base_url="https://new.example/v1", parameters={}
    )
    assert recreated.name == "Disposable"
    secrets.fail_delete = False
    assert configuration.retry_credential_cleanup().cleanup_pending is False
    assert secrets.get(reference) is None
    session.close()
    session.bind.dispose()


def test_profile_archive_repairs_all_affected_defaults(tmp_path: Path) -> None:
    configuration, _, _, session = make_services(tmp_path)
    cloud_connection = configuration.create_connection(
        name="Cloud", protocol="volc_bigasr_flash",
        base_url="https://openspeech.bytedance.com", parameters={}, secret="cloud"
    )
    cloud = configuration.create_profile(
        name="Cloud profile", purpose="cloud_asr",
        connection_id=cloud_connection.id, model="bigmodel"
    )
    configuration.record_profile_test(cloud.id, ok=True, message="ok")
    configuration.authorize_cloud_upload(cloud.id)
    configuration.update_defaults(asr_mode="cloud", cloud_asr_profile_id=cloud.id)
    configuration.delete_profile(cloud.id)
    defaults = configuration.get_defaults()
    assert defaults.asr_mode == "auto"
    assert defaults.cloud_asr_profile_id is None

    chat = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}
    )
    translation = configuration.create_profile(
        name="Translation", purpose="translation",
        connection_id=chat.id, model="translation"
    )
    notes = configuration.create_profile(
        name="Notes", purpose="notes", connection_id=chat.id, model="notes"
    )
    configuration.record_profile_test(translation.id, ok=True, message="ok")
    configuration.record_profile_test(notes.id, ok=True, message="ok")
    configuration.update_defaults(
        translation_enabled=True, translation_profile_id=translation.id,
        notes_enabled=True, notes_profile_id=notes.id,
    )
    configuration.delete_profile(translation.id)
    configuration.delete_profile(notes.id)
    repaired = configuration.update_defaults(translation_target_language="ja")
    assert repaired.translation_enabled is False
    assert repaired.translation_profile_id is None
    assert repaired.notes_enabled is False
    assert repaired.notes_profile_id is None
    session.close()
    session.bind.dispose()


def test_first_successful_notes_profile_auto_enables_once_and_respects_opt_out(
    tmp_path: Path,
) -> None:
    configuration, _, _, session = make_services(tmp_path / "automatic")
    connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}
    )
    notes = configuration.create_profile(
        name="Notes", purpose="notes", connection_id=connection.id, model="notes"
    )
    configuration.record_profile_test(notes.id, ok=True, message="ok")
    defaults = configuration.get_defaults()
    assert defaults.notes_enabled is True
    assert defaults.notes_profile_id == notes.id
    session.close()
    session.bind.dispose()

    configuration, _, _, session = make_services(tmp_path / "opted-out")
    configuration.update_defaults(notes_enabled=False)
    connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}
    )
    notes = configuration.create_profile(
        name="Notes", purpose="notes", connection_id=connection.id, model="notes"
    )
    configuration.record_profile_test(notes.id, ok=True, message="ok")
    defaults = configuration.get_defaults()
    assert defaults.notes_enabled is False
    assert defaults.notes_profile_id is None

    second = configuration.create_profile(
        name="Notes 2", purpose="notes", connection_id=connection.id, model="notes-2"
    )
    configuration.record_profile_test(second.id, ok=True, message="ok")
    assert configuration.get_defaults().notes_enabled is False
    session.close()
    session.bind.dispose()


def test_disabled_defaults_still_validate_profile_reference_and_purpose(
    tmp_path: Path,
) -> None:
    configuration, _, _, session = make_services(tmp_path)
    connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}
    )
    translation = configuration.create_profile(
        name="Translation", purpose="translation",
        connection_id=connection.id, model="translation"
    )
    with pytest.raises(InvalidConfiguration, match="notes profile"):
        configuration.update_defaults(
            notes_enabled=False, notes_profile_id=translation.id
        )
    with pytest.raises(InvalidConfiguration, match="cloud ASR profile"):
        configuration.update_defaults(
            asr_mode="auto", cloud_asr_profile_id="00000000-0000-0000-0000-000000000000"
        )
    session.close()
    session.bind.dispose()


def test_noop_and_display_name_only_patches_do_not_invalidate_revisions(
    tmp_path: Path,
) -> None:
    configuration, _, _, session = make_services(tmp_path)
    connection = configuration.create_connection(
        name="Chat", protocol="openai_compatible",
        base_url="https://api.example/v1", parameters={}
    )
    configuration.record_connection_test(connection.id, ok=True, message="ok")
    assert configuration.update_connection(connection.id).revision == 1
    renamed = configuration.update_connection(connection.id, name="Chat display")
    assert renamed.revision == 1
    assert renamed.tested is True
    assert configuration.update_connection(
        connection.id, base_url="https://api.example/v1"
    ).revision == 1
    changed = configuration.update_connection(
        connection.id, base_url="https://new.example/v1"
    )
    assert changed.revision == 2
    assert changed.tested is False

    profile = configuration.create_profile(
        name="Notes", purpose="notes", connection_id=connection.id, model="notes"
    )
    configuration.record_profile_test(profile.id, ok=True, message="ok")
    assert configuration.update_profile(profile.id).revision == 1
    renamed_profile = configuration.update_profile(profile.id, name="Notes display")
    assert renamed_profile.revision == 1
    assert renamed_profile.tested is True
    assert configuration.update_profile(profile.id, model="notes").revision == 1
    assert configuration.update_profile(profile.id, model="notes-v2").revision == 2
    session.close()
    session.bind.dispose()


def test_cloud_connection_rejects_loopback_http(tmp_path: Path) -> None:
    configuration, _, _, session = make_services(tmp_path)
    with pytest.raises(InvalidConfiguration, match="HTTPS"):
        configuration.create_connection(
            name="Cloud", protocol="volc_bigasr_flash",
            base_url="http://127.0.0.1:9000", parameters={}
        )
    local = configuration.create_connection(
        name="Local", protocol="openai_compatible",
        base_url="http://127.0.0.1:11434/v1", parameters={}
    )
    assert local.base_url.startswith("http://127.0.0.1")
    session.close()
    session.bind.dispose()
