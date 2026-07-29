"""Guarded, resumable evidence journal for the user-authorized VtNote live POC."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from vtnote.diagnostics import sanitize_diagnostic


class PocValidationError(ValueError):
    """Raised before any live or billable POC side effect."""


class ContentAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged: Literal[True]
    rights_statement: str = Field(min_length=20, max_length=500)
    approved_by: str = Field(min_length=1, max_length=120)
    approved_at: datetime


class BillingAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged: Literal[True]
    maximum_cny: float = Field(gt=0, le=100_000)
    approved_by: str = Field(min_length=1, max_length=120)
    approved_at: datetime


class TencentAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    profile_revision: int = Field(gt=0)
    upload_consent_revision: int = Field(gt=0)
    attested_at: datetime


class BailianAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    profile_revision: int = Field(gt=0)
    text_consent_revision: int = Field(gt=0)
    workspace_region: Literal["beijing"]
    attested_at: datetime


class ProviderAuthorizations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tencent_asr: TencentAuthorization
    aliyun_bailian: BailianAuthorization


class NetworkEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str = Field(min_length=1, max_length=80)
    operator: str = Field(min_length=1, max_length=120)
    captured_at: datetime
    direct_only: Literal[True]


class PocSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    source_kind: Literal["bilibili", "youtube", "local_media"]
    source_ref: str = Field(min_length=1, max_length=1_024)
    language: Literal["mandarin", "english", "mixed"]
    duration_band: Literal["3_10m", "10_40m", "40_120m"]
    subtitle_kind: Literal["manual", "automatic", "none"]
    audio_conditions: list[
        Literal["clear_speech", "background_music", "accent", "overlap"]
    ] = Field(min_length=1)
    planned_routes: list[
        Literal[
            "platform_subtitle",
            "tencent_inline",
            "tencent_cos",
            "local_asr",
            "translation",
            "notes",
            "recovery",
        ]
    ] = Field(min_length=1)
    content_authorized: Literal[True]
    login_required: Literal[False]
    drm_protected: Literal[False]
    network: NetworkEvidence


class PocManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    content_authorization: ContentAuthorization
    billing_authorization: BillingAuthorization
    provider_authorizations: ProviderAuthorizations
    samples: list[PocSample] = Field(min_length=30, max_length=50)

    @model_validator(mode="after")
    def validate_corpus_coverage(self) -> "PocManifest":
        ids = [sample.id for sample in self.samples]
        if len(set(ids)) != len(ids):
            raise ValueError("sample IDs must be unique")
        _require_counts(
            "source_kind",
            Counter(sample.source_kind for sample in self.samples),
            {"bilibili": 8, "youtube": 8, "local_media": 8},
        )
        _require_counts(
            "language",
            Counter(sample.language for sample in self.samples),
            {"mandarin": 8, "english": 8, "mixed": 8},
        )
        _require_coverage(
            "duration_band",
            {sample.duration_band for sample in self.samples},
            {"3_10m", "10_40m", "40_120m"},
        )
        _require_coverage(
            "subtitle_kind",
            {sample.subtitle_kind for sample in self.samples},
            {"manual", "automatic", "none"},
        )
        _require_coverage(
            "audio_conditions",
            {
                condition
                for sample in self.samples
                for condition in sample.audio_conditions
            },
            {"clear_speech", "background_music", "accent", "overlap"},
        )
        _require_coverage(
            "planned_routes",
            {route for sample in self.samples for route in sample.planned_routes},
            {
                "platform_subtitle",
                "tencent_inline",
                "tencent_cos",
                "local_asr",
                "translation",
                "notes",
                "recovery",
            },
        )
        return self


def _require_counts(
    label: str,
    actual: Counter[str],
    required: Mapping[str, int],
) -> None:
    missing = {
        name: minimum
        for name, minimum in required.items()
        if actual[name] < minimum
    }
    if missing:
        raise ValueError(f"{label} minimum coverage is missing: {missing}")


def _require_coverage(
    label: str,
    actual: set[str],
    required: set[str],
) -> None:
    missing = sorted(required - actual)
    if missing:
        raise ValueError(f"{label} coverage is missing: {missing}")


def validate_manifest(payload: Mapping[str, Any]) -> PocManifest:
    try:
        return PocManifest.model_validate(payload)
    except ValidationError as exc:
        message = str(exc)
        if "content_authorized" in message:
            message = f"every sample must be authorized; {message}"
        raise PocValidationError(message) from exc


def load_manifest(path: Path) -> PocManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PocValidationError("manifest is not readable valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise PocValidationError("manifest root must be a JSON object")
    return validate_manifest(payload)


def validate_live_gates(
    manifest: PocManifest,
    *,
    output_dir: Path,
    allow_network: bool,
    allow_billing: bool,
) -> None:
    del manifest
    if not allow_network:
        raise PocValidationError("--allow-network is required for a live POC")
    if not allow_billing:
        raise PocValidationError("--allow-billing is required for a live POC")
    selected = Path(output_dir)
    if not selected.is_absolute() or selected.drive.casefold() != "d:":
        raise PocValidationError("live POC output must be an absolute D-drive directory")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with staging.open("xb") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        if staging.exists():
            staging.unlink()


def initialize_run(
    output_dir: Path,
    manifest: PocManifest,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    if state_path.exists():
        state = _read_state(state_path)
        if state.get("run_id") != manifest.run_id:
            raise PocValidationError("existing state belongs to another run")
        return state_path
    manifest_payload = manifest.model_dump(mode="json")
    manifest_bytes = json.dumps(
        manifest_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    _atomic_json(
        state_path,
        {
            "schema_version": 1,
            "run_id": manifest.run_id,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "authorized_sample_ids": sorted(sample.id for sample in manifest.samples),
            "maximum_cny": manifest.billing_authorization.maximum_cny,
            "created_at": now().astimezone(timezone.utc).isoformat(),
            "records": [],
        },
    )
    return state_path


def _read_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PocValidationError("POC state is unreadable or corrupt") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("records"), list)
    ):
        raise PocValidationError("POC state has an unsupported shape")
    return payload


_SENSITIVE_KEY = re.compile(
    r"(?i)(authorization|credential|secret|token|api[_-]?key|prompt|"
    r"raw[_-]?response|source[_-]?ref|source[_-]?path|url|path)"
)


def _sanitize_mapping(
    payload: Mapping[str, Any],
    *,
    numeric_only: bool,
) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if _SENSITIVE_KEY.search(str(key)):
            continue
        safe_key = str(key)[:80]
        if numeric_only:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PocValidationError("raw metrics must contain numeric values only")
            cleaned[safe_key] = value
        elif isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[safe_key] = (
                sanitize_diagnostic(value, max_length=500)
                if isinstance(value, str)
                else value
            )
        else:
            raise PocValidationError("evidence values must be scalar")
    return cleaned


def record_stage_result(
    output_dir: Path,
    *,
    sample_id: str,
    stage: str,
    status: Literal["completed", "failed", "submission_unknown", "canceled"],
    billable: bool,
    raw_metrics: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    state_path = Path(output_dir) / "state.json"
    state = _read_state(state_path)
    records: list[dict[str, Any]] = state["records"]
    if sample_id not in state.get("authorized_sample_ids", []):
        raise PocValidationError("sample is not authorized by this POC manifest")
    for existing in records:
        if existing.get("sample_id") == sample_id and existing.get("stage") == stage:
            if existing.get("billable") or existing.get("status") in {
                "completed",
                "submission_unknown",
            }:
                raise PocValidationError(
                    "completed or possibly submitted paid stage must not be replayed"
                )
            raise PocValidationError("stage result already exists")
    safe_metrics = _sanitize_mapping(raw_metrics, numeric_only=True)
    projected_billing = sum(
        float(record.get("raw_metrics", {}).get("billed_cny", 0))
        for record in records
    ) + float(safe_metrics.get("billed_cny", 0))
    if projected_billing > float(state.get("maximum_cny", 0)):
        raise PocValidationError("recorded billing would exceed the approved maximum")
    records.append(
        {
            "sample_id": sample_id,
            "stage": stage,
            "status": status,
            "billable": billable,
            "raw_metrics": safe_metrics,
            "evidence": _sanitize_mapping(evidence, numeric_only=False),
        }
    )
    _atomic_json(state_path, state)


def aggregate_results(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(record.get("status")) for record in records)
    metrics = [
        record.get("raw_metrics", {})
        for record in records
        if isinstance(record.get("raw_metrics"), Mapping)
    ]
    billed = sum(float(metric.get("billed_cny", 0)) for metric in metrics)
    cer = [float(metric["cer"]) for metric in metrics if "cer" in metric]
    rtf = [
        float(metric["wall_time_ms"]) / float(metric["audio_duration_ms"])
        for metric in metrics
        if float(metric.get("audio_duration_ms", 0)) > 0
        and "wall_time_ms" in metric
    ]
    return {
        "record_count": len(records),
        "status_counts": dict(sorted(statuses.items())),
        "total_billed_cny": billed,
        "mean_cer": sum(cer) / len(cer) if cer else None,
        "mean_real_time_factor": sum(rtf) / len(rtf) if rtf else None,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--allow-billing", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.validate_only:
            print(f"manifest valid: {len(manifest.samples)} authorized samples")
            return 0
        if args.output is None:
            raise PocValidationError("--output is required for a live POC")
        validate_live_gates(
            manifest,
            output_dir=args.output,
            allow_network=args.allow_network,
            allow_billing=args.allow_billing,
        )
        initialize_run(args.output, manifest)
        print(
            "live gates passed; state initialized. "
            "Run stages through the reviewed VtNote adapter and record every result."
        )
        return 0
    except PocValidationError as exc:
        print(f"POC refused: {sanitize_diagnostic(str(exc), max_length=2_000)}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
