"""Windows native-library bootstrap for the declared Conda environment."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, MutableMapping
from pathlib import Path


_DLL_DIRECTORY_HANDLES: list[object] = []
_REGISTERED_DLL_DIRECTORIES: set[str] = set()


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def configure_windows_native_runtime(
    *,
    prefix: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
    platform_name: str | None = None,
    add_dll_directory: Callable[[str], object] | None = None,
) -> tuple[Path, ...]:
    """Expose Conda CUDA DLLs to native loaders and supervised children.

    Selecting a Conda ``python.exe`` directly does not activate the environment.
    CTranslate2 can still detect the NVIDIA driver in that state, but its delayed
    cuBLAS/cuDNN loads fail when inference begins.  Updating ``PATH`` is required
    for those delayed loads; retaining ``add_dll_directory`` handles also covers
    Python extension-module loading on current Windows versions.
    """

    selected_platform = os.name if platform_name is None else platform_name
    if selected_platform != "nt":
        return ()

    selected_prefix = Path(sys.prefix if prefix is None else prefix)
    library_bin = selected_prefix / "Library" / "bin"
    if not library_bin.is_dir():
        return ()

    selected_environ = os.environ if environ is None else environ
    current_path = selected_environ.get("PATH", "")
    entries = tuple(entry for entry in current_path.split(os.pathsep) if entry)
    library_key = _path_key(library_bin)
    if all(_path_key(Path(entry)) != library_key for entry in entries):
        selected_environ["PATH"] = os.pathsep.join(
            (str(library_bin), *entries)
        )

    if library_key not in _REGISTERED_DLL_DIRECTORIES:
        register = (
            getattr(os, "add_dll_directory", None)
            if add_dll_directory is None
            else add_dll_directory
        )
        if callable(register):
            try:
                handle = register(str(library_bin))
            except OSError:
                pass
            else:
                _DLL_DIRECTORY_HANDLES.append(handle)
        _REGISTERED_DLL_DIRECTORIES.add(library_key)

    return (library_bin,)
