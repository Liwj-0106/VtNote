"""Owned-path construction that rejects absolute paths and traversal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vtnote.config import Settings


class UnsafePathError(ValueError):
    """Raised when an untrusted path would leave an application-owned root."""


def _resolve_under(root: Path, parts: tuple[str | Path, ...]) -> Path:
    if not parts:
        return root
    converted = [Path(part) for part in parts]
    if any(part.is_absolute() or ".." in part.parts for part in converted):
        raise UnsafePathError("owned path components must be relative and cannot contain '..'")
    candidate = root.joinpath(*converted).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise UnsafePathError("owned path escapes its configured root") from error
    return candidate


@dataclass(frozen=True, slots=True)
class StoragePaths:
    data_root: Path
    runtime_cache_root: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> "StoragePaths":
        return cls(settings.data_root, settings.runtime_cache_root)

    @property
    def database(self) -> Path:
        return self.durable("vtnote.db")

    def durable(self, *parts: str | Path) -> Path:
        return _resolve_under(self.data_root, parts)

    def runtime(self, *parts: str | Path) -> Path:
        return _resolve_under(self.runtime_cache_root, parts)

    def ensure_roots(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.runtime_cache_root.mkdir(parents=True, exist_ok=True)
