"""Reusable source probing for single links and partial-success batches."""

from __future__ import annotations

from typing import Any

from vtnote.sources import (
    REMOTE_SOURCE_KINDS,
    PlatformSourceError,
    SourceAdapter,
    SourceCapabilityError,
    ThumbnailOutcome,
)
from vtnote.url_security import (
    SourceUrlPolicy,
    UnsafeSourceUrl,
    extract_supported_source_url,
    extract_supported_source_urls,
)


class SourceProbeService:
    def __init__(self, policy: SourceUrlPolicy, adapter: SourceAdapter) -> None:
        self.policy = policy
        self.adapter = adapter

    def probe(self, source_text: str) -> dict[str, Any]:
        submitted_source = extract_supported_source_url(source_text)
        canonical_source = self.policy.validate(submitted_source)
        collection_probe = getattr(self.adapter, "probe_collection", None)
        collection = collection_probe(canonical_source) if callable(collection_probe) else None
        if collection is not None:
            self.policy.validate(collection.canonical_url)
            for item in collection.items:
                self.policy.validate(item.canonical_url)
            return {
                "result_type": "collection",
                "source_kind": collection.source_kind,
                "canonical_url": collection.canonical_url,
                "title": collection.title,
                "duration_ms": None,
                "subtitle_tracks": [],
                "collection": {
                    "id": collection.id,
                    "title": collection.title,
                    "total_items": collection.total_items,
                    "truncated": collection.truncated,
                    "items": [
                        {
                            "id": item.id,
                            "canonical_url": item.canonical_url,
                            "title": item.title,
                            "duration_ms": item.duration_ms,
                        }
                        for item in collection.items
                    ],
                },
            }
        result = self.adapter.probe(canonical_source)
        if result.source_kind not in REMOTE_SOURCE_KINDS or result.canonical_url is None:
            raise ValueError("URL probe returned a non-remote source")
        redirect_targets = list(result.redirect_trace)
        if not redirect_targets or redirect_targets[-1] != result.canonical_url:
            redirect_targets.append(result.canonical_url)
        self.policy.validate_redirect_chain(submitted_source, redirect_targets)
        response: dict[str, Any] = {
            "result_type": "single",
            "source_kind": result.source_kind,
            "canonical_url": result.canonical_url,
            "title": result.title,
            "duration_ms": result.duration_ms,
            "subtitle_tracks": [
                {
                    "id": track.id,
                    "language": track.language,
                    "format": track.format,
                    "kind": track.kind,
                    "ui_label": track.ui_label,
                    "is_translated": track.is_translated,
                    "is_live_chat": track.is_live_chat,
                }
                for track in result.subtitle_tracks
            ],
        }
        if result.author is not None:
            response["author"] = result.author
        if result.published_at is not None:
            response["published_at"] = result.published_at
        if result.thumbnail_url is not None:
            response["thumbnail_url"] = result.thumbnail_url
        if result.description is not None:
            response["description"] = result.description
        return response

    def fetch_thumbnail(self, source_text: str) -> ThumbnailOutcome:
        submitted_source = extract_supported_source_url(source_text)
        canonical_source = self.policy.validate(submitted_source)
        fetch_thumbnail = getattr(self.adapter, "fetch_thumbnail", None)
        if not callable(fetch_thumbnail):
            raise SourceCapabilityError("adapter_unavailable")
        return fetch_thumbnail(canonical_source)

    def probe_batch(self, source_text: str) -> dict[str, Any]:
        candidates = extract_supported_source_urls(source_text)
        seen: dict[str, int] = {}
        results: list[dict[str, Any]] = []
        valid_sources: list[dict[str, str]] = []
        for index, candidate in enumerate(candidates):
            if candidate in seen:
                results.append(
                    {
                        "input_url": candidate,
                        "status": "duplicate",
                        "duplicate_of": seen[candidate],
                    }
                )
                continue
            seen[candidate] = index
            try:
                probe = self.probe(candidate)
                if probe["result_type"] != "single":
                    results.append(
                        {
                            "input_url": candidate,
                            "canonical_url": probe["canonical_url"],
                            "title": probe["title"],
                            "source_kind": probe["source_kind"],
                            "status": "collection_requires_separate_import",
                        }
                    )
                    continue
                source = {
                    "kind": str(probe["source_kind"]),
                    "url": str(probe["canonical_url"]),
                }
                valid_sources.append(source)
                results.append(
                    {
                        "input_url": candidate,
                        "canonical_url": probe["canonical_url"],
                        "title": probe["title"],
                        "source_kind": probe["source_kind"],
                        "status": "ready",
                    }
                )
            except (UnsafeSourceUrl, PlatformSourceError, SourceCapabilityError) as error:
                results.append(
                    {
                        "input_url": candidate,
                        "status": "failed",
                        "error_code": getattr(error, "code", "unsafe_source_url"),
                    }
                )
            except ValueError:
                results.append(
                    {
                        "input_url": candidate,
                        "status": "failed",
                        "error_code": "invalid_source",
                    }
                )
        return {"results": results, "valid_sources": valid_sources}
