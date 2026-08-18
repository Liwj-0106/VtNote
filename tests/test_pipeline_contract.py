from __future__ import annotations

import pytest

from vtnote import pipeline as pipeline_contract
from vtnote.pipeline import (
    RETRY_ACTIVE_CONFLICTS,
    STAGE_DEPENDENCIES,
    aggregate_item_status,
    aggregate_task_status,
)


def test_shared_pipeline_keeps_translation_and_notes_as_parallel_branches() -> None:
    assert STAGE_DEPENDENCIES == {
        "source": frozenset(),
        "transcribe": frozenset({"source"}),
        "translate": frozenset({"transcribe"}),
        "notes": frozenset({"transcribe"}),
    }
    assert "notes" not in RETRY_ACTIVE_CONFLICTS["translate"]
    assert "translate" not in RETRY_ACTIVE_CONFLICTS["notes"]


def test_shared_pipeline_aggregates_core_and_optional_stage_outcomes() -> None:
    core_success = {"source": "completed", "transcribe": "completed"}

    assert aggregate_item_status(core_success) == "completed"
    assert aggregate_item_status(
        {**core_success, "translate": "failed", "notes": "completed"}
    ) == "completed_with_warnings"
    assert aggregate_item_status(
        {**core_success, "translate": "completed", "notes": "running"}
    ) == "running"
    assert aggregate_item_status(
        {"source": "completed", "transcribe": "failed"}
    ) == "failed"
    assert aggregate_task_status(["completed", "completed_with_warnings"]) == (
        "completed_with_warnings"
    )


@pytest.mark.parametrize(
    "progress",
    [
        {"current": True, "total": 10, "unit": "items", "message_code": "working"},
        {"current": -1, "total": 10, "unit": "items", "message_code": "working"},
        {"current": 11, "total": 10, "unit": "items", "message_code": "working"},
        {"current": 0, "total": 0, "unit": "items", "message_code": "working"},
        {
            "current": 1 << 53,
            "total": 1 << 53,
            "unit": "bytes",
            "message_code": "working",
        },
        {"current": 1, "total": 10, "unit": ["items"], "message_code": "working"},
        {"current": 1, "total": 10, "unit": "seconds", "message_code": "working"},
        {"current": 1, "total": 10, "unit": "items", "message_code": "Not free text"},
        {
            "current": 1,
            "total": 10,
            "unit": "items",
            "message_code": "abcdef0123456789abcdef0123456789",
        },
        {
            "current": 1,
            "total": 10,
            "unit": "items",
            "message_code": "working",
            "provider_message": "raw",
        },
    ],
)
def test_stage_progress_contract_rejects_invalid_shapes(
    progress: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        pipeline_contract.validate_stage_progress(progress)


@pytest.mark.parametrize(
    "evidence",
    [
        {"provider_message": "raw provider response"},
        {"provider": {"name": "tencent"}},
        {"provider": "tencent\nAuthorization"},
        {"provider": "AKIDEXAMPLE1234567890"},
        {"provider": "b4cb2d8d-1a37-44c7-8fa1-bb4b23967c39"},
        {"model": "b4cb2d8d-1a37-44c7-8fa1-bb4b23967c39"},
        {"fallback_reason": "human readable free text"},
        {"fallback_reason": "abcdef0123456789abcdef0123456789"},
        {"selected_track_id": "subtitle-1"},
    ],
)
def test_execution_evidence_contract_rejects_unknown_or_free_text(
    evidence: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        pipeline_contract.validate_execution_evidence(evidence)


def test_execution_evidence_accepts_registered_fallback_reason() -> None:
    assert pipeline_contract.validate_execution_evidence(
        {"fallback_reason": "cloud_rate_limited"}
    ) == {"fallback_reason": "cloud_rate_limited"}


@pytest.mark.parametrize(
    "status_code",
    [
        123,
        True,
        {"code": "waiting"},
        "18446744073709551615",
        "b4cb2d8d-1a37-44c7-8fa1-bb4b23967c39",
        "AKIDEXAMPLE1234567890",
    ],
)
def test_provider_status_contract_rejects_unknown_or_non_string_values(
    status_code: object,
) -> None:
    with pytest.raises(ValueError):
        pipeline_contract.validate_provider_status_code(status_code)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (
            {
                "source": "failed",
                "transcribe": "queued",
                "translate": "queued",
                "notes": "queued",
            },
            "failed",
        ),
        (
            {
                "source": "completed",
                "transcribe": "canceled",
                "translate": "queued",
                "notes": "queued",
            },
            "canceled",
        ),
    ],
)
def test_core_terminal_outcome_dominates_queued_dependents(
    statuses: dict[str, str], expected: str
) -> None:
    assert aggregate_item_status(statuses) == expected
