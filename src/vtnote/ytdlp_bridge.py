"""Controlled yt-dlp bridge using only VtNote's pinned HTTPS transport."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yt_dlp
import yt_dlp.globals
import yt_dlp.plugins
from yt_dlp.networking import RequestDirector
from yt_dlp.networking.common import Request, RequestHandler, Response
from yt_dlp.networking.exceptions import (
    RequestError,
    TransportError,
    UnsupportedRequest,
)
from yt_dlp.utils.networking import std_headers

from vtnote.platform_transport import (
    PinnedHttpsTransport,
    SourceHttpRequest,
)
from vtnote.url_security import UpstreamHostPolicy, normalize_host
from vtnote.youtube_runtime import YoutubeRuntime


_SENSITIVE_HEADERS = frozenset({"authorization", "cookie"})
_CONTROLLED_DEFAULT_HEADERS = {
    name: str(std_headers[name])
    for name in (
        "User-Agent",
        "Accept",
        "Accept-Language",
        "Sec-Fetch-Mode",
    )
}
_REPARSE_POINT = 0x400


def controlled_public_headers(*, referer: str | None = None) -> dict[str, str]:
    """Return the fixed non-secret headers allowed for platform requests."""

    headers = dict(_CONTROLLED_DEFAULT_HEADERS)
    if referer is None:
        return headers
    try:
        parts = urlsplit(referer)
        port = parts.port
    except (TypeError, ValueError):
        raise ValueError("invalid controlled request referer") from None
    if (
        parts.scheme.casefold() != "https"
        or not parts.hostname
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("invalid controlled request referer")
    headers["Referer"] = referer
    return headers


class _BridgeLogger:
    def error(self, *_: object, **__: object) -> None:
        pass

    def debug(self, *_: object, **__: object) -> None:
        pass

    def stdout(self, *_: object, **__: object) -> None:
        pass


def _safe_request_error() -> RequestError:
    return RequestError("request rejected by controlled transport scope")


@dataclass(frozen=True, slots=True)
class BoundTransportScope:
    policies: tuple[UpstreamHostPolicy, ...]
    max_wire_bytes: int
    max_decoded_bytes: int

    def __post_init__(self) -> None:
        if not self.policies:
            raise ValueError("bound transport scope requires policies")
        if type(self.max_wire_bytes) is not int or self.max_wire_bytes <= 0:
            raise ValueError("bound wire limit must be positive")
        if (
            type(self.max_decoded_bytes) is not int
            or self.max_decoded_bytes <= 0
        ):
            raise ValueError("bound decoded limit must be positive")
        platforms = {policy.platform for policy in self.policies}
        if len(platforms) != 1:
            raise ValueError("bound policies must use one platform")

    @classmethod
    def for_probe(
        cls,
        page: UpstreamHostPolicy,
        auxiliary: UpstreamHostPolicy,
    ) -> BoundTransportScope:
        if page.stage != "page" or auxiliary.stage != "extractor_aux":
            raise ValueError("probe scope requires page and extractor policies")
        return cls(
            policies=(page, auxiliary),
            max_wire_bytes=32 * 1024 * 1024,
            max_decoded_bytes=64 * 1024 * 1024,
        )

    @classmethod
    def for_resource(
        cls,
        resource: UpstreamHostPolicy,
        *,
        max_wire_bytes: int = 2 * 1024 * 1024 * 1024,
        max_decoded_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> BoundTransportScope:
        if resource.stage != "resource":
            raise ValueError("resource scope requires an ephemeral resource policy")
        return cls(
            policies=(resource,),
            max_wire_bytes=max_wire_bytes,
            max_decoded_bytes=max_decoded_bytes,
        )

    def policy_for(self, url: str) -> UpstreamHostPolicy:
        try:
            parts = urlsplit(url)
            port = parts.port
            host = normalize_host(parts.hostname or "")
        except (TypeError, ValueError):
            raise _safe_request_error() from None
        if (
            parts.scheme.casefold() != "https"
            or port not in {None, 443}
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise _safe_request_error()
        for policy in self.policies:
            if policy.allows(host):
                return policy
        raise _safe_request_error()


class _BoundedResponseAdapter(io.RawIOBase):
    def __init__(self, response: Any) -> None:
        super().__init__()
        self._response = response

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        if self.closed:
            raise ValueError("response is closed")
        content = self._response.read(-1 if size is None else size)
        if content == b"":
            self.close()
        return content

    def readline(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("response is closed")
        content = self._response.readline(size)
        if content == b"":
            self.close()
        return content

    def __iter__(self) -> _BoundedResponseAdapter:
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def close(self) -> None:
        if not self.closed:
            self._response.close()
        super().close()


class VtNoteRequestHandlerRH(RequestHandler):
    _SUPPORTED_URL_SCHEMES = ("https",)
    _SUPPORTED_PROXY_SCHEMES = ()
    _SUPPORTED_FEATURES = ()

    def __init__(
        self,
        *,
        logger: Any,
        transport: PinnedHttpsTransport,
        scope: BoundTransportScope,
        max_wire_bytes: int,
        max_decoded_bytes: int,
        **_: Any,
    ) -> None:
        super().__init__(
            logger=logger,
            headers={},
            cookiejar=None,
            proxies={},
            verify=True,
        )
        self._transport = transport
        self._scope = scope
        self._max_wire_bytes = max_wire_bytes
        self._max_decoded_bytes = max_decoded_bytes
        self._anonymous_session_cookies: dict[str, str] = {}

    def _validate(self, request: Request) -> None:
        try:
            scheme = urlsplit(request.url).scheme.casefold()
        except (TypeError, ValueError):
            scheme = ""
        if (
            scheme != "https"
            or request.extensions
            or request.proxies
        ):
            raise UnsupportedRequest(
                "unsupported controlled request",
                handler=self,
            )

    def _send(self, request: Request) -> Response:
        if request.data is not None or request.method not in {"GET", "HEAD"}:
            raise _safe_request_error()
        if request.extensions or request.proxies:
            raise _safe_request_error()
        headers = controlled_public_headers()
        headers.update(request.headers)
        if any(
            name.casefold() in _SENSITIVE_HEADERS
            or name.casefold().startswith("proxy-")
            for name in headers
        ):
            raise _safe_request_error()
        policy = self._scope.policy_for(request.url)
        source_request = SourceHttpRequest(
            url=request.url,
            method=request.method,
            headers=headers,
            max_wire_bytes=self._max_wire_bytes,
            max_decoded_bytes=self._max_decoded_bytes,
            anonymous_session_cookies=(
                self._anonymous_session_cookies
                if policy.platform == "bilibili"
                else {}
            ),
        )
        try:
            bounded = self._transport.request(source_request, policy)
        except Exception:
            raise TransportError(
                "controlled HTTPS request failed",
                handler=self,
            ) from None
        if policy.platform == "bilibili":
            received = getattr(
                bounded,
                "anonymous_session_cookies",
                None,
            )
            if callable(received):
                self._anonymous_session_cookies.update(received())
        adapter = _BoundedResponseAdapter(bounded)
        return Response(
            adapter,
            url=request.url,
            headers=bounded.headers,
            status=bounded.status,
            extensions={},
        )

    def close(self) -> None:
        self._anonymous_session_cookies.clear()
        super().close()


class VtNoteYoutubeDL(yt_dlp.YoutubeDL):
    def __init__(
        self,
        *,
        transport: PinnedHttpsTransport,
        scope: BoundTransportScope,
        params: dict[str, Any],
    ) -> None:
        self._vtnote_transport = transport
        self._vtnote_scope = scope
        controlled_outtmpl = dict(params["outtmpl"])
        super().__init__(params=params, auto_init="no_verbose_header")
        self.params["outtmpl"] = controlled_outtmpl

    def build_request_director(
        self,
        handlers: Any,
        preferences: Any = None,
    ) -> RequestDirector:
        director = RequestDirector(logger=_BridgeLogger(), verbose=False)
        director.add_handler(
            VtNoteRequestHandlerRH(
                logger=_BridgeLogger(),
                transport=self._vtnote_transport,
                scope=self._vtnote_scope,
                max_wire_bytes=self._vtnote_scope.max_wire_bytes,
                max_decoded_bytes=self._vtnote_scope.max_decoded_bytes,
            )
        )
        return director

    def probe(self, url: str) -> dict[str, Any]:
        result = self.extract_info(url, download=False)
        if not isinstance(result, dict):
            raise RuntimeError("controlled platform probe returned invalid data")
        return result


def _disable_default_plugin_discovery() -> None:
    if (
        yt_dlp.globals.plugin_ies.value
        or yt_dlp.globals.plugin_pps.value
    ):
        raise RuntimeError("yt-dlp plugins were loaded before controlled startup")
    yt_dlp.plugins.plugin_dirs.value = []
    yt_dlp.plugins.all_plugins_loaded.value = True


def _has_reparse_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if not current.exists() and not current.is_symlink():
            continue
        try:
            metadata = os.lstat(current)
        except OSError:
            return True
        if current.is_symlink() or (
            getattr(metadata, "st_file_attributes", 0)
            & _REPARSE_POINT
        ):
            return True
    return False


def _validate_managed_runtime(runtime: YoutubeRuntime) -> None:
    root = runtime.runtime_root.resolve(strict=False)
    executable = runtime.deno_executable.resolve(strict=False)
    deno_dir = runtime.deno_dir.resolve(strict=False)
    try:
        executable.relative_to(root)
        deno_dir.relative_to(root)
    except ValueError:
        contained = False
    else:
        contained = True
    if (
        runtime.js_runtimes != ("deno",)
        or runtime.remote_components
        or runtime.system_runtime_fallback
        or not runtime.runtime_root.is_absolute()
        or runtime.runtime_root.drive.casefold() != "d:"
        or not runtime.deno_executable.is_absolute()
        or runtime.deno_executable.drive.casefold() != "d:"
        or not runtime.deno_executable.is_file()
        or not runtime.deno_dir.is_absolute()
        or runtime.deno_dir.drive.casefold() != "d:"
        or not runtime.deno_dir.is_dir()
        or not contained
        or _has_reparse_component(runtime.runtime_root)
        or _has_reparse_component(runtime.deno_executable)
        or _has_reparse_component(runtime.deno_dir)
    ):
        raise ValueError("managed YouTube runtime is not ready")
    if os.environ.get("DENO_DIR") != str(runtime.deno_dir):
        raise ValueError("DENO_DIR does not match the managed YouTube runtime")


def _validate_output_root(output_root: Path) -> Path:
    root = Path(output_root)
    if not root.is_absolute() or root.drive.casefold() != "d:":
        raise ValueError("yt-dlp output root must be an absolute D-drive path")
    if _has_reparse_component(root):
        raise ValueError("yt-dlp output root contains a reparse point")
    resolved = root.resolve(strict=False)
    if resolved.drive.casefold() != "d:":
        raise ValueError("yt-dlp output root escapes the approved drive")
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_controlled_ytdlp(
    transport: PinnedHttpsTransport,
    runtime: YoutubeRuntime,
    output_root: Path,
    *,
    scope: BoundTransportScope,
) -> VtNoteYoutubeDL:
    return build_controlled_platform_ytdlp(
        transport,
        output_root,
        scope=scope,
        runtime=runtime,
    )


def build_controlled_platform_ytdlp(
    transport: PinnedHttpsTransport,
    output_root: Path,
    *,
    scope: BoundTransportScope,
    runtime: YoutubeRuntime | None = None,
) -> VtNoteYoutubeDL:
    """Build the same controlled bridge with optional YouTube JS support."""

    if runtime is not None:
        _validate_managed_runtime(runtime)
    selected_output = _validate_output_root(output_root)
    _disable_default_plugin_discovery()
    params: dict[str, Any] = {
        "cachedir": False,
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "http_headers": {},
        "noplaylist": True,
        "outtmpl": {
            "default": str(selected_output / "source.%(ext)s")
        },
        "paths": {
            "home": str(selected_output),
            "temp": str(selected_output),
        },
        "proxy": "",
        "quiet": True,
        "remote_components": set(),
        "simulate": True,
        "skip_download": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }
    if runtime is not None:
        params["js_runtimes"] = {
            "deno": {"path": str(runtime.deno_executable)}
        }
    else:
        params["js_runtimes"] = {}
    return VtNoteYoutubeDL(
        transport=transport,
        scope=scope,
        params=params,
    )
