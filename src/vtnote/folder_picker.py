"""Native directory picker used by the loopback-only desktop UI."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class DirectoryPicker(Protocol):
    def __call__(self, initial_directory: Path) -> Path | None: ...


def pick_directory(initial_directory: Path) -> Path | None:
    """Open the operating system folder picker without reading directory contents."""

    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            parent=root,
            initialdir=str(initial_directory),
            mustexist=True,
        )
        root.destroy()
    except Exception as error:
        raise RuntimeError("native directory picker is unavailable") from error
    return Path(selected).absolute() if selected else None
