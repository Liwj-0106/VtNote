from __future__ import annotations

import json
import subprocess
from pathlib import Path


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
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    evidence_path = tmp_path / "release-evidence.json"
    rendered = evidence_path.read_text(encoding="utf-8")
    assert str(Path.cwd()) not in rendered
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
