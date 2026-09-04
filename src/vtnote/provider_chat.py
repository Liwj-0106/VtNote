"""Safe chat adapters for mainstream public model-provider protocols."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import quote, urlsplit

from pydantic import SecretStr

from vtnote.chat import (
    MAX_CHAT_REQUEST_BYTES,
    MAX_CHAT_RESPONSE_BYTES,
    ChatCapabilities,
    ChatError,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    bailian_chat_endpoint,
    validate_chat_model,
)
from vtnote.platform_transport import DirectHttpsConnector, HttpsConnector
from vtnote.provider_credentials import ApiKeyCredentialBundle
from vtnote.tokenhub_chat import TOKENHUB_CHAT_ENDPOINT
from vtnote.url_security import Resolver, SocketResolver, normalize_host, public_ip_answers


MODERN_CHAT_PROTOCOLS = frozenset(
    {
        "openai_chat_completions",
        "anthropic_messages",
        "google_gemini",
        "azure_openai",
    }
)
CHAT_PROTOCOLS = frozenset(
    {"aliyun_bailian", "tencent_tokenhub", *MODERN_CHAT_PROTOCOLS}
)
_SAFE_REQUEST_ID = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)


def _join_endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def provider_chat_endpoint(
    protocol: str,
    base_url: str,
    parameters: Mapping[str, object],
    model: str,
) -> str:
    """Return the exact endpoint bound into a tested profile fingerprint."""

    validate_chat_model(model)
    if protocol == "aliyun_bailian":
        workspace_id = parameters.get("workspace_id")
        if not isinstance(workspace_id, str):
            raise ValueError("invalid Bailian workspace_id")
        return bailian_chat_endpoint(workspace_id)
    if protocol == "tencent_tokenhub":
        return TOKENHUB_CHAT_ENDPOINT
    if protocol in {"openai_chat_completions", "azure_openai"}:
        return _join_endpoint(base_url, "/chat/completions")
    if protocol == "anthropic_messages":
        return _join_endpoint(base_url, "/v1/messages")
    if protocol == "google_gemini":
        encoded_model = quote(model, safe="")
        return _join_endpoint(
            base_url,
            f"/v1beta/models/{encoded_model}:generateContent",
        )
    raise ValueError("unsupported chat protocol")


@dataclass(frozen=True, slots=True)
class ProviderHttpResponse:
    status_code: int
    payload: object
    headers: Mapping[str, str]


class ProviderTransportFailure(RuntimeError):
    def __init__(self, safe_code: str, *, sent: bool) -> None:
        self.safe_code = safe_code
        self.sent = sent
        super().__init__(safe_code)


class ProviderTransport(Protocol):
    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ProviderHttpResponse: ...


class PinnedProviderHttpsTransport:
    """One public HTTPS POST pinned to the resolved address, without redirects."""

    def __init__(
        self,
        *,
        endpoint: str,
        resolver: Resolver | None = None,
        connector: HttpsConnector | None = None,
        max_response_bytes: int = MAX_CHAT_RESPONSE_BYTES,
    ) -> None:
        try:
            parts = urlsplit(endpoint)
            port = parts.port
            host = normalize_host(parts.hostname or "")
        except (TypeError, ValueError):
            raise ValueError("invalid provider endpoint") from None
        if (
            parts.scheme != "https"
            or port not in {None, 443}
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise ValueError("invalid provider endpoint")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("invalid provider response limit")
        self.endpoint = endpoint
        self.host = host
        self.target = parts.path or "/"
        if parts.query:
            self.target += f"?{parts.query}"
        self.resolver = resolver or SocketResolver()
        self.connector = connector or DirectHttpsConnector()
        if not getattr(self.connector, "dns_pinned", False):
            raise ValueError("provider transport requires DNS pinning")
        self.max_response_bytes = max_response_bytes

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ProviderHttpResponse:
        if url != self.endpoint:
            raise ProviderTransportFailure("chat_endpoint_invalid", sent=False)
        if len(body) > MAX_CHAT_REQUEST_BYTES:
            raise ProviderTransportFailure("chat_request_oversize", sent=False)
        try:
            addresses = public_ip_answers(self.resolver.resolve(self.host))
        except Exception:
            raise ProviderTransportFailure("chat_dns_rejected", sent=False) from None
        request_headers = dict(headers)
        request_headers["Host"] = self.host
        request_headers["Accept-Encoding"] = "identity"
        request_headers["Content-Length"] = str(len(body))
        try:
            raw = self.connector.request(
                host=self.host,
                addresses=addresses,
                method="POST",
                target=self.target,
                body=body,
                headers=request_headers,
                connect_timeout=10.0,
                read_timeout=45.0,
            )
        except Exception:
            raise ProviderTransportFailure("chat_response_lost", sent=True) from None
        try:
            public_ip_answers([raw.peer_ip])
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = raw.read(min(64 * 1024, self.max_response_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > self.max_response_bytes:
                    raise ProviderTransportFailure(
                        "chat_response_oversize",
                        sent=True,
                    )
                chunks.append(chunk)
            response_headers = {
                str(name).casefold(): str(value)
                for name, value in raw.headers
            }
            status_code = raw.status
        except ProviderTransportFailure:
            raise
        except Exception:
            raise ProviderTransportFailure("chat_response_lost", sent=True) from None
        finally:
            raw.close()
        content = b"".join(chunks)
        if not content:
            payload: object = {}
        else:
            try:
                payload = json.loads(content)
            except (UnicodeError, json.JSONDecodeError):
                if status_code == 200:
                    raise ProviderTransportFailure(
                        "chat_response_invalid",
                        sent=True,
                    ) from None
                payload = {}
        return ProviderHttpResponse(status_code, payload, response_headers)


def _request_id(headers: Mapping[str, str], payload: Mapping[str, object]) -> str | None:
    candidate = headers.get("x-request-id") or payload.get("id")
    if (
        not isinstance(candidate, str)
        or not 1 <= len(candidate) <= 128
        or any(character not in _SAFE_REQUEST_ID for character in candidate)
    ):
        return None
    return candidate


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _http_error(status_code: int) -> ChatError:
    if 300 <= status_code < 400:
        return ChatError("chat_redirect_blocked")
    codes = {
        400: "chat_invalid_request",
        401: "chat_authentication_failed",
        403: "chat_permission_denied",
        404: "chat_model_not_found",
        429: "chat_rate_limited",
    }
    if status_code >= 500:
        return ChatError("chat_submission_unknown", submission_unknown=True)
    return ChatError(codes.get(status_code, "chat_provider_error"))


def _retry_delay(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(max(float(raw), 0.0), 5.0)
    except ValueError:
        return None


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    body = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(body) > MAX_CHAT_REQUEST_BYTES:
        raise ChatError("chat_request_oversize")
    return body


def _require_json_instruction(request: ChatRequest) -> None:
    if not any("JSON" in message.content for message in request.messages):
        raise ChatError("chat_json_instruction_required")


def _openai_payload(
    request: ChatRequest,
    *,
    json_mode: bool = True,
    deepseek_thinking: bool = False,
) -> bytes:
    _require_json_instruction(request)
    payload: dict[str, object] = {
        "model": request.model,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    if deepseek_thinking and request.enable_thinking is not None:
        payload["thinking"] = {
            "type": "enabled" if request.enable_thinking else "disabled"
        }
    elif request.enable_thinking is True:
        payload["enable_thinking"] = True
    return _json_bytes(payload)


def _response_format_rejected(response: ProviderHttpResponse) -> bool:
    """Recognize a safe, non-billable validation failure for JSON mode."""

    if response.status_code not in {400, 422} or not isinstance(
        response.payload, Mapping
    ):
        return False
    payload = response.payload
    error = payload.get("error")
    candidates: list[object] = [
        payload.get("message"),
        payload.get("detail"),
        error,
    ]
    if isinstance(error, Mapping):
        candidates.extend(
            error.get(key) for key in ("message", "detail", "param", "code", "type")
        )
    text = " ".join(
        value[:512]
        for value in candidates
        if isinstance(value, str) and value
    ).casefold()
    mentions_format = "response_format" in text or "json_object" in text
    rejected = any(
        marker in text
        for marker in (
            "not support",
            "unsupported",
            "unknown",
            "unrecognized",
            "not permitted",
            "extra field",
            "invalid parameter",
        )
    )
    return mentions_format and rejected


def _parse_openai_response(
    response: ProviderHttpResponse,
    requested_model: str,
) -> ChatResponse:
    payload = response.payload
    if not isinstance(payload, dict):
        raise ChatError("chat_response_invalid")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ChatError("chat_response_invalid")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ChatError("chat_response_invalid")
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise ChatError("chat_output_truncated")
    if finish_reason in {"content_filter", "sensitive"}:
        raise ChatError("chat_content_filtered")
    if finish_reason != "stop":
        raise ChatError("chat_response_invalid")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("refusal"):
        raise ChatError("chat_content_filtered")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ChatError("chat_response_empty")
    actual_model = payload.get("model")
    if not isinstance(actual_model, str):
        actual_model = requested_model
    try:
        actual_model = validate_chat_model(actual_model)
    except ValueError:
        actual_model = requested_model
    usage_payload = payload.get("usage")
    if not isinstance(usage_payload, dict):
        usage_payload = {}
    return ChatResponse(
        content=content,
        requested_model=requested_model,
        actual_model=actual_model,
        finish_reason="stop",
        request_id=_request_id(response.headers, payload),
        usage=ChatUsage(
            prompt_tokens=_nonnegative_integer(usage_payload.get("prompt_tokens")),
            completion_tokens=_nonnegative_integer(
                usage_payload.get("completion_tokens")
            ),
            total_tokens=_nonnegative_integer(usage_payload.get("total_tokens")),
        ),
    )


class _ProviderAdapter:
    protocol: str

    def __init__(
        self,
        *,
        protocol: str,
        endpoint: str,
        model: str,
        api_key: SecretStr,
        transport: ProviderTransport | None = None,
        sleeper: Callable[[float], object] = time.sleep,
    ) -> None:
        if protocol not in MODERN_CHAT_PROTOCOLS:
            raise ValueError("unsupported provider protocol")
        if not isinstance(api_key, SecretStr) or not api_key.get_secret_value().strip():
            raise ValueError("provider API key is required")
        self.protocol = protocol
        self.endpoint = endpoint
        self.model = validate_chat_model(model)
        self._api_key = api_key
        self._transport = transport or PinnedProviderHttpsTransport(
            endpoint=endpoint
        )
        self._sleeper = sleeper

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(protocol={self.protocol!r}, "
            f"endpoint={self.endpoint!r}, api_key=[redacted])"
        )

    def capabilities(self) -> ChatCapabilities:
        return ChatCapabilities(
            protocol=self.protocol,
            endpoint=self.endpoint,
            response_format="json_object",
            max_request_bytes=MAX_CHAT_REQUEST_BYTES,
            max_response_bytes=MAX_CHAT_RESPONSE_BYTES,
        )

    def _send(
        self,
        request: ChatRequest,
        *,
        body: bytes,
        headers: dict[str, str],
        parser: Callable[[ProviderHttpResponse, str], ChatResponse],
        compatibility_body: bytes | None = None,
    ) -> ChatResponse:
        if not isinstance(request, ChatRequest) or request.model != self.model:
            raise ValueError("chat request does not match tested profile")
        active_body = body
        compatibility_available = compatibility_body is not None
        while True:
            response: ProviderHttpResponse | None = None
            for attempt in range(2):
                try:
                    response = self._transport.post(
                        url=self.endpoint,
                        headers=headers,
                        body=active_body,
                    )
                except ProviderTransportFailure as error:
                    if error.safe_code in {
                        "chat_endpoint_invalid",
                        "chat_request_oversize",
                        "chat_response_invalid",
                        "chat_response_oversize",
                        "chat_dns_rejected",
                    }:
                        raise ChatError(error.safe_code) from None
                    if error.sent:
                        raise ChatError(
                            "chat_submission_unknown",
                            submission_unknown=True,
                        ) from None
                    raise ChatError(error.safe_code) from None
                if response.status_code == 429 and attempt == 0:
                    delay = _retry_delay(response.headers)
                    if delay is not None:
                        self._sleeper(delay)
                        continue
                break
            if response is None:
                raise AssertionError("provider response unavailable")
            if (
                compatibility_available
                and compatibility_body is not None
                and _response_format_rejected(response)
            ):
                active_body = compatibility_body
                compatibility_available = False
                continue
            if response.status_code != 200:
                raise _http_error(response.status_code)
            return parser(response, request.model)


class OpenAiCompatibleChatAdapter(_ProviderAdapter):
    def complete(self, request: ChatRequest) -> ChatResponse:
        deepseek_thinking = (
            (urlsplit(self.endpoint).hostname or "").casefold()
            == "api.deepseek.com"
        )
        return self._send(
            request,
            body=_openai_payload(
                request,
                deepseek_thinking=deepseek_thinking,
            ),
            headers={
                "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            parser=_parse_openai_response,
            compatibility_body=_openai_payload(
                request,
                json_mode=False,
                deepseek_thinking=deepseek_thinking,
            ),
        )


class AzureOpenAiChatAdapter(_ProviderAdapter):
    def complete(self, request: ChatRequest) -> ChatResponse:
        return self._send(
            request,
            body=_openai_payload(request),
            headers={
                "api-key": self._api_key.get_secret_value(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            parser=_parse_openai_response,
            compatibility_body=_openai_payload(request, json_mode=False),
        )


def _anthropic_payload(request: ChatRequest) -> bytes:
    _require_json_instruction(request)
    system = "\n\n".join(
        message.content for message in request.messages if message.role == "system"
    )
    messages = [
        {"role": message.role, "content": message.content}
        for message in request.messages
        if message.role != "system"
    ]
    if not messages:
        raise ChatError("chat_invalid_request")
    payload: dict[str, object] = {
        "model": request.model,
        "messages": messages,
        "max_tokens": request.max_tokens or 4096,
        "stream": False,
    }
    if system:
        payload["system"] = system
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    return _json_bytes(payload)


def _parse_anthropic_response(
    response: ProviderHttpResponse,
    requested_model: str,
) -> ChatResponse:
    payload = response.payload
    if not isinstance(payload, dict):
        raise ChatError("chat_response_invalid")
    stop_reason = payload.get("stop_reason")
    if stop_reason == "max_tokens":
        raise ChatError("chat_output_truncated")
    if stop_reason not in {"end_turn", "stop_sequence"}:
        raise ChatError("chat_response_invalid")
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        raise ChatError("chat_response_invalid")
    texts = [
        block.get("text")
        for block in blocks
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    content = "".join(texts)
    if not content.strip():
        raise ChatError("chat_response_empty")
    actual_model = payload.get("model")
    if not isinstance(actual_model, str):
        actual_model = requested_model
    try:
        actual_model = validate_chat_model(actual_model)
    except ValueError:
        actual_model = requested_model
    usage_payload = payload.get("usage")
    if not isinstance(usage_payload, dict):
        usage_payload = {}
    prompt_tokens = _nonnegative_integer(usage_payload.get("input_tokens"))
    completion_tokens = _nonnegative_integer(usage_payload.get("output_tokens"))
    total_tokens = (
        prompt_tokens + completion_tokens
        if prompt_tokens is not None and completion_tokens is not None
        else None
    )
    return ChatResponse(
        content=content,
        requested_model=requested_model,
        actual_model=actual_model,
        finish_reason="stop",
        request_id=_request_id(response.headers, payload),
        usage=ChatUsage(prompt_tokens, completion_tokens, total_tokens),
    )


class AnthropicMessagesChatAdapter(_ProviderAdapter):
    def complete(self, request: ChatRequest) -> ChatResponse:
        return self._send(
            request,
            body=_anthropic_payload(request),
            headers={
                "x-api-key": self._api_key.get_secret_value(),
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            parser=_parse_anthropic_response,
        )


def _gemini_payload(request: ChatRequest) -> bytes:
    _require_json_instruction(request)
    system_parts = [
        {"text": message.content}
        for message in request.messages
        if message.role == "system"
    ]
    contents = [
        {
            "role": "model" if message.role == "assistant" else "user",
            "parts": [{"text": message.content}],
        }
        for message in request.messages
        if message.role != "system"
    ]
    if not contents:
        raise ChatError("chat_invalid_request")
    generation: dict[str, object] = {"responseMimeType": "application/json"}
    if request.temperature is not None:
        generation["temperature"] = request.temperature
    if request.max_tokens is not None:
        generation["maxOutputTokens"] = request.max_tokens
    payload: dict[str, object] = {
        "contents": contents,
        "generationConfig": generation,
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    return _json_bytes(payload)


def _parse_gemini_response(
    response: ProviderHttpResponse,
    requested_model: str,
) -> ChatResponse:
    payload = response.payload
    if not isinstance(payload, dict):
        raise ChatError("chat_response_invalid")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ChatError("chat_response_invalid")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise ChatError("chat_response_invalid")
    finish_reason = candidate.get("finishReason")
    if finish_reason == "MAX_TOKENS":
        raise ChatError("chat_output_truncated")
    if finish_reason in {"SAFETY", "RECITATION", "PROHIBITED_CONTENT"}:
        raise ChatError("chat_content_filtered")
    if finish_reason != "STOP":
        raise ChatError("chat_response_invalid")
    content_block = candidate.get("content")
    parts = content_block.get("parts") if isinstance(content_block, dict) else None
    if not isinstance(parts, list):
        raise ChatError("chat_response_invalid")
    content = "".join(
        part.get("text")
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )
    if not content.strip():
        raise ChatError("chat_response_empty")
    usage_payload = payload.get("usageMetadata")
    if not isinstance(usage_payload, dict):
        usage_payload = {}
    return ChatResponse(
        content=content,
        requested_model=requested_model,
        actual_model=requested_model,
        finish_reason="stop",
        request_id=_request_id(response.headers, payload),
        usage=ChatUsage(
            prompt_tokens=_nonnegative_integer(
                usage_payload.get("promptTokenCount")
            ),
            completion_tokens=_nonnegative_integer(
                usage_payload.get("candidatesTokenCount")
            ),
            total_tokens=_nonnegative_integer(usage_payload.get("totalTokenCount")),
        ),
    )


class GoogleGeminiChatAdapter(_ProviderAdapter):
    def complete(self, request: ChatRequest) -> ChatResponse:
        return self._send(
            request,
            body=_gemini_payload(request),
            headers={
                "x-goog-api-key": self._api_key.get_secret_value(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            parser=_parse_gemini_response,
        )


def build_provider_chat_client(
    profile: Mapping[str, object],
    credentials: ApiKeyCredentialBundle,
    *,
    transport: ProviderTransport | None = None,
) -> _ProviderAdapter:
    protocol = profile.get("protocol")
    base_url = profile.get("base_url")
    model = profile.get("model")
    parameters = profile.get("parameters")
    if (
        protocol not in MODERN_CHAT_PROTOCOLS
        or not isinstance(base_url, str)
        or not isinstance(model, str)
        or not isinstance(parameters, Mapping)
        or not isinstance(credentials, ApiKeyCredentialBundle)
    ):
        raise ValueError("invalid provider chat profile")
    endpoint = provider_chat_endpoint(protocol, base_url, parameters, model)
    kwargs = {
        "protocol": protocol,
        "endpoint": endpoint,
        "model": model,
        "api_key": SecretStr(credentials.api_key.get_secret_value()),
        "transport": transport,
    }
    if protocol == "anthropic_messages":
        return AnthropicMessagesChatAdapter(**kwargs)
    if protocol == "google_gemini":
        return GoogleGeminiChatAdapter(**kwargs)
    if protocol == "azure_openai":
        return AzureOpenAiChatAdapter(**kwargs)
    return OpenAiCompatibleChatAdapter(**kwargs)


@dataclass(frozen=True, slots=True)
class ProviderCapabilityTestResult:
    ok: bool
    message: str | None = None


def _profile_mapping(profile: object) -> dict[str, object]:
    if isinstance(profile, Mapping):
        return dict(profile)
    return {
        "protocol": getattr(profile, "protocol", None),
        "base_url": getattr(profile, "base_url", None),
        "parameters": getattr(profile, "parameters", {}),
        "model": getattr(profile, "model", None),
        "options": getattr(profile, "options", {}),
    }


class ProviderProfileTester:
    """Run the explicitly acknowledged minimal JSON capability request."""

    def test_profile(
        self,
        profile: object,
        credentials: object,
        test_input: object,
        *,
        follow_redirects: Literal[False],
    ) -> ProviderCapabilityTestResult:
        del test_input
        if follow_redirects is not False:
            raise ValueError("redirects must remain disabled")
        if not isinstance(credentials, ApiKeyCredentialBundle):
            return ProviderCapabilityTestResult(False, "credential_unavailable")
        selected = _profile_mapping(profile)
        if selected.get("protocol") not in MODERN_CHAT_PROTOCOLS:
            return ProviderCapabilityTestResult(False, "profile_protocol_invalid")
        options = selected.get("options")
        if not isinstance(options, Mapping):
            options = {}
        try:
            client = build_provider_chat_client(selected, credentials)
            response = client.complete(
                ChatRequest(
                    model=selected.get("model"),  # type: ignore[arg-type]
                    messages=(
                        ChatMessage(
                            role="system",
                            content="Return exactly one JSON object and no other text.",
                        ),
                        ChatMessage(
                            role="user",
                            content='Reply with this JSON object: {"ok":true}',
                        ),
                    ),
                    temperature=options.get("temperature"),  # type: ignore[arg-type]
                    max_tokens=min(options.get("max_tokens", 4096), 64),  # type: ignore[arg-type]
                    enable_thinking=options.get("enable_thinking"),  # type: ignore[arg-type]
                )
            )
            parsed = json.loads(response.content)
            if not isinstance(parsed, dict):
                return ProviderCapabilityTestResult(False, "response_not_json_object")
        except (ChatError, TypeError, ValueError, json.JSONDecodeError):
            return ProviderCapabilityTestResult(False, "capability_test_failed")
        return ProviderCapabilityTestResult(True, "profile_capability_tested")
