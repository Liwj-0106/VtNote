from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from vtnote.platform_transport import (
    LoopbackHttpProxyConnector,
    PinnedHttpsTransport,
    SourceHttpRequest,
    TransportLimits,
    TransportSecurityError,
)
from vtnote.url_security import (
    UpstreamHostPolicy,
    extractor_aux_host_policy,
    extracted_resource_hosts,
    page_host_policy,
    resource_host_policy,
)


NOW = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)


class FakeResolver:
    def __init__(self, answers: dict[str, list[str]]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def resolve(self, host: str) -> list[str]:
        self.calls.append(host)
        return self.answers[host]


class FakeRawResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: tuple[tuple[str, str], ...] = (),
        body: bytes = b"",
        peer_ip: str = "142.250.72.14",
    ) -> None:
        self.status = status
        self.headers = headers
        self.peer_ip = peer_ip
        self._body = BytesIO(body)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def close(self) -> None:
        self.closed = True


class ExplodingReadResponse(FakeRawResponse):
    def read(self, size: int = -1) -> bytes:
        raise OSError("read socket secret")


@dataclass
class ConnectorCall:
    host: str
    addresses: tuple[str, ...]
    method: str
    target: str
    body: bytes | None
    headers: dict[str, str]
    connect_timeout: float
    read_timeout: float


@dataclass
class FakeConnector:
    responses: list[FakeRawResponse | Exception]
    calls: list[ConnectorCall] = field(default_factory=list)

    def request(
        self,
        *,
        host: str,
        addresses: tuple[str, ...],
        method: str,
        target: str,
        body: bytes | None,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
    ) -> FakeRawResponse:
        self.calls.append(
            ConnectorCall(
                host=host,
                addresses=addresses,
                method=method,
                target=target,
                body=body,
                headers=headers,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
            )
        )
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeLoopbackProxyConnector(FakeConnector):
    dns_pinned = False


def request(url: str = "https://www.youtube.com/watch?v=abc", **changes: object) -> SourceHttpRequest:
    values: dict[str, object] = {
        "url": url,
        "max_wire_bytes": 1_024,
        "max_decoded_bytes": 2_048,
    }
    values.update(changes)
    return SourceHttpRequest(**values)  # type: ignore[arg-type]


def transport(
    resolver: FakeResolver,
    connector: FakeConnector,
    *,
    limits: TransportLimits | None = None,
) -> PinnedHttpsTransport:
    return PinnedHttpsTransport(
        resolver=resolver,
        connector=connector,
        limits=limits or TransportLimits(),
        clock=lambda: NOW,
    )


def test_direct_request_uses_vetted_addresses_host_sni_inputs_and_no_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9998")
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    connector = FakeConnector([FakeRawResponse(body=b"ok")])

    response = transport(resolver, connector).request(
        request(
            headers={
                "User-Agent": "VtNote-test",
                "Cookie": "session=secret",
                "Authorization": "Bearer secret",
                "Proxy-Authorization": "Basic secret",
                "Connection": "X-Private",
                "X-Private": "secret",
                "Host": "evil.example",
            }
        ),
        page_host_policy("youtube"),
    )

    assert response.read() == b"ok"
    assert len(connector.calls) == 1
    call = connector.calls[0]
    assert call.host == "www.youtube.com"
    assert call.addresses == ("142.250.72.14",)
    assert call.target == "/watch?v=abc"
    assert call.headers == {
        "Accept-Encoding": "identity",
        "Host": "www.youtube.com",
        "User-Agent": "VtNote-test",
    }
    assert "127.0.0.1" not in repr(call)


def test_controlled_post_has_bounded_body_and_managed_content_length() -> None:
    resolver = FakeResolver({"youtubei.googleapis.com": ["142.250.72.10"]})
    connector = FakeConnector(
        [FakeRawResponse(body=b"{}", peer_ip="142.250.72.10")]
    )

    response = transport(resolver, connector).request(
        request(
            "https://youtubei.googleapis.com/youtubei/v1/player",
            method="POST",
            body=b'{"videoId":"abc"}',
            headers={
                "Content-Type": "application/json",
                "content-length": "999999",
            },
        ),
        extractor_aux_host_policy("youtube"),
    )

    assert response.read() == b"{}"
    call = connector.calls[0]
    assert call.method == "POST"
    assert call.body == b'{"videoId":"abc"}'
    assert call.headers["Content-Type"] == "application/json"
    assert call.headers["Content-Length"] == str(len(call.body))
    assert "content-length" not in call.headers


