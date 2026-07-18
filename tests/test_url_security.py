from __future__ import annotations

import pytest

from vtnote.url_security import SourceUrlPolicy, UnsafeSourceUrl


class FakeResolver:
    def __init__(self, addresses: dict[str, list[str]]) -> None:
        self.addresses = addresses

    def resolve(self, host: str) -> list[str]:
        return self.addresses[host]


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


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.1.1", "::1"])
def test_source_policy_rejects_private_dns_answers(address: str) -> None:
    policy = SourceUrlPolicy(FakeResolver({"www.youtube.com": [address]}))
    with pytest.raises(UnsafeSourceUrl, match="public"):
        policy.validate("https://www.youtube.com/watch?v=1")


def test_source_policy_accepts_exact_and_real_subdomains() -> None:
    policy = SourceUrlPolicy(
        FakeResolver(
            {
                "youtu.be": ["142.250.72.14"],
                "www.bilibili.com": ["203.107.1.33"],
            }
        )
    )
    assert policy.validate("https://youtu.be/abc") == "https://youtu.be/abc"
    assert policy.validate("https://www.bilibili.com/video/BV1") == (
        "https://www.bilibili.com/video/BV1"
    )


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
