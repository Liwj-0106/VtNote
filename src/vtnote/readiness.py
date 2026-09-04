"""Read-only capability inspection for the local VtNote installation."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote.local_asr_contract import (
    FASTER_WHISPER_ENGINE,
)
from vtnote.media import FfmpegBinaries
from vtnote.models import DefaultSettingsRecord
from vtnote.native_runtime import configure_windows_native_runtime
from vtnote.paths import StoragePaths
from vtnote.youtube_runtime import inspect_youtube_runtime


class LocalAsrEngineReadiness(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: str


class ReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ready", "partial", "blocked"]
    core: dict[str, bool]
    capabilities: dict[str, bool]
    local_model_state: str
    local_asr_engines: dict[str, LocalAsrEngineReadiness] | None = None


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
        configure_windows_native_runtime()
        import ctranslate2

        return (
            ctranslate2.get_cuda_device_count() > 0
            and "int8_float16"
            in ctranslate2.get_supported_compute_types("cuda")
        )
    except (ImportError, OSError, RuntimeError, ValueError):
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
        local_asr_engine_probes: Mapping[str, Callable[[], str]] | None = None,
        youtube_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.engine = engine
        self.paths = paths
        self.ffmpeg_probe = ffmpeg_probe or _ffmpeg_ready
        self.gpu_probe = gpu_probe or _cuda_ready
        self.model_probe = model_probe or (lambda: "not_installed")
        self.local_asr_engine_probes = local_asr_engine_probes
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
        local_asr_engines: dict[str, LocalAsrEngineReadiness] | None = None
        if self.local_asr_engine_probes is not None:
            local_asr_engines = {
                FASTER_WHISPER_ENGINE: LocalAsrEngineReadiness(
                    state=local_model_state
                )
            }
            for engine_name, probe in self.local_asr_engine_probes.items():
                try:
                    state = str(probe())
                except Exception:
                    state = "unavailable"
                local_asr_engines[engine_name] = LocalAsrEngineReadiness(state=state)

        core = {
            "database": database,
            "data_storage": data_storage,
            "runtime_storage": runtime_storage,
            "ffmpeg": ffmpeg,
        }
        core_ready = all(core.values())
        cpu_fallback = False
        try:
            with Session(self.engine) as session:
                defaults = session.get(DefaultSettingsRecord, 1)
                cpu_fallback = bool(
                    defaults is not None
                    and isinstance(defaults.local_whisper_options, dict)
                    and defaults.local_whisper_options.get("cpu_fallback_enabled") is True
                )
        except Exception:
            cpu_fallback = False
        gpu = self._safe_probe(self.gpu_probe)
        faster_whisper_ready = (
            local_model_state == "installed" and (gpu or cpu_fallback)
        )
        alternate_engine_ready = bool(
            local_asr_engines
            and any(
                name != FASTER_WHISPER_ENGINE and readiness.state == "installed"
                for name, readiness in local_asr_engines.items()
            )
        )
        local_asr = core_ready and (
            faster_whisper_ready or alternate_engine_ready
        )
        capabilities = {
            "local_files": core_ready,
            "bilibili_url": core_ready,
            "douyin_url": core_ready,
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
            local_asr_engines=local_asr_engines,
        )
