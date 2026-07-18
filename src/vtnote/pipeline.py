"""Shared pipeline stage relationships and status aggregation rules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


TERMINAL_STATUSES = frozenset(
    {"canceled", "completed", "completed_with_warnings", "failed"}
)
RETRYABLE_STAGE_STATUSES = frozenset({"failed", "canceled"})
ACTIVE_STAGE_STATUSES = frozenset({"running", "cancel_requested"})
SUCCESSFUL_STAGE_STATUSES = frozenset({"completed", "skipped"})
STAGE_ORDER = {"source": 0, "transcribe": 1, "translate": 2, "notes": 3}
STAGE_DEPENDENCIES = {
    "source": frozenset(),
    "transcribe": frozenset({"source"}),
    "translate": frozenset({"transcribe"}),
    "notes": frozenset({"transcribe"}),
}
RETRY_ACTIVE_CONFLICTS = {
    "source": frozenset({"source", "transcribe", "translate", "notes"}),
    "transcribe": frozenset({"source", "transcribe", "translate", "notes"}),
    "translate": frozenset({"source", "transcribe", "translate"}),
    "notes": frozenset({"source", "transcribe", "notes"}),
}

_KNOWN_STAGE_STATUSES = frozenset(
    {"queued", "running", "cancel_requested", "canceled", "failed", "completed", "skipped"}
)
_CORE_STAGES = ("source", "transcribe")


def aggregate_item_status(
    latest_status_by_stage: Mapping[str, str], *, has_warnings: bool = False
) -> str:
    """Aggregate the latest stage attempts without coupling optional branches."""

    if set(latest_status_by_stage) - set(STAGE_ORDER):
        raise ValueError("unknown pipeline stage")
    if set(latest_status_by_stage.values()) - _KNOWN_STAGE_STATUSES:
        raise ValueError("unknown pipeline stage status")
    if any(status == "cancel_requested" for status in latest_status_by_stage.values()):
        return "cancel_requested"
    if any(status == "running" for status in latest_status_by_stage.values()):
        return "running"

    core_statuses = [latest_status_by_stage.get(stage) for stage in _CORE_STAGES]
    if any(status == "failed" for status in core_statuses):
        return "failed"
    if any(status == "canceled" for status in core_statuses):
        return "canceled"
    if any(status == "queued" for status in latest_status_by_stage.values()):
        return "queued"
    if any(status not in SUCCESSFUL_STAGE_STATUSES for status in core_statuses):
        return "queued"

    optional_statuses = [
        status
        for stage, status in latest_status_by_stage.items()
        if stage not in _CORE_STAGES
    ]
    if has_warnings or any(status in {"failed", "canceled"} for status in optional_statuses):
        return "completed_with_warnings"
    return "completed"


def aggregate_task_status(item_statuses: Iterable[str]) -> str:
    """Aggregate item outcomes while retaining partial-success warnings."""

    statuses = tuple(item_statuses)
    if not statuses:
        return "queued"
    known = TERMINAL_STATUSES | frozenset({"queued", "running", "cancel_requested"})
    if set(statuses) - known:
        raise ValueError("unknown item status")
    if "cancel_requested" in statuses:
        return "cancel_requested"
    if "running" in statuses:
        return "running"
    if "queued" in statuses:
        return "queued"
    if "failed" in statuses:
        return "failed"
    if all(status == "canceled" for status in statuses):
        return "canceled"
    if "completed_with_warnings" in statuses or "canceled" in statuses:
        return "completed_with_warnings"
    return "completed"
