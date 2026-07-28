from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from vtnote.api import create_app
from vtnote.config import Settings
from vtnote.configuration import ConfigurationService
from vtnote.database import initialize_database
from vtnote.models import DefaultSettingsRecord, ItemRecord, TaskRecord
from vtnote.paths import StoragePaths
from vtnote.secrets import MemorySecretStore
from vtnote.tasks import TaskService
from vtnote.url_security import SourceUrlPolicy


BASE_URL = "http://127.0.0.1:8765"
PROMPT = "SENSITIVE-PROMPT-9f2d: summarize private launch details"


class PublicResolver:
    def resolve(self, host: str) -> list[str]:
        return ["142.250.72.14"]


def _sensitive_types() -> Any:
    import vtnote.sensitive_text as sensitive_text

    return sensitive_text


def _paths(tmp_path: Path) -> StoragePaths:
    return StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )


def _services(
    tmp_path: Path, protector: Any
) -> tuple[TaskService, ConfigurationService, Session, StoragePaths]:
    paths = _paths(tmp_path)
    engine = initialize_database(paths.database, sensitive_text_protector=protector)
    session = Session(engine)
    configuration = ConfigurationService(
        session,
        MemorySecretStore(),
        paths=paths,
        sensitive_text_protector=protector,
    )
    tasks = TaskService(
        session,
        configuration,
        paths,
        SourceUrlPolicy(PublicResolver()),
    )
    return tasks, configuration, session, paths


def _configure_custom_notes(
    configuration: ConfigurationService, *, prompt: str = PROMPT
) -> str:
    connection = configuration.create_connection(
        name="Chat",
        protocol="openai_compatible",
        base_url="https://api.example.com/v1",
        parameters={},
    )
    profile = configuration.create_profile(
        name="Notes",
        purpose="notes",
        connection_id=connection.id,
        model="notes-model",
    )
    configuration.record_profile_test(profile.id, ok=True, message="ok")
    configuration.update_defaults(
        notes_enabled=True,
        notes_profile_id=profile.id,
        notes_template="custom",
        notes_custom_prompt=prompt,
    )
    return profile.id


def _create_custom_task(
    tasks: TaskService, configuration: ConfigurationService
) -> Any:
    _configure_custom_notes(configuration)
    return tasks.create_task(
        sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
    )


def _csrf(client: TestClient) -> dict[str, str]:
    response = client.get("/api/security/csrf")
    assert response.status_code == 200
    return {"Origin": BASE_URL, "X-CSRF-Token": response.json()["csrf_token"]}


def _serialized_database(engine: Any) -> str:
    with engine.connect() as connection:
        rows = []
        for table in ("default_settings", "tasks", "sensitive_text_migration"):
            rows.extend(
                tuple(row)
                for row in connection.exec_driver_sql(f'SELECT * FROM "{table}"')
            )
    return json.dumps(rows, default=str, sort_keys=True)


def test_custom_prompt_is_dpapi_protected_in_defaults_and_task_snapshot(
    tmp_path: Path,
) -> None:
    sensitive_text = _sensitive_types()
    protector = sensitive_text.MemorySensitiveTextProtector()
    tasks, configuration, session, _ = _services(tmp_path, protector)
    try:
        created = _create_custom_task(tasks, configuration)
        default_row = session.get(DefaultSettingsRecord, 1)
        task_row = session.get(TaskRecord, created.id)
        assert default_row is not None
        assert task_row is not None

        assert default_row.notes_custom_prompt is None
        default_envelope = sensitive_text.ProtectedTextEnvelope.model_validate(
            default_row.notes_custom_prompt_envelope_json
        )
        task_envelope = sensitive_text.ProtectedTextEnvelope.model_validate(
            task_row.pipeline_snapshot_json["notes"]["custom_prompt_envelope"]
        )
        assert default_envelope.protection == "windows_dpapi_current_user"
        assert task_envelope.protection == "windows_dpapi_current_user"
        assert default_envelope.ciphertext_b64 != task_envelope.ciphertext_b64
        assert (
            protector.unprotect("defaults:notes_custom_prompt", default_envelope)
            == PROMPT
        )
        assert (
            protector.unprotect(
                f"task:{created.id}:notes_custom_prompt", task_envelope
            )
            == PROMPT
        )
        assert PROMPT not in _serialized_database(session.bind)
    finally:
        session.bind.dispose()
        session.close()


