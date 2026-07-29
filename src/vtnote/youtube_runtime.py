"""Managed YouTube EJS/Deno runtime inspection without runtime downloads."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vtnote.config import Settings


_VERSION = re.compile(r"^(?:deno )?([0-9]+)\.([0-9]+)\.([0-9]+)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_POINT = 0x400


def normalize_version(raw: str) -> tuple[int, int, int]:
    if not isinstance(raw, str):
        raise ValueError("runtime version must be a string")
    match = _VERSION.fullmatch(raw.strip())
    if match is None:
        raise ValueError("runtime version must contain exactly three numeric parts")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class YoutubeRuntimeManifest:
    yt_dlp_version: tuple[int, int, int]
    ejs_version: tuple[int, int, int]
    deno_version: tuple[int, int, int]
    ejs_package_sha256: str | None
    deno_executable_sha256: str | None

    def __post_init__(self) -> None:
        for version in (
            self.yt_dlp_version,
            self.ejs_version,
            self.deno_version,
        ):
            if (
                not isinstance(version, tuple)
                or len(version) != 3
                or any(type(part) is not int or part < 0 for part in version)
            ):
                raise ValueError("runtime manifest versions must be numeric triples")
        for digest in (
            self.ejs_package_sha256,
            self.deno_executable_sha256,
        ):
            if digest is not None and _SHA256.fullmatch(digest) is None:
                raise ValueError("runtime manifest hashes must be lowercase SHA-256")


DEFAULT_YOUTUBE_RUNTIME_MANIFEST = YoutubeRuntimeManifest(
    yt_dlp_version=(2026, 7, 4),
    ejs_version=(0, 8, 0),
    deno_version=(2, 8, 1),
    ejs_package_sha256=None,
    deno_executable_sha256=None,
)


@dataclass(frozen=True, slots=True)
class YoutubeRuntime:
    manifest: YoutubeRuntimeManifest
    runtime_root: Path
    deno_executable: Path
    deno_dir: Path
    js_runtimes: tuple[str, ...]
    remote_components: frozenset[str]
    system_runtime_fallback: bool


@dataclass(frozen=True, slots=True)
class YoutubeRuntimeStatus:
    youtube_ready: bool
    bilibili_ready: bool
    codes: tuple[str, ...]
    runtime_root: Path
    deno_executable: Path
    deno_dir: Path
    runtime: YoutubeRuntime | None


class YoutubeRuntimeInventory(Protocol):
    def package_version(self, distribution: str) -> str | None: ...

    def package_hash(self, distribution: str) -> str | None: ...

    def ejs_integration_available(self) -> bool: ...

    def file_sha256(self, path: Path) -> str: ...

    def run_deno_version(
        self,
        executable: Path,
        *,
        deno_dir: Path,
        timeout_seconds: float,
    ) -> str: ...

    def getenv(self, name: str) -> str | None: ...

    def paths_are_safe(self, root: Path, targets: tuple[Path, ...]) -> bool: ...


def _contained(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _existing_path_has_reparse_point(root: Path, target: Path) -> bool:
    current = root
    relative = target.relative_to(root)
    for part in (Path(), *relative.parents[::-1], relative):
        candidate = current if part == Path() else root / part
        if not candidate.exists():
            continue
        try:
            attributes = candidate.stat(follow_symlinks=False).st_file_attributes
        except AttributeError:
            if candidate.is_symlink():
                return True
        except OSError:
            return True
        else:
            if attributes & _REPARSE_POINT:
                return True
    return False


class SystemYoutubeRuntimeInventory:
    def package_version(self, distribution: str) -> str | None:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return None

    def package_hash(self, distribution: str) -> str | None:
        try:
            metadata = importlib.metadata.distribution(distribution)
        except importlib.metadata.PackageNotFoundError:
            return None
        files = tuple(
            sorted(
                (
                    item
                    for item in (metadata.files or ())
                    if "__pycache__" not in item.parts
                    and item.suffix != ".pyc"
                    and item.name not in {"RECORD", "direct_url.json"}
                ),
                key=lambda item: item.as_posix(),
            )
        )
        if not files:
            return None
        digest = hashlib.sha256()
        for item in files:
            path = metadata.locate_file(item)
            if not path.is_file():
                return None
            digest.update(item.as_posix().encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def ejs_integration_available(self) -> bool:
        try:
            solver = importlib.import_module("yt_dlp_ejs.yt.solver")
        except (ImportError, OSError):
            return False
        return callable(getattr(solver, "core", None)) and callable(
            getattr(solver, "lib", None)
        )

    def file_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def run_deno_version(
        self,
        executable: Path,
        *,
        deno_dir: Path,
        timeout_seconds: float,
    ) -> str:
        environment = {"DENO_DIR": str(deno_dir)}
        for name in ("SYSTEMROOT", "WINDIR"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=timeout_seconds,
            creationflags=flags,
        )
        if completed.returncode != 0:
            raise RuntimeError("managed Deno version check failed")
        return completed.stdout[:1024]

    def getenv(self, name: str) -> str | None:
        return os.environ.get(name)

    def paths_are_safe(self, root: Path, targets: tuple[Path, ...]) -> bool:
        try:
            resolved_root = root.resolve(strict=False)
            if (
                not root.is_absolute()
                or root.drive.casefold() != "d:"
                or resolved_root.drive.casefold() != "d:"
            ):
                return False
            for target in targets:
                resolved_target = target.resolve(strict=False)
                if (
                    not target.is_absolute()
                    or target.drive.casefold() != "d:"
                    or resolved_target.drive.casefold() != "d:"
                    or not _contained(resolved_root, resolved_target)
                    or _existing_path_has_reparse_point(root, target)
                ):
                    return False
        except (OSError, ValueError):
            return False
        return True


def _runtime_paths(
    settings: Settings,
    manifest: YoutubeRuntimeManifest,
) -> tuple[Path, Path, Path]:
    version = ".".join(str(part) for part in manifest.deno_version)
    root = settings.runtime_cache_root / "youtube-runtime"
    executable = root / "deno" / version / "deno.exe"
    deno_dir = root / "deno-cache" / version
    return root, executable, deno_dir


def inspect_youtube_runtime(
    settings: Settings,
    *,
    manifest: YoutubeRuntimeManifest = DEFAULT_YOUTUBE_RUNTIME_MANIFEST,
    inventory: YoutubeRuntimeInventory | None = None,
) -> YoutubeRuntimeStatus:
    selected = inventory or SystemYoutubeRuntimeInventory()
    root, executable, deno_dir = _runtime_paths(settings, manifest)
    codes: list[str] = []

    yt_dlp_version = selected.package_version("yt-dlp")
    if yt_dlp_version is None:
        codes.append("yt_dlp_missing")
    else:
        try:
            normalized_yt_dlp = normalize_version(yt_dlp_version)
        except ValueError:
            normalized_yt_dlp = None
        if normalized_yt_dlp != manifest.yt_dlp_version:
            codes.append("yt_dlp_version_mismatch")

    ejs_version = selected.package_version("yt-dlp-ejs")
    if ejs_version is None:
        codes.append("ejs_missing")
    else:
        try:
            normalized_ejs = normalize_version(ejs_version)
        except ValueError:
            normalized_ejs = None
        if normalized_ejs != manifest.ejs_version:
            codes.append("ejs_version_mismatch")
        if not selected.ejs_integration_available():
            codes.append("ejs_integration_unavailable")

    manifest_configured = (
        manifest.ejs_package_sha256 is not None
        and manifest.deno_executable_sha256 is not None
    )
    if not manifest_configured:
        codes.append("manifest_unconfigured")

    if root.drive.casefold() != "d:" or not root.is_absolute():
        codes.append("runtime_root_not_approved")
    elif not selected.paths_are_safe(root, (executable, deno_dir)):
        codes.append("runtime_path_unsafe")

    if not executable.is_file():
        codes.append("deno_missing")
    if not deno_dir.is_dir():
        codes.append("deno_dir_missing")

    if selected.getenv("DENO_DIR") != str(deno_dir):
        codes.append("deno_dir_env_mismatch")

    if (
        ejs_version is not None
        and manifest.ejs_package_sha256 is not None
        and selected.package_hash("yt-dlp-ejs")
        != manifest.ejs_package_sha256
    ):
        codes.append("ejs_hash_mismatch")

    deno_hash_matches = False
    if executable.is_file() and manifest.deno_executable_sha256 is not None:
        try:
            deno_hash_matches = (
                selected.file_sha256(executable)
                == manifest.deno_executable_sha256
            )
        except OSError:
            deno_hash_matches = False
        if not deno_hash_matches:
            codes.append("deno_hash_mismatch")

    if (
        executable.is_file()
        and deno_hash_matches
        and "runtime_path_unsafe" not in codes
        and "runtime_root_not_approved" not in codes
    ):
        try:
            output = selected.run_deno_version(
                executable,
                deno_dir=deno_dir,
                timeout_seconds=2.0,
            )
            first_line = output.splitlines()[0] if output.splitlines() else ""
            deno_version = normalize_version(first_line)
        except (IndexError, OSError, RuntimeError, ValueError):
            codes.append("deno_version_unavailable")
        else:
            if deno_version != manifest.deno_version:
                codes.append("deno_version_mismatch")

    runtime = None
    if not codes:
        runtime = YoutubeRuntime(
            manifest=manifest,
            runtime_root=root,
            deno_executable=executable,
            deno_dir=deno_dir,
            js_runtimes=("deno",),
            remote_components=frozenset(),
            system_runtime_fallback=False,
        )
    return YoutubeRuntimeStatus(
        youtube_ready=runtime is not None,
        bilibili_ready=True,
        codes=tuple(codes),
        runtime_root=root,
        deno_executable=executable,
        deno_dir=deno_dir,
        runtime=runtime,
    )
