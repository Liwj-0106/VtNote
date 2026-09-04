"""Build and verify a self-contained VtNote wheel from the project root."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
DIST = (ROOT / "dist").resolve()
PACKAGE_CACHE = (ROOT / ".vtnote" / "Cache" / "package").resolve()
REQUIRED_MEMBERS = {
    "vtnote/web/index.html",
    "vtnote/resources/models/large-v3-turbo.manifest.json",
    "vtnote/resources/models/sensevoice-small-int8.manifest.json",
    "vtnote/resources/models/silero-vad.manifest.json",
    "vtnote/resources/test-audio/tencent-asr-check.wav",
}
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")


@contextmanager
def package_lock() -> Iterator[None]:
    lock_path = PACKAGE_CACHE / "package.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("another VtNote package build is already running") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run() -> Path:
    if DIST.parent != ROOT.resolve():
        raise RuntimeError("package output escaped the project root")
    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir()
    build_parent = (PACKAGE_CACHE / "builds").resolve()
    if ROOT.resolve() not in build_parent.parents:
        raise RuntimeError("package build directory escaped the project root")
    build_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="build-",
        dir=build_parent,
        ignore_cleanup_errors=True,
    ) as build_temp:
        environment = os.environ.copy()
        environment.update({"TEMP": build_temp, "TMP": build_temp})
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "--wheel-dir",
                str(DIST),
            ],
            cwd=ROOT,
            env=environment,
            check=True,
        )
    wheels = list(DIST.glob("vtnote-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected exactly one VtNote wheel")
    wheel = wheels[0]
    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
        missing = REQUIRED_MEMBERS - members
        if missing or not any(name.startswith("vtnote/web/assets/") for name in members):
            raise RuntimeError("wheel is missing bundled runtime resources")
        for name in members:
            if not name.endswith((".py", ".json", ".html", ".css", ".js", ".txt")):
                continue
            content = archive.read(name).decode("utf-8", errors="ignore")
            if WINDOWS_ABSOLUTE_PATH.search(content):
                raise RuntimeError(f"wheel contains a machine-specific path: {name}")
    return wheel


def verify_install(wheel: Path) -> None:
    smoke_root = (PACKAGE_CACHE / "smoke").resolve()
    if ROOT.resolve() not in smoke_root.parents:
        raise RuntimeError("package smoke directory escaped the project root")
    shutil.rmtree(smoke_root, ignore_errors=True)
    site = smoke_root / "site"
    site.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(site),
            str(wheel),
        ],
        cwd=smoke_root,
        check=True,
    )
    environment = os.environ.copy()
    environment["VTNOTE_WHEEL_SITE"] = str(site)
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os,sys;"
                "sys.path.insert(0,os.environ['VTNOTE_WHEEL_SITE']);"
                "from vtnote.project_resources import "
                "bundled_asset,frontend_dist_path,repository_root;"
                "assert repository_root() is None;"
                "assert (frontend_dist_path()/'index.html').is_file();"
                "assert bundled_asset('models','large-v3-turbo.manifest.json').is_file();"
                "assert bundled_asset('models','sensevoice-small-int8.manifest.json').is_file();"
                "assert bundled_asset('models','silero-vad.manifest.json').is_file();"
                "assert bundled_asset('test-audio','tencent-asr-check.wav').is_file()"
            ),
        ],
        cwd=smoke_root,
        env=environment,
        check=True,
    )


if __name__ == "__main__":
    with package_lock():
        result = run()
        verify_install(result)
        print(result.name)
