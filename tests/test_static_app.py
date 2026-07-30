from __future__ import annotations

from pathlib import Path
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from vtnote.api import create_app
from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.secrets import MemorySecretStore
from vtnote.sensitive_text import MemorySensitiveTextProtector


BASE_URL = "http://127.0.0.1:8765"


def test_built_spa_is_served_without_capturing_api_or_mutation_routes(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    runtime_root = tmp_path / "runtime"
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>VtNote UI</title>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("window.VTNOTE = true;", encoding="utf-8")
    settings = Settings(data_root=data_root, runtime_cache_root=runtime_root)
    engine = initialize_database(data_root / "vtnote.db")
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=MemorySecretStore(),
        sensitive_text_protector=MemorySensitiveTextProtector(),
        frontend_dist=dist,
    )

    with TestClient(app, base_url=BASE_URL) as client:
        assert "VtNote UI" in client.get("/").text
        assert "VtNote UI" in client.get("/tasks").text
        assert "VtNote UI" in client.get(
            "/tasks/94f344da-aa8d-481c-8d91-e6b94efc6e67"
        ).text
        assert client.get("/assets/app.js").text == "window.VTNOTE = true;"
        assert client.get("/api/nope").json()["error"]["code"] == "http_error"
        csrf = client.get("/api/security/csrf").json()["csrf_token"]
        assert (
            client.post(
                "/tasks/94f344da-aa8d-481c-8d91-e6b94efc6e67",
                headers={"Origin": BASE_URL, "X-CSRF-Token": csrf},
            ).status_code
            == 405
        )
        assert client.get("/docs").status_code == 404
        readiness = client.get("/api/readiness").json()
        assert readiness["ui"] == {"available": True}
        diagnostics = client.get("/api/diagnostics")
        assert diagnostics.status_code == 200
        with ZipFile(BytesIO(diagnostics.content)) as archive:
            assert archive.namelist() == ["diagnostics.json"]

    engine.dispose()


def test_missing_build_keeps_developer_api_available(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        runtime_cache_root=tmp_path / "runtime",
    )
    engine = initialize_database(settings.data_root / "vtnote.db")
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=MemorySecretStore(),
        sensitive_text_protector=MemorySensitiveTextProtector(),
        frontend_dist=tmp_path / "missing",
    )

    with TestClient(app, base_url=BASE_URL) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 404
        assert client.get("/api/readiness").json()["ui"] == {"available": False}

    engine.dispose()
