from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from pydantic import SecretStr

from vtnote.chat import ChatError, ChatMessage, ChatRequest
from vtnote.provider_chat import (
    AnthropicMessagesChatAdapter,
    AzureOpenAiChatAdapter,
    GoogleGeminiChatAdapter,
    OpenAiCompatibleChatAdapter,
    ProviderHttpResponse,
    build_provider_chat_client,
    provider_chat_endpoint,
)
from vtnote.provider_credentials import ApiKeyCredentialBundle


@dataclass
class RecordingTransport:
    response: ProviderHttpResponse
    calls: list[dict[str, object]] = field(default_factory=list)

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ProviderHttpResponse:
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.response


@dataclass
class SequenceTransport:
    responses: list[ProviderHttpResponse]
    calls: list[dict[str, object]] = field(default_factory=list)

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ProviderHttpResponse:
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.responses.pop(0)


def request(model: str) -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=(
            ChatMessage(role="system", content="Return one JSON object."),
            ChatMessage(role="user", content='Return {"ok":true} as JSON.'),
        ),
        temperature=0.2,
        max_tokens=1024,
        enable_thinking=False,
    )


def test_openai_compatible_uses_bearer_auth_and_json_mode() -> None:
    transport = RecordingTransport(
        ProviderHttpResponse(
            200,
            {
                "id": "request-1",
                "model": "vendor/model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                },
            },
            {},
        )
    )
    adapter = OpenAiCompatibleChatAdapter(
        protocol="openai_chat_completions",
        endpoint="https://api.example.com/v1/chat/completions",
        model="vendor/model",
        api_key=SecretStr("secret"),
        transport=transport,
    )

    result = adapter.complete(request("vendor/model"))

    call = transport.calls[0]
    assert call["url"] == "https://api.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret"  # type: ignore[index]
    payload = json.loads(call["body"])  # type: ignore[arg-type]
    assert payload["response_format"] == {"type": "json_object"}
    assert "enable_thinking" not in payload
    assert result.content == '{"ok":true}'
    assert result.actual_model == "vendor/model"


@pytest.mark.parametrize(
    ("enable_thinking", "expected_type"),
    [(False, "disabled"), (True, "enabled")],
)
def test_deepseek_uses_native_thinking_toggle(
    enable_thinking: bool,
    expected_type: str,
) -> None:
    transport = RecordingTransport(
        ProviderHttpResponse(
            200,
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok":true}'},
                    }
                ],
            },
            {},
        )
    )
    adapter = OpenAiCompatibleChatAdapter(
        protocol="openai_chat_completions",
        endpoint="https://api.deepseek.com/chat/completions",
        model="deepseek-v4-flash",
        api_key=SecretStr("secret"),
        transport=transport,
    )
    selected_request = request("deepseek-v4-flash")
    selected_request = ChatRequest(
        model=selected_request.model,
        messages=selected_request.messages,
        temperature=selected_request.temperature,
        max_tokens=selected_request.max_tokens,
        enable_thinking=enable_thinking,
    )

    adapter.complete(selected_request)

    payload = json.loads(transport.calls[0]["body"])  # type: ignore[arg-type]
    assert payload["thinking"] == {"type": expected_type}
    assert "enable_thinking" not in payload


def test_openai_compatible_falls_back_when_provider_rejects_json_mode() -> None:
    transport = SequenceTransport(
        [
            ProviderHttpResponse(
                400,
                {
                    "error": {
                        "message": "response_format is not supported by this model"
                    }
                },
                {},
            ),
            ProviderHttpResponse(
                200,
                {
                    "model": "vendor/model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"ok":true}'},
                        }
                    ],
                },
                {},
            ),
        ]
    )
    adapter = OpenAiCompatibleChatAdapter(
        protocol="openai_chat_completions",
        endpoint="https://api.example.com/v1/chat/completions",
        model="vendor/model",
        api_key=SecretStr("secret"),
        transport=transport,
    )

    result = adapter.complete(request("vendor/model"))

    assert result.content == '{"ok":true}'
    assert len(transport.calls) == 2
    first = json.loads(transport.calls[0]["body"])  # type: ignore[arg-type]
    second = json.loads(transport.calls[1]["body"])  # type: ignore[arg-type]
    assert first["response_format"] == {"type": "json_object"}
    assert "response_format" not in second


def test_openai_compatible_does_not_retry_unrelated_invalid_requests() -> None:
    transport = SequenceTransport(
        [
            ProviderHttpResponse(
                400,
                {"error": {"message": "model is unavailable"}},
                {},
            )
        ]
    )
    adapter = OpenAiCompatibleChatAdapter(
        protocol="openai_chat_completions",
        endpoint="https://api.example.com/v1/chat/completions",
        model="vendor/model",
        api_key=SecretStr("secret"),
        transport=transport,
    )

    with pytest.raises(ChatError, match="chat_invalid_request"):
        adapter.complete(request("vendor/model"))

    assert len(transport.calls) == 1


