"""Strict text subtitle parsers and deterministic exporters."""

from __future__ import annotations

import re
from collections.abc import Iterable

from vtnote.schemas import TranscriptSegment


class SubtitleParseError(ValueError):
    """Raised when a subtitle document cannot be represented safely."""


_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})$")
_ASS_CLOCK_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{1,3})$")
_ASS_OVERRIDE_RE = re.compile(r"\{\\[^}]*\}")


def _milliseconds(hours: str | None, minutes: str, seconds: str, fraction: str) -> int:
    if int(minutes) >= 60 or int(seconds) >= 60:
        raise SubtitleParseError("timestamp minute and second fields must be below 60")
    milliseconds = int((fraction + "000")[:3])
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + milliseconds


def _parse_clock(value: str) -> int:
    match = _CLOCK_RE.fullmatch(value.strip())
    if match is None:
        raise SubtitleParseError(f"invalid SRT/VTT timestamp: {value!r}")
    return _milliseconds(*match.groups())


def _parse_ass_clock(value: str) -> int:
    match = _ASS_CLOCK_RE.fullmatch(value.strip())
    if match is None:
        raise SubtitleParseError(f"invalid ASS timestamp: {value!r}")
    return _milliseconds(*match.groups())


def _timing(line: str) -> tuple[int, int]:
    parts = re.split(r"\s+-->\s+", line.strip(), maxsplit=1)
    if len(parts) != 2:
        raise SubtitleParseError(f"invalid cue timing line: {line!r}")
    start_ms = _parse_clock(parts[0])
    end_ms = _parse_clock(parts[1].split(maxsplit=1)[0])
    if end_ms <= start_ms:
        raise SubtitleParseError("subtitle cue end must be after its start")
    return start_ms, end_ms


def _segment(index: int, start_ms: int, end_ms: int, text: str, speaker: str | None = None) -> TranscriptSegment:
    try:
        return TranscriptSegment(
            id=f"segment-{index:06d}",
            start_ms=start_ms,
            end_ms=end_ms,
            text=text,
            speaker=speaker,
        )
    except ValueError as error:
        raise SubtitleParseError(str(error)) from error


def parse_srt(source: str) -> list[TranscriptSegment]:
    normalized = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    segments: list[TranscriptSegment] = []
    for block in re.split(r"\n[ \t]*\n", normalized):
        lines = block.splitlines()
        timing_index = 0 if "-->" in lines[0] else 1
        if len(lines) <= timing_index + 1 or "-->" not in lines[timing_index]:
            raise SubtitleParseError("SRT cue is missing timing or text")
        start_ms, end_ms = _timing(lines[timing_index])
        text = "\n".join(lines[timing_index + 1 :]).strip()
        segments.append(_segment(len(segments) + 1, start_ms, end_ms, text))
    return segments


def parse_vtt(source: str) -> list[TranscriptSegment]:
    normalized = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n[ \t]*\n", normalized) if normalized else []
    if not blocks or not blocks[0].startswith("WEBVTT"):
        raise SubtitleParseError("WebVTT document must start with WEBVTT")

    segments: list[TranscriptSegment] = []
    for block in blocks[1:]:
        lines = block.splitlines()
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = 0 if "-->" in lines[0] else 1
        if len(lines) <= timing_index + 1 or "-->" not in lines[timing_index]:
            raise SubtitleParseError("WebVTT cue is missing timing or text")
        start_ms, end_ms = _timing(lines[timing_index])
        text = "\n".join(lines[timing_index + 1 :]).strip()
        segments.append(_segment(len(segments) + 1, start_ms, end_ms, text))
    return segments


def parse_ass(source: str) -> list[TranscriptSegment]:
    event_format: list[str] | None = None
    in_events = False
    segments: list[TranscriptSegment] = []

    for raw_line in source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_events = line.casefold() == "[events]"
            continue
        if not in_events:
            continue
        if line.casefold().startswith("format:"):
            event_format = [field.strip().casefold() for field in line.split(":", 1)[1].split(",")]
            continue
        if not line.casefold().startswith("dialogue:"):
            continue
        if event_format is None:
            raise SubtitleParseError("ASS Dialogue appears before the Events Format")

        values = [value.strip() for value in line.split(":", 1)[1].split(",", len(event_format) - 1)]
        if len(values) != len(event_format):
            raise SubtitleParseError("ASS Dialogue does not match the Events Format")
        event = dict(zip(event_format, values, strict=True))
        try:
            start_ms = _parse_ass_clock(event["start"])
            end_ms = _parse_ass_clock(event["end"])
            raw_text = event["text"]
        except KeyError as error:
            raise SubtitleParseError(f"ASS Events Format lacks {error.args[0]!r}") from error
        if end_ms <= start_ms:
            raise SubtitleParseError("subtitle cue end must be after its start")
        text = _ASS_OVERRIDE_RE.sub("", raw_text)
        text = text.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ").strip()
        speaker = event.get("name") or None
        segments.append(_segment(len(segments) + 1, start_ms, end_ms, text, speaker))
    return segments


def _format_clock(milliseconds: int, separator: str = ".") -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def _format_ass_clock(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def export_srt(segments: Iterable[TranscriptSegment]) -> str:
    blocks = [
        f"{index}\n{_format_clock(segment.start_ms, ',')} --> {_format_clock(segment.end_ms, ',')}\n{segment.text}"
        for index, segment in enumerate(segments, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_vtt(segments: Iterable[TranscriptSegment]) -> str:
    blocks = [
        f"{_format_clock(segment.start_ms)} --> {_format_clock(segment.end_ms)}\n{segment.text}"
        for segment in segments
    ]
    body = "\n\n".join(blocks)
    return "WEBVTT\n" + (f"\n{body}\n" if body else "")


def export_ass(segments: Iterable[TranscriptSegment]) -> str:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for segment in segments:
        text = segment.text.replace("\n", r"\N")
        events.append(
            "Dialogue: 0,"
            f"{_format_ass_clock(segment.start_ms)},{_format_ass_clock(segment.end_ms)},"
            f"Default,{segment.speaker or ''},0,0,0,,{text}"
        )
    return header + "\n".join(events) + ("\n" if events else "")


def export_txt(segments: Iterable[TranscriptSegment]) -> str:
    texts = [segment.text for segment in segments]
    return "\n".join(texts) + ("\n" if texts else "")


def export_markdown(segments: Iterable[TranscriptSegment]) -> str:
    lines = [
        f"- **{_format_clock(segment.start_ms)} → {_format_clock(segment.end_ms)}** {segment.text}"
        for segment in segments
    ]
    return "\n".join(lines) + ("\n" if lines else "")
