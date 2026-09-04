"""Strict source URL validation independent from provider base URL policy."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from typing import Protocol
from urllib.parse import urlsplit


class UnsafeSourceUrl(ValueError):
    pass


class Resolver(Protocol):
    def resolve(self, host: str) -> list[str]: ...


class SocketResolver:
    def resolve(self, host: str) -> list[str]:
        return sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


_PLATFORM_SUFFIXES = (
    "youtube.com",
    "youtu.be",
    "bilibili.com",
    "b23.tv",
    "douyin.com",
)
_SHARE_TEXT_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "www.bilibili.com",
        "space.bilibili.com",
        "b23.tv",
        "douyin.com",
        "www.douyin.com",
        "v.douyin.com",
    }
)
_SHARE_URL = re.compile(
    r"https://[^\s<>\"'，。；：！？）》】」』…]+",
    re.IGNORECASE,
)
_SHARE_URL_TRAILING = ".,;:!?)]}，。；：！？）》】」』…"
_MAX_SHARE_TEXT_LENGTH = 8_192
_MAX_SOURCE_URL_LENGTH = 4_096
_PROXY_FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")
_HOST = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$"
)
_POLICY_PLATFORMS = frozenset(
    {"bilibili", "douyin", "youtube", "model_assets"}
)
_POLICY_STAGES = frozenset({"page", "extractor_aux", "resource"})
_RESOURCE_REDIRECT_SUFFIXES = {
    "bilibili": frozenset(),
    "douyin": frozenset({"douyinvod.com"}),
    "youtube": frozenset({"googlevideo.com"}),
}


def normalize_host(host: str) -> str:
    if not isinstance(host, str):
        raise ValueError("invalid upstream host")
    try:
        normalized = host.strip().encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as error:
        raise ValueError("invalid upstream host") from error
    if _HOST.fullmatch(normalized) is None:
        raise ValueError("invalid upstream host")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    raise ValueError("upstream host cannot be an IP literal")


def extract_supported_source_url(source_text: str) -> str:
    """Extract exactly one supported HTTPS video URL from pasted share text."""

    if (
        not isinstance(source_text, str)
        or not source_text.strip()
        or len(source_text) > _MAX_SHARE_TEXT_LENGTH
    ):
        raise UnsafeSourceUrl("source text does not contain a supported video URL")
    candidates: list[str] = []
    for match in _SHARE_URL.finditer(source_text):
        candidate = match.group(0).rstrip(_SHARE_URL_TRAILING)
        if not candidate or len(candidate) > _MAX_SOURCE_URL_LENGTH:
            continue
        try:
            parts = urlsplit(candidate)
            host = normalize_host(parts.hostname or "")
        except (TypeError, ValueError):
            continue
        if host in _SHARE_TEXT_HOSTS and candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        raise UnsafeSourceUrl("source text does not contain a supported video URL")
    if len(candidates) != 1:
        raise UnsafeSourceUrl("source text contains multiple supported video URLs")
    return candidates[0]


def extract_supported_source_urls(source_text: str, *, limit: int = 100) -> list[str]:
    """Extract supported HTTPS URLs in share-text order for a bounded batch."""

    if (
        not isinstance(source_text, str)
        or not source_text.strip()
        or len(source_text) > 65_536
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise UnsafeSourceUrl("source text does not contain supported video URLs")
    candidates: list[str] = []
    for match in _SHARE_URL.finditer(source_text):
        candidate = match.group(0).rstrip(_SHARE_URL_TRAILING)
        if not candidate or len(candidate) > _MAX_SOURCE_URL_LENGTH:
            continue
        try:
            parts = urlsplit(candidate)
            host = normalize_host(parts.hostname or "")
        except (TypeError, ValueError):
            continue
        if host in _SHARE_TEXT_HOSTS:
            candidates.append(candidate)
            if len(candidates) > limit:
                raise UnsafeSourceUrl("source text contains too many video URLs")
    if not candidates:
        raise UnsafeSourceUrl("source text does not contain supported video URLs")
    return candidates


def public_ip_answers(addresses: list[str]) -> tuple[str, ...]:
    if not addresses:
        raise ValueError("upstream host has no DNS answers")
    normalized: set[str] = set()
    for answer in addresses:
        try:
            address = ipaddress.ip_address(answer)
        except ValueError as error:
            raise ValueError("upstream host returned an invalid address") from error
        if address.version == 4 and address in _PROXY_FAKE_IP_RANGE:
            raise ValueError("upstream host resolved to a proxy Fake-IP address")
        if (
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError("upstream host must resolve only to public addresses")
        normalized.add(address.compressed)
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class UpstreamHostPolicy:
    platform: Literal["bilibili", "douyin", "youtube", "model_assets"]
    stage: Literal["page", "extractor_aux", "resource"]
    exact_hosts: frozenset[str]
    allowed_suffixes: frozenset[str]
    expires_at: datetime | None = None
    redirect_suffixes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.platform not in _POLICY_PLATFORMS:
            raise ValueError("invalid upstream platform")
        if self.stage not in _POLICY_STAGES:
            raise ValueError("invalid upstream stage")
        exact = frozenset(normalize_host(host) for host in self.exact_hosts)
        suffixes = frozenset(normalize_host(host) for host in self.allowed_suffixes)
        redirect_suffixes = frozenset(
            normalize_host(host) for host in self.redirect_suffixes
        )
        if not exact and not suffixes:
            raise ValueError("upstream policy must allow at least one host")
        if self.stage == "resource" and suffixes:
            raise ValueError("resource policy must be exact-host only")
        if self.stage != "resource" and redirect_suffixes:
            raise ValueError("redirect suffixes are resource-only")
        if self.stage == "resource" and self.expires_at is None:
            raise ValueError("resource policy requires an expiry")
        if self.expires_at is not None:
            if (
                self.expires_at.tzinfo is None
                or self.expires_at.utcoffset() is None
            ):
                raise ValueError("upstream policy expiry must be timezone-aware")
            object.__setattr__(
                self,
                "expires_at",
                self.expires_at.astimezone(timezone.utc),
            )
        object.__setattr__(self, "exact_hosts", exact)
        object.__setattr__(self, "allowed_suffixes", suffixes)
        object.__setattr__(self, "redirect_suffixes", redirect_suffixes)

    def allows(self, host: str, *, is_redirect: bool = False) -> bool:
        normalized = normalize_host(host)
        if normalized in self.exact_hosts or any(
            normalized == suffix or normalized.endswith("." + suffix)
            for suffix in self.allowed_suffixes
        ):
            return True
        return is_redirect and any(
            normalized == suffix or normalized.endswith("." + suffix)
            for suffix in self.redirect_suffixes
        )


def page_host_policy(
    platform: Literal["bilibili", "douyin", "youtube"],
) -> UpstreamHostPolicy:
    if platform == "youtube":
        hosts = frozenset({"youtube.com", "www.youtube.com", "youtu.be"})
    elif platform == "bilibili":
        hosts = frozenset({"www.bilibili.com", "space.bilibili.com", "b23.tv"})
    elif platform == "douyin":
        hosts = frozenset(
            {
                "douyin.com",
                "www.douyin.com",
                "v.douyin.com",
                "www.iesdouyin.com",
            }
        )
    else:
        raise ValueError("invalid upstream platform")
    return UpstreamHostPolicy(
        platform=platform,
        stage="page",
        exact_hosts=hosts,
        allowed_suffixes=frozenset(),
    )


def extractor_aux_host_policy(
    platform: Literal["bilibili", "douyin", "youtube"],
) -> UpstreamHostPolicy:
    if platform == "youtube":
        hosts = frozenset({"www.youtube.com", "youtubei.googleapis.com"})
    elif platform == "bilibili":
        hosts = frozenset({"api.bilibili.com", "www.bilibili.com"})
    elif platform == "douyin":
        hosts = frozenset({"www.douyin.com"})
    else:
        raise ValueError("invalid upstream platform")
    return UpstreamHostPolicy(
        platform=platform,
        stage="extractor_aux",
        exact_hosts=hosts,
        allowed_suffixes=frozenset(),
    )


@dataclass(frozen=True, slots=True)
class _ExtractedResourceHosts:
    hosts: frozenset[str]


def extracted_resource_hosts(hosts: frozenset[str]) -> _ExtractedResourceHosts:
    """Mark the in-memory host projection produced by a controlled extractor."""

    if not isinstance(hosts, frozenset) or not hosts:
        raise ValueError("controlled extractor hosts must be a non-empty frozenset")
    return _ExtractedResourceHosts(
        frozenset(normalize_host(host) for host in hosts)
    )


def resource_host_policy(
    platform: Literal["bilibili", "douyin", "youtube"],
    extracted_hosts: _ExtractedResourceHosts,
    *,
    expires_at: datetime,
) -> UpstreamHostPolicy:
    """Build an ephemeral exact-host policy from in-memory extractor output."""

    if not isinstance(extracted_hosts, _ExtractedResourceHosts):
        raise TypeError("resource policy requires controlled extractor hosts")
    return UpstreamHostPolicy(
        platform=platform,
        stage="resource",
        exact_hosts=extracted_hosts.hosts,
        allowed_suffixes=frozenset(),
        expires_at=expires_at,
        redirect_suffixes=_RESOURCE_REDIRECT_SUFFIXES[platform],
    )


def _platform_host(host: str) -> bool:
    lowered = host.casefold().rstrip(".")
    return any(lowered == suffix or lowered.endswith("." + suffix) for suffix in _PLATFORM_SUFFIXES)


class SourceUrlPolicy:
    def __init__(self, resolver: Resolver, *, resolve_dns: bool = True) -> None:
        self.resolver = resolver
        self.resolve_dns = resolve_dns

    def validate(self, url: str) -> str:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as error:
            raise UnsafeSourceUrl("source URL is malformed") from error
        if parts.scheme != "https" or port not in {None, 443}:
            raise UnsafeSourceUrl("source URL must use public HTTPS on port 443")
        if parts.username or parts.password or parts.fragment:
            raise UnsafeSourceUrl("source URL contains forbidden components")
        host = parts.hostname
        if not host or not _platform_host(host):
            raise UnsafeSourceUrl("source URL host is not an allowed platform")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise UnsafeSourceUrl("source URL cannot use an IP literal")
        if self.resolve_dns:
            try:
                answers = self.resolver.resolve(host)
            except (KeyError, OSError) as error:
                raise UnsafeSourceUrl("source host could not be resolved") from error
            try:
                public_ip_answers(answers)
            except ValueError as error:
                raise UnsafeSourceUrl(str(error).replace("upstream", "source")) from error
        return url

    def validate_redirect_chain(self, initial_url: str, redirects: list[str]) -> str:
        current = self.validate(initial_url)
        for target in redirects:
            current = self.validate(target)
        return current
