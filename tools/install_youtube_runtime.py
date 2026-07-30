"""Install one user-supplied, pinned Deno archive into VtNote's D-drive cache."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile


DENO_VERSION = "2.8.1"
DENO_ARCHIVE_SHA256 = (
    "5fb5bac71f609fb91ec8960fb290885aadc27eeb22f07a8eca0c3db6be38b11a"
)
DENO_EXECUTABLE_SHA256 = (
    "a8afddac131261dc9e085c6a1a79544f0567bd09e481034b5d1533588cba9b30"
)
DEFAULT_RUNTIME_ROOT = Path(
    r"D:\Workspace\Codex\cache\VtNote-runtime\youtube-runtime"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(archive: Path, runtime_root: Path) -> Path:
    selected = archive.resolve(strict=True)
    root = runtime_root.resolve(strict=False)
    if (
        not selected.is_file()
        or selected.suffix.casefold() != ".zip"
        or root.drive.casefold() != "d:"
    ):
        raise ValueError("invalid Deno archive or runtime root")
    if sha256(selected) != DENO_ARCHIVE_SHA256:
        raise ValueError("Deno archive hash mismatch")
    destination = root / "deno" / DENO_VERSION / "deno.exe"
    if destination.is_file():
        if sha256(destination) != DENO_EXECUTABLE_SHA256:
            raise ValueError("existing Deno executable hash mismatch")
        (root / "deno-cache" / DENO_VERSION).mkdir(parents=True, exist_ok=True)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(prefix="deno-install-", dir=root)
    )
    staging = staging_root / "deno.exe"
    try:
        with ZipFile(selected) as bundle:
            names = bundle.namelist()
            if names != ["deno.exe"]:
                raise ValueError("Deno archive layout is invalid")
            with bundle.open("deno.exe") as source, staging.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        if sha256(staging) != DENO_EXECUTABLE_SHA256:
            raise ValueError("Deno executable hash mismatch")
        os.replace(staging, destination)
        (root / "deno-cache" / DENO_VERSION).mkdir(parents=True, exist_ok=True)
        return destination
    except BadZipFile:
        raise ValueError("Deno archive is invalid") from None
    finally:
        if staging.exists():
            staging.unlink()
        try:
            staging_root.rmdir()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--acknowledge-install", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_install:
        parser.error("--acknowledge-install is required")
    destination = install(args.archive, args.runtime_root)
    print(f"installed Deno {DENO_VERSION}: {destination.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
