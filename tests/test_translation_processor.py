from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

import pytest

from vtnote.chat import (
    AiLimits,
    ChatError,
    ChatProfileSnapshot,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    canonical_chat_request_bytes,
)
from vtnote.config import Settings
from vtnote.paths import StoragePaths
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    transcript_sha256,
)
from vtnote.translation import (
    TranslationCanceled,
    TranslationError,
    Translator,
)


def transcript(count: int, *, text_size: int = 8) -> Transcript:
    return Transcript(
        language="en",
        duration_ms=count * 1_000,
        provenance=Provenance(
            method=ProvenanceMethod.PLATFORM_SUBTITLE,
            provider="test",
            model=None,
        ),
        segments=tuple(
            TranscriptSegment(
                id=f"seg_{index:06d}",
                start_ms=(index - 1) * 1_000,
                end_ms=index * 1_000,
                text=f"cue-{index}-" + ("x" * text_size),
            )
            for index in range(1, count + 1)
        ),
    )


def profile() -> ChatProfileSnapshot:
    return ChatProfileSnapshot(
        model="qwen-plus",
        context_length=32768,
        temperature=0.2,
        max_tokens=4096,
        enable_thinking=False,
    )


def response(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        requested_model="qwen-plus",
        actual_model="qwen-plus",
        finish_reason="stop",
        request_id="req-safe",
        usage=ChatUsage(
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    )


def request_data(request: ChatRequest) -> dict[str, object]:
    assert request.messages[0].role == "system"
    assert request.messages[1].role == "user"
    return json.loads(request.messages[1].content)


def valid_content(request: ChatRequest) -> str:
    data = request_data(request)
    cues = data["cues"]
    assert isinstance(cues, list)
    return json.dumps(
        {
            "translations": [
                {
                    "cue_id": cue["cue_id"],
                    "text": f"译文-{cue['cue_id']}",
                }
                for cue in cues
            ]
        },
        ensure_ascii=False,
    )


@dataclass
class ScriptedClient:
    handler: Callable[[ChatRequest, int], ChatResponse]
    calls: list[ChatRequest] = field(default_factory=list)

    def complete(self, request: ChatRequest) -> ChatResponse:
        index = len(self.calls)
        self.calls.append(request)
        return self.handler(request, index)


def test_translation_preserves_order_hash_and_target_language_in_30_cue_batches() -> None:
    source = transcript(31)
    client = ScriptedClient(lambda request, _: response(valid_content(request)))

    translated = Translator().translate(
        source,
        "zh-Hans",
        profile(),
        client,
        AiLimits(),
    )

    assert [entry.cue_id for entry in translated.entries] == [
        segment.id for segment in source.segments
    ]
    assert translated.language == "zh-Hans"
    assert translated.source_transcript_sha256 == transcript_sha256(source)
    assert [len(request_data(call)["cues"]) for call in client.calls] == [30, 1]
    assert all(
        request_data(call)["target_language"] == "zh-Hans"
        for call in client.calls
    )
    assert all(call.response_format == "json_object" for call in client.calls)
    assert all(len(canonical_chat_request_bytes(call)) <= 64 * 1024 for call in client.calls)
    assert all(
        json.loads(canonical_chat_request_bytes(call))["stream"] is False
        for call in client.calls
    )


def test_batching_uses_the_complete_utf8_request_budget_not_only_cue_count() -> None:
    source = transcript(25, text_size=4_000)
    client = ScriptedClient(lambda request, _: response(valid_content(request)))

    translated = Translator().translate(
        source,
        "zh-Hans",
        profile(),
        client,
        AiLimits(),
    )

    assert len(translated.entries) == 25
    assert len(client.calls) > 1
    assert all(len(request_data(call)["cues"]) <= 30 for call in client.calls)
    assert all(len(canonical_chat_request_bytes(call)) <= 64 * 1024 for call in client.calls)


def test_single_cue_that_cannot_fit_is_rejected_without_truncation_or_call() -> None:
    source = transcript(1, text_size=70_000)
    client = ScriptedClient(lambda request, _: response(valid_content(request)))

    with pytest.raises(TranslationError, match="translation_cue_oversize"):
        Translator().translate(source, "zh-Hans", profile(), client, AiLimits())

    assert client.calls == []


def test_one_structural_retry_round_splits_30_cues_into_two_15_cue_calls() -> None:
    source = transcript(30)

    def handler(request: ChatRequest, index: int) -> ChatResponse:
        if index == 0:
            return response('{"translations":[]}')
        return response(valid_content(request))

    client = ScriptedClient(handler)

    translated = Translator().translate(
        source,
        "zh-Hans",
        profile(),
        client,
        AiLimits(),
    )

    assert len(translated.entries) == 30
    assert [len(request_data(call)["cues"]) for call in client.calls] == [30, 15, 15]


@pytest.mark.parametrize(
    "invalid_content",
    [
        "not-json",
        '{"translations":[],"unknown":true}',
        '{"translations":[{"cue_id":"seg_000001","text":"ok","unknown":true}]}',
        '{"translations":[{"cue_id":"seg_000001","text":""}]}',
        '{"translations":[{"cue_id":"seg_999999","text":"wrong"}]}',
        '{"translations":[{"cue_id":"seg_000001","text":"a"},{"cue_id":"seg_000001","text":"b"}]}',
    ],
)
def test_invalid_translation_shape_gets_no_second_retry_round(
    invalid_content: str,
) -> None:
    client = ScriptedClient(lambda _request, _index: response(invalid_content))

    with pytest.raises(TranslationError, match="translation_response_invalid"):
        Translator().translate(
            transcript(1),
            "zh-Hans",
            profile(),
            client,
            AiLimits(),
        )

    assert len(client.calls) == 2


def test_reordered_result_is_rejected_even_when_id_set_matches() -> None:
    source = transcript(2)

    def reversed_content(request: ChatRequest, _: int) -> ChatResponse:
        data = request_data(request)
        cues = data["cues"]
        assert isinstance(cues, list)
        return response(
            json.dumps(
                {
                    "translations": [
                        {"cue_id": cue["cue_id"], "text": "x"}
                        for cue in reversed(cues)
                    ]
                }
            )
        )

    client = ScriptedClient(reversed_content)
    with pytest.raises(TranslationError, match="translation_response_invalid"):
        Translator().translate(source, "zh-Hans", profile(), client, AiLimits())
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "code",
    [
        "chat_response_empty",
        "chat_response_invalid",
        "chat_output_truncated",
        "chat_content_filtered",
        "chat_submission_unknown",
    ],
)
def test_chat_contract_errors_are_not_automatically_replayed(code: str) -> None:
    def fail(_request: ChatRequest, _index: int) -> ChatResponse:
        raise ChatError(
            code,
            submission_unknown=(code == "chat_submission_unknown"),
        )

    client = ScriptedClient(fail)
    with pytest.raises(ChatError, match=code):
        Translator().translate(
            transcript(1),
            "zh-Hans",
            profile(),
            client,
            AiLimits(),
        )
    assert len(client.calls) == 1


