"""Application settings with strict separation of durable and disposable data."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _contains(parent: Path, child: Path) -> bool:
    try:
        return os.path.commonpath((str(parent), str(child))) == str(parent)
    except ValueError:
        return False


def platform_storage_roots(
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: str | os.PathLike[str] | None = None,
) -> tuple[PurePath, PurePath]:
    """Return native per-user durable and disposable roots without touching disk."""

    selected_platform = platform_name or sys.platform
    selected_environment = os.environ if environment is None else environment
    selected_home = str(Path.home() if home is None else home)
    if selected_platform == "win32":
        windows_home = PureWindowsPath(selected_home)
        local_app_data = selected_environment.get("LOCALAPPDATA")
        base = (
            PureWindowsPath(local_app_data)
            if local_app_data
            else windows_home / "AppData" / "Local"
        )
        return base / "VtNote" / "Data", base / "VtNote" / "Cache"
    posix_home = PurePosixPath(selected_home)
    if selected_platform == "darwin":
        return (
            posix_home / "Library" / "Application Support" / "VtNote",
            posix_home / "Library" / "Caches" / "VtNote",
        )
    data_base = PurePosixPath(
        selected_environment.get("XDG_DATA_HOME") or posix_home / ".local" / "share"
    )
    cache_base = PurePosixPath(
        selected_environment.get("XDG_CACHE_HOME") or posix_home / ".cache"
    )
    return data_base / "VtNote", cache_base / "VtNote"


def _default_data_root() -> Path:
    data_root, _ = platform_storage_roots()
    return Path(str(data_root))


def _default_runtime_cache_root() -> Path:
    _, cache_root = platform_storage_roots()
    return Path(str(cache_root))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VTNOTE_",
        extra="ignore",
        validate_default=True,
    )

    data_root: Path = Field(default_factory=_default_data_root)
    runtime_cache_root: Path = Field(default_factory=_default_runtime_cache_root)
    bind_host: Literal["127.0.0.1"] = "127.0.0.1"
    bind_port: int = Field(default=8765, ge=1, le=65_535)
    enable_dev_docs: bool = False

    @field_validator("data_root", "runtime_cache_root")
    @classmethod
    def validate_absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("storage roots must be absolute")
        return value.absolute()

    @model_validator(mode="after")
    def validate_separate_roots(self) -> Self:
        resolved_data = self.data_root.resolve(strict=False)
        resolved_cache = self.runtime_cache_root.resolve(strict=False)
        if _contains(resolved_data, resolved_cache) or _contains(
            resolved_cache, resolved_data
        ):
            raise ValueError("data and runtime cache roots must not overlap")
        if (
            os.name == "nt"
            and resolved_data.anchor.casefold() != resolved_cache.anchor.casefold()
        ):
            raise ValueError("data and runtime cache roots must use the same Windows drive")
        return self
