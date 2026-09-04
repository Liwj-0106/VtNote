"""Keep the offline test suite independent from user runtime configuration."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_vtnote_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("VTNOTE_"):
            monkeypatch.delenv(name, raising=False)