def test_anthropic_messages_uses_native_request_and_key_header() -> None:
    transport = RecordingTransport(
        ProviderHttpResponse(
            200,
            {
                "id": "msg_1",
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": '{"ok":true}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 3},
            },
            {},
        )
    )
    adapter = AnthropicMessagesChatAdapter(
        protocol="anthropic_messages",
        endpoint="https://api.anthropic.com/v1/messages",
        model="claude-sonnet-4-5",
        api_key=SecretStr("secret"),
        transport=transport,
    )

    result = adapter.complete(request("claude-sonnet-4-5"))

    call = transport.calls[0]
    assert call["headers"]["x-api-key"] == "secret"  # type: ignore[index]
    payload = json.loads(call["body"])  # type: ignore[arg-type]
    assert payload["system"] == "Return one JSON object."
    assert payload["messages"] == [
        {"role": "user", "content": 'Return {"ok":true} as JSON.'}
    ]
    assert result.usage.total_tokens == 7


def test_gemini_uses_native_generate_content_and_key_header() -> None:
    transport = RecordingTransport(
        ProviderHttpResponse(
            200,
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": '{"ok":true}'}]},
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 4,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 7,
                },
            },
            {},
        )
    )
    adapter = GoogleGeminiChatAdapter(
        protocol="google_gemini",
        endpoint=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash:generateContent"
        ),
        model="gemini-2.5-flash",
        api_key=SecretStr("secret"),
        transport=transport,
    )

    result = adapter.complete(request("gemini-2.5-flash"))

    call = transport.calls[0]
    assert call["headers"]["x-goog-api-key"] == "secret"  # type: ignore[index]
    payload = json.loads(call["body"])  # type: ignore[arg-type]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["systemInstruction"]["parts"] == [
        {"text": "Return one JSON object."}
    ]
    assert result.content == '{"ok":true}'


def test_azure_openai_uses_api_key_header() -> None:
    transport = RecordingTransport(
        ProviderHttpResponse(
            200,
            {
                "model": "summary-deployment",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"ok":true}'},
                    }
                ],
            },
            {},
        )
    )
    adapter = AzureOpenAiChatAdapter(
        protocol="azure_openai",
        endpoint=(
            "https://summary.openai.azure.com/openai/v1/chat/completions"
        ),
        model="summary-deployment",
        api_key=SecretStr("secret"),
        transport=transport,
    )

    adapter.complete(request("summary-deployment"))

    headers = transport.calls[0]["headers"]
    assert headers["api-key"] == "secret"  # type: ignore[index]
    assert "Authorization" not in headers


def test_provider_endpoints_are_bound_to_protocol_and_model() -> None:
    assert provider_chat_endpoint(
        "openai_chat_completions",
        "https://api.example.com/v1",
        {},
        "vendor/model",
    ) == "https://api.example.com/v1/chat/completions"
    assert provider_chat_endpoint(
        "google_gemini",
        "https://generativelanguage.googleapis.com",
        {},
        "publishers/google/model",
    ).endswith("publishers%2Fgoogle%2Fmodel:generateContent")
    with pytest.raises(ValueError, match="unsupported"):
        provider_chat_endpoint("unknown", "https://api.example.com", {}, "model")


@pytest.mark.parametrize(
    ("protocol", "base_url", "model", "adapter_type"),
    [
        (
            "openai_chat_completions",
            "https://api.openai.com/v1",
            "gpt-4.1-mini",
            OpenAiCompatibleChatAdapter,
        ),
        (
            "anthropic_messages",
            "https://api.anthropic.com",
            "claude-sonnet-4-5",
            AnthropicMessagesChatAdapter,
        ),
        (
            "google_gemini",
            "https://generativelanguage.googleapis.com",
            "gemini-2.5-flash",
            GoogleGeminiChatAdapter,
        ),
        (
            "azure_openai",
            "https://summary.openai.azure.com/openai/v1",
            "summary-deployment",
            AzureOpenAiChatAdapter,
        ),
    ],
)
def test_provider_factory_selects_every_supported_modern_protocol(
    protocol: str,
    base_url: str,
    model: str,
    adapter_type: type,
) -> None:
    client = build_provider_chat_client(
        {
            "protocol": protocol,
            "base_url": base_url,
            "parameters": {},
            "model": model,
        },
        ApiKeyCredentialBundle(api_key=SecretStr("secret")),
        transport=RecordingTransport(ProviderHttpResponse(200, {}, {})),
    )

    assert isinstance(client, adapter_type)
