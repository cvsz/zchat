"""Standalone Camfrog Nickname Color Changer (GUI + CLI).

Provides automatic and manual alternating of nickname flair colors
via profiles.camfrog.com with fail-closed security guards, rate-limiting
protection, session verification, and auto-restoration of initial colors on exit.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from pathlib import Path

# Path detection: frozen vs source
BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent.parent
)

APP_NAME = "name-color-changer"
APP_TITLE = "ZChat Nickname Color Changer"
APP_VERSION = "2.19.2"

SETTINGS_URL = "https://profiles.camfrog.com/settings.php"
POST_COLOR_URL = "https://profiles.camfrog.com/ajax/post_flair.php"
HOME_URL = "https://profiles.camfrog.com/home.php"

DEFAULT_COLOR_A = 1
DEFAULT_COLOR_B = 3555
COLOR_ALTERNATE_INTERVAL_MIN = 10.0
COLOR_ALTERNATE_INTERVAL_DEFAULT = 30.0
COLOR_ALTERNATE_CYCLES_DEFAULT = 4
TIMEOUT_DEFAULT = 15.0

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

THEME = {
    "background": "#1e1e2e",
    "surface": "#252538",
    "surface_high": "#2e2e44",
    "accent": "#89b4fa",
    "accent_hover": "#b4befe",
    "text": "#cdd6f4",
    "muted": "#a6adc8",
    "border": "#45475a",
    "entry": "#181825",
    "button_text": "#cdd6f4",
    "button_active": "#3b3b54",
    "button_pressed": "#4f4f6e",
    "danger": "#f38ba8",
    "success": "#a6e3a1",
    "warning": "#f9e2af",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

LOGGED_OUT_MARKERS = (
    "nav-btn-sign-on",
    ">Sign On<",
    "Logon to view",
    "<title>Camfrog - Login Page</title>",
)
SIGNED_IN_MARKERS = ("nav-user-logged", "var _user_id = ")


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VAL definitions into os.environ if file exists."""
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


