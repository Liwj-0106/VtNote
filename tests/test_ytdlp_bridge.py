from __future__ import annotations

import importlib
import io
from http.cookiejar import Cookie
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yt_dlp.plugins
from yt_dlp.cookies import YoutubeDLCookieJar
from yt_dlp.networking.common import Request
from yt_dlp.networking.exceptions import NoSupportingHandlers, RequestError

from vtnote.platform_transport import SourceHttpRequest
from vtnote.url_security import (
    extracted_resource_hosts,
    extractor_aux_host_policy,
    page_host_policy,
    resource_host_policy,
)
from vtnote.youtube_runtime import YoutubeRuntime, YoutubeRuntimeManifest
from vtnote.ytdlp_bridge import (
    BrowserCookieStore,
    NetscapeCookieFileStore,
    BoundTransportScope,
    VtNoteRequestHandlerRH,
    build_controlled_platform_ytdlp,
    build_controlled_ytdlp,
)


NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)


class NullLogger:
    def error(self, *_: object, **__: object) -> None:
        pass

    def debug(self, *_: object, **__: object) -> None:
        pass

    def stdout(self, *_: object, **__: object) -> None:
        pass


class TrackingBoundedResponse(io.RawIOBase):
    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://www.youtube.com/watch?v=abc",
        status: int = 200,
        headers: dict[str, str] | None = None,
        anonymous_session_cookies: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.url = url
        self.headers = headers or {"Content-Type": "text/plain"}
        self._body = io.BytesIO(body)
        self._anonymous_session_cookies = dict(
            anonymous_session_cookies or {}
        )
        self.close_calls = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        result = self._body.read(size)
        if result == b"":
            self.close()
        return result

    def readline(self, size: int = -1) -> bytes:
        result = self._body.readline(size)
        if result == b"":
            self.close()
        return result

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def close(self) -> None:
        if not self.closed:
            self.close_calls += 1
            self._body.close()
        super().close()

    def anonymous_session_cookies(self) -> dict[str, str]:
        return dict(self._anonymous_session_cookies)


@dataclass
class TransportCall:
    request: SourceHttpRequest
    policy_stage: str
    policy_hosts: frozenset[str]


class FakeTransport:
    def __init__(self, responses: list[TrackingBoundedResponse]) -> None:
        self.responses = responses
        self.calls: list[TransportCall] = []

    def request(self, request: SourceHttpRequest, policy):
        self.calls.append(
            TransportCall(
                request=request,
                policy_stage=policy.stage,
                policy_hosts=policy.exact_hosts,
            )
        )
        return self.responses.pop(0)


def runtime(tmp_path: Path) -> YoutubeRuntime:
    root = tmp_path / "youtube-runtime"
    executable = root / "deno" / "2.8.1" / "deno.exe"
    deno_dir = root / "deno-cache" / "2.8.1"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"deno")
    deno_dir.mkdir(parents=True)
    return YoutubeRuntime(
        manifest=YoutubeRuntimeManifest(
            yt_dlp_version=(2026, 7, 4),
            ejs_version=(0, 8, 0),
            deno_version=(2, 8, 1),
            ejs_package_sha256="1" * 64,
            deno_executable_sha256="2" * 64,
        ),
        runtime_root=root,
        deno_executable=executable,
        deno_dir=deno_dir,
        js_runtimes=("deno",),
        remote_components=frozenset(),
        system_runtime_fallback=False,
    )


