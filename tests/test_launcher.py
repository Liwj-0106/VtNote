from __future__ import annotations

import socket
import sys

import pytest

from vtnote.config import Settings
from vtnote.launcher import (
    LauncherError,
    child_commands,
    ensure_port_available,
    stop_children,
)


def test_launcher_uses_explicit_python_argv_for_api_and_worker() -> None:
    commands = child_commands(python_executable=sys.executable)

    assert commands == {
        "api": (sys.executable, "-m", "vtnote", "api"),
        "worker": (sys.executable, "-m", "vtnote", "worker"),
    }
    assert all(isinstance(command, tuple) for command in commands.values())


def test_port_conflict_is_reported_before_children_start() -> None:
    settings = Settings(bind_host="127.0.0.1", bind_port=0x4455)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((settings.bind_host, settings.bind_port))
    listener.listen(1)
    try:
        with pytest.raises(LauncherError, match="port_unavailable"):
            ensure_port_available(settings)
    finally:
        listener.close()


class _Child:
    def __init__(self, *, exits: bool = False) -> None:
        self.exits = exits
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return 0 if self.exits else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> int:
        assert timeout > 0
        if self.exits or self.terminated:
            return 0
        raise TimeoutError

    def kill(self) -> None:
        self.killed = True
        self.exits = True


def test_launcher_shutdown_terminates_then_kills_only_stragglers() -> None:
    graceful = _Child()
    straggler = _Child()

    def wait(child: _Child, timeout: float) -> int:
        if child is graceful:
            return 0
        raise TimeoutError

    stop_children(
        {"api": graceful, "worker": straggler},
        timeout_seconds=0.1,
        waiter=wait,
    )

    assert graceful.terminated and not graceful.killed
    assert straggler.terminated and straggler.killed
