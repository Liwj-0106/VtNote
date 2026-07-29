"""Read-only capability inspection for the local VtNote installation."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text

from vtnote.config import Settings
from vtnote.media import FfmpegBinaries
from vtnote.paths import StoragePaths
from vtnote.youtube_runtime import inspect_youtube_runtime


class ReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ready", "partial", "blocked"]
    core: dict[str, bool]
    capabilities: dict[str, bool]
    local_model_state: str


def _command_exists(command: str) -> bool:
    candidate = Path(command)
    if candidate.is_absolute():
        return candidate.is_file()
    return shutil.which(command) is not None


def _ffmpeg_ready() -> bool:
    binaries = FfmpegBinaries.discover()
    return _command_exists(binaries.ffmpeg) and _command_exists(binaries.ffprobe)


def _cuda_ready() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except (ImportError, OSError, RuntimeError):
        return False


class ReadinessInspector:
    """Inspect local dependencies without downloading, creating, or mutating files."""

    def __init__(
        self,
        *,
        engine: Engine,
        paths: StoragePaths,
        ffmpeg_probe: Callable[[], bool] | None = None,
        gpu_probe: Callable[[], bool] | None = None,
        model_probe: Callable[[], str] | None = None,
        youtube_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.engine = engine
        self.paths = paths
        self.ffmpeg_probe = ffmpeg_probe or _ffmpeg_ready
        self.gpu_probe = gpu_probe or _cuda_ready
        self.model_probe = model_probe or (lambda: "not_installed")
        self.youtube_probe = youtube_probe or self._youtube_ready

    def _youtube_ready(self) -> bool:
        settings = Settings(
            data_root=self.paths.data_root,
            runtime_cache_root=self.paths.runtime_cache_root,
        )
        return inspect_youtube_runtime(settings).youtube_ready

    @staticmethod
    def _storage_ready(path: Path) -> bool:
        return (
            path.is_dir()
            and os.access(path, os.R_OK)
            and os.access(path, os.W_OK)
        )

    def _database_ready(self) -> bool:
        try:
            with self.engine.connect() as connection:
                return connection.execute(text("SELECT 1")).scalar_one() == 1
        except Exception:
            return False

    @staticmethod
    def _safe_probe(probe: Callable[[], bool]) -> bool:
        try:
            return probe() is True
        except Exception:
            return False

    def inspect(self) -> ReadinessReport:
        database = self._database_ready()
        data_storage = self._storage_ready(self.paths.data_root)
        runtime_storage = self._storage_ready(self.paths.runtime_cache_root)
        ffmpeg = self._safe_probe(self.ffmpeg_probe)
        try:
            local_model_state = str(self.model_probe())
        except Exception:
            local_model_state = "unavailable"

        core = {
            "database": database,
            "data_storage": data_storage,
            "runtime_storage": runtime_storage,
            "ffmpeg": ffmpeg,
        }
        core_ready = all(core.values())
        local_asr = (
            core_ready
            and self._safe_probe(self.gpu_probe)
            and local_model_state == "installed"
        )
        capabilities = {
            "local_files": core_ready,
            "bilibili_url": core_ready,
            "youtube_url": core_ready and self._safe_probe(self.youtube_probe),
            "local_asr": local_asr,
        }
        if not core_ready:
            status: Literal["ready", "partial", "blocked"] = "blocked"
        elif all(capabilities.values()):
            status = "ready"
        else:
            status = "partial"
        return ReadinessReport(
            status=status,
            core=core,
            capabilities=capabilities,
            local_model_state=local_model_state,
        )