def youtube_cookiejar() -> YoutubeDLCookieJar:
    cookiejar = YoutubeDLCookieJar()
    cookiejar.set_cookie(
        Cookie(
            version=0,
            name="SAPISID",
            value="browser-super-secret",
            port=None,
            port_specified=False,
            domain=".youtube.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
    )
    return cookiejar


def probe_scope() -> BoundTransportScope:
    return BoundTransportScope.for_probe(
        page_host_policy("youtube"),
        extractor_aux_host_policy("youtube"),
    )


def bilibili_probe_scope() -> BoundTransportScope:
    return BoundTransportScope.for_probe(
        page_host_policy("bilibili"),
        extractor_aux_host_policy("bilibili"),
    )


def test_only_vtnote_request_handler_is_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DENO_DIR",
        str(tmp_path / "youtube-runtime" / "deno-cache" / "2.8.1"),
    )
    youtube_dl_module = importlib.import_module("yt_dlp.YoutubeDL")
    monkeypatch.setattr(
        youtube_dl_module,
        "load_all_plugins",
        lambda: (_ for _ in ()).throw(AssertionError("plugin discovery called")),
    )
    monkeypatch.setattr(yt_dlp.plugins.all_plugins_loaded, "value", False)
    monkeypatch.setattr(yt_dlp.plugins.plugin_dirs, "value", ["default"])
    bridge = build_controlled_ytdlp(
        FakeTransport([]),
        runtime(tmp_path),
        tmp_path / "output",
        scope=probe_scope(),
    )

    assert set(bridge._request_director.handlers) == {
        "VtNoteRequestHandler"
    }
    assert isinstance(
        bridge._request_director.handlers["VtNoteRequestHandler"],
        VtNoteRequestHandlerRH,
    )
    assert yt_dlp.plugins.plugin_dirs.value == []
    assert yt_dlp.plugins.all_plugins_loaded.value is True


def test_default_urllib_requests_websockets_handlers_are_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_runtime = runtime(tmp_path)
    monkeypatch.setenv("DENO_DIR", str(selected_runtime.deno_dir))
    bridge = build_controlled_ytdlp(
        FakeTransport([]),
        selected_runtime,
        tmp_path / "output",
        scope=probe_scope(),
    )

    keys = {key.casefold() for key in bridge._request_director.handlers}
    assert keys == {"vtnoterequesthandler"}
    assert keys.isdisjoint({"urllib", "requests", "websockets"})


def test_preloaded_third_party_plugins_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yt_dlp.globals

    selected_runtime = runtime(tmp_path)
    monkeypatch.setenv("DENO_DIR", str(selected_runtime.deno_dir))
    monkeypatch.setattr(
        yt_dlp.globals.plugin_ies,
        "value",
        {"InjectedIE": object()},
    )

    with pytest.raises(RuntimeError, match="plugins"):
        build_controlled_ytdlp(
            FakeTransport([]),
            selected_runtime,
            tmp_path / "output",
            scope=probe_scope(),
        )


def test_handler_maps_all_response_read_and_close_shapes() -> None:
    raw = TrackingBoundedResponse(b"one\ntwo\nthree\n")
    transport = FakeTransport([raw])
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
    )

    response = handler.send(
        Request("https://www.youtube.com/watch?v=abc")
    )

    assert response.read(2) == b"on"
    assert response.readline() == b"e\n"
    assert list(response) == [b"two\n", b"three\n"]
    response.close()
    response.close()
    assert raw.close_calls == 1
    assert response.status == 200
    assert response.get_header("Content-Type") == "text/plain"


def test_handler_adds_only_fixed_public_default_headers() -> None:
    transport = FakeTransport([TrackingBoundedResponse(b"ok")])
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
    )

    handler.send(
        Request("https://www.youtube.com/watch?v=abc")
    ).close()

    headers = transport.calls[0].request.headers
    assert set(headers) == {
        "Accept",
        "Accept-Language",
        "Sec-Fetch-Mode",
        "User-Agent",
    }
    assert headers["User-Agent"].startswith("Mozilla/5.0 ")
    assert "Cookie" not in headers
    assert "Authorization" not in headers


def test_handler_scopes_browser_cookie_in_memory_without_public_headers() -> None:
    cookiejar = youtube_cookiejar()
    transport = FakeTransport([TrackingBoundedResponse(b"ok")])
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
        allow_browser_cookies=True,
        cookiejar=cookiejar,
    )

    handler.send(Request("https://www.youtube.com/watch?v=abc")).close()

    sent = transport.calls[0].request
    assert sent.browser_cookie_header == "SAPISID=browser-super-secret"
    assert "Cookie" not in sent.headers
    assert "browser-super-secret" not in repr(sent)


