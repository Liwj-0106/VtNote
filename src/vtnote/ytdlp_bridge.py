"""Controlled yt-dlp bridge using only VtNote's pinned HTTPS transport."""

from __future__ import annotations

import io
import os
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import yt_dlp
import yt_dlp.globals
import yt_dlp.plugins
from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser
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
    is_youtube_browser_authorization,
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
_PLATFORM_COOKIE_DOMAINS = {
    "douyin": ("douyin.com", "iesdouyin.com"),
    "youtube": ("youtube.com",),
}
_BROWSER_COOKIE_DOMAINS = tuple(
    domain
    for domains in _PLATFORM_COOKIE_DOMAINS.values()
    for domain in domains
)
_MAX_COOKIE_FILE_BYTES = 8 * 1024 * 1024


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


class _BrowserCookieLogger:
    def debug(self, *_: object, **__: object) -> None:
        pass

    def info(self, *_: object, **__: object) -> None:
        pass

    def warning(self, *_: object, **__: object) -> None:
        pass

    def error(self, *_: object, **__: object) -> None:
        pass


class PlatformCookieStore(Protocol):
    """Return a new in-memory cookie jar for one controlled operation."""

    def new_cookiejar(self) -> YoutubeDLCookieJar: ...


def _copy_allowed_cookies(
    source: YoutubeDLCookieJar,
    domains: tuple[str, ...],
) -> tuple[Any, ...]:
    return tuple(
        copy(cookie)
        for cookie in source
        if any(
            cookie.domain.lstrip(".").casefold() == domain
            or cookie.domain.lstrip(".").casefold().endswith(f".{domain}")
            for domain in domains
        )
    )


class BrowserCookieStore:
    """One startup-only, domain-filtered browser cookie snapshot in memory."""

    def __init__(self, browser: str) -> None:
        if browser not in {"chrome", "edge", "firefox"}:
            raise ValueError("unsupported platform cookie browser")
        try:
            source = extract_cookies_from_browser(
                browser,
                logger=_BrowserCookieLogger(),
            )
        except Exception:
            raise RuntimeError("browser cookie import failed") from None
        self.browser = browser
        self._cookies = _copy_allowed_cookies(source, _BROWSER_COOKIE_DOMAINS)
        source.clear()

    def new_cookiejar(self) -> YoutubeDLCookieJar:
        cookiejar = YoutubeDLCookieJar()
        for cookie in self._cookies:
            cookiejar.set_cookie(copy(cookie))
        return cookiejar


class NetscapeCookieFileStore:
    """Startup-only, platform-scoped snapshot of an exported cookie file."""

    def __init__(
        self,
        cookie_file: Path,
        platform: Literal["douyin", "youtube"],
    ) -> None:
        if platform not in _PLATFORM_COOKIE_DOMAINS:
            raise ValueError("unsupported cookie file platform")
        path = Path(cookie_file)
        try:
            if not path.is_absolute() or not path.is_file():
                raise OSError
            size = path.stat().st_size
            if size <= 0 or size > _MAX_COOKIE_FILE_BYTES:
                raise OSError
            source = YoutubeDLCookieJar(str(path))
            source.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            raise RuntimeError("platform cookie file import failed") from None
        self._cookies = _copy_allowed_cookies(
            source,
            _PLATFORM_COOKIE_DOMAINS[platform],
        )
        source.clear()
        if not self._cookies:
            raise RuntimeError("platform cookie file import failed")

    def new_cookiejar(self) -> YoutubeDLCookieJar:
        cookiejar = YoutubeDLCookieJar()
        for cookie in self._cookies:
            cookiejar.set_cookie(copy(cookie))
        return cookiejar


def _safe_request_error() -> RequestError:
    return RequestError("request rejected by controlled transport scope")