def test_controlled_post_rejects_oversized_body_before_connection() -> None:
    resolver = FakeResolver({"youtubei.googleapis.com": ["142.250.72.10"]})
    connector = FakeConnector([FakeRawResponse()])

    with pytest.raises(TransportSecurityError, match="request_body_too_large"):
        transport(
            resolver,
            connector,
            limits=TransportLimits(max_request_body_bytes=3),
        ).request(
            request(
                "https://youtubei.googleapis.com/youtubei/v1/player",
                method="POST",
                body=b"four",
            ),
            extractor_aux_host_policy("youtube"),
        )

    assert connector.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "POST"},
        {"method": "POST", "body": b""},
        {"method": "GET", "body": b"unexpected"},
    ],
)
def test_source_request_rejects_invalid_method_body_combinations(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        request(**changes)


def test_bilibili_anonymous_session_accepts_only_allowlisted_cookies() -> None:
    resolver = FakeResolver({"www.bilibili.com": ["203.107.1.33"]})
    connector = FakeConnector(
        [
            FakeRawResponse(
                headers=(
                    ("Set-Cookie", "buvid3=anonymous-1; Domain=.bilibili.com; Path=/"),
                    ("Set-Cookie", "b_nut=1234567890; Domain=.bilibili.com; Path=/"),
                    ("Set-Cookie", "sid=anonymous_2; Domain=.bilibili.com; Path=/"),
                    ("Set-Cookie", "SESSDATA=login-secret; Domain=.bilibili.com; Path=/"),
                ),
                body=b"page",
                peer_ip="203.107.1.33",
            ),
            FakeRawResponse(body=b"api", peer_ip="203.107.1.33"),
        ]
    )
    selected = transport(resolver, connector)
    first = selected.request(
        request("https://www.bilibili.com/video/BV1"),
        page_host_policy("bilibili"),
    )

    cookies = first.anonymous_session_cookies()
    assert cookies == {
        "b_nut": "1234567890",
        "buvid3": "anonymous-1",
        "sid": "anonymous_2",
    }
    selected.request(
        request(
            "https://www.bilibili.com/video/BV1",
            anonymous_session_cookies=cookies,
        ),
        page_host_policy("bilibili"),
    )
    assert connector.calls[1].headers["Cookie"] == (
        "b_nut=1234567890; buvid3=anonymous-1; sid=anonymous_2"
    )
    assert "login-secret" not in repr(connector.calls)


def test_anonymous_session_cookies_are_rejected_for_other_platforms() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    connector = FakeConnector([FakeRawResponse()])
    with pytest.raises(TransportSecurityError, match="anonymous_session_rejected"):
        transport(resolver, connector).request(
            request(anonymous_session_cookies={"sid": "anonymous"}),
            page_host_policy("youtube"),
        )
    assert connector.calls == []


def test_browser_cookie_is_secret_and_never_forwarded_across_redirects() -> None:
    resolver = FakeResolver(
        {
            "www.youtube.com": ["142.250.72.14"],
            "youtu.be": ["142.250.72.15"],
        }
    )
    connector = FakeConnector(
        [
            FakeRawResponse(
                status=302,
                headers=(("Location", "https://youtu.be/abc"),),
                peer_ip="142.250.72.14",
            ),
            FakeRawResponse(body=b"ok", peer_ip="142.250.72.15"),
        ]
    )
    selected_request = request(
        browser_cookie_header="session=browser-super-secret"
    )

    response = transport(resolver, connector).request(
        selected_request,
        page_host_policy("youtube"),
    )

    assert response.read() == b"ok"
    assert connector.calls[0].headers["Cookie"] == "session=browser-super-secret"
    assert "Cookie" not in connector.calls[1].headers
    assert "browser-super-secret" not in repr(selected_request)


def test_browser_authorization_is_only_sent_with_youtube_api_cookie() -> None:
    resolver = FakeResolver({"youtubei.googleapis.com": ["142.250.72.10"]})
    connector = FakeConnector(
        [FakeRawResponse(body=b"{}", peer_ip="142.250.72.10")]
    )
    authorization = f"SAPISIDHASH 1720000000_{'a' * 40}"
    selected_request = request(
        "https://youtubei.googleapis.com/youtubei/v1/player",
        method="POST",
        body=b'{}',
        browser_authorization_header=authorization,
    )

    response = transport(resolver, connector).request(
        selected_request,
        extractor_aux_host_policy("youtube"),
    )

    assert response.read() == b"{}"
    assert connector.calls[0].headers["Authorization"] == authorization
    assert "Cookie" not in connector.calls[0].headers
    assert authorization not in repr(selected_request)


def test_invalid_browser_authorization_is_rejected_before_transport() -> None:
    resolver = FakeResolver({"youtubei.googleapis.com": ["142.250.72.10"]})
    connector = FakeConnector([FakeRawResponse()])

    with pytest.raises(ValueError, match="browser authorization"):
        request(
            "https://youtubei.googleapis.com/youtubei/v1/player",
            method="POST",
            body=b'{}',
            browser_authorization_header="Bearer must-not-pass",
        )

    assert connector.calls == []


@pytest.mark.parametrize(
    ("url", "policy"),
    [
        (
            "https://www.bilibili.com/video/BV1",
            page_host_policy("bilibili"),
        ),
        (
            "https://rr1---sn.example.googlevideo.com/videoplayback",
            resource_host_policy(
                "youtube",
                extracted_resource_hosts(
                    frozenset({"rr1---sn.example.googlevideo.com"})
                ),
                expires_at=NOW + timedelta(minutes=5),
            ),
        ),
    ],
)
def test_browser_cookie_is_rejected_outside_page_or_aux_scope(
    url: str,
    policy: UpstreamHostPolicy,
) -> None:
    host = url.split("/", 3)[2]
    resolver = FakeResolver({host: ["142.250.72.14"]})
    connector = FakeConnector([FakeRawResponse()])

    with pytest.raises(TransportSecurityError, match="browser_session_rejected"):
        transport(resolver, connector).request(
            request(url, browser_cookie_header="session=secret"),
            policy,
        )

    assert connector.calls == []


@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],
        ["142.250.72.14", "10.0.0.1"],
        ["not-an-ip"],
        [],
    ],
)
def test_entire_dns_answer_set_must_be_public(answers: list[str]) -> None:
    resolver = FakeResolver({"www.youtube.com": answers})
    connector = FakeConnector([FakeRawResponse()])

    with pytest.raises(TransportSecurityError) as caught:
        transport(resolver, connector).request(
            request("https://www.youtube.com/path?token=must-not-leak"),
            page_host_policy("youtube"),
        )

    assert connector.calls == []
    assert "must-not-leak" not in str(caught.value)
    assert "127.0.0.1" not in str(caught.value)


