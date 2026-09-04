from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from yt_dlp.utils import ExtractorError

import vtnote.platform_sources as platform_sources_module
from vtnote.media import MediaInfo
from vtnote.paths import StoragePaths
from vtnote.platform_transport import TransportSecurityError
from vtnote.platform_sources import (
    ControlledYtDlpOperations,
    PlatformSourceRegistry,
    YtDlpOperationFailure,
    YtDlpSourceAdapter,
    classify_extractor_failure,
)
from vtnote.sources import (
    AudioOutcome,
    PlatformSourceError,
    SourceCapabilityError,
    SubtitleCandidateError,
    SubtitleOutcome,
    SubtitleTrackSelector,
    acquire_subtitle_or_audio,
)


VALID_VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"
ITEM_ID = "22222222-2222-4222-8222-222222222222"


YOUTUBE_INFO = {
    "id": "abcDEF12345",
    "extractor_key": "Youtube",
    "webpage_url": (
        "https://www.youtube.com/watch?v=abcDEF12345"
        "&utm_source=tracking&token=private"
    ),
    "title": "YouTube Example",
    "duration": 12.345,
    "uploader": "Example Creator",
    "timestamp": 1_704_067_200,
    "description": "A public video description.",
    "thumbnail": "https://i.ytimg.com/vi/abcDEF12345/hqdefault.jpg",
    "subtitles": {
        "ZH_hans": [
            {
                "ext": "vtt",
                "url": (
                    "https://www.youtube.com/api/timedtext"
                    "?lang=zh&token=subtitle-secret"
                ),
            },
            {
                "ext": "ttml",
                "url": "https://www.youtube.com/api/timedtext?fmt=ttml",
            },
        ],
        "en": [
            {
                "ext": "vtt",
                "url": "https://www.youtube.com/api/timedtext?lang=en",
            }
        ],
        "live_chat": [
            {
                "ext": "json",
                "url": "https://www.youtube.com/live_chat",
            }
        ],
    },
    "automatic_captions": {
        "en": [
            {
                "ext": "vtt",
                "url": "https://www.youtube.com/api/timedtext?lang=en&kind=asr",
            }
        ]
    },
    "formats": [
        {
            "format_id": "video",
            "url": "https://rr1---sn.example.googlevideo.com/video",
            "ext": "mp4",
            "acodec": "none",
            "vcodec": "avc1",
        },
        {
            "format_id": "audio-low",
            "url": "https://rr1---sn.example.googlevideo.com/audio-low",
            "ext": "webm",
            "acodec": "opus",
            "vcodec": "none",
            "abr": 64,
        },
        {
            "format_id": "audio-best",
            "url": (
                "https://rr1---sn.example.googlevideo.com/audio-best"
                "?token=audio-secret"
            ),
            "ext": "m4a",
            "acodec": "mp4a.40.2",
            "vcodec": "none",
            "abr": 128,
        },
    ],
}


BILIBILI_INFO = {
    "id": "BV1xx411c7mD",
    "extractor_key": "BiliBili",
    "webpage_url": (
        "https://www.bilibili.com/video/BV1xx411c7mD"
        "?p=2&spm_id_from=tracking&token=private"
    ),
    "title": "Bilibili Example",
    "duration": 20,
    "thumbnail": "https://i0.hdslb.com/bfs/archive/public-cover.jpg",
    "subtitles": {
        "zh_CN": [
            {
                "ext": "json",
                "url": "https://aisubtitle.hdslb.com/bfs/subtitle/example.json",
            }
        ],
        "en-US": [
            {
                "ext": "srt",
                "url": "https://aisubtitle.hdslb.com/bfs/subtitle/example.srt",
                "kind": "manual",
            }
        ],
    },
    "automatic_captions": {},
    "formats": [],
}

BILIBILI_AV_INFO = {
    **BILIBILI_INFO,
    "id": "170001",
    "webpage_url": "https://www.bilibili.com/video/av170001?p=1",
}

DOUYIN_INFO = {
    "id": "7531234567890123456",
    "extractor_key": "Douyin",
    "webpage_url": (
        "https://www.douyin.com/video/7531234567890123456"
        "?previous_page=app_code_link&token=private"
    ),
    "title": "Douyin Example",
    "duration": 15.5,
    "subtitles": {},
    "automatic_captions": {},
    "formats": [
        {
            "format_id": "video-low",
            "url": "https://v3-dy.example.bytecdn.cn/video-low",
            "ext": "mp4",
            "acodec": "aac",
            "vcodec": "h264",
            "tbr": 800,
        },
        {
            "format_id": "video-best",
            "url": "https://v3-dy.example.bytecdn.cn/video-best",
            "ext": "mp4",
            "acodec": "aac",
            "vcodec": "h264",
            "tbr": 1200,
        },
    ],
}


