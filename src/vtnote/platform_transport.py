"""Direct-only, DNS-pinned HTTPS transport for controlled platform access."""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from types import MappingProxyType
from typing import Callable, Protocol
from urllib.parse import urljoin, urlsplit

from vtnote.url_security import (
    Resolver,
    UpstreamHostPolicy,
    normalize_host,
    public_ip_answers,
)


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "proxy-authorization",
        "proxy-connection",
    }
)
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_RESPONSE_HIDDEN_HEADERS = frozenset({"set-cookie", "set-cookie2"})
_BILIBILI_ANONYMOUS_COOKIE_NAMES = frozenset({"b_nut", "buvid3", "sid"})
_ANONYMOUS_COOKIE_VALUE = re.compile(r"^[A-Za-z0-9._~-]{1,256}$")
_ERROR_CATEGORY = re.compile(r"^[a-z_]{1,64}$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class TransportSecurityError(RuntimeError):
    """A safe transport error containing only category and normalized host."""

    def __init__(self, category: str, host: str) -> None:
        if _ERROR_CATEGORY.fullmatch(category) is None:
            raise ValueError("invalid transport error category")
        try:
            normalized_host = normalize_host(host)
        except ValueError:
            if host not in {"bilibili", "youtube"}:
                raise ValueError("invalid transport error host") from None
            normalized_host = host
        self.category = category
        self.host = normalized_host
        super().__init__(f"{self.category} for {self.host}")


@dataclass(frozen=True, slots=True)
class TransportLimits:
    max_redirects: int = 5
    max_header_count: int = 64
    max_header_line_bytes: int = 8_192
    max_request_target_bytes: int = 16_384
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    decode_chunk_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        integer_values = (
            self.max_redirects,
            self.max_header_count,
            self.max_header_line_bytes,
            self.max_request_target_bytes,
            self.decode_chunk_bytes,
        )
        if any(type(value) is not int or value <= 0 for value in integer_values):
            raise ValueError("transport integer limits must be positive")
        if (
            not isinstance(self.connect_timeout, (int, float))
            or isinstance(self.connect_timeout, bool)
            or self.connect_timeout <= 0
            or not isinstance(self.read_timeout, (int, float))
            or isinstance(self.read_timeout, bool)
            or self.read_timeout <= 0
        ):
            raise ValueError("transport timeouts must be positive")


@dataclass(frozen=True, slots=True)
class SourceHttpRequest:
    url: str
    max_wire_bytes: int
    max_decoded_bytes: int
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict)
    anonymous_session_cookies: Mapping[str, str] = field(
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url:
            raise ValueError("source request URL is required")
        if type(self.max_wire_bytes) is not int or self.max_wire_bytes <= 0:
            raise ValueError("max wire bytes must be positive")
        if type(self.max_decoded_bytes) is not int or self.max_decoded_bytes <= 0:
            raise ValueError("max decoded bytes must be positive")
        method = self.method.upper()
        if method not in {"GET", "HEAD"}:
            raise ValueError("source request method must be GET or HEAD")
        if not isinstance(self.headers, Mapping):
            raise ValueError("source request headers must be a mapping")
        normalized: dict[str, str] = {}
        for name, value in self.headers.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not name
                or _HEADER_NAME.fullmatch(name) is None
                or "\r" in name
                or "\n" in name
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError("invalid source request header")
            normalized[name] = value
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "headers", MappingProxyType(normalized))
        if not isinstance(self.anonymous_session_cookies, Mapping):
            raise ValueError("anonymous session cookies must be a mapping")
        anonymous_cookies: dict[str, str] = {}
        for name, value in self.anonymous_session_cookies.items():
            if (
                name not in _BILIBILI_ANONYMOUS_COOKIE_NAMES
                or not isinstance(value, str)
                or _ANONYMOUS_COOKIE_VALUE.fullmatch(value) is None
            ):
                raise ValueError("invalid anonymous session cookie")
            anonymous_cookies[name] = value
        object.__setattr__(
            self,
            "anonymous_session_cookies",
            MappingProxyType(anonymous_cookies),
        )


class RawHttpResponse(Protocol):
    status: int
    headers: tuple[tuple[str, str], ...]
    peer_ip: str

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class HttpsConnector(Protocol):
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
    ) -> RawHttpResponse: ...


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        address: str,
        *,
        connect_timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(host, port=443, timeout=connect_timeout, context=context)
        self._pinned_address = address

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
        )
        try:
            self.sock = self._context.wrap_socket(
                raw,
                server_hostname=self.host,
            )
        except Exception:
            raw.close()
            raise