def test_oversize_response_is_rejected_without_a_retry() -> None:
    client = ScriptedClient(
        lambda _request, _index: response("x" * (256 * 1024 + 1))
    )

    with pytest.raises(TranslationError, match="translation_response_oversize"):
        Translator().translate(
            transcript(1),
            "zh-Hans",
            profile(),
            client,
            AiLimits(),
        )

    assert len(client.calls) == 1


def test_transcript_data_is_separate_from_static_system_instructions() -> None:
    source = transcript(1)
    client = ScriptedClient(lambda request, _: response(valid_content(request)))

    Translator().translate(source, "zh-Hans", profile(), client, AiLimits())

    call = client.calls[0]
    assert source.segments[0].text not in call.messages[0].content
    assert source.segments[0].text in call.messages[1].content
    assert call.messages[0].content == Translator.SYSTEM_PROMPT


def test_cancellation_after_remote_call_discards_result_and_writes_nothing(
    tmp_path: Path,
) -> None:
    canceled = False

    def handler(request: ChatRequest, _: int) -> ChatResponse:
        nonlocal canceled
        canceled = True
        return response(valid_content(request))

    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    item_id = str(uuid4())
    client = ScriptedClient(handler)
    translator = Translator(cancel_check=lambda: canceled)

    with pytest.raises(TranslationCanceled):
        translator.translate_and_write(
            transcript(1),
            "zh-Hans",
            profile(),
            client,
            AiLimits(),
            paths=paths,
            item_id=item_id,
        )

    assert not paths.translation(item_id, "zh-Hans").exists()


def test_failed_retry_publishes_no_partial_translation(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    item_id = str(uuid4())
    client = ScriptedClient(
        lambda _request, _index: response('{"translations":[]}')
    )

    with pytest.raises(TranslationError):
        Translator().translate_and_write(
            transcript(30),
            "zh-Hans",
            profile(),
            client,
            AiLimits(),
            paths=paths,
            item_id=item_id,
        )

    assert not paths.translation(item_id, "zh-Hans").exists()


def test_success_is_atomically_published_as_one_valid_artifact(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    item_id = str(uuid4())
    source = transcript(3)
    client = ScriptedClient(lambda request, _: response(valid_content(request)))

    translated = Translator().translate_and_write(
        source,
        "zh-Hans",
        profile(),
        client,
        AiLimits(),
        paths=paths,
        item_id=item_id,
    )

    stored = json.loads(paths.translation(item_id, "zh-Hans").read_text("utf-8"))
    assert stored == translated.model_dump(mode="json")
    translated.validate_against(source)