class FakeOperations:
    def __init__(
        self,
        extracts: list[dict[str, object] | Exception],
        *,
        resources: dict[str, bytes | Exception] | None = None,
        downloaded_content: bytes = b"audio bytes",
    ) -> None:
        self.extracts = extracts
        self.resources = resources or {}
        self.downloaded_content = downloaded_content
        self.extract_calls: list[str] = []
        self.resource_calls: list[str] = []
        self.download_calls: list[tuple[str, Path, int]] = []

    def extract(self, canonical_url: str) -> dict[str, object]:
        self.extract_calls.append(canonical_url)
        result = self.extracts.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)

    def fetch_resource(self, resource_url: str, *, max_bytes: int) -> bytes:
        self.resource_calls.append(resource_url)
        result = self.resources[resource_url]
        if isinstance(result, Exception):
            raise result
        if len(result) > max_bytes:
            raise YtDlpOperationFailure("invalid_content")
        return result

    def download_resource(
        self,
        resource_url: str,
        target: Path,
        *,
        max_bytes: int,
    ) -> None:
        self.download_calls.append((resource_url, target, max_bytes))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.downloaded_content)


class FakeMediaValidator:
    def validate_media(self, path: Path) -> MediaInfo:
        return MediaInfo(
            duration_ms=12_345,
            size_bytes=path.stat().st_size,
            format_name="m4a",
            audio_codec="aac",
            sample_rate=48_000,
            channels=2,
        )


class FakeRegistrar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path]] = []

    def register_downloaded_audio(self, item_id: str, path: Path) -> str:
        self.calls.append((item_id, path))
        return "asset-123"


def paths(tmp_path: Path) -> StoragePaths:
    return StoragePaths(
        data_root=tmp_path / "data",
        runtime_cache_root=tmp_path / "runtime",
    )


def adapter(
    tmp_path: Path,
    *,
    platform: str,
    extracts: list[dict[str, object] | Exception],
    resources: dict[str, bytes | Exception] | None = None,
) -> tuple[YtDlpSourceAdapter, FakeOperations, FakeRegistrar]:
    operations = FakeOperations(extracts, resources=resources)
    registrar = FakeRegistrar()
    return (
        YtDlpSourceAdapter(
            platform=platform,
            operations=operations,
            paths=paths(tmp_path),
            media_validator=FakeMediaValidator(),
            audio_registrar=registrar,
        ),
        operations,
        registrar,
    )


@pytest.mark.parametrize(
    "source",
    [
        "https://youtube.com/watch?v=abcDEF12345&utm_source=x",
        "https://www.youtube.com/watch?v=abcDEF12345&token=secret",
        "https://youtu.be/abcDEF12345?si=tracking",
        "https://youtube.com/shorts/abcDEF12345?feature=share",
        "https://youtube.com/live/abcDEF12345?tracking=1",
    ],
)
def test_youtube_reviewed_forms_normalize_to_safe_canonical_url(
    tmp_path: Path,
    source: str,
) -> None:
    selected, operations, _ = adapter(
        tmp_path,
        platform="youtube",
        extracts=[YOUTUBE_INFO],
    )

    probe = selected.probe(source)

    assert probe.source_kind == "youtube"
    assert probe.canonical_url == (
        "https://www.youtube.com/watch?v=abcDEF12345"
    )
    assert operations.extract_calls == [
        "https://www.youtube.com/watch?v=abcDEF12345"
    ]
    assert "token" not in repr(probe)


@pytest.mark.parametrize(
    ("source", "expected", "info"),
    [
        (
            "https://www.bilibili.com/video/BV1xx411c7mD"
            "?p=2&spm_id_from=x",
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            BILIBILI_INFO,
        ),
        (
            "https://www.bilibili.com/video/av170001?p=1&token=secret",
            "https://www.bilibili.com/video/av170001?p=1",
            BILIBILI_AV_INFO,
        ),
        (
            "https://b23.tv/short-code",
            "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            BILIBILI_INFO,
        ),
    ],
)
def test_bilibili_reviewed_forms_keep_only_numeric_page(
    tmp_path: Path,
    source: str,
    expected: str,
    info: dict[str, object],
) -> None:
    selected, _, _ = adapter(
        tmp_path,
        platform="bilibili",
        extracts=[info],
    )

    assert selected.probe(source).canonical_url == expected


