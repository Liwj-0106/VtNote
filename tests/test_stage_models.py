from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from vtnote.models import StageRunRecord
from vtnote.stage_models import allowed_stage_models


def _stage(
    stage: str,
    snapshot: dict[str, Any],
    override: dict[str, Any] | None = None,
) -> StageRunRecord:
    row = SimpleNamespace(
        stage=stage,
        retry_override_json=override,
        item=SimpleNamespace(task=SimpleNamespace(pipeline_snapshot_json=snapshot)),
    )
    return cast(StageRunRecord, row)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("local", ("local-model",)),
        ("cloud", ("cloud-model",)),
        ("auto", ("local-model", "cloud-model")),
    ],
)
def test_transcribe_models_follow_snapshot_mode(
    mode: str, expected: tuple[str, ...]
) -> None:
    row = _stage(
        "transcribe",
        {
            "local_whisper": {"model": "local-model"},
            "asr": {"mode": mode, "profile": {"model": "cloud-model"}},
        },
    )

    assert allowed_stage_models(row) == expected


def test_transcribe_retry_override_selects_the_retry_model() -> None:
    snapshot = {
        "local_whisper": {"model": "local-model"},
        "asr": {"mode": "cloud", "profile": {"model": "old-cloud-model"}},
    }

    assert allowed_stage_models(
        _stage("transcribe", snapshot, {"strategy": "local"})
    ) == ("local-model",)
    assert allowed_stage_models(
        _stage(
            "transcribe",
            snapshot,
            {
                "strategy": "cloud_confirmed",
                "asr": {"profile": {"model": "new-cloud-model"}},
            },
        )
    ) == ("new-cloud-model",)


def test_notes_retry_override_selects_the_retry_profile() -> None:
    row = _stage(
        "notes",
        {"notes": {"profile": {"model": "old-notes-model"}}},
        {"notes": {"profile": {"model": "new-notes-model"}}},
    )

    assert allowed_stage_models(row) == ("new-notes-model",)