def test_connected_peer_must_belong_to_current_dns_answer_set() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    raw = FakeRawResponse(peer_ip="8.8.8.8")
    connector = FakeConnector([raw])

    with pytest.raises(TransportSecurityError, match="peer_mismatch"):
        transport(resolver, connector).request(
            request(),
            page_host_policy("youtube"),
        )

    assert raw.closed


def test_explicit_loopback_proxy_requires_loopback_peer_but_not_target_dns_peer() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    connector = FakeLoopbackProxyConnector(
        [FakeRawResponse(body=b"proxied", peer_ip="127.0.0.1")]
    )

    response = transport(resolver, connector).request(
        request(),
        page_host_policy("youtube"),
    )

    assert response.read() == b"proxied"
    assert connector.calls[0].host == "www.youtube.com"
    assert connector.calls[0].addresses == ()
    assert resolver.calls == []


def test_explicit_loopback_proxy_rejects_non_loopback_peer() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    raw = FakeRawResponse(peer_ip="192.168.1.2")
    connector = FakeLoopbackProxyConnector([raw])

    with pytest.raises(TransportSecurityError, match="peer_mismatch"):
        transport(resolver, connector).request(
            request(),
            page_host_policy("youtube"),
        )

    assert raw.closed


@pytest.mark.parametrize(
    "proxy_url",
    [
        "https://127.0.0.1:7897",
        "http://10.0.0.2:7897",
        "http://user:secret@127.0.0.1:7897",
        "http://127.0.0.1:7897/path",
    ],
)
def test_loopback_proxy_connector_rejects_unsafe_configuration(proxy_url: str) -> None:
    with pytest.raises(ValueError):
        LoopbackHttpProxyConnector(proxy_url)