class _DirectRawResponse:
    def __init__(
        self,
        response: http.client.HTTPResponse,
        connection: _PinnedHttpsConnection,
        peer_ip: str,
    ) -> None:
        self.status = response.status
        self.headers = tuple(response.getheaders())
        self.peer_ip = peer_ip
        self._response = response
        self._connection = connection

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


class DirectHttpsConnector:
    """Dial only explicit vetted IPs while retaining hostname TLS verification."""

    def __init__(self, context: ssl.SSLContext | None = None) -> None:
        self.context = context or ssl.create_default_context()
        if (
            not self.context.check_hostname
            or self.context.verify_mode != ssl.CERT_REQUIRED
        ):
            raise ValueError("direct HTTPS requires hostname and certificate verification")

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
    ) -> RawHttpResponse:
        for address in addresses:
            connection = _PinnedHttpsConnection(
                host,
                address,
                connect_timeout=connect_timeout,
                context=self.context,
            )
            try:
                connection.request(method, target, headers=headers)
                if connection.sock is None:
                    raise OSError("TLS socket unavailable")
                connection.sock.settimeout(read_timeout)
                peer_ip = str(connection.sock.getpeername()[0])
                response = connection.getresponse()
                return _DirectRawResponse(response, connection, peer_ip)
            except Exception:
                connection.close()
        raise OSError("all direct HTTPS addresses failed")


class SourceHttpResponse(Iterator[bytes]):
    def __init__(
        self,
        raw: RawHttpResponse,
        *,
        host: str,
        max_wire_bytes: int,
        max_decoded_bytes: int,
        decode_chunk_bytes: int,
        headers: tuple[tuple[str, str], ...],
        anonymous_session_cookies: Mapping[str, str] | None = None,
    ) -> None:
        self.status = raw.status
        self.headers = {
            name: value
            for name, value in headers
            if name.casefold() not in _RESPONSE_HIDDEN_HEADERS
        }
        self._raw = raw
        self._host = host
        self._max_wire_bytes = max_wire_bytes
        self._max_decoded_bytes = max_decoded_bytes
        self._decode_chunk_bytes = decode_chunk_bytes
        self._wire_bytes = 0
        self._decoded_bytes = 0
        self._buffer = bytearray()
        self._eof = False
        self._closed = False
        self._anonymous_session_cookies = MappingProxyType(
            dict(anonymous_session_cookies or {})
        )
        encoding = _header_value(headers, "content-encoding")
        normalized_encoding = encoding.casefold() if encoding is not None else ""
        if normalized_encoding in {"", "identity"}:
            self._decoder: zlib.Decompress | None = None
        elif normalized_encoding == "gzip":
            self._decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif normalized_encoding == "deflate":
            self._decoder = zlib.decompressobj()
        else:
            self.close()
            raise TransportSecurityError("unsupported_content_encoding", host)

    def anonymous_session_cookies(self) -> dict[str, str]:
        return dict(self._anonymous_session_cookies)

    def _fail(self, category: str) -> None:
        self.close()
        raise TransportSecurityError(category, self._host)

    def _append_decoded(self, content: bytes) -> None:
        self._decoded_bytes += len(content)
        if self._decoded_bytes > self._max_decoded_bytes:
            self._fail("decoded_body_too_large")
        self._buffer.extend(content)

    def _read_chunk(self) -> None:
        if self._eof:
            return
        try:
            wire = self._raw.read(self._decode_chunk_bytes)
        except Exception:
            self.close()
            raise TransportSecurityError("read_failed", self._host) from None
        if not isinstance(wire, bytes):
            self._fail("invalid_body")
        self._wire_bytes += len(wire)
        if self._wire_bytes > self._max_wire_bytes:
            self._fail("wire_body_too_large")
        if wire:
            try:
                if self._decoder is None:
                    self._append_decoded(wire)
                    return
                pending = wire
                while pending:
                    remaining = self._max_decoded_bytes - self._decoded_bytes
                    decoded = self._decoder.decompress(pending, remaining + 1)
                    next_pending = self._decoder.unconsumed_tail
                    self._append_decoded(decoded)
                    if next_pending == pending and not decoded:
                        self._fail("invalid_compressed_body")
                    pending = next_pending
            except zlib.error:
                self.close()
                raise TransportSecurityError(
                    "invalid_compressed_body",
                    self._host,
                ) from None
            return
        self._eof = True
        if self._decoder is not None:
            try:
                remaining = self._max_decoded_bytes - self._decoded_bytes
                tail = self._decoder.flush(remaining + 1)
            except zlib.error:
                self.close()
                raise TransportSecurityError(
                    "invalid_compressed_body",
                    self._host,
                ) from None
            self._append_decoded(tail)
            if not self._decoder.eof or self._decoder.unused_data:
                self._fail("invalid_compressed_body")

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            raise ValueError("response is closed")
        if size == 0:
            return b""
        if size < -1:
            raise ValueError("invalid read size")
        if size == -1:
            while not self._eof:
                self._read_chunk()
            content = bytes(self._buffer)
            self._buffer.clear()
            return content
        while len(self._buffer) < size and not self._eof:
            self._read_chunk()
        content = bytes(self._buffer[:size])
        del self._buffer[:size]
        return content

    def readline(self, size: int = -1) -> bytes:
        if self._closed:
            raise ValueError("response is closed")
        if size == 0:
            return b""
        if size < -1:
            raise ValueError("invalid read size")
        while True:
            boundary = self._buffer.find(b"\n")
            if boundary >= 0:
                length = boundary + 1
                break
            if size >= 0 and len(self._buffer) >= size:
                length = size
                break
            if self._eof:
                length = len(self._buffer)
                break
            self._read_chunk()
        if size >= 0:
            length = min(length, size)
        content = bytes(self._buffer[:length])
        del self._buffer[:length]
        return content

    def __iter__(self) -> SourceHttpResponse:
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._raw.close()

    def __enter__(self) -> SourceHttpResponse:
        if self._closed:
            raise ValueError("response is closed")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _header_value(
    headers: tuple[tuple[str, str], ...],
    name: str,
) -> str | None:
    lowered = name.casefold()
    for candidate, value in headers:
        if candidate.casefold() == lowered:
            return value.strip()
    return None


