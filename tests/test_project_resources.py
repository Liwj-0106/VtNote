from __future__ import annotations

from vtnote.project_resources import (
    apply_repository_runtime_defaults,
    bundled_asset,
    frontend_dist_path,
    repository_root,
)


def test_source_resources_stay_inside_the_project() -> None:
    root = repository_root()
    assert root is not None

    resources = [
        frontend_dist_path(),
        bundled_asset("models", "large-v3-turbo.manifest.json"),
        bundled_asset("test-audio", "tencent-asr-check.wav"),
    ]
    for resource in resources:
        resource.resolve(strict=False).relative_to(root.resolve())

    assert resources[1].is_file()
    assert resources[2].is_file()


def test_source_launcher_defaults_all_runtime_roots_inside_ignored_area() -> None:
    root = repository_root()
    assert root is not None
    environment: dict[str, str] = {}

    defaults = apply_repository_runtime_defaults(environment)

    assert defaults == {
        "VTNOTE_DATA_ROOT": str(root / ".vtnote" / "Data"),
        "VTNOTE_RUNTIME_CACHE_ROOT": str(root / ".vtnote" / "Cache"),
        "VTNOTE_MANAGED_ASSETS_ROOT": str(root / ".vtnote" / "ManagedAssets"),
    }
    assert environment == defaults


def test_external_runtime_roots_are_replaced() -> None:
    environment = {
        "VTNOTE_DATA_ROOT": "explicit-data",
        "VTNOTE_RUNTIME_CACHE_ROOT": "explicit-cache",
        "VTNOTE_MANAGED_ASSETS_ROOT": "explicit-assets",
    }

    defaults = apply_repository_runtime_defaults(environment)

    assert environment == defaults


def test_owned_runtime_roots_are_not_overwritten() -> None:
    root = repository_root()
    assert root is not None
    owned = root / ".vtnote" / "isolated"
    environment = {
        "VTNOTE_DATA_ROOT": str(owned / "Data"),
        "VTNOTE_RUNTIME_CACHE_ROOT": str(owned / "Cache"),
        "VTNOTE_MANAGED_ASSETS_ROOT": str(owned / "ManagedAssets"),
    }

    apply_repository_runtime_defaults(environment)

    assert environment == {
        "VTNOTE_DATA_ROOT": str(owned / "Data"),
        "VTNOTE_RUNTIME_CACHE_ROOT": str(owned / "Cache"),
        "VTNOTE_MANAGED_ASSETS_ROOT": str(owned / "ManagedAssets"),
    }