def test_public_views_return_only_has_custom_prompt(tmp_path: Path) -> None:
    sensitive_text = _sensitive_types()
    protector = sensitive_text.MemorySensitiveTextProtector()
    tasks, configuration, session, paths = _services(tmp_path, protector)
    created = _create_custom_task(tasks, configuration)
    engine = session.bind
    session.close()
    app = create_app(
        settings=Settings(
            data_root=paths.data_root, runtime_cache_root=paths.runtime_cache_root
        ),
        engine=engine,
        secret_store=MemorySecretStore(),
        sensitive_text_protector=protector,
        resolver=PublicResolver(),
    )
    client = TestClient(app, base_url=BASE_URL)
    try:
        payloads = [
            client.get("/api/defaults").json(),
            client.get("/api/tasks").json(),
            client.get(f"/api/tasks/{created.id}").json(),
        ]
        for payload in payloads:
            serialized = json.dumps(payload, sort_keys=True)
            assert PROMPT not in serialized
            assert "custom_prompt_envelope" not in serialized
            assert "ciphertext_b64" not in serialized
        assert payloads[0]["has_custom_prompt"] is True
        task_payload = payloads[2]
        assert task_payload["pipeline_snapshot"]["notes"]["has_custom_prompt"] is True
        assert "notes_custom_prompt" not in task_payload["options"]
    finally:
        engine.dispose()


def test_retry_decrypts_the_same_immutable_prompt(tmp_path: Path) -> None:
    sensitive_text = _sensitive_types()
    protector = sensitive_text.MemorySensitiveTextProtector()
    tasks, configuration, session, _ = _services(tmp_path, protector)
    try:
        created = _create_custom_task(tasks, configuration)
        configuration.update_defaults(notes_custom_prompt="a changed future default")
        item = session.get(ItemRecord, created.items[0].id)
        assert item is not None
        for run in item.stage_runs:
            if run.stage in {"source", "transcribe"}:
                run.status = "completed"
            elif run.stage == "notes":
                run.status = "failed"
        session.commit()

        retried = tasks.retry_stage(
            item.id,
            "notes",
            expected_attempt=1,
            override={"schema_version": 1, "strategy": "same"},
        )
        assert [run.attempt for run in retried.stage_runs if run.stage == "notes"] == [
            1,
            2,
        ]
        assert (
            tasks.resolve_notes_custom_prompt(item.id, attempt=2)
            == PROMPT
        )
    finally:
        session.bind.dispose()
        session.close()


def test_legacy_plaintext_default_is_protected_and_cleared(tmp_path: Path) -> None:
    sensitive_text = _sensitive_types()
    engine = initialize_database(tmp_path / "vtnote.db")
    with Session(engine) as session:
        session.add(
            DefaultSettingsRecord(
                id=1,
                notes_template="custom",
                notes_custom_prompt=PROMPT,
            )
        )
        session.commit()

    protector = sensitive_text.MemorySensitiveTextProtector()
    sensitive_text.migrate_sensitive_text(engine, protector)
    with Session(engine) as session:
        row = session.get(DefaultSettingsRecord, 1)
        assert row is not None
        assert row.notes_custom_prompt is None
        envelope = sensitive_text.ProtectedTextEnvelope.model_validate(
            row.notes_custom_prompt_envelope_json
        )
        assert protector.unprotect("defaults:notes_custom_prompt", envelope) == PROMPT
        assert sensitive_text.sensitive_text_migration_status(session) == "complete"
    assert PROMPT not in _serialized_database(engine)
    engine.dispose()


