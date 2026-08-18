"""Tencent TokenHub domestic OpenAI-compatible chat adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from vtnote.chat import (
    MAX_CHAT_REQUEST_BYTES,
    MAX_CHAT_RESPONSE_BYTES,
    AliyunBailianChatAdapter,
    BailianCapabilityTestResult,
    BailianHttpResponse,
    BailianTransport,
    BailianTransportFailure,
    ChatCapabilities,
    ChatError,
    ChatMessage,
    ChatRequest,
    canonical_chat_request_bytes,
)
from vtnote.provider_credentials import TokenHubCredentialBundle


TOKENHUB_BASE_URL = "https://tokenhub.tencentmaas.com/v1"
TOKENHUB_CHAT_ENDPOINT = f"{TOKENHUB_BASE_URL}/chat/completions"
TOKENHUB_DEFAULT_MODEL = "glm-5.1"
TOKENHUB_DEFAULT_CONTEXT_LENGTH = 200_000


class HttpxTokenHubTransport:
    """One fixed TokenHub POST with redirects and environment proxies disabled."""

    def __init__(
        self,
        *,
        endpoint: str = TOKENHUB_CHAT_ENDPOINT,
        client: httpx.Client | None = None,
        max_response_bytes: int = MAX_CHAT_RESPONSE_BYTES,
    ) -> None:
        try:
            parts = urlsplit(endpoint)
            _ = parts.port
        except ValueError:
            raise ValueError("invalid TokenHub endpoint") from None
        if endpoint != TOKENHUB_CHAT_ENDPOINT or parts.username or parts.password:
            raise ValueError("invalid TokenHub endpoint")
        if client is not None and client.follow_redirects:
            raise ValueError("TokenHub transport must not follow redirects")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("invalid TokenHub response limit")
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


class TencentTokenHubChatAdapter(AliyunBailianChatAdapter):
    """Strict TokenHub adapter reusing the validated OpenAI response contract."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        transport: BailianTransport | None = None,
        sleeper: Callable[[float], object] = time.sleep,
    ) -> None:
        if not isinstance(api_key, SecretStr) or not api_key.get_secret_value().strip():
            raise ValueError("TokenHub API key is required")
        self.workspace_id = "tokenhub-guangzhou"
        self.endpoint = TOKENHUB_CHAT_ENDPOINT
        self._api_key = api_key
        self._transport = transport or HttpxTokenHubTransport()
        self._sleeper = sleeper

    def __repr__(self) -> str:
        return "TencentTokenHubChatAdapter(api_key=[redacted])"

    def capabilities(self) -> ChatCapabilities:
        return ChatCapabilities(
            protocol="tencent_tokenhub",
            endpoint=self.endpoint,
            response_format="json_object",
            max_request_bytes=MAX_CHAT_REQUEST_BYTES,
            max_response_bytes=MAX_CHAT_RESPONSE_BYTES,
        )

    @staticmethod
    def _payload(request: ChatRequest) -> bytes:
        payload = json.loads(canonical_chat_request_bytes(request))
        payload.pop("enable_thinking", None)
        if request.enable_thinking is not None:
            payload["thinking"] = {
                "type": "enabled" if request.enable_thinking else "disabled"
            }
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > MAX_CHAT_REQUEST_BYTES:
            raise ChatError("chat_request_oversize")
        return body


class TokenHubProfileTester:
    """Run one explicitly acknowledged minimal TokenHub model request."""

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
        if not isinstance(credentials, TokenHubCredentialBundle):
            return BailianCapabilityTestResult(False, "credential_unavailable")
        if getattr(profile, "protocol", None) != "tencent_tokenhub":
            return BailianCapabilityTestResult(False, "profile_protocol_invalid")
        model = getattr(profile, "model", None)
        options = getattr(profile, "options", None)
        if not isinstance(options, dict):
            options = {}
        try:
            adapter = TencentTokenHubChatAdapter(api_key=credentials.api_key)
            response = adapter.complete(
                ChatRequest(
                    model=model,
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
                    response_format="json_object",
                    temperature=options.get("temperature"),
                    max_tokens=min(options.get("max_tokens", 4096), 64),
                    enable_thinking=False,
                )
            )
            parsed = json.loads(response.content)
            if not isinstance(parsed, dict) or parsed.get("ok") is not True:
                return BailianCapabilityTestResult(False, "response_not_expected_json")
        except (ChatError, TypeError, ValueError, json.JSONDecodeError):
            return BailianCapabilityTestResult(False, "capability_test_failed")
        return BailianCapabilityTestResult(True, "profile_capability_tested")
