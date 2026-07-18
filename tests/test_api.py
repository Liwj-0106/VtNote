from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from vtnote.api import ConnectivityResult, ProbeResult, create_app
from vtnote.artifacts import write_transcript_json
from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.models import ItemRecord
from vtnote.paths import StoragePaths
from vtnote.schemas import Provenance, ProvenanceMethod, Transcript, TranscriptSegment
from vtnote.secrets import MemorySecretStore


BASE_URL = "http://127.0.0.1:8765"


class PublicResolver:
    def resolve(self, host: str) -> list[str]:
        return ["142.250.72.14"]


class FakeConnectionTester:
    def __init__(self) -> None:
        self.called = False

    def test_connection(self, connection, secret, *, follow_redirects: bool):
        self.called = True
        assert secret == "super-secret"
        assert follow_redirects is False
        return ConnectivityResult(ok=True, message="accepted super-secret")


class FakeSourceProbe:
    def probe(self, url: str, validate_redirect):
        assert url == "https://youtu.be/abc"
        validate_redirect("https://www.youtube.com/watch?v=abc")
        return ProbeResult(
            canonical_url="https://www.youtube.com/watch?v=abc",
            title="Example",
            platform="youtube",
            duration_ms=12_345,
            subtitles=(
                {"language": "zh-Hans", "format": "vtt", "is_manual": True},
                {"language": "en", "format": "vtt", "is_manual": False},
            ),
            redirect_chain=("https://www.youtube.com/watch?v=abc",),
        )


class ExplodingSourceProbe:
    def probe(self, url: str, validate_redirect):
        raise RuntimeError("Authorization: super-secret")


class UnsafeReportedRedirectProbe:
    def probe(self, url: str, validate_redirect):
        return ProbeResult(
            canonical_url="https://www.youtube.com/watch?v=abc",
            title="Unsafe",
            platform="youtube",
            redirect_chain=("https://127.0.0.1/internal",),
        )


def make_client(
    tmp_path: Path,
    *,
    connection_tester=None,
    source_probe=None,
) -> tuple[TestClient, object, StoragePaths]:
    settings = Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    paths = StoragePaths.from_settings(settings)
    engine = initialize_database(paths.database)
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=MemorySecretStore(),
        resolver=PublicResolver(),
        connection_tester=connection_tester,
        source_probe=source_probe,
    )
    return TestClient(app, base_url=BASE_URL), engine, paths


def csrf(client: TestClient) -> dict[str, str]:
    response = client.get("/api/security/csrf")
    assert response.status_code == 200
    return {"Origin": BASE_URL, "X-CSRF-Token": response.json()["csrf_token"]}


