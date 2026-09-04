"""Resolve source-tree and installed-package resources without machine paths."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def repository_root() -> Path | None:
    candidate = PACKAGE_ROOT.parents[1]
    if (candidate / "pyproject.toml").is_file() and (candidate / "frontend").is_dir():
        return candidate
    return None


def apply_repository_runtime_defaults(
    environment: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Keep source-checkout runtime state in the repository's ignored area.

    Explicit roots remain supported for isolated tests, but a source checkout
    never accepts a root outside its own ``.vtnote`` directory.  This prevents
    stale user-level environment variables from recreating legacy data folders
    after the checkout has moved.
    """

    root = repository_root()
    if root is None:
        return {}
    selected = os.environ if environment is None else environment
    runtime_root = root / ".vtnote"
    defaults = {
        "VTNOTE_DATA_ROOT": str(runtime_root / "Data"),
        "VTNOTE_RUNTIME_CACHE_ROOT": str(runtime_root / "Cache"),
        "VTNOTE_MANAGED_ASSETS_ROOT": str(runtime_root / "ManagedAssets"),
    }

    def is_owned_root(value: str) -> bool:
        candidate = Path(value)
        if not candidate.is_absolute():
            return False
        try:
            candidate.resolve(strict=False).relative_to(
                runtime_root.resolve(strict=False)
            )
        except (OSError, ValueError):
            return False
        return True

    for name, value in defaults.items():
        configured = selected.get(name)
        if configured is None or not is_owned_root(configured):
            selected[name] = value
    return defaults


def frontend_dist_path() -> Path:
    packaged = PACKAGE_ROOT / "web"
    if (packaged / "index.html").is_file():
        return packaged
    source_root = repository_root()
    if source_root is not None:
        return source_root / "frontend" / "dist"
    return packaged


def bundled_asset(*parts: str) -> Path:
    packaged = PACKAGE_ROOT.joinpath("resources", *parts)
    if packaged.is_file():
        return packaged
    source_root = repository_root()
    if source_root is not None:
        return source_root.joinpath("assets", *parts)
    return packaged