def test_legacy_plaintext_snapshot_is_atomically_protected_and_cleared(
    tmp_path: Path,
) -> None:
    sensitive_text = _sensitive_types()
    engine = initialize_database(tmp_path / "vtnote.db")
    task_id = "11111111-1111-4111-8111-111111111111"
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id=task_id,
                options={"notes_custom_prompt": PROMPT},
                pipeline_snapshot_json={
                    "schema_version": 1,
                    "notes": {
                        "template": "custom",
                        "custom_prompt": PROMPT,
                    },
                },
            )
        )
        session.commit()

    protector = sensitive_text.MemorySensitiveTextProtector()
    sensitive_text.migrate_sensitive_text(engine, protector)
    with Session(engine) as session:
        row = session.get(TaskRecord, task_id)
        assert row is not None
        assert "notes_custom_prompt" not in row.options
        assert "custom_prompt" not in row.pipeline_snapshot_json["notes"]
        envelope = sensitive_text.ProtectedTextEnvelope.model_validate(
            row.pipeline_snapshot_json["notes"]["custom_prompt_envelope"]
        )
        assert (
            protector.unprotect(f"task:{task_id}:notes_custom_prompt", envelope)
            == PROMPT
        )
    assert PROMPT not in _serialized_database(engine)
    engine.dispose()


def test_sensitive_text_migration_rolls_back_and_blocks_on_protection_failure(
    tmp_path: Path,
) -> None:
    sensitive_text = _sensitive_types()

    class FailingProtector(sensitive_text.MemorySensitiveTextProtector):
        def protect(self, purpose: str, plaintext: str) -> Any:
            if plaintext == "cannot protect this row":
                raise sensitive_text.SensitiveTextProtectionError(
                    "native details must not escape"
                )
            return super().protect(purpose, plaintext)

    engine = initialize_database(tmp_path / "vtnote.db")
    with Session(engine) as session:
        session.add_all(
            [
                TaskRecord(
                    id="11111111-1111-4111-8111-111111111111",
                    options={},
                    pipeline_snapshot_json={
                        "notes": {"template": "custom", "custom_prompt": PROMPT}
                    },
                ),
                TaskRecord(
                    id="22222222-2222-4222-8222-222222222222",
                    options={},
                    pipeline_snapshot_json={
                        "notes": {
                            "template": "custom",
                            "custom_prompt": "cannot protect this row",
                        }
                    },
                ),
            ]
        )
        session.commit()

    sensitive_text.migrate_sensitive_text(engine, FailingProtector())
    with Session(engine) as session:
        rows = session.query(TaskRecord).order_by(TaskRecord.id).all()
        assert rows[0].pipeline_snapshot_json["notes"]["custom_prompt"] == PROMPT
        assert "custom_prompt_envelope" not in rows[0].pipeline_snapshot_json["notes"]
        assert (
            sensitive_text.sensitive_text_migration_status(session)
            == "sensitive_snapshot_migration_required"
        )
        configuration = ConfigurationService(
            session,
            MemorySecretStore(),
            sensitive_text_protector=FailingProtector(),
        )
        tasks = TaskService(
            session,
            configuration,
            _paths(tmp_path),
            SourceUrlPolicy(PublicResolver()),
        )
        with pytest.raises(
            sensitive_text.SensitiveTextMigrationRequired,
            match="sensitive_snapshot_migration_required",
        ):
            tasks.resolve_notes_custom_prompt(rows[0].id, attempt=1)
    engine.dispose()


def test_prompt_never_enters_log_error_export_url_or_browser_storage(
    tmp_path: Path, caplog: Any,
) -> None:
    sensitive_text = _sensitive_types()
    protector = sensitive_text.MemorySensitiveTextProtector()
    tasks, configuration, session, paths = _services(tmp_path, protector)
    created = _create_custom_task(tasks, configuration)
    engine = session.bind
    session.close()
    app = create_app(
        settings=Settings(
            data_root=paths.data_root, runtime_cache_root=paths.runtime_cache_root
        ),
        engine=engine,
        secret_store=MemorySecretStore(),
        sensitive_text_protector=protector,
        resolver=PublicResolver(),
    )
    client = TestClient(app, base_url=BASE_URL)
    caplog.set_level(logging.DEBUG)
    try:
        responses = [
            client.get("/api/defaults"),
            client.get("/api/tasks"),
            client.get(f"/api/tasks/{created.id}"),
            client.get(
                f"/api/items/{created.items[0].id}/export",
                params={"variant": "original", "format": "txt"},
            ),
        ]
        browser_storage_facing_payloads = [
            response.text
            for response in responses
            if response.headers.get("content-type", "").startswith("application/json")
        ]
        assert all(PROMPT not in response.text for response in responses)
        assert all(PROMPT not in str(response.request.url) for response in responses)
        assert PROMPT not in json.dumps(browser_storage_facing_payloads)
        assert PROMPT not in caplog.text
    finally:
        engine.dispose()


