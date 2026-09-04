"""Shared HTTP routes for pinned local model assets."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI

from vtnote.http.contracts import ModelInstallInput
from vtnote.http.responses import error_response
from vtnote.model_assets import (
    ModelAssetError,
    ModelAssetService,
    ModelInstallStatus,
)


def _status_payload(status: ModelInstallStatus) -> dict[str, object]:
    return {
        "model_name": status.model_name,
        "revision": status.revision,
        "state": status.state,
        "total_bytes": status.total_bytes,
        "downloaded_bytes": status.downloaded_bytes,
        "completed_files": status.completed_files,
        "current_file": status.current_file,
        "current_file_bytes": status.current_file_bytes,
        "cancel_requested": status.cancel_requested,
        "error_code": status.error_code,
    }


def _register_asset(
    app: FastAPI,
    *,
    slug: str,
    service: ModelAssetService,
) -> None:
    operation_slug = slug.replace("-", "_")

    def get_asset():
        return _status_payload(service.status())

    def install_asset(payload: ModelInstallInput):
        try:
            status = service.request_install(
                acknowledge_download=payload.acknowledge_download,
                expected_revision=payload.expected_revision,
                now=datetime.now(timezone.utc),
            )
        except ModelAssetError as error:
            return error_response(
                400,
                error.code,
                "model installation request rejected",
            )
        return _status_payload(status)

    def cancel_asset():
        try:
            status = service.cancel(now=datetime.now(timezone.utc))
        except ModelAssetError as error:
            return error_response(
                400,
                error.code,
                "model installation cancel rejected",
            )
        return _status_payload(status)

    app.add_api_route(
        f"/api/assets/{slug}",
        get_asset,
        methods=["GET"],
        name=f"get_{operation_slug}_asset",
    )
    app.add_api_route(
        f"/api/assets/{slug}/install",
        install_asset,
        methods=["POST"],
        status_code=202,
        name=f"install_{operation_slug}_asset",
    )
    app.add_api_route(
        f"/api/assets/{slug}/cancel",
        cancel_asset,
        methods=["POST"],
        name=f"cancel_{operation_slug}_asset",
    )


def register_model_asset_routes(
    app: FastAPI,
    *,
    local_whisper: ModelAssetService,
    sensevoice: ModelAssetService,
    silero_vad: ModelAssetService,
) -> None:
    for slug, service in (
        ("local-whisper", local_whisper),
        ("sensevoice", sensevoice),
        ("silero-vad", silero_vad),
    ):
        _register_asset(app, slug=slug, service=service)