@pytest.mark.parametrize(
    ("source", "expected_extract"),
    [
        (
            "https://www.douyin.com/video/7531234567890123456"
            "?previous_page=app_code_link&token=private",
            "https://www.douyin.com/video/7531234567890123456",
        ),
        (
            "https://douyin.com/video/7531234567890123456",
            "https://www.douyin.com/video/7531234567890123456",
        ),
        (
            "https://v.douyin.com/AbC_123/?share_token=private",
            "https://v.douyin.com/AbC_123/",
        ),
    ],
)
def test_douyin_reviewed_forms_normalize_and_drop_tracking(
    tmp_path: Path,
    source: str,
    expected_extract: str,
) -> None:
    selected, operations, _ = adapter(
        tmp_path,
        platform="douyin",
        extracts=[DOUYIN_INFO],
    )

    probe = selected.probe(source)

    assert probe.source_kind == "douyin"
    assert probe.canonical_url == (
        "https://www.douyin.com/video/7531234567890123456"
    )
    assert operations.extract_calls == [expected_extract]
    assert "private" not in repr(probe)


def test_douyin_search_modal_uses_the_open_video_and_drops_tracking(
    tmp_path: Path,
) -> None:
    modal_id = "7666820913983245618"
    selected, operations, _ = adapter(
        tmp_path,
        platform="douyin",
        extracts=[{**DOUYIN_INFO, "id": modal_id}],
    )

    probe = selected.probe(
        "https://www.douyin.com/video/7531064528663039290/"
        "search/%E7%A7%8B%E6%8B%9BAgent"
        "?aid=8db8df1c-8da0-4443-9a9e-bf7e4f48e5fa"
        f"&modal_id={modal_id}&type=general"
    )

    assert probe.canonical_url == f"https://www.douyin.com/video/{modal_id}"
    assert operations.extract_calls == [
        f"https://www.douyin.com/video/{modal_id}"
    ]
    assert "aid=" not in repr(probe)


@pytest.mark.parametrize(
    "source",
    [
        "https://www.douyin.com/search/topic?modal_id=not-a-number",
        "https://www.douyin.com/search/topic?modal_id=1&modal_id=2",
        "https://www.douyin.com/video/7531234567890123456/other/topic"
        "?modal_id=7666820913983245618",
    ],
)
def test_douyin_rejects_unreviewed_or_ambiguous_modal_links(
    tmp_path: Path,
    source: str,
) -> None:
    selected, _, _ = adapter(
        tmp_path,
        platform="douyin",
        extracts=[],
    )

    with pytest.raises(PlatformSourceError, match="unsupported"):
        selected.probe(source)


@pytest.mark.parametrize(
    ("platform", "source", "info"),
    [
        (
            "youtube",
            "http://www.youtube.com/watch?v=abcDEF12345",
            YOUTUBE_INFO,
        ),
        (
            "youtube",
            "https://user@www.youtube.com/watch?v=abcDEF12345",
            YOUTUBE_INFO,
        ),
        (
            "bilibili",
            "https://www.bilibili.com:444/video/BV1xx411c7mD",
            BILIBILI_INFO,
        ),
        (
            "youtube",
            "https://www.youtube.com/playlist?list=secret",
            YOUTUBE_INFO,
        ),
        (
            "youtube",
            "https://www.youtube.com/embed/abcDEF12345",
            YOUTUBE_INFO,
        ),
        (
            "bilibili",
            "https://www.bilibili.com/bangumi/play/ep1",
            BILIBILI_INFO,
        ),
        (
            "bilibili",
            "https://space.bilibili.com/123",
            BILIBILI_INFO,
        ),
        (
            "douyin",
            "https://www.douyin.com/user/MS4wLjAB",
            DOUYIN_INFO,
        ),
        (
            "douyin",
            "https://www.douyin.com/video/not-a-number",
            DOUYIN_INFO,
        ),
    ],
)
def test_unreviewed_initial_forms_are_rejected_before_extraction(
    tmp_path: Path,
    platform: str,
    source: str,
    info: dict[str, object],
) -> None:
    selected, operations, _ = adapter(
        tmp_path,
        platform=platform,
        extracts=[info],
    )

    with pytest.raises(PlatformSourceError) as caught:
        selected.probe(source)

    assert caught.value.code == "unsupported"
    assert operations.extract_calls == []


@pytest.mark.parametrize(
    ("platform", "wrong_key", "source", "info"),
    [
        (
            "youtube",
            "BiliBili",
            "https://youtu.be/abcDEF12345",
            YOUTUBE_INFO,
        ),
        (
            "bilibili",
            "Youtube",
            "https://b23.tv/short-code",
            BILIBILI_INFO,
        ),
        (
            "douyin",
            "Youtube",
            "https://v.douyin.com/AbC_123/",
            DOUYIN_INFO,
        ),
    ],
)
def test_extractor_identity_mismatch_is_adapter_drift(
    tmp_path: Path,
    platform: str,
    wrong_key: str,
    source: str,
    info: dict[str, object],
) -> None:
    changed = {**info, "extractor_key": wrong_key}
    selected, _, _ = adapter(
        tmp_path,
        platform=platform,
        extracts=[changed],
    )

    with pytest.raises(PlatformSourceError) as caught:
        selected.probe(source)

    assert caught.value.code == "adapter_drift"