def test_concurrent_startups_serialize_migration_and_second_is_idempotent(
    tmp_path: Path,
) -> None:
    sensitive_text = _sensitive_types()
    paths = _paths(tmp_path)
    initial_engine = initialize_database(paths.database)
    with Session(initial_engine) as session:
        session.add(
            DefaultSettingsRecord(
                id=1,
                notes_template="custom",
                notes_custom_prompt=PROMPT,
            )
        )
        session.commit()
    initial_engine.dispose()

    class BlockingProtector(sensitive_text.MemorySensitiveTextProtector):
        def __init__(self) -> None:
            super().__init__()
            self.lock = Lock()
            self.calls = 0
            self.first_entered = Event()
            self.second_entered = Event()
            self.release_first = Event()

        def protect(self, purpose: str, plaintext: str) -> Any:
            with self.lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                self.first_entered.set()
                assert self.release_first.wait(5)
            else:
                self.second_entered.set()
            return super().protect(purpose, plaintext)

    protector = BlockingProtector()

    def new_engine() -> Any:
        return create_engine(
            URL.create("sqlite+pysqlite", database=str(paths.database)),
            connect_args={"check_same_thread": False, "timeout": 5},
        )

    engines = [new_engine(), new_engine()]
    errors: list[BaseException] = []

    def start_with_engine(engine: Any) -> None:
        try:
            create_app(
                settings=Settings(
                    data_root=paths.data_root,
                    runtime_cache_root=paths.runtime_cache_root,
                ),
                engine=engine,
                secret_store=MemorySecretStore(),
                sensitive_text_protector=protector,
                resolver=PublicResolver(),
            )
        except BaseException as error:
            errors.append(error)

    first = Thread(target=start_with_engine, args=(engines[0],))
    second = Thread(target=start_with_engine, args=(engines[1],))
    first.start()
    assert protector.first_entered.wait(5)
    second.start()
    overlapped = protector.second_entered.wait(1)
    protector.release_first.set()
    first.join(10)
    second.join(10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert overlapped is False
    assert protector.calls == 1

    third_engine = new_engine()
    sensitive_text.migrate_sensitive_text(third_engine, protector)
    assert protector.calls == 1
    with Session(third_engine) as session:
        assert sensitive_text.sensitive_text_migration_status(session) == "complete"
        row = session.get(DefaultSettingsRecord, 1)
        assert row is not None
        assert row.notes_custom_prompt is None
    for engine in [*engines, third_engine]:
        engine.dispose()


def test_missing_sensitive_text_migration_state_is_fail_closed(
    tmp_path: Path,
) -> None:
    sensitive_text = _sensitive_types()
    engine = initialize_database(tmp_path / "vtnote.db")
    with Session(engine) as session:
        state = session.get(sensitive_text.SensitiveTextMigrationRecord, 1)
        assert state is not None
        session.delete(state)
        session.commit()

        assert (
            sensitive_text.sensitive_text_migration_status(session)
            == "sensitive_snapshot_migration_required"
        )
        with pytest.raises(
            sensitive_text.SensitiveTextMigrationRequired,
            match="sensitive_snapshot_migration_required",
        ):
            sensitive_text.require_sensitive_text_migration(session)
    engine.dispose()


def test_options_only_prompt_with_missing_notes_rolls_back_without_loss(
    tmp_path: Path,
) -> None:
    sensitive_text = _sensitive_types()
    engine = initialize_database(tmp_path / "vtnote.db")
    task_id = "33333333-3333-4333-8333-333333333333"
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id=task_id,
                options={"notes_custom_prompt": PROMPT},
                pipeline_snapshot_json={"schema_version": 1},
            )
        )
        session.commit()

    result = sensitive_text.migrate_sensitive_text(
        engine, sensitive_text.MemorySensitiveTextProtector()
    )
    assert result == "sensitive_snapshot_migration_required"
    with Session(engine) as session:
        row = session.get(TaskRecord, task_id)
        assert row is not None
        assert row.options["notes_custom_prompt"] == PROMPT
        assert row.pipeline_snapshot_json == {"schema_version": 1}
    engine.dispose()


