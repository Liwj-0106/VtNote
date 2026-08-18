"""Pure Tencent Recording ASR request, signing, routing, and error contracts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from vtnote.media import PreparedAudio


TENCENT_ASR_ENDPOINT = "https://asr.tencentcloudapi.com"
TENCENT_ASR_HOST = "asr.tencentcloudapi.com"
TENCENT_ASR_REGION = "ap-guangzhou"
TENCENT_ASR_VERSION = "2019-06-14"
TENCENT_ASR_MODEL = "16k_zh"
TENCENT_LANGUAGE_SCOPE = "zh_with_limited_english"
TENCENT_INLINE_AUDIO_BYTES = 5_000_000
_UINT64_MAX = (1 << 64) - 1
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class TencentResponseError(ValueError):
    """A bounded provider code from a syntactically valid Error envelope."""

    def __init__(self, provider_code: str) -> None:
        self.provider_code = provider_code
        super().__init__(provider_code)


@dataclass(frozen=True, slots=True)
class TencentCreateResult:
    task_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class TencentSentence:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class TencentQueryResult:
    state: Literal["waiting", "running", "success", "failed"]
    provider_status: str
    request_id: str
    sentences: tuple[TencentSentence, ...]


def base64_encoded_length(binary_bytes: int) -> int:
    if type(binary_bytes) is not int or binary_bytes < 0:
        raise ValueError("binary byte length must be nonnegative")
    return 4 * ((binary_bytes + 2) // 3)


@dataclass(frozen=True, slots=True)
class TencentLimits:
    inline_audio_bytes: int = TENCENT_INLINE_AUDIO_BYTES
    maximum_audio_bytes: int = 96 * 1024 * 1024
    maximum_duration_ms: int = 5 * 60 * 60 * 1000

    def __post_init__(self) -> None:
        if (
            type(self.inline_audio_bytes) is not int
            or type(self.maximum_audio_bytes) is not int
            or type(self.maximum_duration_ms) is not int
            or self.inline_audio_bytes <= 0
            or self.maximum_audio_bytes < self.inline_audio_bytes
            or self.maximum_duration_ms <= 0
        ):
            raise ValueError("invalid Tencent limits")


@dataclass(frozen=True, slots=True)
class CloudProfileSnapshot:
    model: str
    language_scope: str
    cos_configured: bool

    def __post_init__(self) -> None:
        if self.model != TENCENT_ASR_MODEL:
            raise ValueError("Tencent model is fixed")
        if self.language_scope != TENCENT_LANGUAGE_SCOPE:
            raise ValueError("Tencent language scope is fixed")
        if type(self.cos_configured) is not bool:
            raise ValueError("invalid COS configuration state")


@dataclass(frozen=True, slots=True)
class CloudEligibility:
    eligible: bool
    route: Literal["inline", "cos", "local"]
    reason_code: str | None
    binary_bytes: int
    base64_bytes: int


class TencentPreflight:
    def evaluate(
        self,
        audio: PreparedAudio,
        profile: CloudProfileSnapshot,
        limits: TencentLimits,
    ) -> CloudEligibility:
        if not isinstance(profile, CloudProfileSnapshot) or not isinstance(
            limits,
            TencentLimits,
        ):
            raise TypeError("invalid Tencent preflight contract")
        info = audio.media_info
        size = info.size_bytes
        encoded = base64_encoded_length(size)
        if (
            "ogg" not in {
                item.strip().casefold()
                for item in info.format_name.split(",")
            }
            or info.audio_codec != "opus"
            or info.channels != 1
        ):
            return CloudEligibility(
                False,
                "local",
                "cloud_audio_invalid",
                size,
                encoded,
            )
        if info.duration_ms > limits.maximum_duration_ms:
            return CloudEligibility(
                False,
                "local",
                "cloud_duration_exceeded",
                size,
                encoded,
            )
        if size > limits.maximum_audio_bytes:
            return CloudEligibility(
                False,
                "local",
                "cloud_payload_exceeded",
                size,
                encoded,
            )
        if size <= limits.inline_audio_bytes:
            return CloudEligibility(True, "inline", None, size, encoded)
        if profile.cos_configured:
            return CloudEligibility(True, "cos", None, size, encoded)
        return CloudEligibility(
            False,
            "local",
            "cloud_cos_unavailable",
            size,
            encoded,
        )


def _base_create_payload() -> dict[str, object]:
    return {
        "ChannelNum": 1,
        "EngineModelType": TENCENT_ASR_MODEL,
        "ResTextFormat": 3,
        "SentenceMaxLength": 20,
    }


def build_create_payload_inline(data: bytes) -> dict[str, object]:
    if not isinstance(data, bytes) or not data:
        raise ValueError("inline audio bytes are required")
    payload = _base_create_payload()
    payload.update(
        {
            "Data": base64.b64encode(data).decode("ascii"),
            "DataLen": len(data),
            "SourceType": 1,
        }
    )
    return payload


def build_create_payload_url(url: str) -> dict[str, object]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        raise ValueError("invalid signed COS URL") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("invalid signed COS URL")
    payload = _base_create_payload()
    payload.update({"SourceType": 0, "Url": url})
    return payload


def build_describe_payload(task_id: str) -> dict[str, object]:
    if (
        not isinstance(task_id, str)
        or not task_id.isdigit()
        or int(task_id) <= 0
        or int(task_id) > _UINT64_MAX
    ):
        raise ValueError("invalid provider task ID")
    return {"TaskId": int(task_id)}


def _canonical_payload(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def tc3_authorization(
    *,
    secret_id: str,
    secret_key: str,
    action: Literal["CreateRecTask", "DescribeTaskStatus"],
    timestamp: int,
    payload: dict[str, object],
) -> str:
    if (
        not secret_id
        or not secret_key
        or action not in {"CreateRecTask", "DescribeTaskStatus"}
        or type(timestamp) is not int
        or timestamp < 0
        or not isinstance(payload, dict)
    ):
        raise ValueError("invalid TC3 signing input")
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    body = _canonical_payload(payload)
    hashed_payload = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = (
        "POST\n/\n\n"
        "content-type:application/json; charset=utf-8\n"
        f"host:{TENCENT_ASR_HOST}\n\n"
        "content-type;host\n"
        f"{hashed_payload}"
    )
    credential_scope = f"{date}/asr/tc3_request"
    string_to_sign = (
        "TC3-HMAC-SHA256\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    date_secret = _hmac(f"TC3{secret_key}".encode("utf-8"), date)
    service_secret = _hmac(date_secret, "asr")
    signing_secret = _hmac(service_secret, "tc3_request")
    signature = hmac.new(
        signing_secret,
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        "SignedHeaders=content-type;host, "
        f"Signature={signature}"
    )


def build_tc3_headers(
    *,
    secret_id: str,
    secret_key: str,
    action: Literal["CreateRecTask", "DescribeTaskStatus"],
    timestamp: int,
    payload: dict[str, object],
) -> dict[str, str]:
    authorization = tc3_authorization(
        secret_id=secret_id,
        secret_key=secret_key,
        action=action,
        timestamp=timestamp,
        payload=payload,
    )
    return {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": TENCENT_ASR_HOST,
        "X-TC-Action": action,
        "X-TC-Region": TENCENT_ASR_REGION,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": TENCENT_ASR_VERSION,
    }


def _response(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("provider_response_invalid")
    response = payload.get("Response")
    if not isinstance(response, dict):
        raise ValueError("provider_response_invalid")
    error = response.get("Error")
    if error is not None:
        if not isinstance(error, dict):
            raise ValueError("provider_response_invalid")
        code = error.get("Code")
        if (
            not isinstance(code, str)
            or not code
            or len(code) > 128
            or any(character.isspace() for character in code)
        ):
            raise ValueError("provider_response_invalid")
        raise TencentResponseError(code)
    return response


def _request_id(response: dict[str, object]) -> str:
    value = response.get("RequestId")
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        raise ValueError("provider_response_invalid")
    return value


def _provider_task_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("provider_response_invalid")
    text = str(value)
    if not text.isdigit() or int(text) <= 0 or int(text) > _UINT64_MAX:
        raise ValueError("provider_response_invalid")
    return text


def parse_create_response(payload: object) -> TencentCreateResult:
    response = _response(payload)
    data = response.get("Data")
    if not isinstance(data, dict):
        raise ValueError("provider_response_invalid")
    return TencentCreateResult(
        task_id=_provider_task_id(data.get("TaskId")),
        request_id=_request_id(response),
    )


def parse_query_response(payload: object) -> TencentQueryResult:
    response = _response(payload)
    request_id = _request_id(response)
    data = response.get("Data")
    if not isinstance(data, dict):
        raise ValueError("provider_response_invalid")
    status = data.get("Status")
    states = {0: "waiting", 1: "running", 2: "success", 3: "failed"}
    if isinstance(status, bool) or status not in states:
        raise ValueError("provider_response_invalid")
    state = states[status]
    provider_status = state
    if state != "success":
        return TencentQueryResult(
            state=state,  # type: ignore[arg-type]
            provider_status=provider_status,
            request_id=request_id,
            sentences=(),
        )

    details = data.get("ResultDetail")
    if not isinstance(details, list) or not details:
        raise ValueError("provider_result_missing_timestamps")
    sentences: list[TencentSentence] = []
    for detail in details:
        if not isinstance(detail, dict):
            raise ValueError("provider_result_missing_timestamps")
        text = detail.get("FinalSentence")
        start = detail.get("StartMs")
        end = detail.get("EndMs")
        if (
            not isinstance(text, str)
            or not text.strip()
            or isinstance(start, bool)
            or not isinstance(start, int)
            or start < 0
            or isinstance(end, bool)
            or not isinstance(end, int)
            or end <= start
        ):
            raise ValueError("provider_result_missing_timestamps")
        sentences.append(
            TencentSentence(start_ms=start, end_ms=end, text=text.strip())
        )
    timeline = [(item.start_ms, item.end_ms) for item in sentences]
    if timeline != sorted(timeline):
        raise ValueError("provider_result_missing_timestamps")
    return TencentQueryResult(
        state="success",
        provider_status=provider_status,
        request_id=request_id,
        sentences=tuple(sentences),
    )


_STOP_CONFIGURATION = frozenset(
    {
        "AuthFailure.InvalidAuthorization",
        "FailedOperation.CheckAuthInfoFailed",
        "FailedOperation.UserNotRegistered",
        "InvalidParameter",
        "InvalidParameterValue",
        "MissingParameter",
        "UnknownParameter",
    }
)
_STOP_BILLING = frozenset(
    {
        "FailedOperation.ServiceIsolate",
        "FailedOperation.UserHasNoAmount",
        "FailedOperation.UserHasNoFreeAmount",
    }
)
_FALLBACK = frozenset(
    {
        "FailedOperation.ErrorDownFile",
        "FailedOperation.ErrorRecognize",
    }
)


def classify_tencent_error(
    code: str,
    *,
    phase: Literal["create", "query"],
) -> str:
    if not isinstance(code, str) or not code or phase not in {"create", "query"}:
        raise ValueError("invalid Tencent error")
    if code in _STOP_CONFIGURATION:
        return "stop_configuration"
    if code in _STOP_BILLING:
        return "stop_billing_or_quota"
    if code == "FailedOperation.NoSuchTask":
        return "stop_no_such_task"
    if code.startswith("InternalError."):
        return "query_retry" if phase == "query" else "fallback_allowed"
    if code == "RequestLimitExceeded.UinLimitExceeded":
        return "query_retry" if phase == "query" else "fallback_allowed"
    if code in _FALLBACK:
        return "fallback_allowed"
    return "stop_unknown_provider_error"
