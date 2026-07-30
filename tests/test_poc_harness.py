from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.run_live_poc import (
    PocValidationError,
    aggregate_results,
    initialize_run,
    record_stage_result,
    validate_live_gates,
    validate_manifest,
)


NOW = "2026-07-30T08:00:00Z"


def _manifest() -> dict[str, object]:
    platforms = ["bilibili"] * 10 + ["youtube"] * 10 + ["local_media"] * 10
    languages = ["mandarin"] * 10 + ["english"] * 10 + ["mixed"] * 10
    duration_bands = ["3_10m", "10_40m", "40_120m"] * 10
    subtitle_kinds = ["manual", "automatic", "none"] * 10
    conditions = ["clear_speech", "background_music", "accent", "overlap"]
    routes = [
        "platform_subtitle",
        "tencent_inline",
        "tencent_cos",
        "local_asr",
        "translation",
        "notes",
        "recovery",
    ]
    samples = []
    for index in range(30):
        samples.append(
            {
                "id": f"sample_{index + 1:03d}",
                "source_kind": platforms[index],
                "source_ref": f"authorized-source-{index + 1}",
                "language": languages[index],
                "duration_band": duration_bands[index],
                "subtitle_kind": subtitle_kinds[index],
                "audio_conditions": [conditions[index % len(conditions)]],
                "planned_routes": [routes[index % len(routes)]],
                "content_authorized": True,
                "login_required": False,
                "drm_protected": False,
                "network": {
                    "region": "CN",
                    "operator": "test-network",
                    "captured_at": NOW,
                    "direct_only": True,
                },
            }
        )
    return {
        "schema_version": 1,
        "run_id": "vtnote-poc-20260730",
        "content_authorization": {
            "acknowledged": True,
            "rights_statement": "Owner confirms a lawful right to process every sample.",
            "approved_by": "owner",
            "approved_at": NOW,
        },
        "billing_authorization": {
            "acknowledged": True,
            "maximum_cny": 100,
            "approved_by": "owner",
            "approved_at": NOW,
        },
        "provider_authorizations": {
            "tencent_asr": {
                "connection_id": "11111111-1111-4111-8111-111111111111",
                "profile_revision": 1,
                "upload_consent_revision": 1,
                "attested_at": NOW,
            },
            "aliyun_bailian": {
                "connection_id": "22222222-2222-4222-8222-222222222222",
                "profile_revision": 1,
                "text_consent_revision": 1,
                "workspace_region": "beijing",
                "attested_at": NOW,
            },
        },
        "samples": samples,
    }


def test_manifest_requires_authorized_30_to_50_sample_coverage() -> None:
    validated = validate_manifest(_manifest())
    assert len(validated.samples) == 30

    too_small = _manifest()
    too_small["samples"] = too_small["samples"][:29]  # type: ignore[index]
    with pytest.raises(PocValidationError, match="30"):
        validate_manifest(too_small)

    uncovered = _manifest()
    for sample in uncovered["samples"]:  # type: ignore[union-attr]
        sample["source_kind"] = "bilibili"
    with pytest.raises(PocValidationError, match="source_kind"):
        validate_manifest(uncovered)

    unauthorized = _manifest()
    unauthorized["samples"][0]["content_authorized"] = False  # type: ignore[index]
    with pytest.raises(PocValidationError, match="authorized"):
        validate_manifest(unauthorized)


def test_live_run_requires_explicit_network_billing_and_d_drive(
    tmp_path: Path,
) -> None:
    manifest = validate_manifest(_manifest())
    with pytest.raises(PocValidationError, match="allow-network"):
        validate_live_gates(
            manifest,
            output_dir=tmp_path,
            allow_network=False,
            allow_billing=True,
        )
    with pytest.raises(PocValidationError, match="allow-billing"):
        validate_live_gates(
            manifest,
            output_dir=tmp_path,
            allow_network=True,
            allow_billing=False,
        )

    non_d_drive = Path("C:/temp/vtnote-poc")
    with pytest.raises(PocValidationError, match="D-drive"):
        validate_live_gates(
            manifest,
            output_dir=non_d_drive,
            allow_network=True,
            allow_billing=True,
        )


def test_redacted_journal_preserves_raw_metrics_and_prevents_paid_replay(
    tmp_path: Path,
) -> None:
    output = tmp_path / "poc"
    manifest = validate_manifest(_manifest())
    initialize_run(output, manifest, now=lambda: datetime.now(timezone.utc))
    record_stage_result(
        output,
        sample_id="sample_001",
        stage="tencent_inline",
        status="completed",
        billable=True,
        raw_metrics={
            "wall_time_ms": 1234,
            "billed_cny": 0.08,
            "authorization": "Bearer secret-token",
            "source_path": r"D:\private\video.mp4",
        },
        evidence={"provider_request_id": "safe-request-id", "api_key": "secret"},
    )

    state = json.loads((output / "state.json").read_text(encoding="utf-8"))
    record = state["records"][0]
    assert record["raw_metrics"]["wall_time_ms"] == 1234
    assert record["raw_metrics"]["billed_cny"] == 0.08
    assert state["manifest_sha256"]
    assert len(state["authorized_sample_ids"]) == 30
    rendered = json.dumps(state, ensure_ascii=False)
    assert "secret-token" not in rendered
    assert "D:\\\\private" not in rendered
    assert "\"api_key\"" not in rendered

    with pytest.raises(PocValidationError, match="must not be replayed"):
        record_stage_result(
            output,
            sample_id="sample_001",
            stage="tencent_inline",
            status="completed",
            billable=True,
            raw_metrics={"wall_time_ms": 1000},
            evidence={},
        )

    with pytest.raises(PocValidationError, match="not authorized"):
        record_stage_result(
            output,
            sample_id="sample_999",
            stage="notes",
            status="completed",
            billable=True,
            raw_metrics={"billed_cny": 0.01},
            evidence={},
        )


def test_aggregates_are_computed_from_preserved_records() -> None:
    summary = aggregate_results(
        [
            {
                "sample_id": "sample_001",
                "stage": "tencent_inline",
                "status": "completed",
                "billable": True,
                "raw_metrics": {
                    "wall_time_ms": 1000,
                    "audio_duration_ms": 2000,
                    "billed_cny": 0.1,
                    "cer": 0.1,
                },
            },
            {
                "sample_id": "sample_002",
                "stage": "tencent_inline",
                "status": "failed",
                "billable": True,
                "raw_metrics": {
                    "wall_time_ms": 2000,
                    "audio_duration_ms": 2000,
                    "billed_cny": 0.2,
                    "cer": 0.3,
                },
            },
        ]
    )
    assert summary["record_count"] == 2
    assert summary["status_counts"] == {"completed": 1, "failed": 1}
    assert summary["total_billed_cny"] == pytest.approx(0.3)
    assert summary["mean_cer"] == pytest.approx(0.2)
    assert summary["mean_real_time_factor"] == pytest.approx(0.75)
