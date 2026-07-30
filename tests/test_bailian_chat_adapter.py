from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from vtnote.chat import (
    AliyunBailianChatAdapter,
    BailianHttpResponse,
    BailianTransportFailure,
    ChatError,
    ChatMessage,
    ChatRequest,
    HttpxBailianTransport,
)


WORKSPACE = "ws-1234"
ENDPOINT = (
    "https://ws-1234.cn-beijing.maas.aliyuncs.com/"
    "compatible-mode/v1/chat/completions"
)


def valid_payload(
    *,
    content: str = '{"ok":true}',
    finish_reason: str = "stop",
    model: str = "qwen-plus",
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-safe-id",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": None,
                },
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "total_tokens": 13,
        },
    }


@dataclass
class FakeTransport:
    responses: list[BailianHttpResponse | BaseException]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(self, *, url: str, headers: dict[str, str], body: bytes) -> BailianHttpResponse:
        self.calls.append({"url": url, "headers": headers, "body": body})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def request(*, model: str = "qwen-plus", content: str = "Return JSON only.") -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=(ChatMessage(role="user", content=content),),
        response_format="json_object",
    )


@pytest.mark.parametrize(
    "workspace_id",
    [
        "",
        "-bad",
        "bad-",
        "UPPER",
        "with.dot",
        "with/path",
        "user@host",
        "a" * 64,
        "https://relay.example/v1",
    ],
)
def test_workspace_id_accepts_only_one_lowercase_dns_label(workspace_id: str) -> None:
    with pytest.raises(ValueError, match="workspace"):
        AliyunBailianChatAdapter(
            workspace_id=workspace_id,
            api_key=SecretStr("secret"),
            transport=FakeTransport([]),
        )


def test_request_is_fixed_to_beijing_workspace_and_nonstreaming_json() -> None:
    transport = FakeTransport(
        [BailianHttpResponse(200, valid_payload(), {"x-request-id": "req-safe"})]
    )
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("top-secret"),
        transport=transport,
    )

    response = adapter.complete(request())

    call = transport.calls[0]
    assert call["url"] == ENDPOINT
    assert call["headers"]["Authorization"] == "Bearer top-secret"
    payload = json.loads(call["body"])
    assert payload == {
        "messages": [{"content": "Return JSON only.", "role": "user"}],
        "model": "qwen-plus",
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    assert response.requested_model == "qwen-plus"
    assert response.actual_model == "qwen-plus"
    assert response.request_id == "req-safe"
    assert response.usage.total_tokens == 13


def test_default_http_transport_disables_proxy_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class CapturingClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", CapturingClient)
    HttpxBailianTransport(endpoint=ENDPOINT)

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False


def test_http_transport_rejects_redirect_without_following() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"location": "https://relay.example"})
        ),
        trust_env=False,
        follow_redirects=False,
    )
    transport = HttpxBailianTransport(endpoint=ENDPOINT, client=client)

    response = transport.post(url=ENDPOINT, headers={}, body=b"{}")

    assert response.status_code == 302


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user@ws-1234.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
        "https://ws-1234.cn-beijing.maas.aliyuncs.com:443/compatible-mode/v1/chat/completions",
        "https://ws-1234.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions?relay=1",
    ],
)
def test_http_transport_accepts_only_the_canonical_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        HttpxBailianTransport(endpoint=endpoint)


@pytest.mark.parametrize("model", ["", "bad model", "../model", "a" * 129])
def test_model_has_a_fixed_safe_1_to_128_character_policy(model: str) -> None:
    with pytest.raises(ValueError, match="model"):
        request(model=model)


