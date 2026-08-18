from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from vtnote.chat import BailianHttpResponse, ChatMessage, ChatRequest
from vtnote.tokenhub_chat import (
    TOKENHUB_CHAT_ENDPOINT,
    TencentTokenHubChatAdapter,
)


@dataclass
class FakeTransport:
    responses: list[BailianHttpResponse]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> BailianHttpResponse:
        self.calls.append({"url": url, "headers": headers, "body": body})
        return self.responses.pop(0)


def test_glm_request_uses_verified_tokenhub_contract() -> None:
    transport = FakeTransport(
        [
            BailianHttpResponse(
                200,
                {
                    "id": "chatcmpl-safe",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "glm-5.1",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": '{"ok":true}',
                                "refusal": None,
                            },
                            "finish_reason": "stop",
                            "logprobs": None,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 4,
                        "total_tokens": 12,
                    },
                },
                {"x-request-id": "tokenhub-safe"},
            )
        ]
    )
    adapter = TencentTokenHubChatAdapter(
        api_key=SecretStr("local-test-key"),
        transport=transport,
    )

    response = adapter.complete(
        ChatRequest(
            model="glm-5.1",
            messages=(ChatMessage(role="user", content="Return JSON only."),),
            response_format="json_object",
            temperature=0.2,
            max_tokens=64,
            enable_thinking=False,
        )
    )

    call = transport.calls[0]
    assert call["url"] == TOKENHUB_CHAT_ENDPOINT
    assert call["headers"]["Authorization"] == "Bearer local-test-key"
    assert json.loads(call["body"]) == {
        "max_tokens": 64,
        "messages": [{"content": "Return JSON only.", "role": "user"}],
        "model": "glm-5.1",
        "response_format": {"type": "json_object"},
        "stream": False,
        "temperature": 0.2,
        "thinking": {"type": "disabled"},
    }
    assert response.actual_model == "glm-5.1"
    assert response.content == '{"ok":true}'
    assert "local-test-key" not in repr(adapter)
