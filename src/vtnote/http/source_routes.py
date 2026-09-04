"""Source probing routes kept outside the API composition root."""

from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from vtnote.source_probing import SourceProbeService


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceTextInput(_StrictModel):
    text: str = Field(min_length=1, max_length=65_536)


def register_source_routes(app: FastAPI, *, service: SourceProbeService) -> None:
    @app.get("/api/sources/thumbnail")
    def fetch_source_thumbnail(
        source_url: str = Query(alias="url", min_length=1, max_length=8_192),
    ):
        thumbnail = service.fetch_thumbnail(source_url)
        return Response(
            content=thumbnail.content,
            media_type=thumbnail.media_type,
            headers={
                "Cache-Control": "private, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/sources/probe-batch")
    def probe_source_batch(payload: SourceTextInput):
        return service.probe_batch(payload.text)