def test_handler_accepts_only_derived_youtube_sid_authorization() -> None:
    transport = FakeTransport([TrackingBoundedResponse(b"{}")])
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
        allow_browser_cookies=True,
        cookiejar=youtube_cookiejar(),
    )
    authorization = f"SAPISIDHASH 1720000000_{'a' * 40}"

    handler.send(
        Request(
            "https://youtubei.googleapis.com/youtubei/v1/player",
            data=b'{}',
            headers={"Authorization": authorization},
        )
    ).close()

    sent = transport.calls[0].request
    assert sent.browser_authorization_header == authorization
    assert sent.browser_cookie_header is None
    assert "Authorization" not in sent.headers
    assert "browser-super-secret" not in repr(sent)


def test_browser_cookie_store_filters_domains_and_returns_fresh_jars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = youtube_cookiejar()
    source.set_cookie(
        Cookie(
            version=0,
            name="unrelated",
            value="must-not-survive",
            port=None,
            port_specified=False,
            domain=".example.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
    )
    monkeypatch.setattr(
        "vtnote.ytdlp_bridge.extract_cookies_from_browser",
        lambda *_args, **_kwargs: source,
    )

    store = BrowserCookieStore("chrome")
    first = store.new_cookiejar()
    second = store.new_cookiejar()

    assert len(source) == 0
    assert first is not second
    assert first.get_cookie_header("https://www.youtube.com/") == (
        "SAPISID=browser-super-secret"
    )
    assert first.get_cookie_header("https://example.com/") is None


def test_netscape_cookie_file_store_is_platform_scoped_and_in_memory(
    tmp_path: Path,
) -> None:
    source = youtube_cookiejar()
    source.set_cookie(
        Cookie(
            version=0,
            name="unrelated",
            value="must-not-survive",
            port=None,
            port_specified=False,
            domain=".example.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
    )
    cookie_file = tmp_path / "private-cookies.txt"
    source.save(cookie_file, ignore_discard=True, ignore_expires=True)

    store = NetscapeCookieFileStore(cookie_file, "youtube")
    first = store.new_cookiejar()
    second = store.new_cookiejar()

    assert first is not second
    assert first.get_cookie_header("https://www.youtube.com/") == (
        "SAPISID=browser-super-secret"
    )
    assert first.get_cookie_header("https://example.com/") is None
    assert "private-cookies" not in repr(store)


def test_netscape_cookie_file_store_rejects_wrong_platform(
    tmp_path: Path,
) -> None:
    source = youtube_cookiejar()
    cookie_file = tmp_path / "private-cookies.txt"
    source.save(cookie_file, ignore_discard=True, ignore_expires=True)

    with pytest.raises(RuntimeError, match="platform cookie file import failed"):
        NetscapeCookieFileStore(cookie_file, "douyin")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/youtubei/v1/player",
        "https://youtubei.googleapis.com/youtubei/v1/player",
    ],
)
def test_handler_allows_only_bounded_youtube_api_posts(url: str) -> None:
    transport = FakeTransport([TrackingBoundedResponse(b"{}")])
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
    )

    handler.send(
        Request(
            url,
            data=b'{"videoId":"abc"}',
            headers={"Content-Type": "application/json"},
        )
    ).close()

    sent = transport.calls[0].request
    assert sent.method == "POST"
    assert sent.body == b'{"videoId":"abc"}'
    assert sent.headers["Content-Type"] == "application/json"


def test_handler_rejects_post_to_non_api_page() -> None:
    transport = FakeTransport([])
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
    )

    with pytest.raises(RequestError):
        handler.send(
            Request(
                "https://www.youtube.com/watch?v=abc",
                data=b"not-an-api-request",
            )
        )

    assert transport.calls == []


def test_bilibili_handler_keeps_anonymous_cookies_in_one_memory_session() -> None:
    transport = FakeTransport(
        [
            TrackingBoundedResponse(
                b"page",
                anonymous_session_cookies={
                    "b_nut": "1234567890",
                    "buvid3": "anonymous-1",
                },
            ),
            TrackingBoundedResponse(b"api"),
        ]
    )
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=bilibili_probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
    )

    handler.send(
        Request("https://www.bilibili.com/video/BV1")
    ).close()
    handler.send(
        Request("https://api.bilibili.com/x/player/pagelist")
    ).close()

    assert transport.calls[0].request.anonymous_session_cookies == {}
    assert transport.calls[1].request.anonymous_session_cookies == {
        "b_nut": "1234567890",
        "buvid3": "anonymous-1",
    }
    handler.close()
    assert handler._anonymous_session_cookies == {}