def _header_values(
    headers: tuple[tuple[str, str], ...],
    name: str,
) -> tuple[str, ...]:
    lowered = name.casefold()
    return tuple(
        value.strip()
        for candidate, value in headers
        if candidate.casefold() == lowered
    )


def _safe_request_headers(
    supplied: Mapping[str, str],
    *,
    host: str,
) -> dict[str, str]:
    connection_tokens: set[str] = set()
    for name, value in supplied.items():
        if name.casefold() == "connection":
            connection_tokens.update(
                token.strip().casefold() for token in value.split(",") if token.strip()
            )
    safe: dict[str, str] = {}
    for name, value in supplied.items():
        lowered = name.casefold()
        if (
            lowered in _SENSITIVE_HEADERS
            or lowered in _HOP_BY_HOP_HEADERS
            or lowered in connection_tokens
            or lowered.startswith("proxy-")
        ):
            continue
        safe[name] = value
    safe["Host"] = host
    safe["Accept-Encoding"] = "identity"
    return dict(sorted(safe.items()))


def _bilibili_anonymous_cookies(
    headers: tuple[tuple[str, str], ...],
) -> dict[str, str]:
    accepted: dict[str, str] = {}
    for name, value in headers:
        if name.casefold() != "set-cookie":
            continue
        parsed = SimpleCookie()
        try:
            parsed.load(value)
        except CookieError:
            continue
        for cookie_name in _BILIBILI_ANONYMOUS_COOKIE_NAMES:
            morsel = parsed.get(cookie_name)
            if morsel is None:
                continue
            domain = morsel["domain"].casefold()
            path = morsel["path"]
            if (
                domain not in {"", "bilibili.com", ".bilibili.com"}
                or path not in {"", "/"}
                or _ANONYMOUS_COOKIE_VALUE.fullmatch(morsel.value) is None
            ):
                continue
            accepted[cookie_name] = morsel.value
    return accepted


def _validate_headers(
    headers: tuple[tuple[str, str], ...],
    *,
    limits: TransportLimits,
    host: str,
) -> None:
    if len(headers) > limits.max_header_count:
        raise TransportSecurityError("headers_too_large", host)
    for name, value in headers:
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or "\r" in name
            or "\n" in name
            or "\r" in value
            or "\n" in value
            or len(f"{name}: {value}".encode("utf-8"))
            > limits.max_header_line_bytes
        ):
            raise TransportSecurityError("headers_too_large", host)


