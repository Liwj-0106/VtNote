"""Strict source URL validation independent from provider base URL policy."""

from __future__ import annotations

import ipaddress
import socket
from typing import Protocol
from urllib.parse import urlsplit


class UnsafeSourceUrl(ValueError):
    pass


class Resolver(Protocol):
    def resolve(self, host: str) -> list[str]: ...


class SocketResolver:
    def resolve(self, host: str) -> list[str]:
        return sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


_PLATFORM_SUFFIXES = ("youtube.com", "youtu.be", "bilibili.com", "b23.tv")


def _platform_host(host: str) -> bool:
    lowered = host.casefold().rstrip(".")
    return any(lowered == suffix or lowered.endswith("." + suffix) for suffix in _PLATFORM_SUFFIXES)


class SourceUrlPolicy:
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

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
        try:
            answers = self.resolver.resolve(host)
        except (KeyError, OSError) as error:
            raise UnsafeSourceUrl("source host could not be resolved") from error
        if not answers:
            raise UnsafeSourceUrl("source host could not be resolved")
        for answer in answers:
            try:
                address = ipaddress.ip_address(answer)
            except ValueError as error:
                raise UnsafeSourceUrl("source host returned an invalid address") from error
            if (
                not address.is_global
                or address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise UnsafeSourceUrl("source host must resolve only to public addresses")
        return url

    def validate_redirect_chain(self, initial_url: str, redirects: list[str]) -> str:
        current = self.validate(initial_url)
        for target in redirects:
            current = self.validate(target)
        return current