def test_probe_returns_safe_title_duration_and_three_valued_tracks_without_fetch(
    tmp_path: Path,
) -> None:
    selected, operations, _ = adapter(
        tmp_path,
        platform="youtube",
        extracts=[YOUTUBE_INFO],
    )

    probe = selected.probe("https://youtu.be/abcDEF12345")

    assert probe.title == "YouTube Example"
    assert probe.duration_ms == 12_345
    assert probe.author == "Example Creator"
    assert probe.published_at == "2024-01-01"
    assert probe.thumbnail_url == "https://i.ytimg.com/vi/abcDEF12345/hqdefault.jpg"
    assert probe.description == "A public video description."
    assert [
        (
            track.language,
            track.format,
            track.kind,
            track.ui_label,
            track.stable_ordinal,
        )
        for track in probe.subtitle_tracks
    ] == [
        ("zh-hans", "vtt", "manual", "人工字幕", 0),
        ("en", "vtt", "manual", "人工字幕", 2),
        ("en", "vtt", "automatic", "自动字幕", 4),
    ]
    assert all(track.id.startswith("trk_") for track in probe.subtitle_tracks)
    assert operations.resource_calls == []
    assert operations.download_calls == []


def test_bilibili_unconfirmed_track_is_ranked_after_confirmed_track(
    tmp_path: Path,
) -> None:
    selected, _, _ = adapter(
        tmp_path,
        platform="bilibili",
        extracts=[BILIBILI_INFO],
    )
    probe = selected.probe("https://b23.tv/short-code")

    assert probe.thumbnail_url == "https://i0.hdslb.com/bfs/archive/public-cover.jpg"

    ranked = SubtitleTrackSelector(("zh-Hans", "en-US")).rank(
        probe.subtitle_tracks
    )

    assert [(track.kind, track.ui_label) for track in ranked] == [
        ("manual", "人工字幕"),
        ("unconfirmed", "字幕类型待确认"),
    ]


def test_bilibili_thumbnail_is_fetched_through_the_controlled_resource_boundary(
    tmp_path: Path,
) -> None:
    cover_url = str(BILIBILI_INFO["thumbnail"])
    cover = b"\xff\xd8\xff\xe0public jpeg"
    selected, operations, _ = adapter(
        tmp_path,
        platform="bilibili",
        extracts=[BILIBILI_INFO],
        resources={cover_url: cover},
    )

    outcome = selected.fetch_thumbnail("https://b23.tv/short-code")

    assert outcome.content == cover
    assert outcome.media_type == "image/jpeg"
    assert operations.resource_calls == [cover_url]


def test_bilibili_http_thumbnail_is_upgraded_before_probe_and_fetch(
    tmp_path: Path,
) -> None:
    insecure_cover_url = "http://i0.hdslb.com/bfs/archive/public-cover.jpg"
    secure_cover_url = "https://i0.hdslb.com/bfs/archive/public-cover.jpg"
    info = {**BILIBILI_INFO, "thumbnail": insecure_cover_url}
    cover = b"\xff\xd8\xff\xe0public jpeg"
    selected, operations, _ = adapter(
        tmp_path,
        platform="bilibili",
        extracts=[info, info],
        resources={secure_cover_url: cover},
    )

    probe = selected.probe("https://b23.tv/short-code")
    outcome = selected.fetch_thumbnail("https://b23.tv/short-code")

    assert probe.thumbnail_url == secure_cover_url
    assert outcome.content == cover
    assert operations.resource_calls == [secure_cover_url]


def test_fetch_subtitle_reuses_the_probe_extraction_and_exact_opaque_track(
    tmp_path: Path,
) -> None:
    resource_url = YOUTUBE_INFO["subtitles"]["ZH_hans"][0]["url"]  # type: ignore[index]
    selected, operations, _ = adapter(
        tmp_path,
        platform="youtube",
        extracts=[YOUTUBE_INFO, YOUTUBE_INFO],
        resources={str(resource_url): VALID_VTT},
    )
    probe = selected.probe("https://youtu.be/abcDEF12345")

    outcome = selected.fetch_subtitle(probe, probe.subtitle_tracks[0])

    assert isinstance(outcome, SubtitleOutcome)
    assert outcome.content == VALID_VTT
    assert operations.extract_calls == [
        "https://www.youtube.com/watch?v=abcDEF12345",
    ]
    assert operations.resource_calls == [resource_url]


