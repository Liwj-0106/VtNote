"""Bounded Bilibili collection discovery over the controlled transport."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import cast
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from vtnote.platform_transport import (
    PinnedHttpsTransport,
    SourceHttpRequest,
    TransportSecurityError,
)
from vtnote.sources import (
    PlatformSourceError,
    SourceCollectionItem,
    SourceCollectionProbeResult,
)
from vtnote.url_security import extractor_aux_host_policy
from vtnote.ytdlp_bridge import controlled_public_headers


MAX_COLLECTION_ITEMS = 500
_MAX_METADATA_BYTES = 8 * 1024 * 1024


def _status_error(status: int) -> str | None:
    if status in {401, 403}:
        return "auth_required"
    if status in {404, 410}:
        return "removed"
    if status == 429 or 500 <= status <= 599:
        return "temporary"
    if status < 200 or status >= 300:
        return "invalid_content"
    return None


def request_bilibili_json(
    transport: PinnedHttpsTransport,
    url: str,
    *,
    referer: str,
) -> dict[str, object]:
    """Read one reviewed public Bilibili API response over the pinned transport."""

    for attempt in range(2):
        response = None
        try:
            response = transport.request(
                SourceHttpRequest(
                    url=url,
                    headers=controlled_public_headers(referer=referer),
                    max_wire_bytes=_MAX_METADATA_BYTES,
                    max_decoded_bytes=_MAX_METADATA_BYTES,
                ),
                extractor_aux_host_policy("bilibili"),
            )
            error_code = _status_error(response.status)
            if error_code is not None:
                if error_code == "temporary" and attempt == 0:
                    continue
                raise PlatformSourceError(error_code)
            content = response.read()
            if not isinstance(content, bytes) or not content:
                raise PlatformSourceError("invalid_content")
            payload = json.loads(content.decode("utf-8"))
        except PlatformSourceError:
            raise
        except TransportSecurityError as error:
            if error.category in {"connection_failed", "read_failed"}:
                if attempt == 0:
                    continue
                raise PlatformSourceError("temporary") from None
            raise PlatformSourceError("adapter_drift") from None
        except (OSError, UnicodeError, ValueError):
            raise PlatformSourceError("invalid_content") from None
        finally:
            if response is not None:
                response.close()
        if not isinstance(payload, dict):
            raise PlatformSourceError("invalid_content")
        result = cast(dict[str, object], payload)
        code = result.get("code")
        if code == 0:
            return result
        if code in {-101, -400, -403}:
            raise PlatformSourceError("auth_required")
        if code in {-404, 62002, 62004}:
            raise PlatformSourceError("removed")
        if code in {-352, -412}:
            raise PlatformSourceError("temporary")
        raise PlatformSourceError("invalid_content")
    raise PlatformSourceError("temporary")


@dataclass(frozen=True, slots=True)
class _CollectionLocator:
    mid: str
    sid: str
    kind: str
    canonical_url: str


def _one_numeric_query(query: str, name: str) -> str | None:
    values = parse_qs(query, keep_blank_values=True).get(name, ())
    if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 20:
        return None
    return values[0]


def parse_bilibili_collection_url(source: str) -> _CollectionLocator | None:
    """Recognize only reviewed collection/list URL shapes and drop tracking data."""

    try:
        parts = urlsplit(source)
        port = parts.port
    except (TypeError, ValueError):
        return None
    if (
        parts.scheme.casefold() != "https"
        or (parts.hostname or "").casefold().rstrip(".") != "space.bilibili.com"
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        return None
    components = [part for part in parts.path.split("/") if part]
    if not components or not components[0].isdigit() or len(components[0]) > 20:
        return None
    mid = components[0]
    sid: str | None = None
    kind: str | None = None
    if len(components) == 3 and components[1] == "lists":
        if components[2].isdigit() and len(components[2]) <= 20:
            sid = components[2]
            type_values = parse_qs(parts.query, keep_blank_values=True).get("type", ())
            if not type_values:
                kind = "season"
            elif len(type_values) == 1 and type_values[0] in {"season", "series"}:
                kind = type_values[0]
    elif len(components) == 3 and components[1] == "channel":
        route_kind = {
            "collectiondetail": "season",
            "seriesdetail": "series",
        }.get(components[2].casefold())
        if route_kind is not None:
            sid = _one_numeric_query(parts.query, "sid")
            kind = route_kind
    if sid is None or kind is None:
        return None
    canonical_url = urlunsplit(
        (
            "https",
            "space.bilibili.com",
            f"/{mid}/lists/{sid}",
            urlencode({"type": kind}),
            "",
        )
    )
    return _CollectionLocator(mid=mid, sid=sid, kind=kind, canonical_url=canonical_url)


def _bounded_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized and len(normalized) <= 4_096 else None


def _duration_ms(value: object) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise PlatformSourceError("adapter_drift")
    return round(value * 1_000)


def _dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


class BilibiliCollectionAdapter:
    """Enumerate public Bilibili seasons and series without media extraction."""

    def __init__(self, transport: PinnedHttpsTransport) -> None:
        self.transport = transport

    def _get_json(self, url: str, *, referer: str) -> dict[str, object]:
        return request_bilibili_json(self.transport, url, referer=referer)

    @staticmethod
    def _page_values(data: dict[str, object]) -> tuple[int, int]:
        page = _dict(data.get("page"))
        total = page.get("total")
        page_size = page.get("page_size", page.get("size"))
        if (
            isinstance(total, bool)
            or not isinstance(total, int)
            or total < 0
            or isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size <= 0
        ):
            raise PlatformSourceError("adapter_drift")
        return total, page_size

    @staticmethod
    def _items(raw_items: object) -> list[SourceCollectionItem]:
        if not isinstance(raw_items, list):
            raise PlatformSourceError("adapter_drift")
        items: list[SourceCollectionItem] = []
        for raw_item in raw_items:
            item = _dict(raw_item)
            bvid = item.get("bvid")
            title = _bounded_title(item.get("title"))
            if (
                not isinstance(bvid, str)
                or not bvid.startswith("BV")
                or not bvid[2:].isalnum()
                or len(bvid) > 64
                or title is None
            ):
                raise PlatformSourceError("adapter_drift")
            items.append(
                SourceCollectionItem(
                    id=bvid,
                    canonical_url=f"https://www.bilibili.com/video/{bvid}",
                    title=title,
                    duration_ms=_duration_ms(item.get("duration")),
                )
            )
        return items

    def _page(self, locator: _CollectionLocator, page_number: int) -> dict[str, object]:
        if locator.kind == "season":
            endpoint = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
            query = {
                "mid": locator.mid,
                "season_id": locator.sid,
                "page_num": page_number,
                "page_size": 30,
            }
        else:
            endpoint = "https://api.bilibili.com/x/series/archives"
            query = {
                "mid": locator.mid,
                "series_id": locator.sid,
                "only_normal": "true",
                "sort": "desc",
                "pn": page_number,
                "ps": 30,
            }
        payload = self._get_json(
            f"{endpoint}?{urlencode(query)}",
            referer=locator.canonical_url,
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlatformSourceError("invalid_content")
        return cast(dict[str, object], data)

    def _series_title(self, locator: _CollectionLocator) -> str:
        payload = self._get_json(
            "https://api.bilibili.com/x/series/series?"
            + urlencode({"series_id": locator.sid}),
            referer=locator.canonical_url,
        )
        data = _dict(payload.get("data"))
        title = _bounded_title(_dict(data.get("meta")).get("name"))
        if title is None:
            raise PlatformSourceError("adapter_drift")
        return title

    def probe_collection(
        self,
        canonical_source: str,
    ) -> SourceCollectionProbeResult | None:
        locator = parse_bilibili_collection_url(canonical_source)
        if locator is None:
            return None
        items: list[SourceCollectionItem] = []
        seen: set[str] = set()
        total_items: int | None = None
        title = self._series_title(locator) if locator.kind == "series" else None
        page_number = 1
        while len(items) < MAX_COLLECTION_ITEMS:
            data = self._page(locator, page_number)
            page_total, page_size = self._page_values(data)
            if page_total < len(items):
                raise PlatformSourceError("adapter_drift")
            if total_items is None:
                total_items = page_total
            elif total_items != page_total:
                raise PlatformSourceError("adapter_drift")
            if title is None:
                title = _bounded_title(_dict(data.get("meta")).get("name"))
            raw_items = data.get("archives")
            page_items = self._items(raw_items)
            if not page_items and len(items) < page_total:
                raise PlatformSourceError("adapter_drift")
            for item in page_items:
                if item.id in seen:
                    continue
                seen.add(item.id)
                items.append(item)
                if len(items) == MAX_COLLECTION_ITEMS:
                    break
            if len(items) >= page_total or not page_items:
                break
            page_number += 1
            if page_number > (page_total + page_size - 1) // page_size:
                break
        if title is None or total_items is None or not items:
            raise PlatformSourceError("invalid_content")
        return SourceCollectionProbeResult(
            source_kind="bilibili",
            id=f"bilibili:{locator.kind}:{locator.mid}:{locator.sid}",
            canonical_url=locator.canonical_url,
            title=title,
            items=tuple(items),
            total_items=total_items,
            truncated=total_items > len(items),
        )
