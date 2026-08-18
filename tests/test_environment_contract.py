from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_dependencies_are_exactly_pinned() -> None:
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


def test_release_evidence_records_hashes_and_refuses_lgpl_label_for_gpl_ffmpeg(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            "node",
            "tools/collect_release_evidence.mjs",
            "--output",
            str(tmp_path),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    evidence_path = tmp_path / "release-evidence.json"
    rendered = evidence_path.read_text(encoding="utf-8")
    assert str(PROJECT_ROOT) not in rendered
    payload = json.loads(rendered)
    assert payload["schema_version"] == 1
    assert payload["ffmpeg"]["distribution_classification"] == (
        "development_gpl_only"
    )
    assert "--enable-gpl" in payload["ffmpeg"]["build_configuration"]
    assert len(payload["ffmpeg"]["executable_sha256"]) == 64
    assert payload["youtube_runtime"]["remote_components_enabled"] is False
    assert len(payload["youtube_runtime"]["deno_executable_sha256"]) == 64
    assert len(payload["youtube_runtime"]["ejs_package_sha256"]) == 64
    assert len(payload["local_model"]["manifest_sha256"]) == 64
