"""Log in through the real browser and harvest the session cookie (stdlib only).

Camfrog's login page answers `captcha` to scripted POSTs and stores its
session cookie as HttpOnly (so copying it out of devtools is the only manual
way). This module drives a private Chrome/Edge profile over the DevTools
Protocol: the user logs in as usual - CAPTCHA included - and the resulting
cookies (including HttpOnly) are read back.

Flow: spawn <browser> --user-data-dir=<temp> --remote-debugging-port=PORT
with the login URL in a new window, poll /json/list for the page target, open
a DevTools WebSocket, wait until the camfrog session cookie appears, then read
all cookies and return them as `name=value; ...` for cookie_from_paste().

No network beyond localhost and camfrog.com. No third-party packages.
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path


def system_taskkill_path():
    """Return taskkill.exe from Windows' system directory, never from PATH/cwd."""
    if os.name != "nt":
        return "taskkill.exe"
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_system_directory = kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = (ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint)
    get_system_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_system_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return os.path.join(buffer.value, "taskkill.exe")

BROWSER_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)
LOGIN_PAGE = "https://www.camfrog.com/th/login.php"
SESSION_COOKIE = "PHPSESSID"
WAIT_TIMEOUT = 300.0  # seconds the user gets to finish logging in


def find_browser():
    for path in BROWSER_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def _close_browser_process(process):
    """Close the dedicated login browser process tree without touching other browsers."""
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    [system_taskkill_path(), "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------- devtools ws
class _WebSocket:
    """Minimal RFC 6455 client: one connection, text frames, no extensions."""

    def __init__(self, url):
        try:
            endpoint = urllib.parse.urlsplit(url)
            port = endpoint.port
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid local DevTools websocket URL") from exc
        host = (endpoint.hostname or "").casefold()
        if (endpoint.scheme.casefold() != "ws"
                or host not in {"127.0.0.1", "localhost", "::1"}
                or not port or endpoint.username or endpoint.password):
            raise ValueError("only loopback ws:// DevTools URLs are supported")
        self.sock = socket.create_connection((host, port), timeout=10)
        self.buf = b""
        key = base64.b64encode(os.urandom(16))
        path = endpoint.path or "/"
        if endpoint.query:
            path += "?" + endpoint.query
        handshake = (
            "GET {path} HTTP/1.1\r\n"
            "Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).format(path=path, host_header=endpoint.netloc, key=key.decode())
        try:
            self.sock.sendall(handshake.encode())
            self._read_http_headers(key)
        except Exception:
            self.close()
            raise

    def _read_http_headers(self, key):
        data = b""
        while b"\r\n\r\n" not in data:
            block = self.sock.recv(4096)
            if not block:
                raise OSError("devtools websocket closed during handshake")
            data += block
            if len(data) > 65536:
                raise OSError("devtools websocket handshake headers are too large")
        head, self.buf = data.split(b"\r\n\r\n", 1)
        lines = head.decode("iso-8859-1").split("\r\n")
        status = lines[0].split()
        headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().casefold()] = value.strip()
        connection_tokens = {
            token.strip().casefold()
            for token in headers.get("connection", "").split(",")
        }
        expected_accept = base64.b64encode(hashlib.sha1(
            key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
            usedforsecurity=False).digest()).decode("ascii")
        if (len(status) < 2 or status[1] != "101"
                or headers.get("upgrade", "").casefold() != "websocket"
                or "upgrade" not in connection_tokens
                or not hmac.compare_digest(
                    headers.get("sec-websocket-accept", ""), expected_accept)):
            raise OSError("devtools websocket handshake refused")

    def send_text(self, text):
        payload = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += n.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, "big")
        self.sock.sendall(bytes(header) + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_frame(self):
        while True:
            if len(self.buf) >= 2:
                fin_op, length = self.buf[0], self.buf[1] & 0x7F
                pos = 2
                if length == 126:
                    if len(self.buf) < 4:
                        break
                    length = int.from_bytes(self.buf[2:4], "big")
                    pos = 4
                elif length == 127:
                    if len(self.buf) < 10:
                        break
                    length = int.from_bytes(self.buf[2:10], "big")
                    pos = 10
                if len(self.buf) >= pos + length:
                    payload = self.buf[pos:pos + length]
                    self.buf = self.buf[pos + length:]
                    return fin_op & 0x0F, payload
            block = self.sock.recv(65536)
            if not block:
                raise OSError("devtools websocket closed")
            self.buf += block

    def read_json(self):
        """Next data frame decoded as JSON; pings answered with pongs."""
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x9:  # ping -> pong
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            return json.loads(payload.decode("utf-8", "replace"))

    def _send_frame(self, opcode, payload):
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode, 0x80 | len(payload)])
        self.sock.sendall(bytes(header) + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _http_json(url):
    try:
        endpoint = urllib.parse.urlsplit(url)
        port = endpoint.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid local DevTools HTTP URL") from exc
    if (endpoint.scheme.casefold() != "http"
            or (endpoint.hostname or "").casefold() != "127.0.0.1"
            or not port or endpoint.username or endpoint.password):
        raise ValueError("only loopback DevTools HTTP URLs are supported")
    # Use a direct HTTP connection so redirects cannot escape loopback.
    target = endpoint.path or "/"
    if endpoint.query:
        target += "?" + endpoint.query
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", target, headers={"Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            raise OSError("local DevTools endpoint returned HTTP {0}".format(
                response.status))
        return json.loads(response.read().decode("utf-8", "replace"))
    finally:
        connection.close()


def _find_page_target(port):
    targets = _http_json("http://127.0.0.1:{0}/json/list".format(port))
    for target in targets:
        if target.get("type") != "page" or not target.get("webSocketDebuggerUrl"):
            continue
        try:
            endpoint = urllib.parse.urlsplit(target["webSocketDebuggerUrl"])
            target_port = endpoint.port
        except (TypeError, ValueError):
            continue
        if (endpoint.scheme.casefold() == "ws"
                and (endpoint.hostname or "").casefold()
                in {"127.0.0.1", "localhost", "::1"}
                and target_port == int(port)
                and not endpoint.username and not endpoint.password):
            return target["webSocketDebuggerUrl"]
    raise OSError("no browser page target found")


def _cdp_call(ws, method, params=None):
    request = {"id": 1, "method": method}
    if params:
        request["params"] = params
    ws.send_text(json.dumps(request))
    while True:
        message = ws.read_json()
        if message.get("id") == 1:
            return message
        # browser events that arrive while we work are ignored


# ---------------------------------------------------------------- the flow
def browser_login(timeout=WAIT_TIMEOUT, on_status=None, browser=None,
                  profile_dir=None, keep_browser=False):
    """Open the login page in a private browser session and harvest cookies.

    Returns (cookie_string, profile_dir). cookie_string is `name=value; ...`
    for cookie_from_paste(). The browser is closed by default; its temporary
    profile is also removed unless `keep_browser` is true. A supplied profile
    directory is never deleted. Raises OSError/RuntimeError on failure.
    """
    browser = browser or find_browser()
    if not browser:
        raise OSError("no Chrome or Edge found on this computer")
    port = _free_port()
    temporary_profile = profile_dir is None
    tmp = Path(profile_dir) if profile_dir else Path(
        tempfile.mkdtemp(prefix="camfrog-login-"))
    tmp.mkdir(parents=True, exist_ok=True)
    process = None
    ws = None
    try:
        spawn = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            spawn["creationflags"] = 0x08000000
        process = subprocess.Popen(
            [browser, "--remote-debugging-port={0}".format(port),
             "--user-data-dir={0}".format(tmp), "--no-first-run",
             "--no-default-browser-check", "--new-window", LOGIN_PAGE],
            **spawn)
        if on_status:
            on_status("browser opened")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if ws is None:
                    ws = _WebSocket(_find_page_target(port))
                cookies = _cdp_call(ws, "Network.getAllCookies").get(
                    "result", {}).get("cookies", [])
                if any(c.get("name") == SESSION_COOKIE and _is_camfrog_cookie_domain(c.get("domain"))
                       for c in cookies):
                    return _cookies_to_string(cookies), (
                        str(tmp) if keep_browser or not temporary_profile else None)
                time.sleep(1.5)
            except (OSError, ValueError, TimeoutError):
                try:
                    ws.close()
                except Exception:
                    pass
                ws = None
                time.sleep(1.5)  # browser still starting
        raise RuntimeError("login was not completed in time")
    finally:
        try:
            ws.close()
        except Exception:
            pass
        if not keep_browser:
            _close_browser_process(process)
            if temporary_profile:
                shutil.rmtree(tmp, ignore_errors=True)



def _is_camfrog_cookie_domain(domain):
    """Accept only the Camfrog registrable domain or a real subdomain."""
    if not isinstance(domain, str):
        return False
    host = domain.lstrip(".").lower()
    return host == "camfrog.com" or host.endswith(".camfrog.com")


def _cookies_to_string(cookies):
    seen, parts = set(), []
    for cookie in cookies:
        domain = (cookie.get("domain") or "").lstrip(".").lower()
        if not _is_camfrog_cookie_domain(domain):
            continue
        name, value = cookie.get("name") or "", cookie.get("value") or ""
        if not name or not value or name in seen:
            continue
        seen.add(name)
        parts.append("{0}={1}".format(name, value))
    if not parts:
        raise ValueError("no camfrog.com cookies in this browser session")
    return "; ".join(parts)
