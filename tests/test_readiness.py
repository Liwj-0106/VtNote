from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.paths import StoragePaths
from vtnote.readiness import ReadinessInspector


def test_readiness_is_read_only_secret_free_and_reports_partial_capabilities(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_settings(
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
        )
    )
    engine = initialize_database(paths.database)
    paths.runtime_cache_root.mkdir(parents=True)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    report = ReadinessInspector(
        engine=engine,
        paths=paths,
        ffmpeg_probe=lambda: True,
        gpu_probe=lambda: False,
        model_probe=lambda: "not_installed",
        youtube_probe=lambda: False,
    ).inspect()

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert report.status == "partial"
    assert report.core == {
        "database": True,
        "data_storage": True,
        "runtime_storage": True,
        "ffmpeg": True,
    }
    assert report.capabilities == {
        "local_files": True,
        "bilibili_url": True,
        "youtube_url": False,
        "local_asr": False,
    }
    assert report.local_model_state == "not_installed"
    assert before == after
    serialized = report.model_dump_json()
    assert "secret" not in serialized.casefold()
    assert "api_key" not in serialized.casefold()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    engine.dispose()


def test_failed_core_dependency_is_blocked_without_raising(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
        )
    )
    engine = initialize_database(paths.database)
    paths.runtime_cache_root.mkdir(parents=True)

    report = ReadinessInspector(
        engine=engine,
        paths=paths,
        ffmpeg_probe=lambda: False,
        gpu_probe=lambda: True,
        model_probe=lambda: "installed",
        youtube_probe=lambda: True,
    ).inspect()

    assert report.status == "blocked"
    assert report.core["ffmpeg"] is False
    assert report.capabilities["local_files"] is False
    engine.dispose()
