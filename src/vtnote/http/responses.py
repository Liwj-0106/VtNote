"""Consistent serialization helpers for the local HTTP boundary."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from vtnote.sources import SubtitleTrack


def error_response(
    status: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def dump_model(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def dump_subtitle(track: SubtitleTrack) -> dict[str, Any]:
    return {
        "id": track.id,
        "language": track.language,
        "format": track.format,
        "kind": track.kind,
        "ui_label": track.ui_label,
        "is_translated": track.is_translated,
        "is_live_chat": track.is_live_chat,
    }