def test_bilibili_fetch_reuses_probe_when_a_second_route_would_drift(
    tmp_path: Path,
) -> None:
    resource_url = BILIBILI_INFO["subtitles"]["zh_CN"][0]["url"]  # type: ignore[index]
    changed = copy.deepcopy(BILIBILI_INFO)
    changed["title"] = "Public API title"
    changed["duration"] = 21
    changed["subtitles"]["zh_CN"][0]["kind"] = "manual"  # type: ignore[index]
    selected, operations, _ = adapter(
        tmp_path,
        platform="bilibili",
        extracts=[BILIBILI_INFO, changed],
        resources={
            str(resource_url): (
                b'{"body":[{"from":0.0,"to":1.0,"content":"Hello"}]}'
            )
        },
    )
    probe = selected.probe("https://b23.tv/short-code")

    outcome = selected.fetch_subtitle(probe, probe.subtitle_tracks[0])

    assert isinstance(outcome, SubtitleOutcome)
    assert outcome.content.startswith(b'{"body"')
    assert operations.extract_calls == ["https://b23.tv/short-code"]
    assert operations.resource_calls == [resource_url]


def test_fetch_subtitle_never_substitutes_reordered_or_missing_track(
    tmp_path: Path,
) -> None:
    changed = copy.deepcopy(YOUTUBE_INFO)
    changed["subtitles"]["ZH_hans"].reverse()  # type: ignore[index, union-attr]
    selected, operations, _ = adapter(
        tmp_path,
        platform="youtube",
        extracts=[YOUTUBE_INFO, changed],
    )
    probe = selected.probe("https://youtu.be/abcDEF12345")
    restarted = YtDlpSourceAdapter(
        platform="youtube",
        operations=operations,
        paths=selected.paths,
        media_validator=selected.media_validator,
        audio_registrar=selected.audio_registrar,
    )

    with pytest.raises(PlatformSourceError) as caught:
        restarted.fetch_subtitle(probe, probe.subtitle_tracks[0])

    assert caught.value.code == "adapter_drift"
    assert operations.resource_calls == []


def test_invalid_subtitle_content_is_candidate_failure_not_success(
    tmp_path: Path,
) -> None:
    resource_url = YOUTUBE_INFO["subtitles"]["ZH_hans"][0]["url"]  # type: ignore[index]
    selected, _, _ = adapter(
        tmp_path,
        platform="youtube",
        extracts=[YOUTUBE_INFO, YOUTUBE_INFO],
        resources={str(resource_url): b"invalid"},
    )
    probe = selected.probe("https://youtu.be/abcDEF12345")

    with pytest.raises(SubtitleCandidateError) as caught:
        selected.fetch_subtitle(probe, probe.subtitle_tracks[0])

    assert str(caught.value) == "subtitle_invalid"


def test_canonical_selector_fetches_audio_exactly_once_after_all_candidates_fail(
    tmp_path: Path,
) -> None:
    no_valid_subtitles = copy.deepcopy(YOUTUBE_INFO)
    resource_index = 0
    for entries in (
        no_valid_subtitles["subtitles"].values(),  # type: ignore[union-attr]
        no_valid_subtitles["automatic_captions"].values(),  # type: ignore[union-attr]
    ):
        for variants in entries:
            for variant in variants:
                variant["url"] = (
                    "https://www.youtube.com/unavailable/"
                    f"{resource_index}/{variant['ext']}"
                )
                resource_index += 1
    resources = {
        variant["url"]: YtDlpOperationFailure("removed")
        for container in (
            no_valid_subtitles["subtitles"],  # type: ignore[dict-item]
            no_valid_subtitles["automatic_captions"],  # type: ignore[dict-item]
        )
        for variants in container.values()
        for variant in variants
        if variant["ext"] in {"vtt", "srt", "ass", "json"}
        and "live_chat" not in variant["url"]
    }
    extract_count = 1 + len(resources) + 1
    selected, operations, registrar = adapter(
        tmp_path,
        platform="youtube",
        extracts=[no_valid_subtitles] * extract_count,
        resources=resources,
    )
    probe = selected.probe("https://youtu.be/abcDEF12345")

    outcome = acquire_subtitle_or_audio(
        probe,
        selected,
        SubtitleTrackSelector(("zh-Hans", "en")),
        item_id=ITEM_ID,
    )

    assert isinstance(outcome, AudioOutcome)
    assert len(operations.download_calls) == 1
    assert len(registrar.calls) == 1


