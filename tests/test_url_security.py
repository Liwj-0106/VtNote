from __future__ import annotations

import pytest

from vtnote.url_security import (
    SourceUrlPolicy,
    UnsafeSourceUrl,
    extract_supported_source_url,
    page_host_policy,
)


class FakeResolver:
    def __init__(self, addresses: dict[str, list[str]]) -> None:
        self.addresses = addresses

    def resolve(self, host: str) -> list[str]:
        return self.addresses[host]


class ExplodingResolver:
    def resolve(self, host: str) -> list[str]:
        raise AssertionError("proxy-mode source validation must not use local DNS")


@pytest.mark.parametrize(
    ("source_text", "expected"),
    [
        (
            "复制打开抖音，看看这个视频 https://v.douyin.com/AbC_123/ 03/18",
            "https://v.douyin.com/AbC_123/",
        ),
        (
            "推荐：Why smarter AI\nhttps://www.youtube.com/watch?v=oZBGAuANX6I。",
            "https://www.youtube.com/watch?v=oZBGAuANX6I",
        ),
        (
            "【课程分享】https://b23.tv/AbC123?share_source=copy_web，复制打开",
            "https://b23.tv/AbC123?share_source=copy_web",
        ),
        (
            "[观看视频](https://youtu.be/oZBGAuANX6I)",
            "https://youtu.be/oZBGAuANX6I",
        ),
        (
            "课程合集 https://space.bilibili.com/2142762/lists/3662502?type=season",
            "https://space.bilibili.com/2142762/lists/3662502?type=season",
        ),
        (
            "https://youtu.be/oZBGAuANX6I 同一个链接 "
            "https://youtu.be/oZBGAuANX6I",
            "https://youtu.be/oZBGAuANX6I",
        ),
    ],
)
def test_share_text_extracts_one_supported_video_url(
    source_text: str,
    expected: str,
) -> None:
    assert extract_supported_source_url(source_text) == expected


@pytest.mark.parametrize(
    "source_text",
    [
        "只有视频标题，没有链接",
        "https://youtube.com.evil.example/watch?v=secret",
        "http://www.youtube.com/watch?v=insecure",
        "https://youtu.be/first https://v.douyin.com/Second_2/",
    ],
)
def test_share_text_rejects_missing_spoofed_insecure_or_ambiguous_urls(
    source_text: str,
) -> None:
    with pytest.raises(UnsafeSourceUrl):
        extract_supported_source_url(source_text)


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=1",
        "https://youtube.com:444/watch?v=1",
        "https://user@youtube.com/watch?v=1",
        "https://127.0.0.1/watch?v=1",
        "https://youtube.com.evil.example/watch?v=1",
        "https://notbilibili.com/video/1",
        "https://[::1",
    ],
)
def test_source_policy_rejects_non_public_or_fake_platform_urls(url: str) -> None:
    policy = SourceUrlPolicy(FakeResolver({"youtube.com": ["142.250.72.14"]}))
    with pytest.raises(UnsafeSourceUrl):
        policy.validate(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.0.0.2", "169.254.1.1", "::1",
        "224.0.0.1", "239.255.255.250", "ff02::1", "2001:db8::1",
    ],
)
def test_source_policy_rejects_private_dns_answers(address: str) -> None:
    policy = SourceUrlPolicy(FakeResolver({"www.youtube.com": [address]}))
    with pytest.raises(UnsafeSourceUrl, match="public"):
        policy.validate("https://www.youtube.com/watch?v=1")


def test_source_policy_reports_proxy_fake_ip_dns_answers() -> None:
    policy = SourceUrlPolicy(FakeResolver({"www.bilibili.com": ["198.18.0.140"]}))
    with pytest.raises(UnsafeSourceUrl, match="proxy Fake-IP"):
        policy.validate("https://www.bilibili.com/video/BV1")


def test_explicit_proxy_mode_keeps_host_policy_without_using_target_dns() -> None:
    policy = SourceUrlPolicy(ExplodingResolver(), resolve_dns=False)

    assert policy.validate("https://www.youtube.com/watch?v=oZBGAuANX6I") == (
        "https://www.youtube.com/watch?v=oZBGAuANX6I"
    )
    with pytest.raises(UnsafeSourceUrl):
        policy.validate("https://127.0.0.1/internal")
    with pytest.raises(UnsafeSourceUrl):
        policy.validate("https://youtube.com.evil.example/watch?v=1")


def test_source_policy_accepts_exact_and_real_subdomains() -> None:
    policy = SourceUrlPolicy(
        FakeResolver(
            {
                "youtu.be": ["142.250.72.14"],
                "www.bilibili.com": ["203.107.1.33"],
                "space.bilibili.com": ["203.107.1.34"],
                "v.douyin.com": ["180.163.151.34"],
            }
        )
    )
    assert policy.validate("https://youtu.be/abc") == "https://youtu.be/abc"
    assert policy.validate("https://www.bilibili.com/video/BV1") == (
        "https://www.bilibili.com/video/BV1"
    )
    assert policy.validate("https://space.bilibili.com/1/lists/2?type=season") == (
        "https://space.bilibili.com/1/lists/2?type=season"
    )
    assert policy.validate("https://v.douyin.com/AbC_123/") == (
        "https://v.douyin.com/AbC_123/"
    )


def test_douyin_page_policy_allows_exact_official_short_link_transition() -> None:
    policy = page_host_policy("douyin")

    assert policy.allows("www.iesdouyin.com")
    assert not policy.allows("evil.iesdouyin.com")


def test_every_redirect_is_revalidated_for_host_and_dns() -> None:
    policy = SourceUrlPolicy(
        FakeResolver(
            {
                "youtu.be": ["142.250.72.14"],
                "www.youtube.com": ["142.250.72.15"],
                "evil.example": ["93.184.216.34"],
            }
        )
    )
    assert policy.validate_redirect_chain(
        "https://youtu.be/abc", ["https://www.youtube.com/watch?v=abc"]
    ) == "https://www.youtube.com/watch?v=abc"
    with pytest.raises(UnsafeSourceUrl):
        policy.validate_redirect_chain(
            "https://youtu.be/abc", ["https://evil.example/steal"]
        )


def test_malformed_port_is_rejected_as_an_unsafe_source() -> None:
    policy = SourceUrlPolicy(FakeResolver({"youtu.be": ["142.250.72.14"]}))
    with pytest.raises(UnsafeSourceUrl):
        policy.validate("https://youtu.be:not-a-port/abc")
