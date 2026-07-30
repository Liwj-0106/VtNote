"""Application settings with strict separation of durable and disposable data."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _contains(parent: Path, child: Path) -> bool:
    try:
        return os.path.commonpath((str(parent), str(child))) == str(parent)
    except ValueError:
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VTNOTE_",
        extra="ignore",
        validate_default=True,
    )

    data_root: Path = Path(r"D:\Workspace\Project\VtNote-data")
    runtime_cache_root: Path = Path(r"D:\Workspace\Codex\cache\VtNote-runtime")
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
