from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine, MetaData, UniqueConstraint, create_engine, event, inspect
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.models import (
    Base,
    ItemRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
    TaskRecord,
)
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


def create_chat_connection(
    configuration: ConfigurationService,
    *,
    name: str = "Chat",
    workspace_id: str = "ws-1234",
    api_key: str | None = None,
):
    kwargs = (
        {"credentials": {"api_key": api_key}}
        if api_key is not None
        else {}
    )
    return configuration.create_connection(
        name=name,
        protocol="aliyun_bailian",
        base_url=(
            f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1"
        ),
        parameters={"workspace_id": workspace_id},
        **kwargs,
    )


def create_chat_profile(
    configuration: ConfigurationService,
    *,
    connection_id: str,
    name: str = "Notes",
    purpose: str = "notes",
    model: str = "qwen-plus",
):
    return configuration.create_profile(
        name=name,
        purpose=purpose,
        connection_id=connection_id,
        model=model,
        context_length=32768,
        options={"max_tokens": 4096},
    )


def ready_chat_profile(configuration: ConfigurationService, profile_id: str) -> None:
    configuration.record_profile_test(profile_id, ok=True, message="ok")
    configuration.authorize_chat_data(profile_id)


def create_legacy_global_name_schema(database_path: Path) -> Engine:
    """Create the pre-partial-index schema with real SQLite UNIQUE constraints."""

    legacy_metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(legacy_metadata)
    legacy_metadata.remove(legacy_metadata.tables["credential_cleanup"])
    for table_name, partial_index_name, constraint_name in (
        (
            "provider_connections",
            "uq_provider_connections_active_name",
            "uq_provider_connections_name",
        ),
        (
            "processor_profiles",
            "uq_processor_profiles_active_name",
            "uq_processor_profiles_name",
        ),
    ):
        table = legacy_metadata.tables[table_name]
        for index in list(table.indexes):
            if index.name == partial_index_name:
                table.indexes.remove(index)
        UniqueConstraint(table.c.name, name=constraint_name)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(database_path))
    )
    legacy_metadata.create_all(engine)
    return engine


def test_secret_rotation_never_exposes_new_secret_with_old_database_config(
    tmp_path: Path,
) -> None:
    configuration, _, secrets, session = make_services(tmp_path)
    connection = create_chat_connection(configuration, api_key="old-key")
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
        connection.id,
        credentials={"api_key": "new-key"},
    )

    assert len(observed) == 1
    assert observed[0][0] == connection.base_url
    assert observed[0][1] == old_reference
    assert observed[0][2] is not None
    assert '"api_key":"old-key"' in observed[0][2]
    session.refresh(stored)
    assert stored.credential_ref != old_reference
    assert secrets.get(old_reference) is None
    current_secret = configuration.secret_for_connection(connection.id)
    assert current_secret is not None
    assert '"api_key":"new-key"' in current_secret
    assert updated.revision == 2
    session.close()
    session.bind.dispose()


def test_clear_secret_rotates_to_a_new_empty_reference(tmp_path: Path) -> None:
    configuration, _, secrets, session = make_services(tmp_path)
    connection = create_chat_connection(configuration, api_key="old-key")
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
    connection = create_chat_connection(configuration, api_key="task-key")
    profile = create_chat_profile(configuration, connection_id=connection.id)
    ready_chat_profile(configuration, profile.id)
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
    task_secret = configuration.secret_for_connection(connection.id)
    assert task_secret is not None and '"api_key":"task-key"' in task_secret
    session.close()
    session.bind.dispose()


