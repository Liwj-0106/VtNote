from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from vtnote.config import Settings
from vtnote.youtube_runtime import (
    DEFAULT_YOUTUBE_RUNTIME_MANIFEST,
    YoutubeRuntimeInventory,
    YoutubeRuntimeManifest,
    inspect_youtube_runtime,
    normalize_version,
)


class FakeInventory(YoutubeRuntimeInventory):
    def __init__(
        self,
        *,
        package_versions: dict[str, str | None],
        package_hashes: dict[str, str | None] | None = None,
        file_hashes: dict[Path, str] | None = None,
        deno_output: str = "deno 2.8.1\nv8 13.6\n",
        environment: dict[str, str] | None = None,
        safe_paths: bool = True,
        ejs_integration: bool = True,
    ) -> None:
        self.package_versions = package_versions
        self.package_hashes = package_hashes or {}
        self.file_hashes = file_hashes or {}
        self.deno_output = deno_output
        self.environment = environment or {}
        self.safe_paths = safe_paths
        self.ejs_integration = ejs_integration
        self.calls: list[tuple[str, object]] = []

    def package_version(self, distribution: str) -> str | None:
        self.calls.append(("package_version", distribution))
        return self.package_versions.get(distribution)

    def package_hash(self, distribution: str) -> str | None:
        self.calls.append(("package_hash", distribution))
        return self.package_hashes.get(distribution)

    def ejs_integration_available(self) -> bool:
        self.calls.append(("ejs_integration_available", None))
        return self.ejs_integration

    def file_sha256(self, path: Path) -> str:
        self.calls.append(("file_sha256", path))
        return self.file_hashes[path]

    def run_deno_version(
        self,
        executable: Path,
        *,
        deno_dir: Path,
        timeout_seconds: float,
    ) -> str:
        self.calls.append(
            (
                "run_deno_version",
                (executable, deno_dir, timeout_seconds),
            )
        )
        return self.deno_output

    def getenv(self, name: str) -> str | None:
        self.calls.append(("getenv", name))
        return self.environment.get(name)

    def paths_are_safe(self, root: Path, targets: tuple[Path, ...]) -> bool:
        self.calls.append(("paths_are_safe", (root, targets)))
        return self.safe_paths


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path / "data",
        runtime_cache_root=tmp_path / "cache",
    )


def configured_manifest() -> YoutubeRuntimeManifest:
    return YoutubeRuntimeManifest(
        yt_dlp_version=(2026, 7, 4),
        ejs_version=(0, 8, 0),
        deno_version=(2, 8, 1),
        ejs_package_sha256="1" * 64,
        deno_executable_sha256="2" * 64,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026.7.4", (2026, 7, 4)),
        ("2026.07.04", (2026, 7, 4)),
        ("deno 2.8.1", (2, 8, 1)),
        (
            "deno 2.8.1 (stable, release, x86_64-pc-windows-msvc)",
            (2, 8, 1),
        ),
        ("0.8.0", (0, 8, 0)),
    ],
)
def test_exact_runtime_versions_normalize_to_numeric_tuple(
    raw: str,
    expected: tuple[int, int, int],
) -> None:
    assert normalize_version(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "2026.7", "v2026.7.4", "2026.7.4.1", "deno latest", True],
)
def test_runtime_version_normalization_rejects_ambiguous_values(
    raw: object,
) -> None:
    with pytest.raises(ValueError):
        normalize_version(raw)  # type: ignore[arg-type]


def test_current_partial_install_is_youtube_only_and_bilibili_remains_ready(
    tmp_path: Path,
) -> None:
    selected = settings(tmp_path)
    runtime_root = selected.runtime_cache_root / "youtube-runtime"
    inventory = FakeInventory(
        package_versions={"yt-dlp": "2026.7.4", "yt-dlp-ejs": None},
        environment={},
    )

    status = inspect_youtube_runtime(
        selected,
        manifest=DEFAULT_YOUTUBE_RUNTIME_MANIFEST,
        inventory=inventory,
    )

    assert not status.youtube_ready
    assert status.bilibili_ready
    assert status.runtime is None
    assert status.runtime_root == runtime_root
    assert status.deno_executable == (
        runtime_root / "deno" / "2.8.1" / "deno.exe"
    )
    assert status.deno_dir == runtime_root / "deno-cache" / "2.8.1"
    assert status.codes == (
        "ejs_missing",
        "deno_missing",
        "deno_dir_missing",
        "deno_dir_env_mismatch",
    )
    assert all(path.is_absolute() for path in (
        status.runtime_root,
        status.deno_executable,
        status.deno_dir,
    ))
    assert all(call[0] not in {"which", "node", "npm", "npx"} for call in inventory.calls)


