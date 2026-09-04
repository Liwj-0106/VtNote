from __future__ import annotations

import os
from pathlib import Path

from vtnote.native_runtime import configure_windows_native_runtime


def test_windows_native_runtime_prepends_conda_library_bin_once(
    tmp_path: Path,
) -> None:
    library_bin = tmp_path / "Library" / "bin"
    library_bin.mkdir(parents=True)
    environment = {"PATH": os.pathsep.join(("first", "second"))}
    registrations: list[str] = []
    handle = object()

    first = configure_windows_native_runtime(
        prefix=tmp_path,
        environ=environment,
        platform_name="nt",
        add_dll_directory=lambda path: registrations.append(path) or handle,
    )
    second = configure_windows_native_runtime(
        prefix=tmp_path,
        environ=environment,
        platform_name="nt",
        add_dll_directory=lambda path: registrations.append(path) or handle,
    )

    assert first == second == (library_bin,)
    assert environment["PATH"].split(os.pathsep) == [
        str(library_bin),
        "first",
        "second",
    ]
    assert registrations == [str(library_bin)]


def test_native_runtime_is_a_noop_outside_windows(tmp_path: Path) -> None:
    environment = {"PATH": "unchanged"}

    configured = configure_windows_native_runtime(
        prefix=tmp_path,
        environ=environment,
        platform_name="posix",
    )

    assert configured == ()
    assert environment == {"PATH": "unchanged"}
