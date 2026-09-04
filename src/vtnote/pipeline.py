"""Shared pipeline stage relationships and status aggregation rules."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Literal, TypedDict, cast


TERMINAL_STATUSES = frozenset(
    {"canceled", "completed", "completed_with_warnings", "failed"}
)
RETRYABLE_STAGE_STATUSES = frozenset({"failed", "canceled"})
ACTIVE_STAGE_STATUSES = frozenset(
    {"running", "waiting_external", "cancel_requested"}
)
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
    {
        "queued",
        "running",
        "waiting_external",
        "cancel_requested",
        "canceled",
        "failed",
        "completed",
        "skipped",
    }
)
_CORE_STAGES = ("source", "transcribe")
_PROGRESS_KEYS = frozenset({"current", "total", "unit", "message_code"})
_PROGRESS_UNITS = frozenset({"bytes", "segments", "cues", "chunks", "items"})
_SAFE_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MODEL_CODE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._:-]{0,63})?$"
)
_TRACK_ID = re.compile(r"^trk_[0-9a-f]{64}$")
_MAX_SAFE_JSON_INTEGER = (1 << 53) - 1
_PROGRESS_MESSAGE_CODES = frozenset(
    {
        "downloading_audio",
        "finalizing_artifacts",
        "merging_notes",
        "preparing_audio",
        "probing_source",
        "selecting_subtitle",
        "submitting_cloud_asr",
        "summarizing_chunks",
        "transcribing_segments",
        "translating_cues",
        "uploading_audio",
        "waiting_cloud_asr",
    }
)
_FALLBACK_REASON_CODES = frozenset(
    {
        "cloud_duration_exceeded",
        "cloud_language_unsupported",
        "cloud_network_error",
        "cloud_payload_exceeded",
        "cloud_profile_unavailable",
        "cloud_rate_limited",
        "cloud_result_invalid",
        "cloud_server_error",
        "cloud_submission_unknown",
        "platform_subtitle_invalid",
        "platform_subtitle_unavailable",
    }
)
_PROVIDER_IDS = frozenset(
    {
        "aliyun_bailian",
        "bilibili",
        "douyin",
        "faster_whisper",
        "sensevoice_sherpa_onnx",
        "moss_transcribe_diarize",
        "tencent_recording_asr",
        "tencent_tokenhub",
        "youtube",
    }
)
_PROVIDER_STATUS_CODES = frozenset({"waiting", "doing", "success", "failed"})
_EVIDENCE_KEYS = (
    "source_method",
    "selected_track_id",
    "asr_route",
    "provider",
    "model",
    "fallback_reason",
    "detected_language",
    "runtime_device",
    "chunk_recovery",
)
_SOURCE_METHODS = frozenset(
    {
        "platform_audio",
        "platform_subtitle",
        "uploaded_subtitle",
        "local_subtitle",
        "cloud_asr",
        "local_asr",
    }
)
_ASR_ROUTES = frozenset({"platform_subtitle", "cloud", "local", "cloud_to_local"})
ProgressUnit = Literal["bytes", "segments", "cues", "chunks", "items"]


class StageProgress(TypedDict):
    current: int | None
    total: int | None
    unit: ProgressUnit | None
    message_code: str


class ExecutionEvidence(TypedDict, total=False):
    source_method: str
    selected_track_id: str
    asr_route: str
    provider: str
    model: str
    fallback_reason: str
    detected_language: str
    runtime_device: str
    chunk_recovery: str


def _bounded_nonnegative_integer(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > _MAX_SAFE_JSON_INTEGER:
        raise ValueError(f"invalid stage progress {field}")
    return value


def validate_stage_progress(progress: object) -> StageProgress:
    """Return a storage-safe progress object with an exact public shape."""

    if not isinstance(progress, Mapping):
        raise ValueError("invalid stage progress")
    if set(progress) != _PROGRESS_KEYS:
        raise ValueError("invalid stage progress fields")
    current = _bounded_nonnegative_integer(progress["current"], field="current")
    total = _bounded_nonnegative_integer(progress["total"], field="total")
    if total == 0:
        raise ValueError("stage progress total must be positive")
    if current is not None and total is not None and current > total:
        raise ValueError("stage progress current exceeds total")
    unit = progress["unit"]
    if unit is not None and (
        not isinstance(unit, str) or unit not in _PROGRESS_UNITS
    ):
        raise ValueError("invalid stage progress unit")
    if (current is not None or total is not None) and unit is None:
        raise ValueError("stage progress values require a unit")
    if current is None and total is None and unit is not None:
        raise ValueError("stage progress unit requires a value")
    message_code = progress["message_code"]
    if (
        not isinstance(message_code, str)
        or message_code not in _PROGRESS_MESSAGE_CODES
    ):
        raise ValueError("invalid stage progress message code")
    return {
        "current": current,
        "total": total,
        "unit": cast(ProgressUnit | None, unit),
        "message_code": message_code,
    }


def validate_execution_evidence(
    evidence: object,
    *,
    allowed_models: Iterable[str] = (),
) -> ExecutionEvidence:
    """Accept only bounded provider-neutral codes, never provider free text."""

    if not isinstance(evidence, Mapping):
        raise ValueError("invalid execution evidence")
    unknown = set(evidence) - set(_EVIDENCE_KEYS)
    if unknown or not evidence:
        raise ValueError("invalid execution evidence fields")
    safe_models = frozenset(
        model
        for model in allowed_models
        if (
            isinstance(model, str)
            and len(model) <= 128
            and _MODEL_CODE.fullmatch(model) is not None
        )
    )
    normalized: ExecutionEvidence = {}
    for field in _EVIDENCE_KEYS:
        if field not in evidence:
            continue
        value = evidence[field]
        if not isinstance(value, str):
            raise ValueError(f"invalid execution evidence {field}")
        if field == "source_method" and value not in _SOURCE_METHODS:
            raise ValueError("invalid execution evidence source method")
        if field == "selected_track_id" and _TRACK_ID.fullmatch(value) is None:
            raise ValueError("invalid execution evidence track id")
        if field == "asr_route" and value not in _ASR_ROUTES:
            raise ValueError("invalid execution evidence ASR route")
        if field == "provider" and value not in _PROVIDER_IDS:
            raise ValueError("invalid execution evidence provider")
        if field == "model" and value not in safe_models:
            raise ValueError("invalid execution evidence model")
        if field == "fallback_reason" and value not in _FALLBACK_REASON_CODES:
            raise ValueError("invalid execution evidence fallback reason")
        if field == "detected_language" and _SAFE_CODE.fullmatch(value) is None:
            raise ValueError("invalid execution evidence language")
        if field == "runtime_device" and value not in {"cuda", "cpu"}:
            raise ValueError("invalid execution evidence runtime device")
        if field == "chunk_recovery" and value not in {"used", "unused"}:
            raise ValueError("invalid execution evidence chunk recovery")
        normalized[field] = value
    return normalized


def validate_provider_status_code(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _PROVIDER_STATUS_CODES:
        raise ValueError("invalid provider status code")
    return value


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
    if any(
        status in {"running", "waiting_external"}
        for status in latest_status_by_stage.values()
    ):
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
