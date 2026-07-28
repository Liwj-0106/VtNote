from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest
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

        retried = tasks.retry_stage(item.id, "notes")
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
