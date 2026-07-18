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

    @field_validator("data_root", "runtime_cache_root")
    @classmethod
    def validate_absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("storage roots must be absolute")
        return value.resolve(strict=False)

    @model_validator(mode="after")
    def validate_separate_roots(self) -> Self:
        if _contains(self.data_root, self.runtime_cache_root) or _contains(
            self.runtime_cache_root, self.data_root
        ):
            raise ValueError("data and runtime cache roots must not overlap")
        return self