def test_each_redirect_revalidates_scheme_host_dns_and_peer() -> None:
    resolver = FakeResolver(
        {
            "www.youtube.com": ["142.250.72.14"],
            "youtu.be": ["142.250.72.15"],
        }
    )
    first = FakeRawResponse(
        status=302,
        headers=(("Location", "https://youtu.be/next"),),
        peer_ip="142.250.72.14",
    )
    second = FakeRawResponse(body=b"done", peer_ip="142.250.72.15")
    connector = FakeConnector([first, second])

    response = transport(resolver, connector).request(
        request(),
        page_host_policy("youtube"),
    )

    assert response.read() == b"done"
    assert response.url == "https://youtu.be/next"
    assert resolver.calls == ["www.youtube.com", "youtu.be"]
    assert [call.host for call in connector.calls] == [
        "www.youtube.com",
        "youtu.be",
    ]
    assert first.closed


def test_douyin_short_link_allows_only_reviewed_official_transition_host() -> None:
    resolver = FakeResolver(
        {
            "v.douyin.com": ["180.163.151.34"],
            "www.iesdouyin.com": ["180.163.151.35"],
            "www.douyin.com": ["180.163.151.36"],
        }
    )
    connector = FakeConnector(
        [
            FakeRawResponse(
                status=302,
                headers=(("Location", "https://www.iesdouyin.com/share/video/1/"),),
                peer_ip="180.163.151.34",
            ),
            FakeRawResponse(
                status=302,
                headers=(("Location", "https://www.douyin.com/video/1"),),
                peer_ip="180.163.151.35",
            ),
            FakeRawResponse(body=b"page", peer_ip="180.163.151.36"),
        ]
    )

    response = transport(resolver, connector).request(
        request("https://v.douyin.com/AbC_123/"),
        page_host_policy("douyin"),
    )

    assert response.read() == b"page"
    assert response.url == "https://www.douyin.com/video/1"
    assert [call.host for call in connector.calls] == [
        "v.douyin.com",
        "www.iesdouyin.com",
        "www.douyin.com",
    ]


@pytest.mark.parametrize(
    "location",
    [
        "http://www.youtube.com/insecure",
        "https://www.youtube.com:444/wrong-port",
        "https://example.com/not-allowed",
        "https://user:pass@www.youtube.com/credentials",
    ],
)
def test_redirect_rejects_unsafe_target_without_second_connection(location: str) -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    first = FakeRawResponse(
        status=302,
        headers=(("Location", location),),
    )
    connector = FakeConnector([first])

    with pytest.raises(TransportSecurityError):
        transport(resolver, connector).request(
            request(),
            page_host_policy("youtube"),
        )

    assert len(connector.calls) == 1
    assert first.closed


def test_relative_redirect_resolves_against_previous_url() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    connector = FakeConnector(
        [
            FakeRawResponse(
                status=301,
                headers=(("Location", "../two?x=1"),),
            ),
            FakeRawResponse(body=b"ok"),
        ]
    )

    response = transport(resolver, connector).request(
        request("https://www.youtube.com/one/path"),
        page_host_policy("youtube"),
    )

    assert response.read() == b"ok"
    assert connector.calls[1].target == "/two?x=1"


def test_resource_policy_is_exact_host_only_and_expires() -> None:
    policy = resource_host_policy(
        "youtube",
        extracted_resource_hosts(
            frozenset({"rr1---sn.example.googlevideo.com"})
        ),
        expires_at=NOW + timedelta(minutes=5),
    )
    resolver = FakeResolver(
        {"rr1---sn.example.googlevideo.com": ["142.250.72.14"]}
    )
    connector = FakeConnector([FakeRawResponse(body=b"media")])
    response = transport(resolver, connector).request(
        request("https://rr1---sn.example.googlevideo.com/videoplayback"),
        policy,
    )
    assert response.read() == b"media"

    expired = resource_host_policy(
        "youtube",
        extracted_resource_hosts(
            frozenset({"rr1---sn.example.googlevideo.com"})
        ),
        expires_at=NOW,
    )
    with pytest.raises(TransportSecurityError, match="policy_expired"):
        transport(resolver, FakeConnector([])).request(
            request("https://rr1---sn.example.googlevideo.com/videoplayback"),
            expired,
        )


