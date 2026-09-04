"""Application settings with strict separation of durable and disposable data."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Literal, Self
from urllib.parse import urlsplit

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
    managed_assets_root: Path | None = None
    platform_proxy_url: str | None = None
    platform_cookie_browser: Literal["chrome", "edge", "firefox"] | None = None
    platform_douyin_cookie_file: Path | None = Field(default=None, repr=False)
    platform_youtube_cookie_file: Path | None = Field(default=None, repr=False)
    bind_host: Literal["127.0.0.1"] = "127.0.0.1"
    bind_port: int = Field(default=8766, ge=1, le=65_535)
    enable_dev_docs: bool = False

    @field_validator("data_root", "runtime_cache_root")
    @classmethod
    def validate_absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("storage roots must be absolute")
        return value.absolute()

    @field_validator("managed_assets_root")
    @classmethod
    def validate_optional_absolute_root(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            raise ValueError("managed assets root must be absolute")
        return value.absolute()

    @field_validator(
        "platform_douyin_cookie_file",
        "platform_youtube_cookie_file",
    )
    @classmethod
    def validate_cookie_file_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            raise ValueError("platform cookie file path must be absolute")
        return value.absolute()

    @field_validator("platform_proxy_url")
    @classmethod
    def validate_platform_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parts = urlsplit(value)
            port = parts.port
        except (TypeError, ValueError):
            raise ValueError("platform proxy URL is invalid") from None
        if (
            parts.scheme.casefold() != "http"
            or parts.hostname not in {"127.0.0.1", "::1"}
            or port is None
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "platform proxy must be an unauthenticated loopback HTTP URL with a port"
            )
        host = "[::1]" if parts.hostname == "::1" else "127.0.0.1"
        return f"http://{host}:{port}"

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
        if self.managed_assets_root is not None:
            resolved_assets = self.managed_assets_root.resolve(strict=False)
            if (
                _contains(resolved_data, resolved_assets)
                or _contains(resolved_assets, resolved_data)
                or _contains(resolved_cache, resolved_assets)
                or _contains(resolved_assets, resolved_cache)
            ):
                raise ValueError("managed assets root must not overlap storage roots")
        return self
