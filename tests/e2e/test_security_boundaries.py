from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from vtnote.api import create_app
from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.secrets import MemorySecretStore
from vtnote.sensitive_text import MemorySensitiveTextProtector


BASE_URL = "http://127.0.0.1:8765"


class _Resolver:
    def resolve(self, host: str) -> list[str]:
        return ["127.0.0.1"]


def test_host_origin_csrf_ssrf_and_filename_boundaries_fail_closed(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        runtime_cache_root=tmp_path / "runtime",
    )
    engine = initialize_database(settings.data_root / "vtnote.db")
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=MemorySecretStore(),
        resolver=_Resolver(),
        sensitive_text_protector=MemorySensitiveTextProtector(),
        frontend_dist=tmp_path / "no-ui",
    )
    with TestClient(app, base_url=BASE_URL) as client:
        assert client.get("/api/health", headers={"Host": "evil.invalid"}).status_code == 403
        assert client.post("/api/tasks", json={}).status_code == 403
        csrf = client.get("/api/security/csrf").json()["csrf_token"]
        headers = {"Origin": BASE_URL, "X-CSRF-Token": csrf}
        blocked = client.post(
            "/api/sources/probe",
            headers=headers,
            json={"url": "http://127.0.0.1/private"},
        )
        assert blocked.status_code == 400
        upload = client.post(
            "/api/tasks",
            headers=headers,
            files=[
                (
                    "metadata",
                    (
                        None,
                        json.dumps({"kind": "subtitle"}),
                        "application/json",
                    ),
                ),
                ("file", ("../escape.srt", b"unsafe", "application/x-subrip")),
            ],
        )
        assert upload.status_code == 400
        diagnostics = client.get("/api/diagnostics")
        assert str(settings.data_root).encode() not in diagnostics.content
        assert str(settings.runtime_cache_root).encode() not in diagnostics.content
    engine.dispose()
