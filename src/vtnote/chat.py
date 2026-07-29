"""Domestic-only chat contract and Aliyun Bailian Beijing adapter."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from vtnote.provider_credentials import BailianCredentialBundle


BAILIAN_BASE_SUFFIX = (
    ".cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
BAILIAN_CHAT_PATH = "/chat/completions"
MAX_CHAT_REQUEST_BYTES = 64 * 1024
MAX_CHAT_RESPONSE_BYTES = 256 * 1024
_WORKSPACE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "id",
        "object",
        "created",
        "model",
        "choices",
        "usage",
        "service_tier",
        "system_fingerprint",
    }
)
_CHOICE_FIELDS = frozenset(
    {"index", "message", "finish_reason", "logprobs"}
)
_MESSAGE_FIELDS = frozenset(
    {
        "role",
        "content",
        "refusal",
        "reasoning_content",
        "tool_calls",
        "function_call",
        "annotations",
        "audio",
    }
)
_USAGE_FIELDS = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
    }
)


class ChatError(RuntimeError):
    """Safe, stable error that never includes provider content or prompts."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        submission_unknown: bool = False,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.submission_unknown = submission_unknown
        super().__init__(code)

    def __repr__(self) -> str:
        return (
            f"ChatError(code={self.code!r}, retryable={self.retryable!r}, "
            f"submission_unknown={self.submission_unknown!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ValueError("invalid chat role")
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("chat message content cannot be empty")

    def __repr__(self) -> str:
        return f"ChatMessage(role={self.role!r}, content=[redacted])"


@dataclass(frozen=True, slots=True, repr=False)
class ChatRequest:
    model: str
    messages: tuple[ChatMessage, ...]
    response_format: Literal["json_object"] = "json_object"
    temperature: float | None = None
    max_tokens: int | None = None
    enable_thinking: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or _MODEL_RE.fullmatch(self.model) is None:
            raise ValueError("invalid chat model")
        if (
            not isinstance(self.messages, tuple)
            or not self.messages
            or any(not isinstance(item, ChatMessage) for item in self.messages)
        ):
            raise ValueError("chat request requires messages")
        if self.response_format != "json_object":
            raise ValueError("only json_object response format is supported")
        if self.temperature is not None and (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError("invalid chat temperature")
        if self.max_tokens is not None and (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens <= 0
        ):
            raise ValueError("invalid chat max_tokens")
        if self.enable_thinking is not None and not isinstance(
            self.enable_thinking, bool
        ):
            raise ValueError("invalid chat thinking mode")

    def __repr__(self) -> str:
        return (
            f"ChatRequest(model={self.model!r}, messages=[redacted], "
            f"response_format={self.response_format!r})"
        )


@dataclass(frozen=True, slots=True)
class ChatUsage:
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    requested_model: str
    actual_model: str
    finish_reason: str
    request_id: str | None
    usage: ChatUsage


@dataclass(frozen=True, slots=True)
class ChatCapabilities:
    protocol: Literal["aliyun_bailian"]
    endpoint: str
    response_format: Literal["json_object"]
    max_request_bytes: int
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class ChatProfileSnapshot:
    model: str
    context_length: int
    temperature: float
    max_tokens: int
    enable_thinking: bool

    def __post_init__(self) -> None:
        validate_chat_model(self.model)
        if (
            isinstance(self.context_length, bool)
            or not isinstance(self.context_length, int)
            or self.context_length < 32_768
        ):
            raise ValueError("invalid chat context length")
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError("invalid chat temperature")
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens < 1_024
            or self.max_tokens >= self.context_length
        ):
            raise ValueError("invalid chat max_tokens")
        if not isinstance(self.enable_thinking, bool):
            raise ValueError("invalid chat thinking mode")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ChatProfileSnapshot":
        if not isinstance(value, Mapping) or value.get("protocol") != "aliyun_bailian":
            raise ValueError("invalid domestic chat profile snapshot")
        options = value.get("options")
        if not isinstance(options, Mapping):
            raise ValueError("invalid domestic chat profile options")
        return cls(
            model=value.get("model"),  # type: ignore[arg-type]
            context_length=value.get("context_length"),  # type: ignore[arg-type]
            temperature=options.get("temperature"),  # type: ignore[arg-type]
            max_tokens=options.get("max_tokens"),  # type: ignore[arg-type]
            enable_thinking=options.get("enable_thinking"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class AiLimits:
    max_request_bytes: int = MAX_CHAT_REQUEST_BYTES
    max_response_bytes: int = MAX_CHAT_RESPONSE_BYTES
    translation_batch_cues: int = 30
    translation_retry_batch_cues: int = 15
    note_source_chunk_bytes: int = 48 * 1024
    note_max_initial_chunks: int = 24
    note_max_reduce_levels: int = 4

    def __post_init__(self) -> None:
        integer_values = (
            self.max_request_bytes,
            self.max_response_bytes,
            self.translation_batch_cues,
            self.translation_retry_batch_cues,
            self.note_source_chunk_bytes,
            self.note_max_initial_chunks,
            self.note_max_reduce_levels,
        )
        if any(type(value) is not int or value <= 0 for value in integer_values):
            raise ValueError("AI limits must be positive integers")
        if self.max_request_bytes > MAX_CHAT_REQUEST_BYTES:
            raise ValueError("AI request limit exceeds transport boundary")
        if self.max_response_bytes > MAX_CHAT_RESPONSE_BYTES:
            raise ValueError("AI response limit exceeds transport boundary")
        if self.translation_batch_cues > 30:
            raise ValueError("translation batch cue limit exceeds implementation baseline")
        if self.translation_retry_batch_cues > 15:
            raise ValueError("translation retry cue limit exceeds implementation baseline")
        if self.note_source_chunk_bytes > 48 * 1024:
            raise ValueError("note source chunk limit exceeds implementation baseline")
        if self.note_max_initial_chunks > 24:
            raise ValueError("note chunk count exceeds implementation baseline")
        if self.note_max_reduce_levels > 4:
            raise ValueError("note reduce depth exceeds implementation baseline")


class ChatClient(Protocol):
    def complete(self, request: ChatRequest) -> ChatResponse: ...


class DomesticChatAdapter(ChatClient, Protocol):
    def capabilities(self) -> ChatCapabilities: ...


@dataclass(frozen=True, slots=True)
class BailianHttpResponse:
    status_code: int
    payload: object
    headers: Mapping[str, str]


class BailianTransportFailure(RuntimeError):
    def __init__(self, safe_code: str, *, sent: bool) -> None:
        self.safe_code = safe_code
        self.sent = sent
        super().__init__(safe_code)


class BailianTransport(Protocol):
    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> BailianHttpResponse: ...


def bailian_base_url(workspace_id: str) -> str:
    if not isinstance(workspace_id, str) or _WORKSPACE_RE.fullmatch(workspace_id) is None:
        raise ValueError("invalid Bailian workspace_id")
    return f"https://{workspace_id}{BAILIAN_BASE_SUFFIX}"


def bailian_chat_endpoint(workspace_id: str) -> str:
    return bailian_base_url(workspace_id) + BAILIAN_CHAT_PATH


def validate_chat_model(model: str) -> str:
    if not isinstance(model, str) or _MODEL_RE.fullmatch(model) is None:
        raise ValueError("invalid chat model")
    return model


def canonical_chat_request_bytes(request: ChatRequest) -> bytes:
    """Serialize the complete non-streaming request used for exact budget checks."""

    if not isinstance(request, ChatRequest):
        raise ValueError("ChatRequest is required")
    if not any("JSON" in message.content for message in request.messages):
        raise ChatError("chat_json_instruction_required")
    payload: dict[str, object] = {
        "model": request.model,
        "messages": [
            {"role": item.role, "content": item.content}
            for item in request.messages
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    if request.enable_thinking is not None:
        payload["enable_thinking"] = request.enable_thinking
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class HttpxBailianTransport:
    """One fixed-host POST with no redirect or environment proxy behavior."""

    def __init__(
        self,
        *,
        endpoint: str,
        client: httpx.Client | None = None,
        max_response_bytes: int = MAX_CHAT_RESPONSE_BYTES,
    ) -> None:
        try:
            parts = urlsplit(endpoint)
            host = parts.hostname
            workspace_id = (
                host.removesuffix(".cn-beijing.maas.aliyuncs.com")
                if isinstance(host, str)
                else ""
            )
            canonical = bailian_chat_endpoint(workspace_id)
            _ = parts.port
        except (TypeError, ValueError):
            raise ValueError("invalid Bailian endpoint") from None
        if endpoint != canonical:
            raise ValueError("invalid Bailian endpoint")
        if client is not None and client.follow_redirects:
            raise ValueError("Bailian transport must not follow redirects")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("invalid Bailian response limit")
        self.endpoint = endpoint
        self.max_response_bytes = max_response_bytes
        self.client = client or httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(45.0, connect=10.0),
        )

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> BailianHttpResponse:
        if url != self.endpoint:
            raise BailianTransportFailure("chat_endpoint_invalid", sent=False)
        try:
            with self.client.stream(
                "POST",
                url,
                headers=headers,
                content=body,
            ) as response:
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        raise BailianTransportFailure(
                            "chat_response_oversize",
                            sent=True,
                        )
                    chunks.append(chunk)
                status_code = response.status_code
                response_headers = {
                    str(name).casefold(): str(value)
                    for name, value in response.headers.items()
                }
        except BailianTransportFailure:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise BailianTransportFailure("chat_connect_failed", sent=False) from None
        except httpx.RequestError:
            raise BailianTransportFailure("chat_response_lost", sent=True) from None

        raw = b"".join(chunks)
        if not raw:
            payload: object = {}
        else:
            try:
                payload = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError):
                if status_code == 200:
                    raise BailianTransportFailure(
                        "chat_response_invalid",
                        sent=True,
                    ) from None
                payload = {}
        return BailianHttpResponse(status_code, payload, response_headers)


def _integer_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_request_id(headers: Mapping[str, str], payload: dict[str, object]) -> str | None:
    candidate = headers.get("x-request-id") or payload.get("id")
    if not isinstance(candidate, str) or _SAFE_REQUEST_ID_RE.fullmatch(candidate) is None:
        return None
    return candidate


class AliyunBailianChatAdapter:
    """Strict adapter for a single Aliyun Bailian Beijing workspace."""

    def __init__(
        self,
        *,
        workspace_id: str,
        api_key: SecretStr,
        transport: BailianTransport | None = None,
        sleeper: Callable[[float], object] = time.sleep,
    ) -> None:
        self.workspace_id = workspace_id
        self.endpoint = bailian_chat_endpoint(workspace_id)
        if not isinstance(api_key, SecretStr) or not api_key.get_secret_value().strip():
            raise ValueError("Bailian API key is required")
        self._api_key = api_key
        self._transport = transport or HttpxBailianTransport(endpoint=self.endpoint)
        self._sleeper = sleeper

    def __repr__(self) -> str:
        return (
            f"AliyunBailianChatAdapter(workspace_id={self.workspace_id!r}, "
            "api_key=[redacted])"
        )

    def capabilities(self) -> ChatCapabilities:
        return ChatCapabilities(
            protocol="aliyun_bailian",
            endpoint=self.endpoint,
            response_format="json_object",
            max_request_bytes=MAX_CHAT_REQUEST_BYTES,
            max_response_bytes=MAX_CHAT_RESPONSE_BYTES,
        )

    @staticmethod
    def _payload(request: ChatRequest) -> bytes:
        body = canonical_chat_request_bytes(request)
        if len(body) > MAX_CHAT_REQUEST_BYTES:
            raise ChatError("chat_request_oversize")
        return body

    @staticmethod
    def _retry_delay(headers: Mapping[str, str]) -> float | None:
        raw = headers.get("retry-after")
        if raw is None:
            return None
        try:
            delay = float(raw)
        except ValueError:
            return None
        return min(max(delay, 0.0), 5.0)

    @staticmethod
    def _http_error(status_code: int) -> ChatError:
        codes = {
            400: "chat_invalid_request",
            401: "chat_authentication_failed",
            403: "chat_permission_denied",
            404: "chat_model_not_found",
            429: "chat_rate_limited",
        }
        if 300 <= status_code < 400:
            return ChatError("chat_redirect_blocked")
        if status_code >= 500:
            return ChatError(
                "chat_submission_unknown",
                submission_unknown=True,
            )
        return ChatError(codes.get(status_code, "chat_provider_error"))

    @staticmethod
    def _parse_response(
        response: BailianHttpResponse,
        requested_model: str,
    ) -> ChatResponse:
        payload = response.payload
        if (
            not isinstance(payload, dict)
            or set(payload) - _TOP_LEVEL_FIELDS
        ):
            raise ChatError("chat_response_invalid")
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ChatError("chat_response_invalid")
        choice = choices[0]
        if not isinstance(choice, dict) or set(choice) - _CHOICE_FIELDS:
            raise ChatError("chat_response_invalid")
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise ChatError("chat_output_truncated")
        if finish_reason in {"content_filter", "sensitive"}:
            raise ChatError("chat_content_filtered")
        if finish_reason != "stop":
            raise ChatError("chat_response_invalid")
        message = choice.get("message")
        if not isinstance(message, dict) or set(message) - _MESSAGE_FIELDS:
            raise ChatError("chat_response_invalid")
        if message.get("refusal"):
            raise ChatError("chat_content_filtered")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ChatError("chat_response_empty")
        actual_model = payload.get("model")
        if (
            not isinstance(actual_model, str)
            or _MODEL_RE.fullmatch(actual_model) is None
        ):
            raise ChatError("chat_response_invalid")
        usage_payload = payload.get("usage", {})
        if (
            not isinstance(usage_payload, dict)
            or set(usage_payload) - _USAGE_FIELDS
        ):
            raise ChatError("chat_response_invalid")
        usage = ChatUsage(
            prompt_tokens=_integer_or_none(usage_payload.get("prompt_tokens")),
            completion_tokens=_integer_or_none(
                usage_payload.get("completion_tokens")
            ),
            total_tokens=_integer_or_none(usage_payload.get("total_tokens")),
        )
        return ChatResponse(
            content=content,
            requested_model=requested_model,
            actual_model=actual_model,
            finish_reason=finish_reason,
            request_id=_safe_request_id(response.headers, payload),
            usage=usage,
        )

    def complete(self, request: ChatRequest) -> ChatResponse:
        if not isinstance(request, ChatRequest):
            raise ValueError("ChatRequest is required")
        body = self._payload(request)
        headers = {
            "Authorization": (
                f"Bearer {self._api_key.get_secret_value()}"
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        for attempt in range(2):
            try:
                response = self._transport.post(
                    url=self.endpoint,
                    headers=headers,
                    body=body,
                )
            except BailianTransportFailure as error:
                if error.safe_code in {
                    "chat_response_oversize",
                    "chat_response_invalid",
                    "chat_endpoint_invalid",
                }:
                    raise ChatError(error.safe_code) from None
                if error.sent:
                    raise ChatError(
                        "chat_submission_unknown",
                        submission_unknown=True,
                    ) from None
                raise ChatError(error.safe_code) from None
            if response.status_code == 429 and attempt == 0:
                retry_delay = self._retry_delay(response.headers)
                if retry_delay is not None:
                    self._sleeper(retry_delay)
                    continue
            if response.status_code != 200:
                raise self._http_error(response.status_code)
            return self._parse_response(response, request.model)
        raise AssertionError("unreachable")


@dataclass(frozen=True, slots=True)
class BailianCapabilityTestResult:
    ok: bool
    message: str | None = None


class BailianProfileTester:
    """Run the one explicitly acknowledged, minimal capability request."""

    def test_profile(
        self,
        profile: object,
        credentials: object,
        test_input: object,
        *,
        follow_redirects: Literal[False],
    ) -> BailianCapabilityTestResult:
        del test_input
        if follow_redirects is not False:
            raise ValueError("redirects must remain disabled")
        if not isinstance(credentials, BailianCredentialBundle):
            return BailianCapabilityTestResult(False, "credential_unavailable")
        protocol = getattr(profile, "protocol", None)
        parameters = getattr(profile, "parameters", None)
        if parameters is None:
            parameters = {}
        if protocol != "aliyun_bailian":
            return BailianCapabilityTestResult(False, "profile_protocol_invalid")
        workspace_id = None
        if isinstance(parameters, dict):
            workspace_id = parameters.get("workspace_id")
        if workspace_id is None:
            base_url = getattr(profile, "base_url", "")
            if isinstance(base_url, str):
                host = base_url.removeprefix("https://").split(".", 1)
                workspace_id = host[0] if len(host) == 2 else None
        model = getattr(profile, "model", None)
        options = getattr(profile, "options", None)
        if not isinstance(options, dict):
            options = {}
        try:
            adapter = AliyunBailianChatAdapter(
                workspace_id=workspace_id,
                api_key=credentials.api_key,
            )
            response = adapter.complete(
                ChatRequest(
                    model=model,
                    messages=(
                        ChatMessage(
                            role="system",
                            content=(
                                "Return exactly one JSON object and no other text."
                            ),
                        ),
                        ChatMessage(
                            role="user",
                            content='Reply with this JSON object: {"ok":true}',
                        ),
                    ),
                    response_format="json_object",
                    temperature=options.get("temperature"),
                    max_tokens=min(options.get("max_tokens", 4096), 64),
                    enable_thinking=options.get("enable_thinking"),
                )
            )
            parsed = json.loads(response.content)
            if not isinstance(parsed, dict):
                return BailianCapabilityTestResult(False, "response_not_json_object")
        except (ChatError, TypeError, ValueError, json.JSONDecodeError):
            return BailianCapabilityTestResult(False, "capability_test_failed")
        return BailianCapabilityTestResult(True, "profile_capability_tested")