def test_audio_uses_uuid_owned_target_and_ignores_extractor_filename(
    tmp_path: Path,
) -> None:
    info = copy.deepcopy(YOUTUBE_INFO)
    info["_filename"] = r"C:\Users\someone\secret\escape.m4a"
    selected, operations, registrar = adapter(
        tmp_path,
        platform="youtube",
        extracts=[info, info],
    )
    probe = selected.probe("https://youtu.be/abcDEF12345")

    outcome = selected.fetch_audio(probe, ITEM_ID)

    expected = paths(tmp_path).downloaded_audio(ITEM_ID, "m4a")
    assert operations.extract_calls == [
        "https://www.youtube.com/watch?v=abcDEF12345",
    ]
    assert operations.download_calls[0][1] == expected
    assert registrar.calls == [(ITEM_ID, expected)]
    assert outcome == AudioOutcome(
        asset_id="asset-123",
        format="m4a",
        duration_ms=12_345,
        size_bytes=len(b"audio bytes"),
    )
    assert "someone" not in str(operations.download_calls[0][1])


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("This video has been removed by the uploader", "removed"),
        ("HTTP Error 503: Service Unavailable", "temporary"),
        ("Unable to extract initial state", "temporary"),
        ("Sign in to confirm your age", "auth_required"),
        ("Fresh cookies (not necessarily logged in) are needed", "auth_required"),
        ("Could not copy Chrome cookie database", "auth_required"),
        ("failed to load cookies", "auth_required"),
        ("The uploader has not made this video available in your country", "region_restricted"),
        ("Unsupported URL: https://example.invalid/?token=secret", "unsupported"),
        ("No video formats found due to changed metadata", "adapter_drift"),
        ("Invalid subtitle content", "invalid_content"),
    ],
)
def test_extractor_failures_map_to_closed_safe_codes(
    message: str,
    expected: str,
) -> None:
    error = ExtractorError(message, expected=True)

    classified = classify_extractor_failure(error)

    assert classified == expected
    assert "secret" not in classified
    assert "http" not in classified


def test_registry_selects_platform_and_keeps_youtube_capability_separate(
    tmp_path: Path,
) -> None:
    bilibili, _, _ = adapter(
        tmp_path,
        platform="bilibili",
        extracts=[BILIBILI_INFO],
    )
    registry = PlatformSourceRegistry(
        bilibili=bilibili,
        douyin=None,
        youtube=None,
        youtube_unavailable_code="youtube_runtime_unavailable",
    )

    assert (
        registry.probe("https://b23.tv/short-code").source_kind
        == "bilibili"
    )
    with pytest.raises(SourceCapabilityError) as caught:
        registry.probe("https://youtu.be/abcDEF12345")
    assert caught.value.code == "youtube_runtime_unavailable"
    with pytest.raises(SourceCapabilityError) as caught:
        registry.probe("https://v.douyin.com/AbC_123/")
    assert caught.value.code == "adapter_unavailable"


def test_douyin_combined_media_is_a_valid_asr_handoff(tmp_path: Path) -> None:
    selected, operations, registrar = adapter(
        tmp_path,
        platform="douyin",
        extracts=[DOUYIN_INFO, DOUYIN_INFO],
    )
    probe = selected.probe(
        "https://www.douyin.com/video/7531234567890123456"
    )

    outcome = selected.fetch_audio(probe, ITEM_ID)

    expected = paths(tmp_path).downloaded_audio(ITEM_ID, "mp4")
    assert operations.download_calls[0][0] == (
        "https://v3-dy.example.bytecdn.cn/video-best"
    )
    assert operations.download_calls[0][1] == expected
    assert registrar.calls == [(ITEM_ID, expected)]
    assert outcome.format == "mp4"


class FakeControlledBridge:
    def __init__(self, info: dict[str, object]) -> None:
        self.info = info
        self.closed = False

    def probe(self, _: str) -> dict[str, object]:
        return copy.deepcopy(self.info)

    def close(self) -> None:
        self.closed = True


class InitialStateFailureBridge(FakeControlledBridge):
    def __init__(self, info: dict[str, object], message: str) -> None:
        super().__init__(info)
        self.message = message

    def probe(self, _: str) -> dict[str, object]:
        raise ExtractorError(self.message, expected=True)


class FakeControlledResponse:
    status = 200

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.closed = False

    def read(self) -> bytes:
        return self.content

    def __iter__(self):
        yield self.content

    def close(self) -> None:
        self.closed = True


class FakePinnedTransport:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[tuple[object, object]] = []

    def request(self, request: object, policy: object) -> FakeControlledResponse:
        self.calls.append((request, policy))
        return FakeControlledResponse(self.content)


class SequencedPinnedTransport(FakePinnedTransport):
    def __init__(self, contents: list[bytes]) -> None:
        super().__init__(b"")
        self.contents = contents

    def request(self, request: object, policy: object) -> FakeControlledResponse:
        self.calls.append((request, policy))
        return FakeControlledResponse(self.contents.pop(0))


