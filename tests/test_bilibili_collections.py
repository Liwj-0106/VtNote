from __future__ import annotations

import json
from typing import Any

import pytest

from vtnote.bilibili_collections import (
    BilibiliCollectionAdapter,
    parse_bilibili_collection_url,
)
from vtnote.sources import PlatformSourceError


class FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.status = status
        self.payload = payload
        self.closed = False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[object, object]] = []

    def request(self, request: object, policy: object) -> FakeResponse:
        self.calls.append((request, policy))
        return self.responses.pop(0)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://space.bilibili.com/2142762/lists/3662502?type=season&from=search",
            "https://space.bilibili.com/2142762/lists/3662502?type=season",
        ),
        (
            "https://space.bilibili.com/1958703906/lists/547718?type=series",
            "https://space.bilibili.com/1958703906/lists/547718?type=series",
        ),
        (
            "https://space.bilibili.com/2142762/channel/collectiondetail?sid=57445",
            "https://space.bilibili.com/2142762/lists/57445?type=season",
        ),
        (
            "https://space.bilibili.com/1958703906/channel/seriesdetail?sid=547718&ctype=0",
            "https://space.bilibili.com/1958703906/lists/547718?type=series",
        ),
    ],
)
def test_collection_urls_are_recognized_and_tracking_is_dropped(
    source: str,
    expected: str,
) -> None:
    result = parse_bilibili_collection_url(source)

    assert result is not None
    assert result.canonical_url == expected


@pytest.mark.parametrize(
    "source",
    [
        "http://space.bilibili.com/1/lists/2?type=season",
        "https://space.bilibili.com/1/lists/not-numeric?type=season",
        "https://space.bilibili.com/1/lists/2?type=favorites",
        "https://space.bilibili.com/1/channel/collectiondetail?sid=2&sid=3",
        "https://www.bilibili.com/video/BV1xx411c7mD",
    ],
)
def test_unreviewed_collection_urls_are_not_recognized(source: str) -> None:
    assert parse_bilibili_collection_url(source) is None


def test_season_collection_is_paginated_into_safe_video_urls() -> None:
    first_page = {
        "code": 0,
        "data": {
            "meta": {"name": "课程合集"},
            "page": {"total": 3, "page_size": 2},
            "archives": [
                {"bvid": "BV1111111111", "title": "第一课", "duration": 61},
                {"bvid": "BV2222222222", "title": "第二课", "duration": 62.5},
            ],
        },
    }
    second_page = {
        "code": 0,
        "data": {
            "meta": {"name": "课程合集"},
            "page": {"total": 3, "page_size": 2},
            "archives": [
                {"bvid": "BV3333333333", "title": "第三课", "duration": 63},
            ],
        },
    }
    transport = FakeTransport([FakeResponse(first_page), FakeResponse(second_page)])
    adapter = BilibiliCollectionAdapter(transport)  # type: ignore[arg-type]

    result = adapter.probe_collection(
        "https://space.bilibili.com/2142762/lists/3662502?type=season"
    )

    assert result is not None
    assert result.title == "课程合集"
    assert result.total_items == 3
    assert result.truncated is False
    assert [item.title for item in result.items] == ["第一课", "第二课", "第三课"]
    assert result.items[1].duration_ms == 62_500
    assert result.items[2].canonical_url == (
        "https://www.bilibili.com/video/BV3333333333"
    )
    assert len(transport.calls) == 2


def test_collection_probe_returns_all_130_items() -> None:
    total = 130
    page_size = 30
    responses: list[FakeResponse] = []
    for start in range(0, total, page_size):
        archives = [
            {
                "bvid": f"BV{index:010d}",
                "title": f"Video {index}",
                "duration": 60,
            }
            for index in range(start + 1, min(start + page_size, total) + 1)
        ]
        responses.append(
            FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "meta": {"name": "Weekly collection"},
                        "page": {"total": total, "page_size": page_size},
                        "archives": archives,
                    },
                }
            )
        )
    transport = FakeTransport(responses)
    adapter = BilibiliCollectionAdapter(transport)  # type: ignore[arg-type]

    result = adapter.probe_collection(
        "https://space.bilibili.com/2142762/lists/3662502?type=season"
    )

    assert result is not None
    assert len(result.items) == total
    assert result.total_items == total
    assert result.truncated is False
    assert len(transport.calls) == 5


def test_series_collection_uses_metadata_and_archive_endpoints() -> None:
    metadata = {
        "code": 0,
        "data": {"meta": {"name": "直播回放"}},
    }
    page = {
        "code": 0,
        "data": {
            "page": {"total": 1, "size": 30},
            "archives": [
                {"bvid": "BV4444444444", "title": "第一场", "duration": 100},
            ],
        },
    }
    transport = FakeTransport([FakeResponse(metadata), FakeResponse(page)])
    adapter = BilibiliCollectionAdapter(transport)  # type: ignore[arg-type]

    result = adapter.probe_collection(
        "https://space.bilibili.com/1958703906/lists/547718?type=series"
    )

    assert result is not None
    assert result.title == "直播回放"
    request_urls = [getattr(call[0], "url") for call in transport.calls]
    assert "/x/series/series?" in request_urls[0]
    assert "/x/series/archives?" in request_urls[1]


def test_collection_auth_failure_is_closed_and_actionable() -> None:
    transport = FakeTransport([FakeResponse({}, status=403)])
    adapter = BilibiliCollectionAdapter(transport)  # type: ignore[arg-type]

    with pytest.raises(PlatformSourceError, match="auth_required"):
        adapter.probe_collection(
            "https://space.bilibili.com/2142762/lists/3662502?type=season"
        )