def test_resource_policy_allows_only_known_cdn_hosts_after_redirect() -> None:
    policy = resource_host_policy(
        "douyin",
        extracted_resource_hosts(frozenset({"api-play.amemv.com"})),
        expires_at=NOW + timedelta(minutes=5),
    )
    resolver = FakeResolver(
        {
            "api-play.amemv.com": ["142.250.72.14"],
            "v5-traffic.douyinvod.com": ["142.250.72.14"],
        }
    )
    connector = FakeConnector(
        [
            FakeRawResponse(
                status=302,
                headers=(("Location", "https://v5-traffic.douyinvod.com/media"),),
            ),
            FakeRawResponse(body=b"media"),
        ]
    )

    response = transport(resolver, connector).request(
        request("https://api-play.amemv.com/redirect"),
        policy,
    )

    assert response.read() == b"media"
    assert [call.host for call in connector.calls] == [
        "api-play.amemv.com",
        "v5-traffic.douyinvod.com",
    ]

    with pytest.raises(TransportSecurityError, match="host_not_allowed"):
        transport(resolver, FakeConnector([])).request(
            request("https://v5-traffic.douyinvod.com/media"),
            policy,
        )


def test_resource_policy_rejects_unknown_redirect_host() -> None:
    policy = resource_host_policy(
        "douyin",
        extracted_resource_hosts(frozenset({"api-play.amemv.com"})),
        expires_at=NOW + timedelta(minutes=5),
    )
    resolver = FakeResolver({"api-play.amemv.com": ["142.250.72.14"]})
    connector = FakeConnector(
        [
            FakeRawResponse(
                status=302,
                headers=(("Location", "https://private.example/media"),),
            )
        ]
    )

    with pytest.raises(TransportSecurityError, match="host_not_allowed"):
        transport(resolver, connector).request(
            request("https://api-play.amemv.com/redirect"),
            policy,
        )


def test_response_limits_content_length_wire_and_decoded_gzip() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    early = FakeRawResponse(
        headers=(("Content-Length", "11"),),
        body=b"small",
    )
    with pytest.raises(TransportSecurityError, match="wire_body_too_large"):
        transport(resolver, FakeConnector([early])).request(
            request(max_wire_bytes=10),
            page_host_policy("youtube"),
        )
    assert early.closed

    wire = FakeRawResponse(body=b"12345678901")
    response = transport(resolver, FakeConnector([wire])).request(
        request(max_wire_bytes=10),
        page_host_policy("youtube"),
    )
    with pytest.raises(TransportSecurityError, match="wire_body_too_large"):
        response.read()
    assert wire.closed

    compressed = gzip.compress(b"A" * 100)
    decoded = FakeRawResponse(
        headers=(("Content-Encoding", "gzip"),),
        body=compressed,
    )
    response = transport(resolver, FakeConnector([decoded])).request(
        request(max_wire_bytes=len(compressed), max_decoded_bytes=50),
        page_host_policy("youtube"),
    )
    with pytest.raises(TransportSecurityError, match="decoded_body_too_large"):
        response.read()
    assert decoded.closed


def test_duplicate_content_length_and_truncated_compression_are_rejected() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    duplicate = FakeRawResponse(
        headers=(("Content-Length", "2"), ("Content-Length", "2")),
        body=b"ok",
    )
    with pytest.raises(TransportSecurityError, match="invalid_content_length"):
        transport(resolver, FakeConnector([duplicate])).request(
            request(),
            page_host_policy("youtube"),
        )
    assert duplicate.closed

    compressed = gzip.compress(b"complete")[:-4]
    truncated = FakeRawResponse(
        headers=(("Content-Encoding", "GZip"),),
        body=compressed,
    )
    response = transport(resolver, FakeConnector([truncated])).request(
        request(),
        page_host_policy("youtube"),
    )
    with pytest.raises(TransportSecurityError, match="invalid_compressed_body"):
        response.read()
    assert truncated.closed