class FlakyPinnedTransport(FakePinnedTransport):
    def request(self, request: object, policy: object) -> FakeControlledResponse:
        self.calls.append((request, policy))
        if len(self.calls) == 1:
            raise TransportSecurityError("connection_failed", "youtube")
        return FakeControlledResponse(self.content)


class RejectedPinnedTransport(FakePinnedTransport):
    def request(self, request: object, policy: object) -> FakeControlledResponse:
        self.calls.append((request, policy))
        raise TransportSecurityError("host_not_allowed", "example.com")


class InterruptedResponse(FakeControlledResponse):
    def __iter__(self):
        yield b"partial"
        raise TransportSecurityError("read_failed", "youtube")


class InterruptedDownloadTransport(FakePinnedTransport):
    def request(self, request: object, policy: object) -> FakeControlledResponse:
        self.calls.append((request, policy))
        if len(self.calls) == 1:
            return InterruptedResponse(b"")
        return FakeControlledResponse(self.content)


@pytest.mark.parametrize(
    "extractor_message",
    [
        "Unable to extract initial state",
        "Unable to download webpage: HTTP Error 412: Precondition Failed",
        "No video formats found due to changed metadata",
    ],
)
def test_bilibili_api_fallback_recovers_risk_control_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extractor_message: str,
) -> None:
    bridge = InitialStateFailureBridge({}, extractor_message)
    monkeypatch.setattr(
        platform_sources_module,
        "build_controlled_platform_ytdlp",
        lambda *args, **kwargs: bridge,
    )
    payloads = [
        {
            "code": 0,
            "data": {
                "bvid": "BV1xx411c7mD",
                "aid": 170001,
                "title": "Public video",
                "duration": 20,
                "pubdate": 1_704_067_200,
                "pic": "https://i0.hdslb.com/bfs/archive/public-cover.jpg",
                "desc": "Public description",
                "owner": {"name": "Public creator"},
                "pages": [{"page": 1, "cid": 12345, "duration": 20}],
            },
        },
        {
            "code": 0,
            "data": {
                "dash": {
                    "audio": [
                        {
                            "id": 30280,
                            "baseUrl": "https://audio.hdslb.com/audio.m4s",
                            "mimeType": "audio/mp4",
                            "codecs": "mp4a.40.2",
                            "bandwidth": 132_000,
                        }
                    ]
                }
            },
        },
        {
            "code": 0,
            "data": {
                "subtitle": {
                    "subtitles": [
                        {
                            "lan": "zh-CN",
                            "subtitle_url": "//aisubtitle.hdslb.com/subtitle.json",
                        }
                    ]
                }
            },
        },
    ]
    transport = SequencedPinnedTransport(
        [json.dumps(payload).encode("utf-8") for payload in payloads]
    )
    operations = ControlledYtDlpOperations(
        platform="bilibili",
        transport=transport,  # type: ignore[arg-type]
        output_root=tmp_path,
    )

    info = operations.extract("https://www.bilibili.com/video/BV1xx411c7mD")

    assert info["id"] == "BV1xx411c7mD"
    assert info["title"] == "Public video"
    assert info["uploader"] == "Public creator"
    assert info["thumbnail"] == "https://i0.hdslb.com/bfs/archive/public-cover.jpg"
    assert info["formats"] == [
        {
            "format_id": "30280",
            "url": "https://audio.hdslb.com/audio.m4s",
            "ext": "m4a",
            "acodec": "mp4a.40.2",
            "vcodec": "none",
            "abr": 132.0,
        }
    ]
    assert info["subtitles"] == {
        "zh-CN": [
            {
                "ext": "json",
                "url": "https://aisubtitle.hdslb.com/subtitle.json",
                "kind": "manual",
            }
        ]
    }
    assert bridge.closed
    assert len(transport.calls) == 3


def test_controlled_operations_authorize_only_the_current_extraction_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = copy.deepcopy(BILIBILI_INFO)
    second = copy.deepcopy(BILIBILI_INFO)
    first_url = first["subtitles"]["zh_CN"][0]["url"]  # type: ignore[index]
    second_url = "https://new-resource.example/video/subtitle.json"
    second["subtitles"]["zh_CN"][0]["url"] = second_url  # type: ignore[index]
    bridges = [FakeControlledBridge(first), FakeControlledBridge(second)]
    monkeypatch.setattr(
        platform_sources_module,
        "build_controlled_platform_ytdlp",
        lambda *args, **kwargs: bridges.pop(0),
    )
    transport = FakePinnedTransport(VALID_VTT)
    operations = ControlledYtDlpOperations(
        platform="bilibili",
        transport=transport,  # type: ignore[arg-type]
        output_root=tmp_path,
    )

    operations.extract("https://www.bilibili.com/video/BV1xx411c7mD")
    assert operations.fetch_resource(str(first_url), max_bytes=1024) == VALID_VTT
    request, policy = transport.calls[0]
    assert getattr(request, "url") == first_url
    assert getattr(policy, "exact_hosts") == frozenset({"aisubtitle.hdslb.com"})
    assert getattr(policy, "allowed_suffixes") == frozenset()

    operations.extract("https://www.bilibili.com/video/BV1xx411c7mD")
    with pytest.raises(YtDlpOperationFailure) as caught:
        operations.fetch_resource(str(first_url), max_bytes=1024)
    assert caught.value.code == "adapter_drift"
    assert len(transport.calls) == 1


