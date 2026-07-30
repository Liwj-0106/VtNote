from __future__ import annotations

from pathlib import Path

import pytest

from vtnote.media import MediaInfo, PreparedAudio
from vtnote.tencent_contract import (
    CloudProfileSnapshot,
    TencentResponseError,
    TencentLimits,
    TencentPreflight,
    base64_encoded_length,
    build_create_payload_inline,
    build_create_payload_url,
    build_describe_payload,
    build_tc3_headers,
    classify_tencent_error,
    parse_create_response,
    parse_query_response,
    tc3_authorization,
)


def prepared(tmp_path: Path, *, size: int, duration_ms: int) -> PreparedAudio:
    path = tmp_path / "cloud.ogg"
    path.write_bytes(b"x")
    return PreparedAudio(
        path=path,
        asset_id="asset-id",
        converted=True,
        media_info=MediaInfo(
            duration_ms=duration_ms,
            size_bytes=size,
            format_name="ogg",
            audio_codec="opus",
            sample_rate=16_000,
            channels=1,
        ),
    )


@pytest.mark.parametrize(
    ("binary_bytes", "encoded_bytes"),
    [(0, 0), (1, 4), (2, 4), (3, 4), (4, 8), (4_500_000, 6_000_000)],
)
def test_base64_formula_is_exact(
    binary_bytes: int,
    encoded_bytes: int,
) -> None:
    assert base64_encoded_length(binary_bytes) == encoded_bytes


def profile(*, cos_configured: bool = False) -> CloudProfileSnapshot:
    return CloudProfileSnapshot(
        model="16k_zh_en_2.0",
        language_scope="zh_en_dialects",
        cos_configured=cos_configured,
    )


def test_preflight_routes_exact_inline_boundary(tmp_path: Path) -> None:
    evaluator = TencentPreflight()
    limits = TencentLimits()

    inline = evaluator.evaluate(
        prepared(tmp_path, size=4_500_000, duration_ms=60_000),
        profile(),
        limits,
    )
    without_cos = evaluator.evaluate(
        prepared(tmp_path, size=4_500_001, duration_ms=60_000),
        profile(),
        limits,
    )
    with_cos = evaluator.evaluate(
        prepared(tmp_path, size=4_500_001, duration_ms=60_000),
        profile(cos_configured=True),
        limits,
    )

    assert (inline.eligible, inline.route, inline.reason_code) == (
        True,
        "inline",
        None,
    )
    assert (without_cos.eligible, without_cos.route, without_cos.reason_code) == (
        False,
        "local",
        "cloud_cos_unavailable",
    )
    assert (with_cos.eligible, with_cos.route, with_cos.reason_code) == (
        True,
        "cos",
        None,
    )


@pytest.mark.parametrize(
    ("size", "duration_ms", "reason"),
    [
        (96 * 1024 * 1024 + 1, 60_000, "cloud_payload_exceeded"),
        (4_500_001, 5 * 60 * 60 * 1000 + 1, "cloud_duration_exceeded"),
    ],
)
def test_preflight_enforces_five_hour_and_96_mib_limits(
    tmp_path: Path,
    size: int,
    duration_ms: int,
    reason: str,
) -> None:
    outcome = TencentPreflight().evaluate(
        prepared(tmp_path, size=size, duration_ms=duration_ms),
        profile(cos_configured=True),
        TencentLimits(),
    )

    assert not outcome.eligible
    assert outcome.route == "local"
    assert outcome.reason_code == reason


def test_profile_contract_rejects_model_or_language_variation() -> None:
    with pytest.raises(ValueError):
        CloudProfileSnapshot(
            model="16k_zh",
            language_scope="zh_en_dialects",
            cos_configured=False,
        )
    with pytest.raises(ValueError):
        CloudProfileSnapshot(
            model="16k_zh_en_2.0",
            language_scope="auto",
            cos_configured=False,
        )


def test_create_payloads_force_large_model_2_and_subtitle_format() -> None:
    inline = build_create_payload_inline(b"abc")
    remote = build_create_payload_url(
        "https://private-bucket.cos.ap-guangzhou.myqcloud.com/object?signature=x"
    )

    assert inline == {
        "ChannelNum": 1,
        "Data": "YWJj",
        "DataLen": 3,
        "EngineModelType": "16k_zh_en_2.0",
        "ResTextFormat": 3,
        "SentenceMaxLength": 20,
        "SourceType": 1,
    }
    assert remote == {
        "ChannelNum": 1,
        "EngineModelType": "16k_zh_en_2.0",
        "ResTextFormat": 3,
        "SentenceMaxLength": 20,
        "SourceType": 0,
        "Url": (
            "https://private-bucket.cos.ap-guangzhou.myqcloud.com/"
            "object?signature=x"
        ),
    }