@pytest.mark.parametrize("malformed_notes", [None, "not-a-notes-object", []])
def test_options_prompt_with_malformed_notes_rolls_back_without_loss(
    tmp_path: Path, malformed_notes: Any,
) -> None:
    sensitive_text = _sensitive_types()
    engine = initialize_database(tmp_path / "vtnote.db")
    task_id = "44444444-4444-4444-8444-444444444444"
    snapshot = {"schema_version": 1, "notes": malformed_notes}
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id=task_id,
                options={"notes_custom_prompt": PROMPT},
                pipeline_snapshot_json=snapshot,
            )
        )
        session.commit()

    result = sensitive_text.migrate_sensitive_text(
        engine, sensitive_text.MemorySensitiveTextProtector()
    )
    assert result == "sensitive_snapshot_migration_required"
    with Session(engine) as session:
        row = session.get(TaskRecord, task_id)
        assert row is not None
        assert row.options["notes_custom_prompt"] == PROMPT
        assert row.pipeline_snapshot_json == snapshot
    engine.dispose()


@pytest.mark.parametrize(
    "protector_factory",
    [
        lambda sensitive_text: sensitive_text.MemorySensitiveTextProtector(),
        lambda sensitive_text: sensitive_text.WindowsDpapiSensitiveTextProtector(),
    ],
)
@pytest.mark.parametrize(
    "malformed_envelope",
    [
        {
            "schema_version": 2,
            "protection": "windows_dpapi_current_user",
            "ciphertext_b64": "LEAK-MARKER-wrong-version",
        },
        {
            "schema_version": 1,
            "protection": "windows_dpapi_current_user",
            "ciphertext_b64": "LEAK-MARKER-invalid-base64!",
        },
        {
            "schema_version": 1,
            "protection": "windows_dpapi_current_user",
            "ciphertext_b64": "QUJDRA==",
            "LEAK-MARKER-extra-field": True,
        },
    ],
)
def test_malformed_envelopes_raise_bounded_safe_error(
    protector_factory: Any,
    malformed_envelope: dict[str, Any],
) -> None:
    sensitive_text = _sensitive_types()
    protector = protector_factory(sensitive_text)
    with pytest.raises(sensitive_text.SensitiveTextProtectionError) as raised:
        protector.unprotect("task:marker-test:notes_custom_prompt", malformed_envelope)
    assert str(raised.value) == "protected sensitive text is invalid"
    assert "LEAK-MARKER" not in str(raised.value)


@pytest.mark.parametrize(
    ("template", "options", "snapshot_prompt", "expected_prompt"),
    [
        ("summary", {}, None, None),
        ("key_points", {}, None, None),
        (
            "custom",
            {"notes_custom_prompt": PROMPT},
            None,
            PROMPT,
        ),
        (
            "custom",
            {"notes_custom_prompt": PROMPT},
            PROMPT,
            PROMPT,
        ),
    ],
)
def test_legacy_null_prompt_placeholder_matrix(
    tmp_path: Path,
    template: str,
    options: dict[str, Any],
    snapshot_prompt: str | None,
    expected_prompt: str | None,
) -> None:
    sensitive_text = _sensitive_types()
    engine = initialize_database(tmp_path / "vtnote.db")
    task_id = "55555555-5555-4555-8555-555555555555"
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id=task_id,
                options=options,
                pipeline_snapshot_json={
                    "schema_version": 1,
                    "notes": {
                        "template": template,
                        "custom_prompt": snapshot_prompt,
                    },
                },
            )
        )
        session.commit()

    class CountingProtector(sensitive_text.MemorySensitiveTextProtector):
        def __init__(self) -> None:
            super().__init__()
            self.protect_calls = 0

        def protect(self, purpose: str, plaintext: str) -> Any:
            self.protect_calls += 1
            return super().protect(purpose, plaintext)

    protector = CountingProtector()
    assert sensitive_text.migrate_sensitive_text(engine, protector) == "complete"
    assert protector.protect_calls == (1 if expected_prompt is not None else 0)
    with Session(engine) as session:
        row = session.get(TaskRecord, task_id)
        assert row is not None
        assert "notes_custom_prompt" not in row.options
        notes = row.pipeline_snapshot_json["notes"]
        assert "custom_prompt" not in notes
        if expected_prompt is None:
            assert "custom_prompt_envelope" not in notes
        else:
            envelope = sensitive_text.validate_protected_text_envelope(
                notes["custom_prompt_envelope"]
            )
            assert (
                protector.unprotect(
                    f"task:{task_id}:notes_custom_prompt", envelope
                )
                == expected_prompt
            )
    engine.dispose()


