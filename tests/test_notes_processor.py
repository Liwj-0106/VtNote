from __future__ import annotations

import json
from dataclasses import dataclass, field
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
from vtnote.notes import (
    NoteCanceled,
    NoteError,
    NoteGenerator,
)
from vtnote.paths import StoragePaths
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    transcript_sha256,
)


def transcript(count: int, *, text_size: int = 10) -> Transcript:
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


def response(content: str, *, actual_model: str = "qwen-plus-actual") -> ChatResponse:
    return ChatResponse(
        content=content,
        requested_model="qwen-plus",
        actual_model=actual_model,
        finish_reason="stop",
        request_id="req-safe",
        usage=ChatUsage(
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
        ),
    )


def request_data(request: ChatRequest) -> dict[str, object]:
    return json.loads(request.messages[1].content)


def first_citation(data: dict[str, object]) -> dict[str, object]:
    if data["operation"] == "map":
        cues = data["cues"]
        assert isinstance(cues, list) and cues
        cue = cues[0]
        return {
            "cue_id": cue["cue_id"],
            "start_ms": cue["start_ms"],
            "end_ms": cue["end_ms"],
        }
    nodes = data["nodes"]
    assert isinstance(nodes, list) and nodes
    return nodes[0]["summary_citations"][0]


def valid_content(request: ChatRequest, *, suffix: str = "") -> str:
    citation = first_citation(request_data(request))
    return json.dumps(
        {
            "title": f"笔记{suffix}",
            "summary": f"总结{suffix}",
            "summary_citations": [citation],
            "key_points": [
                {
                    "text": f"要点{suffix}",
                    "citations": [citation],
                }
            ],
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


@pytest.mark.parametrize("template", ["summary", "key_points"])
def test_builtin_templates_propagate_output_language_and_provenance(template: str) -> None:
    source = transcript(2)
    client = ScriptedClient(
        lambda request, index: response(
            valid_content(request, suffix=str(index)),
            actual_model="qwen-max",
        )
    )
    task_id = str(uuid4())

    note = NoteGenerator(task_id=task_id).generate(
        source,
        profile(),
        client,
        template=template,
        output_language="zh-Hans",
        custom_prompt=None,
        limits=AiLimits(),
    )

    assert note.generated_by_ai is True
    assert note.task_id == task_id
    assert note.transcript_sha256 == transcript_sha256(source)
    assert note.template == template
    assert note.output_language == "zh-Hans"
    assert note.requested_model == "qwen-plus"
    assert note.response_model == "qwen-max"
    assert all(request_data(call)["output_language"] == "zh-Hans" for call in client.calls)
    assert all(request_data(call)["template"] == template for call in client.calls)
    assert all(call.response_format == "json_object" for call in client.calls)
    assert all(
        json.loads(canonical_chat_request_bytes(call))["stream"] is False
        for call in client.calls
    )


def test_custom_prompt_is_required_only_for_custom_and_is_data_not_system_text() -> None:
    source = transcript(1)
    with pytest.raises(NoteError, match="note_custom_prompt_required"):
        NoteGenerator(task_id=str(uuid4())).generate(
            source,
            profile(),
            ScriptedClient(lambda request, _: response(valid_content(request))),
            template="custom",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(),
        )
    with pytest.raises(NoteError, match="note_custom_prompt_forbidden"):
        NoteGenerator(task_id=str(uuid4())).generate(
            source,
            profile(),
            ScriptedClient(lambda request, _: response(valid_content(request))),
            template="summary",
            output_language="zh-Hans",
            custom_prompt="private instruction",
            limits=AiLimits(),
        )

    prompt = "PRIVATE-CUSTOM-INSTRUCTION"
    client = ScriptedClient(lambda request, _: response(valid_content(request)))
    note = NoteGenerator(task_id=str(uuid4())).generate(
        source,
        profile(),
        client,
        template="custom",
        output_language="zh-Hans",
        custom_prompt=prompt,
        limits=AiLimits(),
    )

    assert prompt not in client.calls[0].messages[0].content
    assert request_data(client.calls[0])["custom_instruction"] == prompt
    assert prompt not in repr(client.calls[0])
    assert prompt not in repr(note)
    assert prompt not in note.to_markdown(source)


def test_map_chunks_respect_48k_source_and_complete_64k_request_budgets() -> None:
    source = transcript(20, text_size=4_000)
    client = ScriptedClient(
        lambda request, index: response(valid_content(request, suffix=str(index)))
    )

    note = NoteGenerator(task_id=str(uuid4())).generate(
        source,
        profile(),
        client,
        template="summary",
        output_language="zh-Hans",
        custom_prompt=None,
        limits=AiLimits(),
    )

    map_calls = [call for call in client.calls if request_data(call)["operation"] == "map"]
    assert len(map_calls) > 1
    assert all(
        len(
            json.dumps(
                request_data(call)["cues"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        <= 48 * 1024
        for call in map_calls
    )
    assert all(len(canonical_chat_request_bytes(call)) <= 64 * 1024 for call in client.calls)
    assert note.summary


def test_single_cue_oversize_and_more_than_24_initial_chunks_fail_before_call() -> None:
    client = ScriptedClient(lambda request, _: response(valid_content(request)))
    generator = NoteGenerator(task_id=str(uuid4()))
    with pytest.raises(NoteError, match="note_cue_oversize"):
        generator.generate(
            transcript(1, text_size=49 * 1024),
            profile(),
            client,
            template="summary",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(),
        )
    assert client.calls == []

    with pytest.raises(NoteError, match="note_chunk_limit_exceeded"):
        generator.generate(
            transcript(25, text_size=45 * 1024),
            profile(),
            client,
            template="summary",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(),
        )
    assert client.calls == []


def test_reduce_order_is_deterministic_and_citation_lineage_is_preserved() -> None:
    source = transcript(14, text_size=8_000)
    client = ScriptedClient(
        lambda request, index: response(valid_content(request, suffix=str(index)))
    )

    note = NoteGenerator(task_id=str(uuid4())).generate(
        source,
        profile(),
        client,
        template="key_points",
        output_language="zh-Hans",
        custom_prompt=None,
        limits=AiLimits(),
    )

    reduce_calls = [
        call for call in client.calls if request_data(call)["operation"] == "reduce"
    ]
    assert reduce_calls
    first_reduce_nodes = request_data(reduce_calls[0])["nodes"]
    cited_ids = [
        node["summary_citations"][0]["cue_id"]
        for node in first_reduce_nodes
    ]
    assert cited_ids == sorted(cited_ids)
    note.validate_against(source)


def test_reduce_depth_limit_is_enforced() -> None:
    source = transcript(32, text_size=10_000)

    def large_result(request: ChatRequest, index: int) -> ChatResponse:
        citation = first_citation(request_data(request))
        return response(
            json.dumps(
                {
                    "title": "title",
                    "summary": ("s" * 20_000) + str(index),
                    "summary_citations": [citation],
                    "key_points": [
                        {"text": "point", "citations": [citation]}
                    ],
                }
            )
        )

    client = ScriptedClient(large_result)
    with pytest.raises(NoteError, match="note_reduce_depth_exceeded"):
        NoteGenerator(task_id=str(uuid4())).generate(
            source,
            profile(),
            client,
            template="summary",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(note_max_reduce_levels=1),
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: {**payload, "unknown": True},
        lambda payload: {**payload, "summary_citations": []},
        lambda payload: {
            **payload,
            "summary_citations": [
                {
                    "cue_id": "seg_999999",
                    "start_ms": 0,
                    "end_ms": 1_000,
                }
            ],
        },
        lambda payload: {
            **payload,
            "summary_citations": [
                {
                    "cue_id": "seg_000001",
                    "start_ms": 1,
                    "end_ms": 1_000,
                }
            ],
        },
        lambda payload: {
            **payload,
            "key_points": [{"text": "point", "citations": []}],
        },
    ],
)
def test_missing_mismatched_unrelated_or_unknown_citation_data_is_rejected(
    mutator: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    def invalid(request: ChatRequest, _: int) -> ChatResponse:
        payload = json.loads(valid_content(request))
        return response(json.dumps(mutator(payload)))

    client = ScriptedClient(invalid)
    with pytest.raises(NoteError, match="note_response_invalid"):
        NoteGenerator(task_id=str(uuid4())).generate(
            transcript(1),
            profile(),
            client,
            template="summary",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(),
        )
    assert len(client.calls) == 1


def test_reduce_cannot_invent_a_citation_outside_child_lineage() -> None:
    source = transcript(14, text_size=8_000)

    def handler(request: ChatRequest, _: int) -> ChatResponse:
        data = request_data(request)
        if data["operation"] == "map":
            return response(valid_content(request))
        invented = {
            "cue_id": source.segments[-1].id,
            "start_ms": source.segments[-1].start_ms,
            "end_ms": source.segments[-1].end_ms,
        }
        return response(
            json.dumps(
                {
                    "title": "title",
                    "summary": "summary",
                    "summary_citations": [invented],
                    "key_points": [
                        {"text": "point", "citations": [invented]}
                    ],
                }
            )
        )

    with pytest.raises(NoteError, match="note_response_invalid"):
        NoteGenerator(task_id=str(uuid4())).generate(
            source,
            profile(),
            ScriptedClient(handler),
            template="summary",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(),
        )


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
def test_chat_contract_errors_are_not_replayed(code: str) -> None:
    def fail(_request: ChatRequest, _index: int) -> ChatResponse:
        raise ChatError(
            code,
            submission_unknown=(code == "chat_submission_unknown"),
        )

    client = ScriptedClient(fail)
    with pytest.raises(ChatError, match=code):
        NoteGenerator(task_id=str(uuid4())).generate(
            transcript(1),
            profile(),
            client,
            template="summary",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(),
        )
    assert len(client.calls) == 1


def test_oversize_response_is_rejected() -> None:
    client = ScriptedClient(
        lambda _request, _index: response("x" * (256 * 1024 + 1))
    )
    with pytest.raises(NoteError, match="note_response_oversize"):
        NoteGenerator(task_id=str(uuid4())).generate(
            transcript(1),
            profile(),
            client,
            template="summary",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(),
        )


def test_cancellation_discards_remote_result_and_publishes_nothing(
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
    note_id = str(uuid4())
    client = ScriptedClient(handler)
    generator = NoteGenerator(
        task_id=str(uuid4()),
        cancel_check=lambda: canceled,
    )

    with pytest.raises(NoteCanceled):
        generator.generate_and_write(
            transcript(1),
            profile(),
            client,
            template="summary",
            output_language="zh-Hans",
            custom_prompt=None,
            limits=AiLimits(),
            paths=paths,
            item_id=item_id,
            note_id=note_id,
        )

    assert not paths.note(item_id, note_id).exists()


def test_markdown_contains_stable_citations_provenance_and_review_warning(
    tmp_path: Path,
) -> None:
    source = transcript(2)
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    item_id = str(uuid4())
    note_id = str(uuid4())
    task_id = str(uuid4())
    client = ScriptedClient(lambda request, _: response(valid_content(request)))

    note = NoteGenerator(task_id=task_id).generate_and_write(
        source,
        profile(),
        client,
        template="summary",
        output_language="zh-Hans",
        custom_prompt=None,
        limits=AiLimits(),
        paths=paths,
        item_id=item_id,
        note_id=note_id,
    )

    markdown = paths.note(item_id, note_id).read_text("utf-8")
    assert "generated_by_ai: true" in markdown
    assert task_id in markdown
    assert transcript_sha256(source) in markdown
    assert "requested_model: qwen-plus" in markdown
    assert "response_model: qwen-plus-actual" in markdown
    assert "seg_000001" in markdown
    assert "00:00.000" in markdown
    assert "请核对人名、数字、术语和引用" in markdown
    assert note.to_markdown(source) == markdown


def test_document_validation_rejects_a_different_transcript() -> None:
    source = transcript(1)
    client = ScriptedClient(lambda request, _: response(valid_content(request)))
    note = NoteGenerator(task_id=str(uuid4())).generate(
        source,
        profile(),
        client,
        template="summary",
        output_language="zh-Hans",
        custom_prompt=None,
        limits=AiLimits(),
    )
    with pytest.raises(ValueError, match="hash"):
        note.validate_against(transcript(2))