def _allows_controlled_post(policy: UpstreamHostPolicy, url: str) -> bool:
    if policy.platform != "youtube":
        return False
    try:
        parts = urlsplit(url)
        host = normalize_host(parts.hostname or "")
    except (TypeError, ValueError):
        return False
    if policy.stage == "extractor_aux":
        return True
    return (
        policy.stage == "page"
        and host == "www.youtube.com"
        and parts.path.startswith("/youtubei/v1/")
    )


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
        allow_browser_cookies: bool = False,
        cookiejar: Any = None,
        **_: Any,
    ) -> None:
        super().__init__(
            logger=logger,
            headers={},
            cookiejar=cookiejar,
            proxies={},
            verify=True,
        )
        self._transport = transport
        self._scope = scope
        self._max_wire_bytes = max_wire_bytes
        self._max_decoded_bytes = max_decoded_bytes
        self._allow_browser_cookies = allow_browser_cookies
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
        if request.method not in {"GET", "HEAD", "POST"}:
            raise _safe_request_error()
        if request.extensions or request.proxies:
            raise _safe_request_error()
        headers = controlled_public_headers()
        headers.update(request.headers)
        policy = self._scope.policy_for(request.url)
        browser_authorization: str | None = None
        for name in tuple(headers):
            if name.casefold() != "authorization":
                continue
            browser_authorization = headers.pop(name)
        if any(
            name.casefold() in _SENSITIVE_HEADERS
            or name.casefold().startswith("proxy-")
            for name in headers
        ):
            raise _safe_request_error()
        body: bytes | None = None
        if request.method == "POST":
            if not _allows_controlled_post(policy, request.url) or not isinstance(
                request.data,
                bytes,
            ) or not request.data:
                raise _safe_request_error()
            body = request.data
        elif request.data is not None:
            raise _safe_request_error()
        if browser_authorization is not None and (
            not self._allow_browser_cookies
            or policy.platform != "youtube"
            or request.method != "POST"
            or not _allows_controlled_post(policy, request.url)
            or not is_youtube_browser_authorization(browser_authorization)
        ):
            raise _safe_request_error()
        source_request = SourceHttpRequest(
            url=request.url,
            method=request.method,
            body=body,
            headers=headers,
            max_wire_bytes=self._max_wire_bytes,
            max_decoded_bytes=self._max_decoded_bytes,
            browser_cookie_header=(
                self.cookiejar.get_cookie_header(request.url)
                if self._allow_browser_cookies
                else None
            ),
            browser_authorization_header=browser_authorization,
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
            url=bounded.url,
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
        browser_cookiejar: YoutubeDLCookieJar | None = None,
    ) -> None:
        self._vtnote_transport = transport
        self._vtnote_scope = scope
        self._browser_cookies_enabled = browser_cookiejar is not None
        if browser_cookiejar is not None:
            self.__dict__["cookiejar"] = browser_cookiejar
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
                allow_browser_cookies=(
                    self._browser_cookies_enabled
                ),
                cookiejar=self.cookiejar,
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
        or not runtime.deno_executable.is_absolute()
        or not runtime.deno_executable.is_file()
        or not runtime.deno_dir.is_absolute()
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
    if not root.is_absolute():
        raise ValueError("yt-dlp output root must be absolute")
    if _has_reparse_component(root):
        raise ValueError("yt-dlp output root contains a reparse point")
    resolved = root.resolve(strict=False)
    if not resolved.is_absolute():
        raise ValueError("yt-dlp output root must resolve to an absolute path")
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_controlled_ytdlp(
    transport: PinnedHttpsTransport,
    runtime: YoutubeRuntime,
    output_root: Path,
    *,
    scope: BoundTransportScope,
    browser_cookiejar: YoutubeDLCookieJar | None = None,
) -> VtNoteYoutubeDL:
    return build_controlled_platform_ytdlp(
        transport,
        output_root,
        scope=scope,
        runtime=runtime,
        browser_cookiejar=browser_cookiejar,
    )


def build_controlled_platform_ytdlp(
    transport: PinnedHttpsTransport,
    output_root: Path,
    *,
    scope: BoundTransportScope,
    runtime: YoutubeRuntime | None = None,
    browser_cookiejar: YoutubeDLCookieJar | None = None,
) -> VtNoteYoutubeDL:
    """Build the same controlled bridge with optional YouTube JS support."""

    platform = scope.policies[0].platform
    if browser_cookiejar is not None and platform not in {"douyin", "youtube"}:
        raise ValueError("browser cookies are not enabled for this platform")
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
        params["extractor_args"] = {
            "youtube": {"skip": ["hls", "dash"]}
        }
    else:
        params["js_runtimes"] = {}
    return VtNoteYoutubeDL(
        transport=transport,
        scope=scope,
        params=params,
        browser_cookiejar=browser_cookiejar,
    )
