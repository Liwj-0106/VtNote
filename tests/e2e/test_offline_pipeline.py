from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from vtnote.api import create_app
from vtnote.artifacts import validate_source_subtitle
from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.media import MediaInfo
from vtnote.paths import StoragePaths
from vtnote.secrets import MemorySecretStore
from vtnote.sensitive_text import MemorySensitiveTextProtector
from vtnote.source_stage import SourceStageHandler
from vtnote.worker import StageContext
from vtnote.worker_store import WorkerStore


BASE_URL = "http://127.0.0.1:8765"
NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
SRT = b"1\n00:00:00,000 --> 00:00:01,200\nVtNote offline\n"


class _Resolver:
    def resolve(self, host: str) -> list[str]:
        return ["142.250.72.14"]


class _LocalFiles:
    def validate_media(self, path: Path) -> MediaInfo:
        raise AssertionError("subtitle flow must not probe media")

    def validate_subtitle(self, path: Path) -> None:
        validate_source_subtitle(path.suffix.removeprefix("."), path.read_bytes())


def test_uploaded_subtitle_reaches_immutable_transcript_and_regenerated_export(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        runtime_cache_root=tmp_path / "runtime",
    )
    paths = StoragePaths.from_settings(settings)
    engine = initialize_database(paths.database)
    local_files = _LocalFiles()
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=MemorySecretStore(),
        resolver=_Resolver(),
        local_source_validator=local_files,
        sensitive_text_protector=MemorySensitiveTextProtector(),
        frontend_dist=tmp_path / "no-ui",
    )
    with TestClient(app, base_url=BASE_URL) as client:
        csrf = client.get("/api/security/csrf").json()["csrf_token"]
        created = client.post(
            "/api/tasks",
            headers={"Origin": BASE_URL, "X-CSRF-Token": csrf},
            files=[
                (
                    "metadata",
                    (
                        None,
                        json.dumps(
                            {
                                "kind": "subtitle",
                                "asr_mode": "auto",
                                "translation_enabled": False,
                                "notes_enabled": False,
                            }
                        ),
                        "application/json",
                    ),
                ),
                ("file", ("offline.srt", SRT, "application/x-subrip")),
            ],
        )
        assert created.status_code == 201
        item_id = created.json()["items"][0]["id"]

        store = WorkerStore(engine)
        claim = store.claim_next(
            "offline-source",
            NOW,
            timedelta(minutes=2),
        )
        assert claim is not None and claim.stage == "source"
        result = SourceStageHandler(
            paths=paths,
            platform_source=None,
            local_sources=local_files,
            preferred_languages=("zh-Hans", "en"),
        ).run(StageContext(store=store, claim=claim, clock=lambda: NOW))
        assert store.complete(claim, result, now=NOW)

        transcript = client.get(f"/api/items/{item_id}/transcript")
        assert transcript.status_code == 200
        assert transcript.json()["segments"][0]["text"] == "VtNote offline"
        exported = client.get(
            f"/api/items/{item_id}/export?variant=original&format=markdown"
        )
        assert exported.status_code == 200
        assert "VtNote offline" in exported.text
        assert paths.transcript(item_id).is_file()
        assert paths.source_original(item_id, "srt").read_bytes() == SRT

    engine.dispose()
