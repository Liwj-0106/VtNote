from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_task3_python_dependencies_are_exactly_pinned() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = set(project["project"]["dependencies"])

    assert {
        "yt-dlp==2026.7.4",
        "python-multipart==0.0.32",
        "faster-whisper==1.2.1",
        "ctranslate2==4.8.1",
    } <= dependencies


def test_environment_records_verified_native_runtime_versions() -> None:
    environment = (PROJECT_ROOT / "environment.yml").read_text(encoding="utf-8")

    for expected in (
        "python=3.11.15",
        "ffmpeg=7.1.1",
        "cuda-cudart=12.8.90",
        "libcublas=12.8.4.1",
        "libcudnn=9.10.2.21",
    ):
        assert expected in environment