def test_managed_assets_root_places_youtube_runtime_outside_primary_cache(
    tmp_path: Path,
) -> None:
    selected = Settings(
        data_root=tmp_path / "primary-data",
        runtime_cache_root=tmp_path / "primary-cache",
        managed_assets_root=tmp_path / "managed-assets",
    )
    status = inspect_youtube_runtime(
        selected,
        manifest=DEFAULT_YOUTUBE_RUNTIME_MANIFEST,
        inventory=FakeInventory(
            package_versions={"yt-dlp": "2026.7.4", "yt-dlp-ejs": "0.8.0"},
            package_hashes={"yt-dlp-ejs": "1" * 64},
            environment={},
        ),
    )

    assert status.runtime_root == (
        selected.managed_assets_root / "Cache" / "youtube-runtime"
    )
    assert not status.runtime_root.is_relative_to(selected.runtime_cache_root)


def test_release_manifest_is_immutable_and_contains_reviewed_hashes() -> None:
    assert DEFAULT_YOUTUBE_RUNTIME_MANIFEST.ejs_package_sha256 == (
        "ff4842afba40d40e34c37184543d15ae036d171dd525863f7835af557600402a"
    )
    assert DEFAULT_YOUTUBE_RUNTIME_MANIFEST.deno_executable_sha256 == (
        "a8afddac131261dc9e085c6a1a79544f0567bd09e481034b5d1533588cba9b30"
    )
    with pytest.raises(FrozenInstanceError):
        DEFAULT_YOUTUBE_RUNTIME_MANIFEST.deno_version = (9, 9, 9)  # type: ignore[misc]


def test_fully_matching_managed_runtime_is_ready_without_remote_components(
    tmp_path: Path,
) -> None:
    selected = settings(tmp_path)
    root = selected.runtime_cache_root / "youtube-runtime"
    executable = root / "deno" / "2.8.1" / "deno.exe"
    deno_dir = root / "deno-cache" / "2.8.1"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"controlled deno fixture")
    deno_dir.mkdir(parents=True)
    inventory = FakeInventory(
        package_versions={
            "yt-dlp": "2026.07.04",
            "yt-dlp-ejs": "0.8.0",
        },
        package_hashes={"yt-dlp-ejs": "1" * 64},
        file_hashes={executable: "2" * 64},
        environment={"DENO_DIR": str(deno_dir)},
    )

    status = inspect_youtube_runtime(
        selected,
        manifest=configured_manifest(),
        inventory=inventory,
    )

    assert status.youtube_ready
    assert status.bilibili_ready
    assert status.codes == ()
    assert status.runtime is not None
    assert status.runtime.deno_executable == executable
    assert status.runtime.deno_dir == deno_dir
    assert status.runtime.js_runtimes == ("deno",)
    assert status.runtime.remote_components == frozenset()
    assert status.runtime.system_runtime_fallback is False


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ({"yt_dlp": "2026.7.5"}, "yt_dlp_version_mismatch"),
        ({"ejs": "0.7.0"}, "ejs_version_mismatch"),
        ({"ejs_hash": "3" * 64}, "ejs_hash_mismatch"),
        ({"ejs_integration": False}, "ejs_integration_unavailable"),
        ({"deno_hash": "4" * 64}, "deno_hash_mismatch"),
        ({"deno_output": "deno 2.9.0"}, "deno_version_mismatch"),
        ({"safe_paths": False}, "runtime_path_unsafe"),
        ({"deno_dir_env": "D:\\wrong"}, "deno_dir_env_mismatch"),
    ],
)
def test_each_runtime_integrity_failure_has_a_granular_safe_code(
    tmp_path: Path,
    change: dict[str, object],
    expected_code: str,
) -> None:
    selected = settings(tmp_path)
    root = selected.runtime_cache_root / "youtube-runtime"
    executable = root / "deno" / "2.8.1" / "deno.exe"
    deno_dir = root / "deno-cache" / "2.8.1"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"deno")
    deno_dir.mkdir(parents=True)
    inventory = FakeInventory(
        package_versions={
            "yt-dlp": str(change.get("yt_dlp", "2026.7.4")),
            "yt-dlp-ejs": str(change.get("ejs", "0.8.0")),
        },
        package_hashes={
            "yt-dlp-ejs": str(change.get("ejs_hash", "1" * 64))
        },
        file_hashes={
            executable: str(change.get("deno_hash", "2" * 64))
        },
        deno_output=str(change.get("deno_output", "deno 2.8.1")),
        environment={
            "DENO_DIR": str(change.get("deno_dir_env", deno_dir))
        },
        safe_paths=bool(change.get("safe_paths", True)),
        ejs_integration=bool(change.get("ejs_integration", True)),
    )

    status = inspect_youtube_runtime(
        selected,
        manifest=configured_manifest(),
        inventory=inventory,
    )

    assert not status.youtube_ready
    assert expected_code in status.codes
    assert status.bilibili_ready


def test_runtime_root_is_not_bound_to_one_windows_drive(
    tmp_path: Path,
) -> None:
    selected = Settings(
        data_root=Path(r"C:\VtNote-data"),
        runtime_cache_root=Path(r"C:\VtNote-runtime"),
    )
    inventory = FakeInventory(
        package_versions={"yt-dlp": "2026.7.4", "yt-dlp-ejs": "0.8.0"},
    )
    status = inspect_youtube_runtime(
        selected,
        manifest=configured_manifest(),
        inventory=inventory,
    )

    assert "runtime_root_not_approved" not in status.codes
    assert "C:\\" not in repr(status.codes)