def test_exact_host_origin_and_double_submit_csrf_are_enforced(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        assert client.get("/api/tasks", headers={"Host": "localhost:8765"}).status_code == 403
        no_origin = client.post("/api/tasks", json={"sources": []})
        assert no_origin.status_code == 403
        token_headers = csrf(client)
        wrong_origin = client.post(
            "/api/tasks",
            json={"sources": []},
            headers={**token_headers, "Origin": "http://localhost:8765"},
        )
        assert wrong_origin.status_code == 403
        wrong_csrf = client.post(
            "/api/tasks",
            json={"sources": []},
            headers={**token_headers, "X-CSRF-Token": "wrong"},
        )
        assert wrong_csrf.status_code == 403
        assert "access-control-allow-origin" not in client.get("/api/tasks").headers
    finally:
        engine.dispose()


def test_interactive_api_docs_are_disabled_unless_dev_mode_is_explicit(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    finally:
        engine.dispose()

    settings = Settings(
        data_root=tmp_path / "dev-data",
        runtime_cache_root=tmp_path / "dev-cache",
        enable_dev_docs=True,
    )
    paths = StoragePaths.from_settings(settings)
    engine = initialize_database(paths.database)
    client = TestClient(
        create_app(
            settings=settings,
            engine=engine,
            secret_store=MemorySecretStore(),
            resolver=PublicResolver(),
        ),
        base_url=BASE_URL,
    )
    try:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
    finally:
        engine.dispose()


def test_errors_use_one_sanitized_shape(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post("/api/tasks", json={"sources": []}, headers=csrf(client))
        assert response.status_code == 400
        assert response.json() == {
            "error": {"code": "invalid_task", "message": "at least one source is required", "details": None}
        }
        not_found = client.get("/api/tasks/00000000-0000-0000-0000-000000000000")
        assert not_found.status_code == 404
        assert set(not_found.json()) == {"error"}
    finally:
        engine.dispose()


def test_connection_profile_and_defaults_routes_redact_secrets(tmp_path: Path) -> None:
    tester = FakeConnectionTester()
    client, engine, _ = make_client(tmp_path, connection_tester=tester)
    headers = csrf(client)
    try:
        created = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Chat",
                "protocol": "openai_compatible",
                "base_url": "https://api.example.com/v1",
                "parameters": {},
                "secret": "super-secret",
            },
        )
        assert created.status_code == 201
        connection = created.json()
        assert connection["has_secret"] is True
        assert "secret" not in connection
        assert "super-secret" not in created.text
        assert "credential_ref" not in created.text

        tested = client.post(f"/api/connections/{connection['id']}/test", headers=headers)
        assert tested.status_code == 200
        assert tester.called is True
        assert "super-secret" not in tested.text
        assert tested.json()["test_message"] == "Connection test succeeded"

        assert client.get("/api/connections").status_code == 200
        assert client.get(f"/api/connections/{connection['id']}").status_code == 200
        patched = client.patch(
            f"/api/connections/{connection['id']}",
            headers=headers,
            json={"name": "Chat updated"},
        )
        assert patched.status_code == 200
        assert patched.json()["revision"] == 1

        profile = client.post(
            "/api/profiles",
            headers=headers,
            json={
                "name": "Notes",
                "purpose": "notes",
                "connection_id": connection["id"],
                "model": "gpt",
                "options": {},
            },
        )
        assert profile.status_code == 201
        profile_id = profile.json()["id"]
        assert client.get("/api/profiles").status_code == 200
        assert client.patch(
            f"/api/profiles/{profile_id}", headers=headers, json={"model": "gpt-2"}
        ).status_code == 200
        assert client.get("/api/defaults").json()["translation_enabled"] is False
        assert client.patch("/api/defaults", headers=headers, json={"asr_mode": "local"}).status_code == 200
        translation = client.post(
            "/api/profiles",
            headers=headers,
            json={
                "name": "Translation", "purpose": "translation",
                "connection_id": connection["id"], "model": "translate", "options": {}
            },
        )
        assert translation.status_code == 201
        selected = client.patch(
            "/api/defaults", headers=headers,
            json={"translation_profile_id": translation.json()["id"]},
        )
        assert selected.json()["translation_profile_id"] == translation.json()["id"]
        cleared = client.patch(
            "/api/defaults", headers=headers, json={"translation_profile_id": None}
        )
        assert cleared.json()["translation_profile_id"] is None
        assert client.delete(
            f"/api/profiles/{translation.json()['id']}", headers=headers
        ).status_code == 204
        assert client.delete(f"/api/profiles/{profile_id}", headers=headers).status_code == 204
        assert client.delete(f"/api/connections/{connection['id']}", headers=headers).status_code == 204
        assert client.get(f"/api/profiles/{profile_id}").status_code == 404
        assert client.get(f"/api/connections/{connection['id']}").status_code == 404
        assert all(
            item["id"] != profile_id for item in client.get("/api/profiles").json()
        )
        assert all(
            item["id"] != connection["id"]
            for item in client.get("/api/connections").json()
        )
    finally:
        engine.dispose()


def test_patch_rejects_explicit_null_and_empty_patch_is_a_noop(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    headers = csrf(client)
    try:
        connection = client.post(
            "/api/connections", headers=headers,
            json={
                "name": "Chat", "protocol": "openai_compatible",
                "base_url": "https://api.example/v1", "parameters": {},
            },
        ).json()
        empty = client.patch(
            f"/api/connections/{connection['id']}", headers=headers, json={}
        )
        assert empty.status_code == 200
        assert empty.json()["revision"] == connection["revision"]
        assert client.patch(
            f"/api/connections/{connection['id']}", headers=headers,
            json={"base_url": None},
        ).status_code == 422

        profile = client.post(
            "/api/profiles", headers=headers,
            json={
                "name": "Notes", "purpose": "notes",
                "connection_id": connection["id"], "model": "notes",
            },
        ).json()
        assert client.patch(
            f"/api/profiles/{profile['id']}", headers=headers,
            json={"model": None},
        ).status_code == 422
        assert client.patch(
            f"/api/profiles/{profile['id']}", headers=headers, json={}
        ).json()["revision"] == profile["revision"]
    finally:
        engine.dispose()


def test_missing_real_adapters_return_501_and_probe_injection_revalidates(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post(
            "/api/sources/probe",
            headers=csrf(client),
            json={"url": "https://youtu.be/abc"},
        )
        assert response.status_code == 501
    finally:
        engine.dispose()

    client, engine, _ = make_client(tmp_path / "injected", source_probe=FakeSourceProbe())
    try:
        response = client.post(
            "/api/sources/probe",
            headers=csrf(client),
            json={"url": "https://youtu.be/abc"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "canonical_url": "https://www.youtube.com/watch?v=abc",
            "title": "Example",
            "platform": "youtube",
            "duration_ms": 12_345,
            "subtitles": [
                {"language": "zh-Hans", "format": "vtt", "is_manual": True},
                {"language": "en", "format": "vtt", "is_manual": False},
            ],
            "redirect_chain": ["https://www.youtube.com/watch?v=abc"],
        }
    finally:
        engine.dispose()


def test_probe_centrally_rejects_an_unsafe_reported_redirect_chain(tmp_path: Path) -> None:
    client, engine, _ = make_client(
        tmp_path, source_probe=UnsafeReportedRedirectProbe()
    )
    try:
        response = client.post(
            "/api/sources/probe", headers=csrf(client),
            json={"url": "https://youtu.be/abc"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsafe_source_url"
    finally:
        engine.dispose()


def test_task_list_get_cancel_retry_and_export_routes(tmp_path: Path) -> None:
    client, engine, paths = make_client(tmp_path)
    headers = csrf(client)
    try:
        created = client.post(
            "/api/tasks",
            headers=headers,
            json={"sources": [{"kind": "url", "locator": "https://youtu.be/abc"}]},
        )
        assert created.status_code == 201
        task = created.json()
        overridden = client.post(
            "/api/tasks",
            headers=headers,
            json={
                "sources": [{"kind": "url", "locator": "https://youtu.be/def"}],
                "asr_mode": "local",
                "translation_enabled": False,
                "translation_target_language": "ja",
                "notes_enabled": False,
                "notes_template": "key_points",
                "notes_output_language": "en",
            },
        )
        assert overridden.status_code == 201
        assert overridden.json()["pipeline_snapshot"]["schema_version"] == 1
        assert overridden.json()["pipeline_snapshot"]["asr"]["mode"] == "local"
        assert overridden.json()["pipeline_snapshot"]["translation"]["target_language"] == "ja"
        assert overridden.json()["pipeline_snapshot"]["notes"]["template"] == "key_points"
        assert client.get("/api/tasks").status_code == 200
        assert client.get(f"/api/tasks/{task['id']}").status_code == 200

        item_id = task["items"][0]["id"]
        with Session(engine) as session:
            item = session.get(ItemRecord, item_id)
            assert item is not None
            next(run for run in item.stage_runs if run.stage == "source").status = "completed"
            next(run for run in item.stage_runs if run.stage == "transcribe").status = "failed"
            session.commit()
        retried = client.post(
            f"/api/tasks/{task['id']}/retry", headers=headers,
            json={"item_id": item_id, "stage": "transcribe"},
        )
        assert retried.status_code == 201
        assert client.post(
            f"/api/items/{item_id}/stages/transcribe/retry", headers=headers
        ).status_code == 404

        with Session(engine) as session:
            item = session.get(ItemRecord, item_id)
            assert item is not None
            latest = [run for run in item.stage_runs if run.stage == "transcribe"][-1]
            latest.status = "failed"
            session.commit()
        task_retry = client.post(
            f"/api/tasks/{task['id']}/retry",
            headers=headers,
            json={"item_id": item_id, "stage": "transcribe"},
        )
        assert task_retry.status_code == 201

        transcript = Transcript(
            language="en",
            duration_ms=500,
            provenance=Provenance(method=ProvenanceMethod.PLATFORM_SUBTITLE, provider="youtube"),
            segments=[TranscriptSegment(id="seg_000001", start_ms=0, end_ms=500, text="Hello")],
        )
        write_transcript_json(paths, item_id, transcript)
        exported = client.get(f"/api/items/{item_id}/export?variant=original&format=txt")
        assert exported.status_code == 200
        assert exported.text == "Hello\n"

        canceled = client.post(f"/api/tasks/{task['id']}/cancel", headers=headers)
        assert canceled.status_code == 200
    finally:
        engine.dispose()


def test_task_options_cannot_smuggle_credentials_into_durable_responses(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post(
            "/api/tasks",
            headers=csrf(client),
            json={
                "sources": [{"kind": "url", "locator": "https://youtu.be/abc"}],
                "options": {"secret": "leak-me"},
            },
        )
        assert response.status_code == 422
        assert "leak-me" not in response.text
    finally:
        engine.dispose()


def test_unexpected_adapter_errors_are_sanitized(
    tmp_path: Path, caplog,
) -> None:
    settings = Settings(
        data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache"
    )
    paths = StoragePaths.from_settings(settings)
    engine = initialize_database(paths.database)
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=MemorySecretStore(),
        resolver=PublicResolver(),
        source_probe=ExplodingSourceProbe(),
    )
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=True)
    try:
        with caplog.at_level(logging.ERROR):
            response = client.post(
                "/api/sources/probe",
                headers=csrf(client),
                json={"url": "https://youtu.be/abc"},
            )
        assert response.status_code == 500
        assert response.json() == {
            "error": {"code": "internal_error", "message": "internal server error", "details": None}
        }
        assert "super-secret" not in response.text
        assert "super-secret" not in caplog.text
    finally:
        engine.dispose()