class PinnedHttpsTransport:
    def __init__(
        self,
        *,
        resolver: Resolver,
        connector: HttpsConnector | None = None,
        limits: TransportLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.resolver = resolver
        self.connector = connector or DirectHttpsConnector()
        self.limits = limits or TransportLimits()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _parse_url(
        self,
        url: str,
        policy: UpstreamHostPolicy,
    ) -> tuple[str, str]:
        safe_host = policy.platform
        try:
            parts = urlsplit(url)
            port = parts.port
            host = normalize_host(parts.hostname or "")
        except (TypeError, ValueError) as error:
            raise TransportSecurityError("invalid_url", safe_host) from None
        if (
            parts.scheme.casefold() != "https"
            or port not in {None, 443}
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise TransportSecurityError("invalid_url", host)
        if not policy.allows(host):
            raise TransportSecurityError("host_not_allowed", host)
        target = parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"
        return host, target

    def _resolve(self, host: str) -> tuple[str, ...]:
        try:
            answers = self.resolver.resolve(host)
            return public_ip_answers(answers)
        except Exception:
            raise TransportSecurityError("dns_rejected", host) from None

    def request(
        self,
        request: SourceHttpRequest,
        policy: UpstreamHostPolicy,
    ) -> SourceHttpResponse:
        if not isinstance(request, SourceHttpRequest):
            raise TypeError("request must be SourceHttpRequest")
        if not isinstance(policy, UpstreamHostPolicy):
            raise TypeError("policy must be UpstreamHostPolicy")
        if policy.expires_at is not None and policy.expires_at <= self.clock():
            raise TransportSecurityError("policy_expired", policy.platform)

        current_url = request.url
        headers = dict(request.headers)
        for redirect_count in range(self.limits.max_redirects + 1):
            host, target = self._parse_url(current_url, policy)
            if (
                len(f"{request.method} {target} HTTP/1.1".encode("utf-8"))
                > self.limits.max_request_target_bytes
            ):
                raise TransportSecurityError("request_target_too_large", host)
            addresses = self._resolve(host)
            safe_headers = _safe_request_headers(headers, host=host)
            if request.anonymous_session_cookies:
                if policy.platform != "bilibili" or not (
                    host == "bilibili.com" or host.endswith(".bilibili.com")
                ):
                    raise TransportSecurityError(
                        "anonymous_session_rejected",
                        host,
                    )
                safe_headers["Cookie"] = "; ".join(
                    f"{name}={value}"
                    for name, value in sorted(
                        request.anonymous_session_cookies.items()
                    )
                )
            _validate_headers(
                tuple(safe_headers.items()),
                limits=self.limits,
                host=host,
            )
            try:
                raw = self.connector.request(
                    host=host,
                    addresses=addresses,
                    method=request.method,
                    target=target,
                    headers=safe_headers,
                    connect_timeout=float(self.limits.connect_timeout),
                    read_timeout=float(self.limits.read_timeout),
                )
            except Exception:
                raise TransportSecurityError("connection_failed", host) from None
            try:
                peer = ipaddress.ip_address(raw.peer_ip).compressed
            except ValueError:
                raw.close()
                raise TransportSecurityError("peer_mismatch", host) from None
            if peer not in addresses:
                raw.close()
                raise TransportSecurityError("peer_mismatch", host)
            response_headers = tuple(raw.headers)
            try:
                _validate_headers(response_headers, limits=self.limits, host=host)
            except Exception:
                raw.close()
                raise
            content_lengths = _header_values(response_headers, "content-length")
            transfer_encodings = _header_values(
                response_headers,
                "transfer-encoding",
            )
            if len(content_lengths) > 1:
                raw.close()
                raise TransportSecurityError("invalid_content_length", host)
            if content_lengths and transfer_encodings:
                raw.close()
                raise TransportSecurityError("ambiguous_body_framing", host)
            content_encodings = _header_values(
                response_headers,
                "content-encoding",
            )
            if len(content_encodings) > 1:
                raw.close()
                raise TransportSecurityError("invalid_content_encoding", host)
            if content_lengths:
                content_length = content_lengths[0]
                if not content_length.isdecimal():
                    raw.close()
                    raise TransportSecurityError("invalid_content_length", host)
                if int(content_length) > request.max_wire_bytes:
                    raw.close()
                    raise TransportSecurityError("wire_body_too_large", host)
            location = _header_value(response_headers, "location")
            if raw.status in _REDIRECT_STATUSES and location is not None:
                raw.close()
                if redirect_count >= self.limits.max_redirects:
                    raise TransportSecurityError("too_many_redirects", host)
                current_url = urljoin(current_url, location)
                continue
            return SourceHttpResponse(
                raw,
                host=host,
                max_wire_bytes=request.max_wire_bytes,
                max_decoded_bytes=request.max_decoded_bytes,
                decode_chunk_bytes=self.limits.decode_chunk_bytes,
                headers=response_headers,
                anonymous_session_cookies=(
                    _bilibili_anonymous_cookies(response_headers)
                    if policy.platform == "bilibili"
                    else {}
                ),
            )
        raise TransportSecurityError("too_many_redirects", policy.platform)
