"""Pure validation and normalization policy for provider configuration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from vtnote.application.configuration_contracts import InvalidConfiguration
from vtnote.chat import bailian_base_url, validate_chat_model
from vtnote.diagnostics import sanitize_diagnostic
from vtnote.provider_chat import CHAT_PROTOCOLS, provider_chat_endpoint
from vtnote.tencent_contract import (
    TENCENT_ASR_ENDPOINT,
    TENCENT_ASR_MODEL,
    TENCENT_ASR_REGION,
    TENCENT_LANGUAGE_SCOPE,
)
from vtnote.tokenhub_chat import TOKENHUB_BASE_URL
from vtnote.url_security import normalize_host


class _ConnectionProfileRow(Protocol):
    protocol: str
    parameters: dict[str, Any]
    revision: int


class _ProcessorProfileRow(Protocol):
    purpose: str
    connection: _ConnectionProfileRow
    revision: int
    model: str
    options: dict[str, Any]

_PROTOCOL_PARAMETERS = {
    "volc_bigasr_flash": frozenset(),
    "tencent_recording_asr": frozenset(
        {
            "asr_region",
            "cos_bucket",
            "cos_region",
            "cos_prefix",
            "cos_private",
            "cos_configured",
        }
    ),
    "aliyun_bailian": frozenset({"workspace_id"}),
    "tencent_tokenhub": frozenset(),
    "openai_chat_completions": frozenset(),
    "anthropic_messages": frozenset(),
    "google_gemini": frozenset(),
    "azure_openai": frozenset(),
}
_ACTIVE_PROTOCOLS = frozenset(
    {"tencent_recording_asr", *CHAT_PROTOCOLS}
)
_PURPOSE_PROTOCOL = {
    "cloud_asr": frozenset({"tencent_recording_asr"}),
    "translation": CHAT_PROTOCOLS,
    "notes": CHAT_PROTOCOLS,
}
_PURPOSE_OPTIONS = {
    "cloud_asr": frozenset(
        {"language_scope", "res_text_format", "sentence_max_length"}
    ),
    "translation": frozenset({"temperature", "max_tokens", "enable_thinking"}),
    "notes": frozenset({"temperature", "max_tokens", "enable_thinking"}),
}
_TERMINAL_TASK_STATUSES = frozenset(
    {"canceled", "completed", "completed_with_warnings", "failed"}
)
_RETRYABLE_STAGE_STATUSES = frozenset({"failed", "canceled"})
_ARCHIVED_NAME_PREFIX = "__vtnote_archived__:"
_CHAT_PURPOSES = frozenset({"translation", "notes"})
_MIN_CHAT_CONTEXT_LENGTH = 32_768
_MIN_CHAT_MAX_TOKENS = 1_024


def _reject_reserved_name(value: str) -> None:
    if value.casefold().startswith(_ARCHIVED_NAME_PREFIX):
        raise InvalidConfiguration("configuration name uses a reserved internal prefix")


_COS_BUCKET_RE = re.compile(
    r"^[a-z0-9][a-z0-9-]{1,58}[a-z0-9]-[1-9][0-9]{4,11}$"
)


def _normalize_connection_parameters(
    protocol: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    if set(parameters) - _PROTOCOL_PARAMETERS[protocol]:
        raise InvalidConfiguration("unsupported or secret connection parameter")
    if protocol == "tencent_recording_asr":
        configured = parameters.get("cos_configured")
        cos_keys = {"cos_bucket", "cos_region", "cos_prefix", "cos_private"}
        supplied_cos_keys = cos_keys.intersection(parameters)
        if supplied_cos_keys:
            if supplied_cos_keys != cos_keys:
                raise InvalidConfiguration("COS configuration must be complete")
            if configured is False:
                raise InvalidConfiguration("COS configuration state conflicts with fields")
            bucket = parameters["cos_bucket"]
            region = parameters["cos_region"]
            prefix = parameters["cos_prefix"]
            private = parameters["cos_private"]
            if not isinstance(bucket, str) or _COS_BUCKET_RE.fullmatch(bucket) is None:
                raise InvalidConfiguration("invalid private COS bucket")
            if region != TENCENT_ASR_REGION:
                raise InvalidConfiguration("COS region must be ap-guangzhou")
            if prefix != "vtnote-runtime":
                raise InvalidConfiguration("COS prefix must be vtnote-runtime")
            if private is not True:
                raise InvalidConfiguration("COS bucket must be private")
            if parameters.get("asr_region", TENCENT_ASR_REGION) != TENCENT_ASR_REGION:
                raise InvalidConfiguration("Tencent ASR region must be ap-guangzhou")
            return {
                "asr_region": TENCENT_ASR_REGION,
                "cos_bucket": bucket,
                "cos_region": region,
                "cos_prefix": prefix,
                "cos_private": True,
                "cos_configured": True,
            }
        if set(parameters) - {"asr_region", "cos_configured"}:
            raise InvalidConfiguration("COS configuration must be complete")
        if parameters.get("asr_region", TENCENT_ASR_REGION) != TENCENT_ASR_REGION:
            raise InvalidConfiguration("Tencent ASR region must be ap-guangzhou")
        if configured is not None and configured is not False:
            raise InvalidConfiguration("COS configuration fields are missing")
        return {
            "asr_region": TENCENT_ASR_REGION,
            "cos_configured": False,
        }
    if protocol == "aliyun_bailian":
        workspace_id = parameters.get("workspace_id")
        try:
            bailian_base_url(workspace_id)
        except ValueError:
            raise InvalidConfiguration("invalid Bailian workspace_id") from None
        return {"workspace_id": workspace_id}
    if protocol == "tencent_tokenhub" or protocol in CHAT_PROTOCOLS:
        return {}
    if any(not isinstance(value, str) or not value.strip() for value in parameters.values()):
        raise InvalidConfiguration("connection parameter values must be non-empty strings")
    return dict(parameters)


def _validate_profile_options(purpose: str, options: dict[str, Any]) -> None:
    if set(options) - _PURPOSE_OPTIONS[purpose]:
        raise InvalidConfiguration("unsupported profile option")
    language = options.get("language")
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise InvalidConfiguration("invalid profile option value")
    temperature = options.get("temperature")
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not 0 <= temperature <= 2
    ):
        raise InvalidConfiguration("invalid profile option value")
    max_tokens = options.get("max_tokens")
    if max_tokens is not None and (
        isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0
    ):
        raise InvalidConfiguration("invalid profile option value")
    enable_thinking = options.get("enable_thinking")
    if enable_thinking is not None and not isinstance(enable_thinking, bool):
        raise InvalidConfiguration("invalid profile option value")


def _normalize_profile_contract(
    purpose: str,
    protocol: str,
    model: str,
    options: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    cleaned_model = model.strip()
    if purpose == "cloud_asr":
        if protocol == "volc_bigasr_flash":
            if set(options) - {"language"}:
                raise InvalidConfiguration("unsupported profile option")
            language = options.get("language")
            if language is not None and (
                not isinstance(language, str) or not language.strip()
            ):
                raise InvalidConfiguration("invalid profile option value")
            return cleaned_model, dict(options)
        if protocol != "tencent_recording_asr":
            raise InvalidConfiguration(
                "profile purpose is incompatible with connection protocol"
            )
        if cleaned_model != TENCENT_ASR_MODEL:
            raise InvalidConfiguration(
                f"Tencent ASR model must be {TENCENT_ASR_MODEL}"
            )
        if set(options) - _PURPOSE_OPTIONS[purpose]:
            raise InvalidConfiguration("unsupported profile option")
        if options.get("language_scope", TENCENT_LANGUAGE_SCOPE) != TENCENT_LANGUAGE_SCOPE:
            raise InvalidConfiguration("Tencent ASR language scope is fixed")
        if options.get("res_text_format", 3) != 3:
            raise InvalidConfiguration("Tencent ASR result format is fixed")
        if options.get("sentence_max_length", 20) != 20:
            raise InvalidConfiguration("Tencent ASR sentence length is fixed")
        return cleaned_model, {
            "language_scope": TENCENT_LANGUAGE_SCOPE,
            "res_text_format": 3,
            "sentence_max_length": 20,
        }
    if protocol not in CHAT_PROTOCOLS:
        raise InvalidConfiguration(
            "profile purpose is incompatible with connection protocol"
        )
    _validate_profile_options(purpose, options)
    try:
        cleaned_model = validate_chat_model(cleaned_model)
    except ValueError:
        raise InvalidConfiguration("invalid chat model") from None
    normalized = {
        "temperature": options.get("temperature", 0.2),
        "max_tokens": options.get("max_tokens", 4096),
        "enable_thinking": options.get("enable_thinking", False),
    }
    if normalized["max_tokens"] < _MIN_CHAT_MAX_TOKENS:
        raise InvalidConfiguration(
            f"max_tokens must be at least {_MIN_CHAT_MAX_TOKENS}"
        )
    return cleaned_model, normalized


def _purpose_protocol_is_compatible(purpose: str, protocol: str) -> bool:
    return protocol in _PURPOSE_PROTOCOL.get(purpose, frozenset())


def _validate_profile_capacity(
    purpose: str,
    context_length: int,
    options: dict[str, Any],
) -> None:
    if purpose not in _CHAT_PURPOSES:
        return
    if context_length < _MIN_CHAT_CONTEXT_LENGTH:
        raise InvalidConfiguration(
            f"chat context_length must be at least {_MIN_CHAT_CONTEXT_LENGTH}"
        )
    max_tokens = options.get("max_tokens")
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens < _MIN_CHAT_MAX_TOKENS
        or max_tokens >= context_length
    ):
        raise InvalidConfiguration(
            f"max_tokens must be at least {_MIN_CHAT_MAX_TOKENS} "
            "and smaller than context_length"
        )


def _validate_local_whisper_options(options: dict[str, Any]) -> None:
    if set(options) - {
        "model",
        "device",
        "compute_type",
        "vad_filter",
        "model_root",
        "cache_root",
        "schema_version",
        "cpu_fallback_enabled",
        "word_timestamps",
        "punctuation_normalization",
        "speaker_diarization_enabled",
        "chunk_duration_ms",
        "chunk_overlap_ms",
        "vad_parameters",
    }:
        raise InvalidConfiguration("unsupported local Whisper option")
    string_keys = {"model", "device", "compute_type", "model_root", "cache_root"}
    if any(
        key in string_keys and (not isinstance(value, str) or not value.strip())
        for key, value in options.items()
    ) or ("vad_filter" in options and not isinstance(options["vad_filter"], bool)):
        raise InvalidConfiguration("local Whisper options must be non-empty strings")
    if any(
        options.get(name) != expected
        for name, expected in {
            "model": "large-v3-turbo",
            "device": "cuda",
            "compute_type": "int8_float16",
            "vad_filter": True,
        }.items()
    ):
        raise InvalidConfiguration(
            "local Whisper keeps a GPU-only primary runtime with a fixed model"
        )
    boolean_keys = {
        "cpu_fallback_enabled",
        "word_timestamps",
        "punctuation_normalization",
        "speaker_diarization_enabled",
    }
    if any(key in options and type(options[key]) is not bool for key in boolean_keys):
        raise InvalidConfiguration("local Whisper feature flags must be boolean")
    if "schema_version" in options and options["schema_version"] != 2:
        raise InvalidConfiguration("unsupported local Whisper schema")
    duration = options.get("chunk_duration_ms")
    overlap = options.get("chunk_overlap_ms")
    if duration is not None and (
        type(duration) is not int or not 60_000 <= duration <= 3_600_000
    ):
        raise InvalidConfiguration("invalid local Whisper chunk duration")
    if overlap is not None and (
        type(overlap) is not int
        or duration is None
        or not 0 <= overlap < min(duration, 60_000)
    ):
        raise InvalidConfiguration("invalid local Whisper chunk overlap")
    vad = options.get("vad_parameters")
    expected_vad = {
        "threshold",
        "min_speech_duration_ms",
        "min_silence_duration_ms",
        "speech_pad_ms",
    }
    if vad is not None and (
        not isinstance(vad, dict)
        or set(vad) != expected_vad
        or isinstance(vad["threshold"], bool)
        or not isinstance(vad["threshold"], (int, float))
        or not 0 <= vad["threshold"] <= 1
        or any(
            type(vad[key]) is not int or vad[key] < 0
            for key in expected_vad - {"threshold"}
        )
    ):
        raise InvalidConfiguration("invalid local Whisper VAD parameters")


def _clean_base_url(
    value: str | None,
    protocol: str,
    parameters: dict[str, Any] | None = None,
) -> str:
    if protocol == "aliyun_bailian":
        try:
            expected = bailian_base_url((parameters or {}).get("workspace_id"))
        except ValueError:
            raise InvalidConfiguration("invalid Bailian workspace_id") from None
        if value is not None and value != expected:
            raise InvalidConfiguration("Bailian endpoint is fixed to Beijing workspace")
        return expected
    if protocol == "tencent_tokenhub":
        if value is not None and value != TOKENHUB_BASE_URL:
            raise InvalidConfiguration("TokenHub endpoint is fixed to Guangzhou")
        return TOKENHUB_BASE_URL
    if not isinstance(value, str):
        raise InvalidConfiguration("provider base URL is required")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError as error:
        raise InvalidConfiguration("invalid provider base URL") from error
    if parts.username or parts.password or parts.query or parts.fragment:
        raise InvalidConfiguration("invalid provider base URL")
    if not host or port not in {None, 443}:
        raise InvalidConfiguration("invalid provider base URL")
    if parts.scheme == "http":
        raise InvalidConfiguration("cloud provider base URL must use HTTPS")
    if parts.scheme not in {"http", "https"}:
        raise InvalidConfiguration("invalid provider base URL")
    if parts.scheme != "https":
        raise InvalidConfiguration("invalid provider base URL")
    try:
        normalize_host(host)
    except ValueError:
        raise InvalidConfiguration("invalid provider base URL") from None
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    if protocol == "tencent_recording_asr" and cleaned != TENCENT_ASR_ENDPOINT:
        raise InvalidConfiguration("Tencent ASR endpoint is fixed")
    return cleaned


def _clean_message(
    message: str | None, sensitive_values: tuple[str | None, ...] = ()
) -> str | None:
    return sanitize_diagnostic(message, sensitive_values)


def _chat_capability_fingerprint(
    row: _ProcessorProfileRow,
) -> dict[str, Any] | None:
    connection = row.connection
    if (
        row.purpose not in _CHAT_PURPOSES
        or connection.protocol not in CHAT_PROTOCOLS
    ):
        return None
    options = dict(row.options)
    try:
        endpoint = provider_chat_endpoint(
            connection.protocol,
            getattr(connection, "base_url", ""),
            connection.parameters,
            row.model,
        )
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "schema_version": 1,
        "protocol": connection.protocol,
        "endpoint": endpoint,
        "connection_revision": connection.revision,
        "profile_revision": row.revision,
        "model": row.model,
        "response_format": "json_object",
        "enable_thinking": options["enable_thinking"],
        "options": {
            "temperature": options["temperature"],
            "max_tokens": options["max_tokens"],
        },
    }


def chat_capability_fingerprint_digest(value: Mapping[str, Any]) -> str:
    """Return the stable non-secret digest bound to one tested chat capability."""

    body = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()