def test_page_aux_and_ephemeral_resource_host_policies_are_distinct() -> None:
    page = page_host_policy("youtube")
    auxiliary = extractor_aux_host_policy("youtube")
    resource = resource_host_policy(
        "youtube",
        extracted_resource_hosts(
            frozenset({"rr1---sn.example.googlevideo.com"})
        ),
        expires_at=NOW + timedelta(minutes=5),
    )
    probe = BoundTransportScope.for_probe(page, auxiliary)
    fetch = BoundTransportScope.for_resource(resource)

    assert probe.policy_for("https://www.youtube.com/watch?v=abc").stage == "page"
    assert (
        probe.policy_for("https://youtubei.googleapis.com/youtubei/v1/player").stage
        == "extractor_aux"
    )
    with pytest.raises(RequestError):
        probe.policy_for(
            "https://rr1---sn.example.googlevideo.com/videoplayback"
        )
    assert (
        fetch.policy_for(
            "https://rr1---sn.example.googlevideo.com/videoplayback"
        ).stage
        == "resource"
    )
    with pytest.raises(RequestError):
        fetch.policy_for("https://www.youtube.com/watch?v=abc")


def test_extracted_resource_host_is_exact_public_https_only() -> None:
    policy = resource_host_policy(
        "youtube",
        extracted_resource_hosts(
            frozenset({"rr1---sn.example.googlevideo.com"})
        ),
        expires_at=NOW + timedelta(minutes=5),
    )
    scope = BoundTransportScope.for_resource(policy)

    assert scope.policy_for(
        "https://rr1---sn.example.googlevideo.com/videoplayback?token=private"
    ) is policy
    for unsafe in (
        "http://rr1---sn.example.googlevideo.com/videoplayback",
        "https://sub.rr1---sn.example.googlevideo.com/videoplayback",
        "https://127.0.0.1/videoplayback",
        "https://rr2---sn.example.googlevideo.com/videoplayback",
    ):
        with pytest.raises(RequestError) as caught:
            scope.policy_for(unsafe)
        assert "token=private" not in str(caught.value)


@pytest.mark.parametrize(
    "headers",
    [
        {"Cookie": "secret=1"},
        {"Authorization": "Bearer secret"},
        {"Proxy-Authorization": "secret"},
    ],
)
def test_handler_rejects_sensitive_headers_before_transport(
    headers: dict[str, str],
) -> None:
    transport = FakeTransport([])
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
    )
    with pytest.raises(RequestError) as caught:
        handler.send(
            Request(
                "https://www.youtube.com/watch?v=abc&token=query-secret",
                headers=headers,
            )
        )
    assert transport.calls == []
    assert "secret" not in str(caught.value)


def test_unknown_extensions_and_proxies_have_no_default_handler_fallback() -> None:
    transport = FakeTransport([])
    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=transport,
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
    )
    from yt_dlp.networking import RequestDirector

    director = RequestDirector(NullLogger())
    director.add_handler(handler)
    with pytest.raises(NoSupportingHandlers) as extension_error:
        director.send(
            Request(
                "https://www.youtube.com/watch?v=abc",
                extensions={"secret-extension-name": "secret-value"},
            )
        )
    with pytest.raises(NoSupportingHandlers) as proxy_error:
        director.send(
            Request(
                "https://www.youtube.com/watch?v=abc",
                proxies={"https": "http://proxy-secret@127.0.0.1:8080"},
            )
        )
    assert "secret" not in str(extension_error.value)
    assert "secret" not in str(proxy_error.value)
    assert transport.calls == []


