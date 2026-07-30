from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from vtnote.platform_transport import (
    PinnedHttpsTransport,
    SourceHttpRequest,
    TransportLimits,
    TransportSecurityError,
)
from vtnote.url_security import (
    UpstreamHostPolicy,
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
                headers=headers,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
            )
        )
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


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
    assert resolver.calls == ["www.youtube.com", "youtu.be"]
    assert [call.host for call in connector.calls] == [
        "www.youtube.com",
        "youtu.be",
    ]
    assert first.closed


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