def test_json_response_format_requires_literal_json_in_prompt() -> None:
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("secret"),
        transport=FakeTransport([]),
    )

    with pytest.raises(ChatError, match="chat_json_instruction_required"):
        adapter.complete(request(content="Return one object."))


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({}, "chat_response_invalid"),
        ({"choices": []}, "chat_response_invalid"),
        (valid_payload(content=""), "chat_response_empty"),
        (valid_payload(finish_reason="length"), "chat_output_truncated"),
        (valid_payload(finish_reason="content_filter"), "chat_content_filtered"),
        (
            {
                **valid_payload(),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "refusal": "blocked",
                        },
                        "finish_reason": "stop",
                        "logprobs": None,
                    }
                ],
            },
            "chat_content_filtered",
        ),
        ({**valid_payload(), "unexpected": True}, "chat_response_invalid"),
    ],
)
def test_response_contract_rejects_unsafe_or_incomplete_results(
    payload: dict[str, Any],
    code: str,
) -> None:
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("secret"),
        transport=FakeTransport([BailianHttpResponse(200, payload, {})]),
    )

    with pytest.raises(ChatError, match=code):
        adapter.complete(request())


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (302, "chat_redirect_blocked"),
        (400, "chat_invalid_request"),
        (401, "chat_authentication_failed"),
        (403, "chat_permission_denied"),
        (404, "chat_model_not_found"),
    ],
)
def test_nonretryable_http_errors_stop(status: int, code: str) -> None:
    transport = FakeTransport([BailianHttpResponse(status, {}, {})])
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("secret"),
        transport=transport,
    )

    with pytest.raises(ChatError, match=code):
        adapter.complete(request())
    assert len(transport.calls) == 1


def test_429_retries_exactly_once_with_bounded_retry_after() -> None:
    delays: list[float] = []
    transport = FakeTransport(
        [
            BailianHttpResponse(429, {}, {"retry-after": "99"}),
            BailianHttpResponse(200, valid_payload(), {}),
        ]
    )
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("secret"),
        transport=transport,
        sleeper=delays.append,
    )

    adapter.complete(request())

    assert len(transport.calls) == 2
    assert delays == [5.0]


def test_429_without_valid_retry_after_is_not_replayed() -> None:
    transport = FakeTransport(
        [BailianHttpResponse(429, {}, {"retry-after": "invalid"})]
    )
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("secret"),
        transport=transport,
    )

    with pytest.raises(ChatError, match="chat_rate_limited"):
        adapter.complete(request())
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status", [500, 503])
def test_server_error_has_unknown_submission_outcome_and_no_retry(status: int) -> None:
    transport = FakeTransport([BailianHttpResponse(status, {}, {})])
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("secret"),
        transport=transport,
    )

    with pytest.raises(ChatError) as caught:
        adapter.complete(request())
    assert caught.value.code == "chat_submission_unknown"
    assert caught.value.submission_unknown is True
    assert len(transport.calls) == 1


def test_response_loss_has_unknown_submission_outcome_and_no_retry() -> None:
    transport = FakeTransport(
        [BailianTransportFailure("chat_response_lost", sent=True)]
    )
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("secret"),
        transport=transport,
    )

    with pytest.raises(ChatError) as caught:
        adapter.complete(request())
    assert caught.value.code == "chat_submission_unknown"
    assert len(transport.calls) == 1


def test_request_and_response_size_limits_are_enforced() -> None:
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("secret"),
        transport=FakeTransport([]),
    )
    with pytest.raises(ChatError, match="chat_request_oversize"):
        adapter.complete(request(content=("JSON " + "x" * (64 * 1024))))

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"x" * (256 * 1024 + 1))
        ),
        trust_env=False,
    )
    transport = HttpxBailianTransport(endpoint=ENDPOINT, client=client)
    with pytest.raises(BailianTransportFailure, match="chat_response_oversize"):
        transport.post(url=ENDPOINT, headers={}, body=b"{}")


def test_secrets_and_prompt_are_absent_from_repr_and_errors() -> None:
    prompt = "JSON private-custom-prompt"
    adapter = AliyunBailianChatAdapter(
        workspace_id=WORKSPACE,
        api_key=SecretStr("private-api-key"),
        transport=FakeTransport([BailianHttpResponse(400, {}, {})]),
    )
    chat_request = request(content=prompt)

    with pytest.raises(ChatError) as caught:
        adapter.complete(chat_request)

    combined = repr(adapter) + repr(chat_request) + repr(caught.value)
    assert "private-api-key" not in combined
    assert prompt not in combined