def test_unexpected_transport_exception_is_sanitized() -> None:
    class ExplodingTransport:
        def request(self, request: SourceHttpRequest, policy):
            raise OSError("socket credential super-secret")

    handler = VtNoteRequestHandlerRH(
        logger=NullLogger(),
        transport=ExplodingTransport(),  # type: ignore[arg-type]
        scope=probe_scope(),
        max_wire_bytes=100,
        max_decoded_bytes=100,
    )

    with pytest.raises(RequestError) as caught:
        handler.send(Request("https://www.youtube.com/watch?v=abc"))

    assert "super-secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_controlled_builder_uses_static_output_and_runtime_options_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_runtime = runtime(tmp_path)
    monkeypatch.setenv("DENO_DIR", str(selected_runtime.deno_dir))
    output_root = tmp_path / "output"
    bridge = build_controlled_ytdlp(
        FakeTransport([]),
        selected_runtime,
        output_root,
        scope=probe_scope(),
    )

    assert bridge.params["outtmpl"] == {
        "default": str(output_root / "source.%(ext)s")
    }
    assert bridge.params["js_runtimes"] == {
        "deno": {"path": str(selected_runtime.deno_executable)}
    }
    assert bridge.params["extractor_args"] == {
        "youtube": {"skip": ["hls", "dash"]}
    }
    assert bridge.params["remote_components"] == set()
    assert bridge.params["proxy"] == ""
    assert bridge.params["cookiefile"] is None
    assert bridge.params["cookiesfrombrowser"] is None
    assert bridge.params["skip_download"] is True
    assert not any(
        key in bridge.params
        for key in (
            "exec_cmd",
            "external_downloader",
            "postprocessors",
            "update_self",
        )
    )
    with pytest.raises(TypeError):
        build_controlled_ytdlp(  # type: ignore[call-arg]
            FakeTransport([]),
            selected_runtime,
            output_root,
            scope=probe_scope(),
            options={"nocheckcertificate": True},
        )


def test_bilibili_builder_accepts_owned_absolute_output_root(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "bilibili"

    bridge = build_controlled_platform_ytdlp(
        FakeTransport([]),
        output_root,
        scope=bilibili_probe_scope(),
    )

    assert output_root.is_dir()
    assert bridge.params["outtmpl"] == {
        "default": str(output_root / "source.%(ext)s")
    }
    assert bridge.params["js_runtimes"] == {}


def test_builder_accepts_only_preloaded_in_memory_browser_cookies(
    tmp_path: Path,
) -> None:
    cookiejar = YoutubeDLCookieJar()

    bridge = build_controlled_platform_ytdlp(
        FakeTransport([]),
        tmp_path / "douyin",
        scope=BoundTransportScope.for_probe(
            page_host_policy("douyin"),
            extractor_aux_host_policy("douyin"),
        ),
        browser_cookiejar=cookiejar,
    )

    assert bridge.params["cookiefile"] is None
    assert bridge.params["cookiesfrombrowser"] is None
    assert bridge.cookiejar is cookiejar


def test_builder_rejects_browser_cookies_for_bilibili(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not enabled"):
        build_controlled_platform_ytdlp(
            FakeTransport([]),
            tmp_path / "bilibili-cookie",
            scope=bilibili_probe_scope(),
            browser_cookiejar=YoutubeDLCookieJar(),
        )


def test_probe_forces_download_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_runtime = runtime(tmp_path)
    monkeypatch.setenv("DENO_DIR", str(selected_runtime.deno_dir))
    bridge = build_controlled_ytdlp(
        FakeTransport([]),
        selected_runtime,
        tmp_path / "output",
        scope=probe_scope(),
    )
    calls: list[tuple[str, bool]] = []

    def extract_info(url: str, *, download: bool):
        calls.append((url, download))
        return {"id": "abc", "title": "Example"}

    monkeypatch.setattr(bridge, "extract_info", extract_info)

    assert bridge.probe("https://www.youtube.com/watch?v=abc") == {
        "id": "abc",
        "title": "Example",
    }
    assert calls == [
        ("https://www.youtube.com/watch?v=abc", False)
    ]


def test_builder_rejects_wrong_deno_dir_without_mutating_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_runtime = runtime(tmp_path)
    monkeypatch.setenv("DENO_DIR", str(tmp_path / "wrong"))
    before = dict(__import__("os").environ)

    with pytest.raises(ValueError, match="DENO_DIR"):
        build_controlled_ytdlp(
            FakeTransport([]),
            selected_runtime,
            tmp_path / "output",
            scope=probe_scope(),
        )

    assert dict(__import__("os").environ) == before
