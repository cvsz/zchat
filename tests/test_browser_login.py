"""Offline tests for the browser-login harvester (fake CDP server)."""

import base64
import hashlib
import http.server
import json
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import browser_login as bl  # noqa: E402

_COOKIES = [
    {"name": "PHPSESSID", "value": "abc123", "domain": ".camfrog.com"},
    {"name": "lang", "value": "th", "domain": "profiles.camfrog.com"},
    {"name": "_ga", "value": "x", "domain": ".google.com"},
]


def _frame(payload):
    n = len(payload)
    head = bytearray([0x81])
    if n < 126:
        head.append(n)
    elif n < 1 << 16:
        head.append(126)
        head += n.to_bytes(2, "big")
    else:
        head.append(127)
        head += n.to_bytes(8, "big")
    return bytes(head) + payload


@pytest.fixture
def fake_cdp():
    """A CDP-shaped WebSocket server that answers Network.getAllCookies."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    received = []

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        try:
            req = b""
            while b"\r\n\r\n" not in req:
                block = conn.recv(4096)
                if not block:
                    return
                req += block
            key = [
                ln.split(b": ", 1)[1]
                for ln in req.split(b"\r\n")
                if b"Sec-WebSocket-Key" in ln
            ][0]
            accept = base64.b64encode(
                hashlib.sha1(
                    key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11", usedforsecurity=False
                ).digest()
            )
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
            )
            raw = conn.recv(65536)
            length = raw[1] & 0x7F
            mask = raw[2:6]
            received.append(
                json.loads(
                    bytes(b ^ mask[i % 4] for i, b in enumerate(raw[6 : 6 + length]))
                )
            )
            conn.sendall(
                _frame(json.dumps({"id": 1, "result": {"cookies": _COOKIES}}).encode())
            )
            conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return SimpleNamespace(port=srv.getsockname()[1], received=received)


def _connect(fake_cdp):
    return bl._WebSocket("ws://127.0.0.1:{0}/devtools/page/1".format(fake_cdp.port))


def test_websocket_sends_cdp_request_and_parses_reply(fake_cdp):
    ws = _connect(fake_cdp)
    message = bl._cdp_call(ws, "Network.getAllCookies")
    assert fake_cdp.received == [{"id": 1, "method": "Network.getAllCookies"}]
    assert any(
        c["name"] == "PHPSESSID" for c in message.get("result", {}).get("cookies", [])
    )
    ws.close()


def test_cookies_are_filtered_to_camfrog(fake_cdp):
    ws = _connect(fake_cdp)
    message = bl._cdp_call(ws, "Network.getAllCookies")
    text = bl._cookies_to_string(message.get("result", {}).get("cookies", []))
    assert "PHPSESSID=abc123" in text
    assert "lang=th" in text
    assert "_ga" not in text, "foreign-domain cookies must be dropped"
    ws.close()


def test_cookies_without_camfrog_raises():
    with pytest.raises(ValueError):
        bl._cookies_to_string([{"name": "_ga", "value": "x", "domain": ".google.com"}])


def test_find_browser_returns_first_available_candidate(monkeypatch):
    monkeypatch.setattr(bl, "BROWSER_CANDIDATES", ("first.exe", "second.exe"))
    monkeypatch.setattr(bl.Path, "is_file", lambda path: path.name == "second.exe")

    assert bl.find_browser() == "second.exe"


def test_find_browser_returns_none_when_no_candidate_exists(monkeypatch):
    monkeypatch.setattr(bl, "BROWSER_CANDIDATES", ("first.exe", "second.exe"))
    monkeypatch.setattr(bl.Path, "is_file", lambda _path: False)

    assert bl.find_browser() is None


def test_websocket_rejects_non_ws_urls():
    with pytest.raises(ValueError):
        bl._WebSocket("http://example.test")


@pytest.mark.parametrize(
    "url",
    [
        "ws://example.test:9222/devtools/page/1",
        "ws://127.0.0.1.evil.test:9222/devtools/page/1",
        "ws://user@127.0.0.1:9222/devtools/page/1",
    ],
)
def test_websocket_rejects_non_loopback_targets_before_connect(url, monkeypatch):
    monkeypatch.setattr(
        bl.socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("untrusted target must not connect"),
    )
    with pytest.raises(ValueError):
        bl._WebSocket(url)


def test_find_page_target_rejects_remote_websocket(monkeypatch):
    monkeypatch.setattr(
        bl,
        "_http_json",
        lambda _url: [
            {
                "type": "page",
                "webSocketDebuggerUrl": "ws://evil.example:9222/devtools/page/1",
            }
        ],
    )
    with pytest.raises(OSError, match="no browser page target"):
        bl._find_page_target(9222)


def test_websocket_handshake_keeps_early_frame_bytes():
    key = b"dGhlIHNhbXBsZSBub25jZQ=="
    accept = base64.b64encode(
        hashlib.sha1(
            key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11", usedforsecurity=False
        ).digest()
    )
    frame = b"\x81\x02{}"

    class FakeSocket:
        def recv(self, _size):
            return (
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n" + frame
            )

    ws = bl._WebSocket.__new__(bl._WebSocket)
    ws.sock = FakeSocket()
    ws.buf = b""
    ws._read_http_headers(key)
    assert ws.buf == frame


def test_websocket_rejects_invalid_accept_header():
    class FakeSocket:
        def recv(self, _size):
            return (
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: invalid\r\n\r\n"
            )

    ws = bl._WebSocket.__new__(bl._WebSocket)
    ws.sock = FakeSocket()
    ws.buf = b""
    with pytest.raises(OSError, match="handshake refused"):
        ws._read_http_headers(b"dGhlIHNhbXBsZSBub25jZQ==")


def test_local_devtools_http_does_not_follow_redirects():
    redirects = []

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            redirects.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/collect")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with pytest.raises(OSError, match="HTTP 302"):
            bl._http_json("http://127.0.0.1:{0}/json/list".format(port))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert redirects == ["/json/list"]


def test_browser_login_removes_temporary_profile_and_closes_browser(
    tmp_path, monkeypatch
):
    profile = tmp_path / "temporary-login-profile"

    class FakeProcess:
        pid = 43210

    class FakeWebSocket:
        closed = False

        def close(self):
            self.closed = True

    process, websocket = FakeProcess(), FakeWebSocket()
    closed_processes = []
    monkeypatch.setattr(bl, "find_browser", lambda: "fake-browser")
    monkeypatch.setattr(bl, "_free_port", lambda: 43211)
    monkeypatch.setattr(bl.tempfile, "mkdtemp", lambda **_kwargs: str(profile))
    monkeypatch.setattr(bl.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(
        bl, "_close_browser_process", lambda child: closed_processes.append(child)
    )
    monkeypatch.setattr(bl, "_find_page_target", lambda _port: "ws://127.0.0.1:43211/x")
    monkeypatch.setattr(bl, "_WebSocket", lambda _url: websocket)
    monkeypatch.setattr(
        bl, "_cdp_call", lambda *_args: {"result": {"cookies": _COOKIES}}
    )

    cookies, retained_profile = bl.browser_login(timeout=1)

    assert "PHPSESSID=abc123" in cookies
    assert retained_profile is None
    assert not profile.exists()
    assert websocket.closed
    assert closed_processes == [process]


def test_browser_login_can_keep_explicitly_requested_profile(tmp_path, monkeypatch):
    profile = tmp_path / "retained-login-profile"
    process = SimpleNamespace(pid=43212)
    websocket = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(bl, "find_browser", lambda: "fake-browser")
    monkeypatch.setattr(bl, "_free_port", lambda: 43213)
    monkeypatch.setattr(bl.tempfile, "mkdtemp", lambda **_kwargs: str(profile))
    monkeypatch.setattr(bl.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(
        bl,
        "_close_browser_process",
        lambda _child: pytest.fail("browser should remain open"),
    )
    monkeypatch.setattr(bl, "_find_page_target", lambda _port: "ws://127.0.0.1:43213/x")
    monkeypatch.setattr(bl, "_WebSocket", lambda _url: websocket)
    monkeypatch.setattr(
        bl, "_cdp_call", lambda *_args: {"result": {"cookies": _COOKIES}}
    )

    _cookies, retained_profile = bl.browser_login(timeout=1, keep_browser=True)

    assert retained_profile == str(profile)
    assert profile.is_dir()


@pytest.mark.parametrize(
    "domain, accepted",
    [
        ("camfrog.com", True),
        (".camfrog.com", True),
        ("profiles.camfrog.com", True),
        ("PROFILES.CAMFROG.COM", True),
        ("evilcamfrog.com", False),
        ("camfrog.com.evil.example", False),
        ("foo.camfrog.com.evil.example", False),
        ("camfrog.comevil", False),
        ("camfrog.com.", False),
        ("", False),
        (None, False),
    ],
)
def test_cookie_domain_boundary(domain, accepted):
    assert bl._is_camfrog_cookie_domain(domain) is accepted


def test_cookie_serialization_rejects_suffix_spoofing():
    cookies = [
        {"domain": "evilcamfrog.com", "name": "PHPSESSID", "value": "spoof"},
        {"domain": "profiles.camfrog.com", "name": "PHPSESSID", "value": "real"},
    ]
    assert bl._cookies_to_string(cookies) == "PHPSESSID=real"