def test_controlled_resource_retries_one_temporary_connection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = copy.deepcopy(BILIBILI_INFO)
    resource_url = info["subtitles"]["zh_CN"][0]["url"]  # type: ignore[index]
    monkeypatch.setattr(
        platform_sources_module,
        "build_controlled_platform_ytdlp",
        lambda *args, **kwargs: FakeControlledBridge(info),
    )
    transport = FlakyPinnedTransport(VALID_VTT)
    operations = ControlledYtDlpOperations(
        platform="bilibili",
        transport=transport,  # type: ignore[arg-type]
        output_root=tmp_path,
    )
    operations.extract("https://www.bilibili.com/video/BV1xx411c7mD")

    assert operations.fetch_resource(str(resource_url), max_bytes=1024) == VALID_VTT
    assert len(transport.calls) == 2


def test_controlled_resource_never_retries_security_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = copy.deepcopy(BILIBILI_INFO)
    resource_url = info["subtitles"]["zh_CN"][0]["url"]  # type: ignore[index]
    monkeypatch.setattr(
        platform_sources_module,
        "build_controlled_platform_ytdlp",
        lambda *args, **kwargs: FakeControlledBridge(info),
    )
    transport = RejectedPinnedTransport(VALID_VTT)
    operations = ControlledYtDlpOperations(
        platform="bilibili",
        transport=transport,  # type: ignore[arg-type]
        output_root=tmp_path,
    )
    operations.extract("https://www.bilibili.com/video/BV1xx411c7mD")

    with pytest.raises(YtDlpOperationFailure) as caught:
        operations.fetch_resource(str(resource_url), max_bytes=1024)

    assert caught.value.code == "adapter_drift"
    assert len(transport.calls) == 1


def test_controlled_download_publishes_complete_target_via_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = copy.deepcopy(BILIBILI_INFO)
    audio_url = "https://audio.hdslb.com/bfs/audio/example.webm"
    info["formats"] = [
        {
            "url": audio_url,
            "ext": "webm",
            "acodec": "opus",
            "vcodec": "none",
        }
    ]
    bridge = FakeControlledBridge(info)
    monkeypatch.setattr(
        platform_sources_module,
        "build_controlled_platform_ytdlp",
        lambda *args, **kwargs: bridge,
    )
    transport = FakePinnedTransport(b"complete audio")
    operations = ControlledYtDlpOperations(
        platform="bilibili",
        transport=transport,  # type: ignore[arg-type]
        output_root=tmp_path,
    )
    operations.extract("https://www.bilibili.com/video/BV1xx411c7mD")
    target = tmp_path / "downloaded.webm"

    operations.download_resource(audio_url, target, max_bytes=1024)

    assert target.read_bytes() == b"complete audio"
    assert list(tmp_path.glob("*.partial")) == []
    request, _ = transport.calls[0]
    headers = getattr(request, "headers")
    assert headers["Referer"] == (
        "https://www.bilibili.com/video/BV1xx411c7mD"
    )
    assert headers["User-Agent"]


def test_controlled_download_retries_read_failure_and_removes_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = copy.deepcopy(BILIBILI_INFO)
    audio_url = "https://audio.hdslb.com/bfs/audio/example.webm"
    info["formats"] = [
        {
            "url": audio_url,
            "ext": "webm",
            "acodec": "opus",
            "vcodec": "none",
        }
    ]
    monkeypatch.setattr(
        platform_sources_module,
        "build_controlled_platform_ytdlp",
        lambda *args, **kwargs: FakeControlledBridge(info),
    )
    transport = InterruptedDownloadTransport(b"complete audio")
    operations = ControlledYtDlpOperations(
        platform="bilibili",
        transport=transport,  # type: ignore[arg-type]
        output_root=tmp_path,
    )
    operations.extract("https://www.bilibili.com/video/BV1xx411c7mD")
    target = tmp_path / "downloaded.webm"

    operations.download_resource(audio_url, target, max_bytes=1024)

    assert target.read_bytes() == b"complete audio"
    assert list(tmp_path.glob("*.partial")) == []
    assert len(transport.calls) == 2