def test_all_read_styles_share_one_limit_and_response_is_context_managed() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    raw = FakeRawResponse(
        headers=(("Set-Cookie", "secret=1"), ("X-Safe", "yes")),
        body=b"one\ntwo\nthree\n",
    )
    response = transport(resolver, FakeConnector([raw])).request(
        request(max_wire_bytes=14, max_decoded_bytes=14),
        page_host_policy("youtube"),
    )
    assert response.headers == {"X-Safe": "yes"}
    with response:
        assert response.read(2) == b"on"
        assert response.readline() == b"e\n"
        assert list(response) == [b"two\n", b"three\n"]
    assert raw.closed


def test_read_failure_is_sanitized_and_closes_response() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    raw = ExplodingReadResponse()
    response = transport(resolver, FakeConnector([raw])).request(
        request(),
        page_host_policy("youtube"),
    )
    with pytest.raises(TransportSecurityError, match="read_failed") as caught:
        response.read()
    assert "read socket secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert raw.closed


def test_header_redirect_and_timeout_limits_are_bounded_and_errors_are_safe() -> None:
    limits = TransportLimits(
        max_redirects=1,
        max_header_count=2,
        max_header_line_bytes=32,
        connect_timeout=1.25,
        read_timeout=2.5,
        decode_chunk_bytes=8,
    )
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    oversized_headers = FakeRawResponse(
        headers=(("X-One", "1"), ("X-Two", "2"), ("X-Three", "3")),
    )
    with pytest.raises(TransportSecurityError, match="headers_too_large"):
        transport(
            resolver,
            FakeConnector([oversized_headers]),
            limits=limits,
        ).request(request(), page_host_policy("youtube"))

    connector = FakeConnector([OSError("socket body secret")])
    with pytest.raises(TransportSecurityError) as caught:
        transport(resolver, connector, limits=limits).request(
            request("https://www.youtube.com/path?secret=query-secret"),
            page_host_policy("youtube"),
        )
    assert connector.calls[0].connect_timeout == 1.25
    assert connector.calls[0].read_timeout == 2.5
    assert "socket body secret" not in str(caught.value)
    assert "query-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_ambiguous_framing_and_oversized_request_target_are_rejected() -> None:
    resolver = FakeResolver({"www.youtube.com": ["142.250.72.14"]})
    ambiguous = FakeRawResponse(
        headers=(
            ("Content-Length", "2"),
            ("Transfer-Encoding", "chunked"),
        ),
        body=b"ok",
    )
    with pytest.raises(TransportSecurityError, match="ambiguous_body_framing"):
        transport(resolver, FakeConnector([ambiguous])).request(
            request(),
            page_host_policy("youtube"),
        )
    assert ambiguous.closed

    duplicate_encoding = FakeRawResponse(
        headers=(
            ("Content-Encoding", "gzip"),
            ("Content-Encoding", "gzip"),
        ),
    )
    with pytest.raises(TransportSecurityError, match="invalid_content_encoding"):
        transport(resolver, FakeConnector([duplicate_encoding])).request(
            request(),
            page_host_policy("youtube"),
        )
    assert duplicate_encoding.closed

    small_limits = TransportLimits(max_request_target_bytes=32)
    with pytest.raises(TransportSecurityError, match="request_target_too_large"):
        transport(
            resolver,
            FakeConnector([]),
            limits=small_limits,
        ).request(
            request("https://www.youtube.com/" + ("x" * 64)),
            page_host_policy("youtube"),
        )


def test_request_and_policy_runtime_validation() -> None:
    with pytest.raises(ValueError):
        request(max_wire_bytes=0)
    with pytest.raises(ValueError):
        request(max_decoded_bytes=-1)
    with pytest.raises(ValueError):
        request(headers={"Bad Header": "value"})
    with pytest.raises(ValueError):
        resource_host_policy(
            "youtube",
            extracted_resource_hosts(frozenset({"media.example.com"})),
            expires_at=datetime(2026, 7, 29, 8, 5),
        )
    with pytest.raises(TypeError):
        resource_host_policy(
            "youtube",
            frozenset({"media.example.com"}),  # type: ignore[arg-type]
            expires_at=NOW + timedelta(minutes=5),
        )
    with pytest.raises(ValueError):
        UpstreamHostPolicy(
            platform="vimeo",
            stage="page",
            exact_hosts=frozenset({"example.com"}),
            allowed_suffixes=frozenset(),
        )