def redact_sensitive(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Mask tokens, passwords, cookies, and caller-supplied secrets."""
    res = str(text)
    for s in sorted((str(x) for x in secrets if x), key=len, reverse=True):
        if len(s) <= 3:
            res = re.sub(rf"(?<!\w){re.escape(s)}(?!\w)", "[redacted]", res)
        else:
            res = res.replace(s, "[redacted]")
    res = re.sub(
        r"(?i)(\b(?:csrf(?:[_-]?token)?|phpsessid|session[_-]?id|token|password|passwd)\b\s*[:=]\s*)[^\s,;]+",
        r"\1[redacted]",
        res,
    )
    res = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[email redacted]",
        res,
    )
    res = re.sub(
        r"\b[rsp]k_(?:live|test)_[a-zA-Z0-9]{20,}\b",
        "[stripe-key redacted]",
        res,
    )
    return res


def extract_external_data(html: str) -> dict | None:
    """Parse var external_data object literal with balanced braces. Fails closed."""
    prefix = "var external_data = (function(){return "
    start_pos = html.find(prefix)
    if start_pos == -1:
        return None
    brace_start = html.find("{", start_pos + len(prefix) - 5)
    if brace_start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    brace_end = -1

    for idx in range(brace_start, len(html)):
        char = html[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = False
        else:
            if char in ("'", '"'):
                in_string = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    brace_end = idx + 1
                    break

    if brace_end == -1:
        return None

    raw_json = html[brace_start:brace_end]
    try:
        data = json.loads(raw_json)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def parse_settings_nickname_colors(data: dict) -> tuple[str, dict[int, str]]:
    """Parse external_data into (current_color_hex, allowed_colors_dict)."""
    if not isinstance(data, dict) or data.get("type") != "settings":
        raise ValueError("unexpected external_data type in settings.php")

    settings_block = data.get("settings")
    if not isinstance(settings_block, dict):
        raise ValueError("missing settings block in external_data")

    nickname_block = settings_block.get("nickname")
    if not isinstance(nickname_block, dict):
        raise ValueError("missing nickname block in settings.external_data")

    colors_list = nickname_block.get("colors")
    if not isinstance(colors_list, list) or not colors_list:
        raise ValueError("missing or empty colors list in settings.nickname")

    allowed = {}
    for entry in colors_list:
        if isinstance(entry, dict) and "id" in entry and "color" in entry:
            try:
                cid = int(entry["id"])
                chex = str(entry["color"]).strip().lower()
                if chex.startswith("#") and len(chex) in (4, 7):
                    allowed[cid] = chex
            except (ValueError, TypeError):
                continue

    if not allowed:
        raise ValueError("no valid color entries parsed from settings.php")

    current_hex = str(data.get("nickname_color") or "").strip().lower()
    return current_hex, allowed


def extract_csrf(html: str) -> str:
    """Return per-session CSRF token from page."""
    match = re.search(r"var\s+csrf\s*=\s*['\"]([0-9a-fA-F]{16,})['\"]", html)
    return match.group(1) if match else ""


def extract_profile_identity(html: str) -> dict[str, str] | None:
    """Return provider identity only when both external ID and nickname exist."""
    user = re.search(r"var\s+_user_id\s*=\s*['\"](\d+)['\"]", html)
    nickname = re.search(r"var\s+nick\s*=\s*['\"]([^'\"]+)['\"]", html)
    if not user or not nickname or not nickname.group(1).strip():
        return None
    return {"user_id": user.group(1), "nickname": nickname.group(1).strip()}


def summarize_session(html: str) -> tuple[str, str]:
    """Classify the session into 'signed-in', 'logged-out', or 'unrecognized'."""
    for m in SIGNED_IN_MARKERS:
        if m in html:
            return "signed-in", m
    for m in LOGGED_OUT_MARKERS:
        if m in html:
            return "logged-out", m
    return "unrecognized", "no markers matched"


def fetch_settings(opener, timeout: float = TIMEOUT_DEFAULT) -> tuple[str, int]:
    """Fetch settings.php using the authenticated opener."""
    req = urllib.request.Request(
        SETTINGS_URL,
        headers=dict(BROWSER_HEADERS, Referer=SETTINGS_URL),
    )
    with opener.open(req, timeout=timeout) as resp:
        code = getattr(resp, "status", None)
        if code is None and hasattr(resp, "getcode"):
            code = resp.getcode()
        html = resp.read().decode("utf-8", "replace")
        return html, (code or 200)


def post_flair_color(
    opener, color_id: int, csrf: str, timeout: float = TIMEOUT_DEFAULT
) -> tuple[str, int, str | None]:
    """Post nickname flair color change request."""
    data = urllib.parse.urlencode({"id": str(color_id), "csrf": csrf}).encode("utf-8")
    req = urllib.request.Request(
        POST_COLOR_URL,
        data=data,
        headers=dict(BROWSER_HEADERS, Referer=SETTINGS_URL),
    )
    with opener.open(req, timeout=timeout) as resp:
        code = getattr(resp, "status", None)
        if code is None and hasattr(resp, "getcode"):
            code = resp.getcode()
        headers = getattr(resp, "headers", None)
        retry_after = headers.get("Retry-After") if headers is not None else None
        body = resp.read().decode("utf-8", "replace").strip()
        return body, (code or 200), retry_after


def verify_active_session(
    opener, expected_nickname: str, timeout: float = TIMEOUT_DEFAULT
) -> tuple[bool, dict | None, str | None, str]:
    """Verify session against HOME_URL and confirm signed-in nickname matches."""
    try:
        req = urllib.request.Request(
            HOME_URL,
            headers=dict(BROWSER_HEADERS, Referer=HOME_URL),
        )
        with opener.open(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, None, None, f"session request failed: {type(exc).__name__}"

    verdict, _marker = summarize_session(html)
    if verdict == "logged-out":
        return False, None, None, "server returned login page (session expired/invalid)"
    if verdict != "signed-in":
        return False, None, None, "unrecognized session response (not signed-in)"

    identity = extract_profile_identity(html)
    if not identity:
        return False, None, None, "could not extract user identity from session"

    if (
        expected_nickname
        and identity["nickname"].casefold() != expected_nickname.strip().casefold()
    ):
        return (
            False,
            identity,
            None,
            f"session user {identity['nickname']!r} != expected {expected_nickname!r}",
        )

    csrf = extract_csrf(html)
    if not csrf:
        return False, identity, None, "no CSRF token found on profile page"

    return True, identity, csrf, ""


def update_nickname_color_once(
    opener, color_id: int, expected_color_hex: str, timeout: float = TIMEOUT_DEFAULT
) -> tuple[bool, bool, str, bool]:
    """Execute one live color change with full verification and fail-closed stopping.

    Returns: (success, verified, message, stop_error).
    """
    try:
        settings_html, s_code = fetch_settings(opener, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            ra_msg = f" (Retry-After: {retry_after})" if retry_after else ""
            return False, False, f"HTTP 429 rate limit on settings fetch{ra_msg}", True
        return False, False, f"settings fetch failed: HTTP {exc.code}", True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, False, f"settings fetch failed: {type(exc).__name__}", True

    if s_code != 200:
        return False, False, f"settings fetch returned HTTP {s_code}", True

    verdict, _ = summarize_session(settings_html)
    if verdict == "logged-out":
        return False, False, "session is logged out on settings page", True

    ext_data = extract_external_data(settings_html)
    if not ext_data:
        return False, False, "missing or malformed external_data in settings", True

    try:
        _curr_hex, allowed_colors = parse_settings_nickname_colors(ext_data)
    except ValueError as exc:
        return False, False, f"failed to parse settings nickname colors: {exc}", True

    if color_id not in allowed_colors:
        return (
            False,
            False,
            f"color_id {color_id} is not in allowed nickname colors",
            True,
        )

    csrf = extract_csrf(settings_html)
    if not csrf and isinstance(ext_data.get("csrf"), str):
        csrf = ext_data["csrf"]
    if not csrf:
        return False, False, "no CSRF token found on settings page", True

    try:
        reply_body, status_code, retry_after = post_flair_color(
            opener, color_id, csrf, timeout
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            ra = exc.headers.get("Retry-After") if exc.headers else None
            ra_msg = f" (Retry-After: {ra})" if ra else ""
            return False, False, f"HTTP 429 rate limited on color update{ra_msg}", True
        return False, False, f"color update HTTP error {exc.code}", True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, False, f"color update transport error: {type(exc).__name__}", True

    if status_code == 429:
        ra_msg = f" (Retry-After: {retry_after})" if retry_after else ""
        return False, False, f"HTTP 429 rate limited on color update{ra_msg}", True

    if status_code != 200:
        return False, False, f"color update returned HTTP {status_code}", True

    try:
        resp_data = json.loads(reply_body)
    except (ValueError, TypeError):
        return False, False, "unexpected non-JSON response from color update", True

    if not isinstance(resp_data, dict):
        return False, False, "unexpected response shape from color update", True

    if resp_data.get("redirect"):
        return False, False, "session problem: server returned redirect", True

    if resp_data.get("error"):
        return (
            False,
            False,
            f"server returned error: {resp_data.get('response', True)}",
            True,
        )

    # Re-read settings.php to verify external_data.nickname_color matches
    try:
        verify_html, v_code = fetch_settings(opener, timeout)
    except (urllib.error.URLError, TimeoutError, OSError):
        return (
            True,
            False,
            "server acknowledged (HTTP 200), but re-read failed: UNVERIFIED",
            False,
        )

    if v_code != 200:
        return (
            True,
            False,
            f"server acknowledged (HTTP 200), re-read HTTP {v_code}: UNVERIFIED",
            False,
        )

    verify_data = extract_external_data(verify_html)
    if not verify_data:
        return (
            True,
            False,
            "server acknowledged (HTTP 200), re-read missing external_data: UNVERIFIED",
            False,
        )

    try:
        new_hex, _ = parse_settings_nickname_colors(verify_data)
    except ValueError:
        return (
            True,
            False,
            "server acknowledged (HTTP 200), re-read parse error: UNVERIFIED",
            False,
        )

    if new_hex.lower() == expected_color_hex.lower():
        return (
            True,
            True,
            f"server acknowledged (HTTP 200); color changed to {expected_color_hex}",
            False,
        )
    else:
        msg = (
            f"server acknowledged (HTTP 200), current color {new_hex} "
            f"!= requested {expected_color_hex}: UNVERIFIED"
        )
        return (
            True,
            False,
            msg,
            False,
        )


def parse_color_alternate_interval(raw: float | int | str) -> float:
    """Clamped interval for nickname color alternator: minimum 10.0s."""
    try:
        val = float(str(raw).strip())
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid interval: {raw!r}") from exc
    if not math.isfinite(val):
        raise ValueError(f"invalid interval: {raw!r}")
    return max(COLOR_ALTERNATE_INTERVAL_MIN, val)


def cookie_from_paste(raw: str) -> CookieJar:
    """Build a CookieJar with a single PHPSESSID cookie from raw paste."""
    raw = (raw or "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    val = raw.split("=", 1)[1].strip() if "=" in raw else raw.strip()
    if not val:
        raise ValueError("empty session cookie token")
    jar = CookieJar()
    cookie = Cookie(
        version=0,
        name="PHPSESSID",
        value=val,
        port=None,
        port_specified=False,
        domain=".camfrog.com",
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": None},
        rfc2109=False,
    )
    jar.set_cookie(cookie)
    return jar


def run_color_alternator(
    opener,
    expected_login: str,
    color_a: int = DEFAULT_COLOR_A,
    color_b: int = DEFAULT_COLOR_B,
    interval: float = COLOR_ALTERNATE_INTERVAL_DEFAULT,
    max_cycles: int = COLOR_ALTERNATE_CYCLES_DEFAULT,
    confirm: bool = False,
    timeout: float = TIMEOUT_DEFAULT,
    time_module=None,
    print_fn=print,
) -> int:
    """Execute color alternator loop (A <-> B)."""

    def safe_print(msg, sec=()):
        return print_fn(redact_sensitive(msg, secrets=sec))

    clamped_interval = max(COLOR_ALTERNATE_INTERVAL_MIN, float(interval))

    safe_print("=== Camfrog Nickname Color Alternator ===")
    safe_print(f"Target: Alternate Color A (id: {color_a}) <-> Color B (id: {color_b})")
    settings_summary = (
        f"Settings: interval={clamped_interval:.1f}s, "
        f"max_cycles={max_cycles}, confirm={confirm}"
    )
    safe_print(settings_summary)

    if not confirm:
        safe_print(
            f"  [DRY-RUN] Color A: id {color_a}, Color B: id {color_b}, "
            f"interval: {clamped_interval:.1f}s, max_cycles: {max_cycles}"
        )
        safe_print(
            "  Dry-run active; no requests sent. Pass --confirm for live execution."
        )
        return EXIT_OK

    if not opener:
        safe_print(
            "Live color alternator requires a valid session "
            "(e.g. cookies or WEBAUTH_USER)."
        )
        return EXIT_USAGE

    ok_sess, identity, _sess_csrf, sess_err = verify_active_session(
        opener, expected_login, timeout
    )
    if not ok_sess:
        safe_print(f"Session verification failed: {sess_err}")
        return EXIT_BLOCKED

    try:
        settings_html, s_code = fetch_settings(opener, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            ra = exc.headers.get("Retry-After") if exc.headers else None
            ra_msg = f" (Retry-After: {ra})" if ra else ""
            safe_print(f"Initial settings fetch rate limited (HTTP 429){ra_msg}")
            return EXIT_BLOCKED
        safe_print(f"Initial settings fetch failed: HTTP {exc.code}")
        return EXIT_BLOCKED
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        safe_print(f"Initial settings fetch failed: {type(exc).__name__}")
        return EXIT_BLOCKED

    if s_code != 200:
        safe_print(f"Initial settings fetch returned HTTP {s_code}")
        return EXIT_BLOCKED

    ext_data = extract_external_data(settings_html)
    if not ext_data:
        safe_print("Failed closed: missing external_data in settings.php")
        return EXIT_BLOCKED

    try:
        initial_color_hex, allowed_colors = parse_settings_nickname_colors(ext_data)
    except ValueError as exc:
        safe_print(f"Failed closed: invalid nickname colors structure: {exc}")
        return EXIT_BLOCKED

    if color_a not in allowed_colors:
        safe_print(
            f"Refusing color A: id {color_a} is not in allowed colors: "
            f"{sorted(allowed_colors.keys())}"
        )
        return EXIT_USAGE
    if color_b not in allowed_colors:
        safe_print(
            f"Refusing color B: id {color_b} is not in allowed colors: "
            f"{sorted(allowed_colors.keys())}"
        )
        return EXIT_USAGE

    initial_color_id = None
    for cid, chex in allowed_colors.items():
        if chex.lower() == initial_color_hex.lower():
            initial_color_id = cid
            break

    safe_print(
        f"Live color alternator started for {identity['nickname']} "
        f"(initial color: {initial_color_hex}, id: {initial_color_id})"
    )
    colors_str = ", ".join(f"{c}: {h}" for c, h in sorted(allowed_colors.items()))
    safe_print(f"Allowed colors: {colors_str}")

    targets = [(color_a, allowed_colors[color_a]), (color_b, allowed_colors[color_b])]
    curr_target_idx = 1 if (initial_color_id == color_a) else 0

    t_mod = time_module or time
    cycle = 0
    exit_code = EXIT_OK
    interrupted = False

    try:
        while cycle < max_cycles:
            target_id, target_hex = targets[curr_target_idx]
            safe_print(
                f"[Cycle {cycle + 1}/{max_cycles}] Alternating to "
                f"color_id {target_id} ({target_hex})..."
            )

            _ack, _verified, msg, stop_err = update_nickname_color_once(
                opener, target_id, target_hex, timeout
            )
            safe_print(f"  Result: {msg}")

            if stop_err:
                safe_print(
                    "STOPPING IMMEDIATELY: failure or unexpected response encountered."
                )
                exit_code = EXIT_BLOCKED
                break

            curr_target_idx = 1 - curr_target_idx
            cycle += 1

            if cycle < max_cycles:
                t_mod.sleep(clamped_interval)

    except KeyboardInterrupt:
        safe_print("\nColor alternation interrupted by user.")
        interrupted = True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        safe_print(f"Unexpected error in alternation loop: {type(exc).__name__}")
        exit_code = EXIT_BLOCKED

    if initial_color_id is not None:
        safe_print(
            f"Restoring initial color (id {initial_color_id}, {initial_color_hex})..."
        )
        _r_ack, r_verified, r_msg, _r_err = update_nickname_color_once(
            opener, initial_color_id, initial_color_hex, timeout
        )
        if r_verified:
            safe_print(f"Restoration succeeded: color changed to {initial_color_hex}")
        else:
            status_desc = "UNVERIFIED" if _r_ack else "FAILED"
            safe_print(f"Restoration {status_desc}: {r_msg}")
    else:
        safe_print("Initial color id unknown; skipping restoration.")

    if interrupted:
        return EXIT_USAGE
    return exit_code


# ---------- Inlined ClipboardController ----------


_WIDGET_CLASSES = {
    "Entry",
    "TEntry",
    "TCombobox",
    "Spinbox",
    "TSpinbox",
    "Text",
    "Listbox",
}
_EDITABLE_CLASSES = {"Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text"}


def _windows_open_clipboard(clipboard, attempts=20, interval=0.025):
    """Windows clipboard is often briefly busy; retry opening it for 0.5 seconds."""
    last_error = None
    for attempt in range(attempts):
        try:
            clipboard.OpenClipboard(None)
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(interval)
    raise OSError(f"Could not open the Windows clipboard: {last_error}") from last_error


def set_clipboard_text(root, text):
    text = str(text)
    if os.name == "nt":
        import win32clipboard
        import win32con

        _windows_open_clipboard(win32clipboard)
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update_idletasks()


def get_clipboard_text(root):
    if os.name == "nt":
        import win32clipboard
        import win32con

        _windows_open_clipboard(win32clipboard)
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                value = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                value = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                if isinstance(value, bytes):
                    value = value.decode("mbcs", errors="replace")
            else:
                return ""
            return value if isinstance(value, str) else str(value)
        finally:
            win32clipboard.CloseClipboard()
    try:
        return root.clipboard_get()
    except Exception:
        return ""


def _widget_class(widget):
    try:
        return widget.winfo_class()
    except Exception:
        return ""


def _has_selection(widget, widget_class):
    try:
        if widget_class == "Text":
            return bool(widget.tag_ranges("sel"))
        if widget_class == "Listbox":
            return bool(widget.curselection())
        return bool(widget.selection_present())
    except Exception:
        return False


def _selected_text(widget, widget_class):
    if widget_class == "Text":
        return widget.get("sel.first", "sel.last")
    if widget_class == "Listbox":
        selected = widget.curselection()
        return "\n".join(widget.get(index) for index in selected)
    value = widget.get()
    start, end = int(widget.index("sel.first")), int(widget.index("sel.last"))
    return value[start:end]


def _editable(widget, widget_class):
    if widget_class not in _EDITABLE_CLASSES:
        return False
    try:
        return str(widget.cget("state")) not in {"disabled", "readonly"}
    except Exception:
        return True


def _delete_selection(widget, widget_class):
    if widget_class == "Text":
        widget.delete("sel.first", "sel.last")
    else:
        widget.delete("sel.first", "sel.last")


def _operate(root, widget, action):
    widget_class = _widget_class(widget)
    if widget_class not in _WIDGET_CLASSES:
        return False

    if action in {"copy", "cut"}:
        if not _has_selection(widget, widget_class):
            return False
        if action == "cut" and not _editable(widget, widget_class):
            return False
        selected = _selected_text(widget, widget_class)
        set_clipboard_text(root, selected)
        if action == "cut":
            _delete_selection(widget, widget_class)
        return True

    if action == "paste":
        if not _editable(widget, widget_class):
            return False
        value = get_clipboard_text(root)
        if not value:
            return False
        if widget_class == "Text":
            if _has_selection(widget, widget_class):
                widget.delete("sel.first", "sel.last")
            widget.insert("insert", value)
        else:
            if _has_selection(widget, widget_class):
                _delete_selection(widget, widget_class)
            widget.insert("insert", value)
        return True
    return False


class ClipboardController:
    """Attach reliable Ctrl+C/X/V/A, Insert shortcuts, and a right-click menu."""

    def __init__(self, root, translate=None, on_error=None):
        import tkinter as tk

        self.root = root
        self.translate = translate or (lambda text: str(text).partition("|")[0])
        self.on_error = on_error
        self.target = None
        self.widgets = []
        import weakref

        self._installed_widgets = weakref.WeakSet()
        self.menu = tk.Menu(root, tearoff=False)
        for label, action in (
            ("Cut|ตัด", "cut"),
            ("Copy|คัดลอก", "copy"),
            ("Paste|วาง", "paste"),
        ):
            self.menu.add_command(
                label=self.translate(label),
                command=lambda op=action: self.perform(self.target, op),
            )
        self.menu.add_separator()
        self.menu.add_command(
            label=self.translate("Select all|เลือกทั้งหมด"),
            command=lambda: self.select_all(self.target),
        )

    def refresh_menu(self):
        labels = (
            "Cut|\u0e15\u0e31\u0e14",
            "Copy|\u0e04\u0e31\u0e14\u0e25\u0e2d\u0e01",
            "Paste|\u0e27\u0e32\u0e07",
            "Select all|\u0e40\u0e25\u0e37\u0e2d\u0e01\u0e17\u0e31\u0e49\u0e07\u0e2b\u0e21\u0e14",
        )
        for index, label in enumerate(labels):
            menu_index = index if index < 3 else 4
            self.menu.entryconfigure(menu_index, label=self.translate(label))

    def install(self, parent):
        """Bind editors recursively, including controls added after the first build."""
        for widget in parent.winfo_children():
            widget_class = _widget_class(widget)
            if (
                widget_class in _WIDGET_CLASSES
                and widget not in self._installed_widgets
            ):
                self._installed_widgets.add(widget)
                self.widgets.append(widget)
                if widget_class in {"Text", "Listbox"}:
                    try:
                        widget.configure(
                            selectbackground=THEME.get("accent", "#8b672b"),
                            selectforeground=THEME.get("surface", "#fffdfa"),
                            insertbackground=THEME.get("text", "#283648"),
                            highlightbackground=THEME.get("border", "#d5c8ac"),
                            highlightcolor=THEME.get("accent", "#8b672b"),
                        )
                    except Exception:
                        pass
                for sequence, action in (
                    ("<Control-c>", "copy"),
                    ("<Control-x>", "cut"),
                    ("<Control-v>", "paste"),
                    ("<Control-Insert>", "copy"),
                    ("<Shift-Delete>", "cut"),
                    ("<Shift-Insert>", "paste"),
                ):
                    widget.bind(
                        sequence,
                        lambda event, op=action: self.handle(event, op),
                        add="+",
                    )
                # The generic key binding also recognizes Windows virtual-key
                # codes and control characters when a Thai/non-Latin layout
                # reports an unknown keysym for Ctrl+A/C/V/X.
                widget.bind("<Control-KeyPress>", self.handle_control_key, add="+")
                widget.bind("<Control-a>", self._select_all_event, add="+")
                widget.bind("<Button-3>", self.show_menu, add="+")
                widget.bind("<Shift-F10>", self.show_menu, add="+")
            self.install(widget)

    def handle_control_key(self, event):
        """Dispatch Ctrl+A/C/V/X consistently across keyboard layouts."""
        keysym = str(getattr(event, "keysym", "")).lower()
        char = str(getattr(event, "char", "") or "")
        try:
            keycode = int(getattr(event, "keycode", -1))
        except (TypeError, ValueError):
            keycode = -1
        shortcuts = {
            "a": ({"a", "\x01"}, 65, None),
            "c": ({"c", "\x03"}, 67, "copy"),
            "v": ({"v", "\x16"}, 86, "paste"),
            "x": ({"x", "\x18"}, 88, "cut"),
        }
        key = next(
            (
                name
                for name, (chars, code, _action) in shortcuts.items()
                if keysym == name or char in chars or keycode == code
            ),
            None,
        )
        if key is None:
            return None
        self.target = event.widget
        _chars, _code, action = shortcuts[key]
        if action is None:
            return self._select_all_event(event)
        return self.handle(event, action)

    def perform(self, widget, action):
        if widget is None:
            return False
        try:
            return _operate(self.root, widget, action)
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
            return False

    def handle(self, event, action):
        self.target = event.widget
        self.perform(self.target, action)
        return "break"

    def select_all(self, widget):
        if widget is None:
            return "break"
        widget_class = _widget_class(widget)
        try:
            if widget_class == "Text":
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
            elif widget_class == "Listbox":
                widget.selection_set(0, "end")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
            widget.focus_set()
        except Exception:
            pass
        return "break"

    def _select_all_event(self, event):
        self.target = event.widget
        return self.select_all(event.widget)

    def show_menu(self, event):
        self.target = event.widget
        try:
            event.widget.focus_set()
            self.menu.tk_popup(event.x_root, event.y_root)
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
        finally:
            try:
                self.menu.grab_release()
            except Exception:
                pass
        return "break"


# ==============================================================================
# Standalone GUI Implementation
# ==============================================================================


class StandaloneNameColorChangerGUI:
    """Tkinter-based GUI for Standalone Nickname Color Changer."""

    def __init__(self, root=None):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox

        self.root = root or self.tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("520x460")
        self.root.minsize(450, 400)

        self._init_styles()

        self.nickname_var = self.tk.StringVar(value=os.environ.get("CAMFROG_USER", ""))
        self.cookie_var = self.tk.StringVar(value=os.environ.get("WEBAUTH_USER", ""))
        self.color_a_var = self.tk.StringVar(value=str(DEFAULT_COLOR_A))
        self.color_b_var = self.tk.StringVar(value=str(DEFAULT_COLOR_B))
        self.interval_var = self.tk.StringVar(
            value=str(int(COLOR_ALTERNATE_INTERVAL_DEFAULT))
        )
        self.max_cycles_var = self.tk.StringVar(
            value=str(COLOR_ALTERNATE_CYCLES_DEFAULT)
        )
        self.confirm_live_var = self.tk.BooleanVar(value=False)
        self.status_text_var = self.tk.StringVar(value="Ready. Default mode: Dry-run")

        self.is_running = False
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()

        self._build_ui()
        self.clipboard_controller = ClipboardController(self.root)
        self.clipboard_controller.install(self.root)
        self.root.after(100, self._process_log_queue)

    def _init_styles(self):
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass
        style.configure("TFrame", background=THEME["background"])
        style.configure(
            "TLabel",
            background=THEME["background"],
            foreground=THEME["text"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "TLabelframe",
            background=THEME["background"],
            bordercolor=THEME["border"],
        )
        style.configure(
            "TLabelframe.Label",
            background=THEME["background"],
            foreground=THEME["accent"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "TCheckbutton",
            background=THEME["background"],
            foreground=THEME["text"],
        )
        style.configure(
            "TButton",
            padding=(8, 4),
            font=("Segoe UI", 9),
            background=THEME["surface_high"],
            foreground=THEME["button_text"],
        )
        style.map(
            "TButton",
            background=[
                ("active", THEME["button_active"]),
                ("pressed", THEME["button_pressed"]),
            ],
        )
        style.configure(
            "Accent.TButton",
            padding=(8, 4),
            font=("Segoe UI", 9, "bold"),
            background=THEME["accent"],
            foreground=THEME["surface"],
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", THEME["accent_hover"]),
                ("pressed", THEME["accent_hover"]),
            ],
        )

    def _build_ui(self):
        container = self.ttk.Frame(self.root, padding=12)
        container.pack(fill=self.tk.BOTH, expand=True)

        # Title Header
        header = self.ttk.Frame(container)
        header.pack(fill=self.tk.X, pady=(0, 8))
        lbl_title = self.tk.Label(
            header,
            text="Camfrog Nickname Color Alternator",
            font=("Segoe UI", 12, "bold"),
            bg=THEME["background"],
            fg=THEME["accent"],
        )
        lbl_title.pack(side=self.tk.LEFT)

        # Account & Session Frame
        acc_frame = self.ttk.LabelFrame(
            container, text=" Session & Credentials ", padding=8
        )
        acc_frame.pack(fill=self.tk.X, pady=(0, 8))

        row1 = self.ttk.Frame(acc_frame)
        row1.pack(fill=self.tk.X, pady=2)
        self.ttk.Label(row1, text="Nickname:", width=12).pack(side=self.tk.LEFT)
        self.ttk.Entry(row1, textvariable=self.nickname_var).pack(
            side=self.tk.LEFT, fill=self.tk.X, expand=True
        )

        row2 = self.ttk.Frame(acc_frame)
        row2.pack(fill=self.tk.X, pady=2)
        self.ttk.Label(row2, text="PHPSESSID:", width=12).pack(side=self.tk.LEFT)
        self.ttk.Entry(row2, textvariable=self.cookie_var, show="*").pack(
            side=self.tk.LEFT, fill=self.tk.X, expand=True
        )

        # Alternator Configuration Frame
        cfg_frame = self.ttk.LabelFrame(
            container, text=" Alternator Parameters ", padding=8
        )
        cfg_frame.pack(fill=self.tk.X, pady=(0, 8))

        grid = self.ttk.Frame(cfg_frame)
        grid.pack(fill=self.tk.X)

        self.ttk.Label(grid, text="Color A ID:").grid(
            row=0, column=0, sticky=self.tk.W, pady=2
        )
        self.ttk.Entry(grid, textvariable=self.color_a_var, width=8).grid(
            row=0, column=1, sticky=self.tk.W, padx=(4, 16), pady=2
        )

        self.ttk.Label(grid, text="Color B ID:").grid(
            row=0, column=2, sticky=self.tk.W, pady=2
        )
        self.ttk.Entry(grid, textvariable=self.color_b_var, width=8).grid(
            row=0, column=3, sticky=self.tk.W, padx=4, pady=2
        )

        self.ttk.Label(grid, text="Interval (s):").grid(
            row=1, column=0, sticky=self.tk.W, pady=2
        )
        self.ttk.Entry(grid, textvariable=self.interval_var, width=8).grid(
            row=1, column=1, sticky=self.tk.W, padx=(4, 16), pady=2
        )

        self.ttk.Label(grid, text="Max Cycles:").grid(
            row=1, column=2, sticky=self.tk.W, pady=2
        )
        self.ttk.Entry(grid, textvariable=self.max_cycles_var, width=8).grid(
            row=1, column=3, sticky=self.tk.W, padx=4, pady=2
        )

        chk_confirm = self.ttk.Checkbutton(
            cfg_frame,
            text="Confirm Live Submission (Uncheck for safe Dry-Run)",
            variable=self.confirm_live_var,
        )
        chk_confirm.pack(anchor=self.tk.W, pady=(4, 0))

        # Controls & Action Buttons
        btn_box = self.ttk.Frame(container)
        btn_box.pack(fill=self.tk.X, pady=(0, 8))

        self.btn_start = self.ttk.Button(
            btn_box,
            text="Start Alternator",
            style="Accent.TButton",
            command=self.on_start,
        )
        self.btn_start.pack(side=self.tk.LEFT, padx=(0, 8))

        self.btn_stop = self.ttk.Button(
            btn_box, text="Stop / Cancel", command=self.on_stop, state=self.tk.DISABLED
        )
        self.btn_stop.pack(side=self.tk.LEFT)

        # Activity Log Area
        log_frame = self.ttk.LabelFrame(container, text=" Activity Log ", padding=6)
        log_frame.pack(fill=self.tk.BOTH, expand=True)

        self.log_text = self.tk.Text(
            log_frame,
            wrap=self.tk.WORD,
            bg=THEME["surface"],
            fg=THEME["text"],
            insertbackground=THEME["accent"],
            font=("Consolas", 8),
            relief=self.tk.FLAT,
        )
        self.log_text.pack(side=self.tk.LEFT, fill=self.tk.BOTH, expand=True)
        scrollbar = self.ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=self.tk.RIGHT, fill=self.tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def log(self, message: str):
        self.log_queue.put(message)

    def _process_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.log_text.insert(self.tk.END, msg + "\n")
            self.log_text.see(self.tk.END)
        self.root.after(100, self._process_log_queue)

    def on_start(self):
        if self.is_running:
            return

        nickname = self.nickname_var.get().strip()
        cookie = self.cookie_var.get().strip()

        try:
            color_a = int(self.color_a_var.get().strip())
            color_b = int(self.color_b_var.get().strip())
            interval = parse_color_alternate_interval(self.interval_var.get().strip())
            max_cycles = int(self.max_cycles_var.get().strip())
        except (ValueError, TypeError) as exc:
            self.messagebox.showerror("Invalid Input", f"Configuration error: {exc}")
            return

        confirm = self.confirm_live_var.get()
        if confirm and not cookie:
            self.messagebox.showerror(
                "Session Required",
                "Live mode requires a valid PHPSESSID cookie value.",
            )
            return

        opener = None
        if cookie:
            try:
                jar = cookie_from_paste(cookie)
                opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(jar)
                )
            except (ValueError, TypeError) as exc:
                self.messagebox.showerror(
                    "Cookie Error", f"Failed to initialize session: {exc}"
                )
                return

        self.is_running = True
        self.stop_event.clear()
        self.btn_start.configure(state=self.tk.DISABLED)
        self.btn_stop.configure(state=self.tk.NORMAL)

        def worker():
            class StoppableTime:
                def __init__(self, stop_ev):
                    self.stop_ev = stop_ev

                def sleep(self, seconds):
                    elapsed = 0.0
                    while elapsed < seconds and not self.stop_ev.is_set():
                        time.sleep(min(0.2, seconds - elapsed))
                        elapsed += 0.2

            def safe_logger(msg):
                self.log(msg)

            try:
                run_color_alternator(
                    opener,
                    nickname,
                    color_a=color_a,
                    color_b=color_b,
                    interval=interval,
                    max_cycles=max_cycles,
                    confirm=confirm,
                    timeout=TIMEOUT_DEFAULT,
                    time_module=StoppableTime(self.stop_event),
                    print_fn=safe_logger,
                )
            finally:
                self.root.after(0, self._on_worker_finished)

        self.worker_thread = threading.Thread(
            target=worker, name="color-alternator-worker", daemon=True
        )
        self.worker_thread.start()

    def on_stop(self):
        if self.is_running:
            self.log("[USER] Stop requested. Restoring and halting...")
            self.stop_event.set()
            self.btn_stop.configure(state=self.tk.DISABLED)

    def _on_worker_finished(self):
        self.is_running = False
        self.btn_start.configure(state=self.tk.NORMAL)
        self.btn_stop.configure(state=self.tk.DISABLED)
        self.log("[INFO] Alternator stopped / finished.")


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone Camfrog Nickname Color Changer (GUI or CLI)."
    )
    parser.add_argument("--gui", action="store_true", help="force open GUI")
    parser.add_argument("--login", required=False, help="Camfrog nickname")
    parser.add_argument(
        "--cookies-file", help="Netscape cookies file exported from browser"
    )
    parser.add_argument("--cookie", help="raw PHPSESSID cookie string for session")
    parser.add_argument(
        "--alternate-colors",
        action="store_true",
        help="alternate nickname color between color A and B",
    )
    parser.add_argument(
        "--color-a",
        type=int,
        default=DEFAULT_COLOR_A,
        help=f"color ID for color A (default: {DEFAULT_COLOR_A})",
    )
    parser.add_argument(
        "--color-b",
        type=int,
        default=DEFAULT_COLOR_B,
        help=f"color ID for color B (default: {DEFAULT_COLOR_B})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=COLOR_ALTERNATE_INTERVAL_DEFAULT,
        help="alternation interval in seconds (default: 30, min: 10)",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=COLOR_ALTERNATE_CYCLES_DEFAULT,
        help="maximum number of color switches (default: 4)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="explicit live consent for color alternation (default is dry-run)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=TIMEOUT_DEFAULT,
        help="request timeout in seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(BASE / ".env")
    if argv is None:
        argv = sys.argv[1:]

    # If launched with no arguments or explicitly with --gui, open GUI
    if len(argv) == 0 or "--gui" in argv:
        app = StandaloneNameColorChangerGUI()
        app.root.mainloop()
        return EXIT_OK

    args = build_parser().parse_args(argv)
    login = args.login or (os.environ.get("CAMFROG_USER") or "").strip()

    opener = None
    raw_cookie = args.cookie or os.environ.get("WEBAUTH_USER")
    if args.cookies_file:
        jar = MozillaCookieJar(args.cookies_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    elif raw_cookie:
        jar = cookie_from_paste(raw_cookie)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    return run_color_alternator(
        opener,
        login,
        color_a=args.color_a,
        color_b=args.color_b,
        interval=args.interval,
        max_cycles=args.max_cycles,
        confirm=args.confirm,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