def test_archived_names_can_be_reused_and_terminal_tasks_allow_purge(
    tmp_path: Path,
) -> None:
    configuration, tasks, secrets, session = make_services(tmp_path)
    old_connection = create_chat_connection(
        configuration,
        workspace_id="ws-old",
        api_key="old-task-key",
    )
    old_profile = create_chat_profile(
        configuration,
        connection_id=old_connection.id,
        model="qwen-old",
    )
    ready_chat_profile(configuration, old_profile.id)
    task = tasks.create_task(
        sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
    )
    old_row = session.get(ProviderConnectionRecord, old_connection.id)
    assert old_row is not None
    old_reference = old_row.credential_ref

    configuration.delete_profile(old_profile.id)
    configuration.delete_connection(old_connection.id)
    replacement = create_chat_connection(
        configuration,
        workspace_id="ws-new",
    )
    replacement_profile = create_chat_profile(
        configuration,
        connection_id=replacement.id,
        model="qwen-new",
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


@pytest.mark.parametrize(
    ("terminal_status", "retryable_stage_status"),
    [
        ("failed", "failed"),
        ("completed_with_warnings", "failed"),
        ("canceled", "canceled"),
    ],
)
def test_retryable_terminal_task_pins_archives_until_latest_attempt_succeeds(
    tmp_path: Path,
    terminal_status: str,
    retryable_stage_status: str,
) -> None:
    configuration, tasks, secrets, session = make_services(tmp_path)
    connection = create_chat_connection(
        configuration,
        api_key="retry-secret",
    )
    profile = create_chat_profile(
        configuration,
        connection_id=connection.id,
    )
    ready_chat_profile(configuration, profile.id)
    task = tasks.create_task(
        sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
    )
    task_row = session.get(TaskRecord, task.id)
    assert task_row is not None
    item = task_row.items[0]
    for run in item.stage_runs:
        run.status = (
            retryable_stage_status if run.stage == "notes" else "completed"
        )
    task_row.status = terminal_status
    item.status = terminal_status
    connection_row = session.get(ProviderConnectionRecord, connection.id)
    assert connection_row is not None
    credential_ref = connection_row.credential_ref
    session.commit()

    configuration.delete_profile(profile.id)
    configuration.delete_connection(connection.id)
    assert configuration.purge_unreferenced_archived() == {
        "profiles": 0,
        "connections": 0,
    }
    assert configuration.resolve_profile_for_execution(profile.id)["id"] == profile.id
    retry_secret = configuration.secret_for_connection(connection.id)
    assert retry_secret is not None
    assert '"api_key":"retry-secret"' in retry_secret

    retried = tasks.retry_stage(
        item.id,
        "notes",
        expected_attempt=1,
        override={"schema_version": 1, "strategy": "same"},
    )
    assert [
        (run.attempt, run.status)
        for run in retried.stage_runs
        if run.stage == "notes"
    ] == [(1, retryable_stage_status), (2, "queued")]
    item_row = session.get(ItemRecord, item.id)
    assert item_row is not None
    latest_notes = max(
        (run for run in item_row.stage_runs if run.stage == "notes"),
        key=lambda run: run.attempt,
    )
    latest_notes.status = "completed"
    item_row.status = "completed"
    task_row.status = "completed"
    session.commit()

    assert configuration.purge_unreferenced_archived() == {
        "profiles": 1,
        "connections": 1,
    }
    assert secrets.get(credential_ref) is None
    session.close()
    session.bind.dispose()


def test_legacy_global_unique_schema_reuses_archived_names_without_rebuild(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    paths.database.parent.mkdir(parents=True, exist_ok=True)
    secrets = MemorySecretStore()
    legacy_engine = create_legacy_global_name_schema(paths.database)
    with Session(legacy_engine) as legacy_session:
        old_connection_row = ProviderConnectionRecord(
            name="Chat",
            protocol="openai_compatible",
            base_url="https://old.example/v1",
            parameters={"organization": "legacy-org"},
            credential_ref="connection:legacy-chat",
            archived_at=datetime.now(timezone.utc),
        )
        old_profile_row = ProcessorProfileRecord(
            name="Notes",
            purpose="notes",
            connection=old_connection_row,
            model="legacy-notes",
            context_length=4096,
            options={"temperature": 0.2},
            archived_at=datetime.now(timezone.utc),
        )
        legacy_session.add(old_profile_row)
        legacy_session.commit()
        old_connection_id = old_connection_row.id
        old_profile_id = old_profile_row.id
        old_reference = old_connection_row.credential_ref
        secrets.set(old_reference, "legacy-secret")
    legacy_engine.dispose()

    engine = initialize_database(paths.database)
    try:
        assert any(
            constraint["column_names"] == ["name"]
            for constraint in inspect(engine).get_unique_constraints(
                "provider_connections"
            )
        )
        with Session(engine) as session:
            configuration = ConfigurationService(session, secrets, paths=paths)
            replacement = create_chat_connection(
                configuration,
                workspace_id="ws-replacement",
            )
            replacement_profile = create_chat_profile(
                configuration,
                connection_id=replacement.id,
                model="qwen-new",
            )

            old_connection_row = session.get(
                ProviderConnectionRecord, old_connection_id
            )
            old_profile_row = session.get(ProcessorProfileRecord, old_profile_id)
            assert old_connection_row is not None
            assert old_profile_row is not None
            assert old_connection_row.name.startswith("__vtnote_archived__:")
            assert old_connection_row.base_url == "https://old.example/v1"
            assert old_connection_row.parameters == {"organization": "legacy-org"}
            assert old_connection_row.credential_ref == old_reference
            assert old_profile_row.name.startswith("__vtnote_archived__:")
            assert old_profile_row.model == "legacy-notes"
            assert old_profile_row.context_length == 4096
            assert old_profile_row.options == {"temperature": 0.2}
            assert old_profile_row.connection_id == old_connection_id
            assert secrets.get(old_reference) == "legacy-secret"
            with pytest.raises(
                InvalidConfiguration,
                match="legacy_chat_endpoint_blocked",
            ):
                configuration.secret_for_connection(old_connection_id)
            assert replacement_profile.name == "Notes"

            with pytest.raises(InvalidConfiguration, match="already exists"):
                create_chat_connection(
                    configuration,
                    name="chat",
                    workspace_id="ws-duplicate",
                )
            with pytest.raises(InvalidConfiguration, match="already exists"):
                create_chat_profile(
                    configuration,
                    name="notes",
                    connection_id=replacement.id,
                    model="qwen-duplicate",
                )
    finally:
        engine.dispose()


def test_reserved_archived_name_prefix_is_rejected_for_user_configuration(
    tmp_path: Path,
) -> None:
    configuration, _, _, session = make_services(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration, match="reserved"):
            create_chat_connection(
                configuration,
                name="__VTNOTE_ARCHIVED__:manual",
            )
        connection = create_chat_connection(configuration)
        with pytest.raises(InvalidConfiguration, match="reserved"):
            configuration.update_connection(
                connection.id, name="__vtnote_archived__:manual"
            )
        with pytest.raises(InvalidConfiguration, match="reserved"):
            create_chat_profile(
                configuration,
                name="__vtnote_archived__:manual",
                connection_id=connection.id,
            )
        profile = create_chat_profile(
            configuration,
            connection_id=connection.id,
        )
        with pytest.raises(InvalidConfiguration, match="reserved"):
            configuration.update_profile(
                profile.id, name="__vtnote_archived__:manual"
            )
    finally:
        session.close()
        session.bind.dispose()


def test_archived_name_retirement_rolls_back_if_profile_create_fails(
    tmp_path: Path,
) -> None:
    configuration, _, _, session = make_services(tmp_path)
    connection = create_chat_connection(configuration)
    old_profile = create_chat_profile(
        configuration,
        connection_id=connection.id,
        model="qwen-old",
    )
    old_row = session.get(ProcessorProfileRecord, old_profile.id)
    assert old_row is not None
    old_row.archived_at = datetime.now(timezone.utc)
    session.commit()

    def fail_commit(_: Session) -> None:
        raise RuntimeError("database unavailable")

    event.listen(session, "before_commit", fail_commit, once=True)
    with pytest.raises(RuntimeError, match="database unavailable"):
        create_chat_profile(
            configuration,
            name="Notes",
            connection_id=connection.id,
            model="qwen-replacement",
        )

    assert session.in_transaction() is False
    session.refresh(old_row)
    assert old_row.name == "Notes"
    session.close()
    session.bind.dispose()


def test_failed_obsolete_credential_delete_is_tracked_and_retryable(
    tmp_path: Path,
) -> None:
    secrets = RecoveringDeleteSecretStore()
    configuration, _, _, session = make_services(tmp_path, secrets)
    connection = create_chat_connection(configuration, api_key="old-secret")
    stored = session.get(ProviderConnectionRecord, connection.id)
    assert stored is not None
    old_reference = stored.credential_ref

    updated = configuration.update_connection(
        connection.id,
        credentials={"api_key": "new-secret"},
    )
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
    connection = create_chat_connection(
        configuration,
        name="Disposable",
        workspace_id="ws-disposable",
        api_key="purge-secret",
    )
    stored = session.get(ProviderConnectionRecord, connection.id)
    assert stored is not None
    reference = stored.credential_ref
    configuration.delete_connection(connection.id)
    with pytest.raises(KeyError):
        configuration.secret_for_connection(connection.id)
    assert configuration.credential_cleanup_status().cleanup_pending is True
    assert reference in configuration.diagnostic_sensitive_values()

    recreated = create_chat_connection(
        configuration,
        name="Disposable",
        workspace_id="ws-recreated",
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
        name="Cloud", protocol="tencent_recording_asr",
        base_url="https://asr.tencentcloudapi.com", parameters={},
        credentials={"secret_id": "AKID", "secret_key": "cloud"}
    )
    cloud = configuration.create_profile(
        name="Cloud profile", purpose="cloud_asr",
        connection_id=cloud_connection.id, model="16k_zh"
    )
    configuration.record_profile_test(cloud.id, ok=True, message="ok")
    configuration.authorize_cloud_upload(cloud.id)
    configuration.update_defaults(asr_mode="cloud", cloud_asr_profile_id=cloud.id)
    configuration.delete_profile(cloud.id)
    defaults = configuration.get_defaults()
    assert defaults.asr_mode == "auto"
    assert defaults.cloud_asr_profile_id is None

    chat = create_chat_connection(configuration)
    translation = create_chat_profile(
        configuration,
        name="Translation",
        purpose="translation",
        connection_id=chat.id,
    )
    notes = create_chat_profile(configuration, connection_id=chat.id)
    ready_chat_profile(configuration, translation.id)
    ready_chat_profile(configuration, notes.id)
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
    connection = create_chat_connection(configuration)
    notes = create_chat_profile(configuration, connection_id=connection.id)
    configuration.record_profile_test(notes.id, ok=True, message="ok")
    assert configuration.get_defaults().notes_enabled is False
    configuration.authorize_chat_data(notes.id)
    defaults = configuration.get_defaults()
    assert defaults.notes_enabled is True
    assert defaults.notes_profile_id == notes.id
    session.close()
    session.bind.dispose()

    configuration, _, _, session = make_services(tmp_path / "opted-out")
    configuration.update_defaults(notes_enabled=False)
    connection = create_chat_connection(configuration)
    notes = create_chat_profile(configuration, connection_id=connection.id)
    configuration.record_profile_test(notes.id, ok=True, message="ok")
    configuration.authorize_chat_data(notes.id)
    defaults = configuration.get_defaults()
    assert defaults.notes_enabled is False
    assert defaults.notes_profile_id is None

    second = create_chat_profile(
        configuration,
        name="Notes 2",
        connection_id=connection.id,
        model="qwen-plus-2",
    )
    configuration.record_profile_test(second.id, ok=True, message="ok")
    configuration.authorize_chat_data(second.id)
    assert configuration.get_defaults().notes_enabled is False
    session.close()
    session.bind.dispose()


def test_disabled_defaults_still_validate_profile_reference_and_purpose(
    tmp_path: Path,
) -> None:
    configuration, _, _, session = make_services(tmp_path)
    connection = create_chat_connection(configuration)
    translation = create_chat_profile(
        configuration,
        name="Translation",
        purpose="translation",
        connection_id=connection.id,
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
    connection = create_chat_connection(configuration)
    configuration.record_connection_test(connection.id, ok=True, message="ok")
    assert configuration.update_connection(connection.id).revision == 1
    renamed = configuration.update_connection(connection.id, name="Chat display")
    assert renamed.revision == 1
    assert renamed.tested is True
    assert configuration.update_connection(
        connection.id, base_url=connection.base_url
    ).revision == 1
    changed = configuration.update_connection(
        connection.id,
        credentials={"api_key": "new-key"},
    )
    assert changed.revision == 2
    assert changed.tested is False

    profile = create_chat_profile(configuration, connection_id=connection.id)
    configuration.record_profile_test(profile.id, ok=True, message="ok")
    assert configuration.update_profile(profile.id).revision == 1
    renamed_profile = configuration.update_profile(profile.id, name="Notes display")
    assert renamed_profile.revision == 1
    assert renamed_profile.tested is True
    assert configuration.update_profile(profile.id, model="qwen-plus").revision == 1
    assert configuration.update_profile(profile.id, model="qwen-max").revision == 2
    session.close()
    session.bind.dispose()


def test_cloud_connection_rejects_loopback_http(tmp_path: Path) -> None:
    configuration, _, _, session = make_services(tmp_path)
    with pytest.raises(InvalidConfiguration, match="HTTPS"):
        configuration.create_connection(
            name="Cloud", protocol="tencent_recording_asr",
            base_url="http://127.0.0.1:9000", parameters={}
        )
    with pytest.raises(InvalidConfiguration, match="endpoint"):
        configuration.create_connection(
            name="Local",
            protocol="aliyun_bailian",
            base_url="http://127.0.0.1:11434/v1",
            parameters={"workspace_id": "ws-local"},
        )
    session.close()
    session.bind.dispose()
