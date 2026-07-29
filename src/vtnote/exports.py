"""On-demand export views regenerated from canonical JSON artifacts."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Literal

from vtnote.schemas import (
    Transcript,
    TranscriptSegment,
    Translation,
    canonical_transcript_bytes,
)
from vtnote.subtitles import export_markdown, export_srt, export_txt, export_vtt


class ExportFormat(str, Enum):
    JSON = "json"
    SRT = "srt"
    VTT = "vtt"
    TXT = "txt"
    MARKDOWN = "markdown"


def _translated_segments(
    transcript: Transcript, translation: Translation | None
) -> tuple[TranscriptSegment, ...]:
    if translation is None:
        return transcript.segments
    translation.validate_against(transcript)
    return tuple(
        segment.model_copy(update={"text": entry.text})
        for segment, entry in zip(transcript.segments, translation.entries, strict=True)
    )


def render_export(
    transcript: Transcript,
    export_format: ExportFormat | str,
    translation: Translation | None = None,
) -> str:
    """Render an export without creating a durable export file."""

    selected = ExportFormat(export_format)
    segments = _translated_segments(transcript, translation)
    if selected is ExportFormat.JSON:
        if translation is None:
            return canonical_transcript_bytes(transcript).decode("utf-8")
        payload = {
            "schema_version": 1,
            "language": translation.language,
            "duration_ms": transcript.duration_ms,
            "source_transcript_sha256": translation.source_transcript_sha256,
            "segments": [segment.model_dump(mode="json") for segment in segments],
        }
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) + "\n"
    if selected is ExportFormat.SRT:
        return export_srt(segments)
    if selected is ExportFormat.VTT:
        return export_vtt(segments)
    if selected is ExportFormat.TXT:
        return export_txt(segments)
    return export_markdown(segments)


def render_export_from_json(
    transcript_json: bytes | str,
    export_format: ExportFormat | str,
    translation_json: bytes | str | None = None,
) -> str:
    transcript = Transcript.model_validate_json(transcript_json)
    translation = (
        Translation.model_validate_json(translation_json)
        if translation_json is not None
        else None
    )
    return render_export(transcript, export_format, translation)


def render_execution_summary(
    payload: dict[str, Any],
    export_format: Literal["json", "markdown"] | str,
) -> str:
    """Render the sanitized, deterministic execution summary."""

    selected = str(export_format)
    if selected == "json":
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
    if selected != "markdown":
        raise ValueError("unsupported execution summary format")

    lines = [
        "# 执行摘要",
        "",
        f"- 任务状态：{payload['task_status']}",
        f"- 条目状态：{payload['item_status']}",
        f"- 来源类型：{payload['source_kind']}",
    ]
    if payload.get("title"):
        lines.append(f"- 标题：{payload['title']}")
    lines.extend(("", "## 处理阶段", ""))
    for stage in payload["stages"]:
        lines.append(
            f"### {stage['stage']} · 第 {stage['attempt']} 次 · {stage['status']}"
        )
        if stage.get("warning"):
            lines.append(f"- 警告：{stage['warning']}")
        if stage.get("error_code"):
            lines.append(f"- 错误代码：{stage['error_code']}")
        evidence = stage.get("execution_evidence")
        if evidence:
            details = "，".join(
                f"{key}={value}" for key, value in sorted(evidence.items())
            )
            lines.append(f"- 执行依据：{details}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