@pytest.mark.parametrize(
    ("option_prompt", "snapshot_prompt"),
    [
        ("option value", "conflicting snapshot value"),
        (123, None),
        (None, {"invalid": "object"}),
    ],
)
def test_conflicting_or_invalid_non_null_legacy_prompts_roll_back(
    tmp_path: Path,
    option_prompt: Any,
    snapshot_prompt: Any,
) -> None:
    sensitive_text = _sensitive_types()
    engine = initialize_database(tmp_path / "vtnote.db")
    task_id = "66666666-6666-4666-8666-666666666666"
    options = {"notes_custom_prompt": option_prompt}
    snapshot = {
        "schema_version": 1,
        "notes": {
            "template": "custom",
            "custom_prompt": snapshot_prompt,
        },
    }
    with Session(engine) as session:
        session.add(
            TaskRecord(
                id=task_id,
                options=options,
                pipeline_snapshot_json=snapshot,
            )
        )
        session.commit()

    assert (
        sensitive_text.migrate_sensitive_text(
            engine, sensitive_text.MemorySensitiveTextProtector()
        )
        == "sensitive_snapshot_migration_required"
    )
    with Session(engine) as session:
        row = session.get(TaskRecord, task_id)
        assert row is not None
        assert row.options == options
        assert row.pipeline_snapshot_json == snapshot
    engine.dispose()


def test_failed_migrator_does_not_publish_stale_required_after_peer_completes(
    tmp_path: Path,
) -> None:
    sensitive_text = _sensitive_types()
    database_path = tmp_path / "vtnote.db"
    initial_engine = initialize_database(database_path)
    with Session(initial_engine) as session:
        session.add(
            DefaultSettingsRecord(
                id=1,
                notes_template="custom",
                notes_custom_prompt=PROMPT,
            )
        )
        session.commit()
    initial_engine.dispose()

    def new_engine() -> Any:
        return create_engine(
            URL.create("sqlite+pysqlite", database=str(database_path)),
            connect_args={"check_same_thread": False, "timeout": 5},
        )

    failing_engine = new_engine()
    peer_engine = new_engine()
    primary_rolled_back = Event()
    allow_failure_publication = Event()
    paused_once = Event()

    def pause_after_primary_connection_returns(*_: Any) -> None:
        if paused_once.is_set():
            return
        paused_once.set()
        primary_rolled_back.set()
        assert allow_failure_publication.wait(5)

    event.listen(
        failing_engine.pool,
        "checkin",
        pause_after_primary_connection_returns,
    )

    class FailingProtector(sensitive_text.MemorySensitiveTextProtector):
        def protect(self, purpose: str, plaintext: str) -> Any:
            raise sensitive_text.SensitiveTextProtectionError(
                "safe injected failure"
            )

    failing_results: list[str] = []
    failure = Thread(
        target=lambda: failing_results.append(
            sensitive_text.migrate_sensitive_text(
                failing_engine, FailingProtector()
            )
        )
    )
    failure.start()
    assert primary_rolled_back.wait(5)

    peer_result = sensitive_text.migrate_sensitive_text(
        peer_engine, sensitive_text.MemorySensitiveTextProtector()
    )
    assert peer_result == "complete"
    allow_failure_publication.set()
    failure.join(10)

    assert not failure.is_alive()
    assert failing_results == ["complete"]
    with Session(peer_engine) as session:
        assert sensitive_text.sensitive_text_migration_status(session) == "complete"
        row = session.get(DefaultSettingsRecord, 1)
        assert row is not None
        assert row.notes_custom_prompt is None
    failing_engine.dispose()
    peer_engine.dispose()