def test_tc3_signature_matches_static_vector() -> None:
    payload = build_create_payload_inline(b"abc")

    authorization = tc3_authorization(
        secret_id="AKID-example",
        secret_key="secret-key",
        action="CreateRecTask",
        timestamp=1_700_000_000,
        payload=payload,
    )

    assert authorization == (
        "TC3-HMAC-SHA256 "
        "Credential=AKID-example/2023-11-14/asr/tc3_request, "
        "SignedHeaders=content-type;host, "
        "Signature=c3aea2f52c4ce52b96db18c3f8779ed183629367d0bf3ae"
        "813459aeba502d68b"
    )


def test_request_headers_and_describe_payload_are_fixed() -> None:
    payload = build_describe_payload("18446744073709551615")
    headers = build_tc3_headers(
        secret_id="AKID-example",
        secret_key="secret-key",
        action="DescribeTaskStatus",
        timestamp=1_700_000_000,
        payload=payload,
    )

    assert payload == {"TaskId": 18446744073709551615}
    assert headers["X-TC-Action"] == "DescribeTaskStatus"
    assert headers["X-TC-Version"] == "2019-06-14"
    assert headers["X-TC-Region"] == "ap-guangzhou"
    assert headers["X-TC-Timestamp"] == "1700000000"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert "secret-key" not in repr(headers)


def test_http_200_response_error_is_not_treated_as_success() -> None:
    with pytest.raises(TencentResponseError) as caught:
        parse_create_response(
            {
                "Response": {
                    "Error": {
                        "Code": "AuthFailure.InvalidAuthorization",
                        "Message": "must never be persisted",
                    },
                    "RequestId": "request-1",
                }
            }
        )

    assert caught.value.provider_code == "AuthFailure.InvalidAuthorization"
    assert "must never be persisted" not in str(caught.value)


def test_create_and_query_parsers_validate_task_id_and_sentence_timestamps() -> None:
    created = parse_create_response(
        {
            "Response": {
                "Data": {"TaskId": 18446744073709551615},
                "RequestId": "request-create",
            }
        }
    )
    query = parse_query_response(
        {
            "Response": {
                "Data": {
                    "Status": 2,
                    "StatusStr": "success",
                    "ResultDetail": [
                        {
                            "FinalSentence": "第一句",
                            "StartMs": 0,
                            "EndMs": 1200,
                            "Words": [],
                        },
                        {
                            "FinalSentence": "Second sentence",
                            "StartMs": 1200,
                            "EndMs": 2400,
                        },
                    ],
                },
                "RequestId": "request-query",
            }
        }
    )

    assert created.task_id == "18446744073709551615"
    assert created.request_id == "request-create"
    assert query.state == "success"
    assert query.provider_status == "success"
    assert [(item.start_ms, item.end_ms, item.text) for item in query.sentences] == [
        (0, 1200, "第一句"),
        (1200, 2400, "Second sentence"),
    ]


@pytest.mark.parametrize(
    "result_detail",
    [
        [],
        [{"FinalSentence": "", "StartMs": 0, "EndMs": 100}],
        [{"FinalSentence": "text", "StartMs": -1, "EndMs": 100}],
        [{"FinalSentence": "text", "StartMs": 100, "EndMs": 100}],
        [{"FinalSentence": "text", "StartMs": 100, "EndMs": "200"}],
        [
            {"FinalSentence": "later", "StartMs": 100, "EndMs": 200},
            {"FinalSentence": "earlier", "StartMs": 0, "EndMs": 50},
        ],
    ],
)
def test_success_result_never_fabricates_or_repairs_invalid_timestamps(
    result_detail: list[object],
) -> None:
    with pytest.raises(ValueError, match="provider_result_missing_timestamps"):
        parse_query_response(
            {
                "Response": {
                    "Data": {
                        "Status": 2,
                        "StatusStr": "success",
                        "ResultDetail": result_detail,
                    },
                    "RequestId": "request-query",
                }
            }
        )


@pytest.mark.parametrize(
    ("code", "phase", "expected"),
    [
        ("AuthFailure.InvalidAuthorization", "create", "stop_configuration"),
        ("FailedOperation.UserHasNoAmount", "create", "stop_billing_or_quota"),
        ("InvalidParameterValue", "create", "stop_configuration"),
        ("FailedOperation.ErrorRecognize", "create", "fallback_allowed"),
        ("RequestLimitExceeded.UinLimitExceeded", "create", "fallback_allowed"),
        ("RequestLimitExceeded.UinLimitExceeded", "query", "query_retry"),
        ("InternalError.Unknown", "query", "query_retry"),
        ("FailedOperation.NoSuchTask", "query", "stop_no_such_task"),
        ("Unlisted.Provider.Code", "create", "stop_unknown_provider_error"),
    ],
)
def test_official_error_codes_map_to_closed_actions(
    code: str,
    phase: str,
    expected: str,
) -> None:
    assert classify_tencent_error(code, phase=phase) == expected
