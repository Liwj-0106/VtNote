from __future__ import annotations

import json
import logging
from pathlib import Path
from zipfile import ZipFile

from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.diagnostics import build_diagnostic_bundle
from vtnote.logging_setup import configure_logging


def test_json_logging_rotates_and_redacts_credentials_paths_and_prompts(
    tmp_path: Path,
) -> None:
    log_path = configure_logging(
        tmp_path / "logs",
        process_name="test",
        max_bytes=450,
        backup_count=1,
    )
    logger = logging.getLogger("vtnote.security-test")
    for _ in range(12):
        logger.error(
            "SecretKey=top-secret custom_prompt='private words' "
            r"path=D:\Private\clip.mp4 Authorization=Bearer abc.def"
        )

    rendered = "".join(
        path.read_text(encoding="utf-8")
        for path in sorted(log_path.parent.glob("test.log*"))
    )
    assert "top-secret" not in rendered
    assert "private words" not in rendered
    assert "D:\\Private" not in rendered
    assert "abc.def" not in rendered
    line = next(
        line
        for path in sorted(log_path.parent.glob("test.log*"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )
    payload = json.loads(line)
    assert set(payload) == {"timestamp", "level", "logger", "message"}
    assert len(list(log_path.parent.glob("test.log*"))) <= 2


def test_diagnostic_bundle_contains_only_bounded_safe_metadata(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "private-data",
        runtime_cache_root=tmp_path / "private-runtime",
    )
    engine = initialize_database(settings.data_root / "vtnote.db")
    destination = tmp_path / "diagnostics.zip"

    build_diagnostic_bundle(destination, settings=settings, engine=engine)

    with ZipFile(destination) as archive:
        assert archive.namelist() == ["diagnostics.json"]
        raw = archive.read("diagnostics.json").decode("utf-8")
    assert str(settings.data_root) not in raw
    assert str(settings.runtime_cache_root) not in raw
    assert "credential" not in raw.casefold()
    assert "secret" not in raw.casefold()
    payload = json.loads(raw)
    assert payload["schema_version"] == 1
    assert payload["database_reachable"] is True
    engine.dispose()
