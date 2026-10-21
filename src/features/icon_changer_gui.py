"""Standalone Camfrog Room Icon Changer (GUI + CLI).

Provides automatic and manual alternating of room user-list icons
via profiles.camfrog.com with fail-closed security guards, rate-limiting
protection, session verification, and auto-restoration of initial icon on exit.
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

APP_NAME = "icon-changer"
APP_TITLE = "ZChat Room Icon Changer"
APP_VERSION = "2.19.2"

SETTINGS_URL = "https://profiles.camfrog.com/settings.php"
POST_ICON_URL = "https://profiles.camfrog.com/ajax/post_flair.php"
HOME_URL = "https://profiles.camfrog.com/home.php"

DEFAULT_ICON_A = 1
DEFAULT_ICON_B = 2
ICON_ALTERNATE_INTERVAL_MIN = 10.0
ICON_ALTERNATE_INTERVAL_DEFAULT = 30.0
ICON_ALTERNATE_CYCLES_DEFAULT = 4
TIMEOUT_DEFAULT = 15.0

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

THEME = {
    "background": "#1e1e2e",
    "surface": "#252538",
    "surface_high": "#2e2e44",
    "accent": "#cba6f7",
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


def parse_settings_icons(data: dict) -> tuple[int | None, dict[int, str]]:
    """Parse external_data into (current_icon_id, allowed_icons_dict).

    Returns (current_icon_id, {icon_id: icon_url_or_label}).
    Looks for settings.icon.icons list in the external_data structure.
    Falls back to settings.nickname.icons if the icon block lives there.
    """
    if not isinstance(data, dict) or data.get("type") != "settings":
        raise ValueError("unexpected external_data type in settings.php")

    settings_block = data.get("settings")
    if not isinstance(settings_block, dict):
        raise ValueError("missing settings block in external_data")

    # Try settings.icon first, then settings.nickname as fallback
    icon_block = settings_block.get("icon")
    if not isinstance(icon_block, dict):
        icon_block = settings_block.get("nickname", {})

    icons_list = icon_block.get("icons")
    if not isinstance(icons_list, list) or not icons_list:
        raise ValueError("missing or empty icons list in settings")

    allowed: dict[int, str] = {}
    for entry in icons_list:
        if isinstance(entry, dict) and "id" in entry:
            try:
                iid = int(entry["id"])
                # Use url, name, or label as display string
                label = str(
                    entry.get("url")
                    or entry.get("image")
                    or entry.get("name")
                    or entry.get("label")
                    or f"icon-{iid}"
                ).strip()
                allowed[iid] = label
            except (ValueError, TypeError):
                continue

    if not allowed:
        raise ValueError("no valid icon entries parsed from settings.php")

    # Current icon: look for icon_id, current_icon, or flair_icon
    current_icon_id = None
    for key in ("icon_id", "current_icon", "flair_icon"):
        raw = data.get(key)
        if raw is not None:
            try:
                current_icon_id = int(raw)
                break
            except (ValueError, TypeError):
                continue

    return current_icon_id, allowed


def extract_csrf(html: str) -> str:
    """Return per-session CSRF token from page."""
    match = re.search(r"var\s+csrf\s*=\s*['\"]([0-9a-fA-F]{16,})['\"]", html)
    return match.group(1) if match else ""


def extract_profile_identity(html: str) -> dict[str, str] | None:
    """Return provider identity only when both external ID and nickname exist."""
    user = re.search(r"var\s+_user_id\s*=\s*['\"'](\d+)['\"]", html)
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


def post_icon_change(
    opener, icon_id: int, csrf: str, timeout: float = TIMEOUT_DEFAULT
) -> tuple[str, int, str | None]:
    """Post room icon change request via the flair endpoint."""
    data = urllib.parse.urlencode({"id": str(icon_id), "csrf": csrf}).encode("utf-8")
    req = urllib.request.Request(
        POST_ICON_URL,
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


def update_icon_once(
    opener, icon_id: int, timeout: float = TIMEOUT_DEFAULT
) -> tuple[bool, bool, str, bool]:
    """Execute one live icon change with full verification and fail-closed stopping.

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
        _curr_icon, allowed_icons = parse_settings_icons(ext_data)
    except ValueError as exc:
        return False, False, f"failed to parse settings icons: {exc}", True

    if icon_id not in allowed_icons:
        return (
            False,
            False,
            f"icon_id {icon_id} is not in allowed room icons",
            True,
        )

    csrf = extract_csrf(settings_html)
    if not csrf and isinstance(ext_data.get("csrf"), str):
        csrf = ext_data["csrf"]
    if not csrf:
        return False, False, "no CSRF token found on settings page", True

    try:
        reply_body, status_code, retry_after = post_icon_change(
            opener, icon_id, csrf, timeout
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            ra = exc.headers.get("Retry-After") if exc.headers else None
            ra_msg = f" (Retry-After: {ra})" if ra else ""
            return False, False, f"HTTP 429 rate limited on icon update{ra_msg}", True
        return False, False, f"icon update HTTP error {exc.code}", True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, False, f"icon update transport error: {type(exc).__name__}", True

    if status_code == 429:
        ra_msg = f" (Retry-After: {retry_after})" if retry_after else ""
        return False, False, f"HTTP 429 rate limited on icon update{ra_msg}", True

    if status_code != 200:
        return False, False, f"icon update returned HTTP {status_code}", True

    try:
        resp_data = json.loads(reply_body)
    except (ValueError, TypeError):
        return False, False, "unexpected non-JSON response from icon update", True

    if not isinstance(resp_data, dict):
        return False, False, "unexpected response shape from icon update", True

    if resp_data.get("redirect"):
        return False, False, "session problem: server returned redirect", True

    if resp_data.get("error"):
        return (
            False,
            False,
            f"server returned error: {resp_data.get('response', True)}",
            True,
        )

    # Re-read settings.php to verify the icon was changed
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
        new_icon_id, _ = parse_settings_icons(verify_data)
    except ValueError:
        return (
            True,
            False,
            "server acknowledged (HTTP 200), re-read parse error: UNVERIFIED",
            False,
        )

    if new_icon_id == icon_id:
        return (
            True,
            True,
            f"server acknowledged (HTTP 200); icon changed to id {icon_id}",
            False,
        )
    else:
        msg = (
            f"server acknowledged (HTTP 200), current icon {new_icon_id} "
            f"!= requested {icon_id}: UNVERIFIED"
        )
        return (
            True,
            False,
            msg,
            False,
        )


def parse_icon_alternate_interval(raw: float | int | str) -> float:
    """Clamped interval for icon alternator: minimum 10.0s."""
    try:
        val = float(str(raw).strip())
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid interval: {raw!r}") from exc
    if not math.isfinite(val):
        raise ValueError(f"invalid interval: {raw!r}")
    return max(ICON_ALTERNATE_INTERVAL_MIN, val)


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


def run_icon_alternator(
    opener,
    expected_login: str,
    icon_a: int = DEFAULT_ICON_A,
    icon_b: int = DEFAULT_ICON_B,
    interval: float = ICON_ALTERNATE_INTERVAL_DEFAULT,
    max_cycles: int = ICON_ALTERNATE_CYCLES_DEFAULT,
    confirm: bool = False,
    timeout: float = TIMEOUT_DEFAULT,
    time_module=None,
    print_fn=print,
) -> int:
    """Execute icon alternator loop (A <-> B)."""

    def safe_print(msg, sec=()):
        return print_fn(redact_sensitive(msg, secrets=sec))

    clamped_interval = max(ICON_ALTERNATE_INTERVAL_MIN, float(interval))

    safe_print("=== Camfrog Room Icon Alternator ===")
    safe_print(f"Target: Alternate Icon A (id: {icon_a}) <-> Icon B (id: {icon_b})")
    settings_summary = (
        f"Settings: interval={clamped_interval:.1f}s, "
        f"max_cycles={max_cycles}, confirm={confirm}"
    )
    safe_print(settings_summary)

    if not confirm:
        safe_print(
            f"  [DRY-RUN] Icon A: id {icon_a}, Icon B: id {icon_b}, "
            f"interval: {clamped_interval:.1f}s, max_cycles: {max_cycles}"
        )
        safe_print(
            "  Dry-run active; no requests sent. Pass --confirm for live execution."
        )
        return EXIT_OK

    if not opener:
        safe_print(
            "Live icon alternator requires a valid session "
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
        initial_icon_id, allowed_icons = parse_settings_icons(ext_data)
    except ValueError as exc:
        safe_print(f"Failed closed: invalid icon structure: {exc}")
        return EXIT_BLOCKED

    if icon_a not in allowed_icons:
        safe_print(
            f"Refusing Icon A: id {icon_a} is not in allowed icons: "
            f"{sorted(allowed_icons.keys())}"
        )
        return EXIT_USAGE
    if icon_b not in allowed_icons:
        safe_print(
            f"Refusing Icon B: id {icon_b} is not in allowed icons: "
            f"{sorted(allowed_icons.keys())}"
        )
        return EXIT_USAGE

    safe_print(
        f"Live icon alternator started for {identity['nickname']} "
        f"(initial icon id: {initial_icon_id})"
    )
    icons_str = ", ".join(f"{i}: {lbl}" for i, lbl in sorted(allowed_icons.items()))
    safe_print(f"Allowed icons: {icons_str}")

    targets = [(icon_a, allowed_icons[icon_a]), (icon_b, allowed_icons[icon_b])]
    curr_target_idx = 1 if (initial_icon_id == icon_a) else 0

    t_mod = time_module or time
    cycle = 0
    exit_code = EXIT_OK
    interrupted = False

    try:
        while cycle < max_cycles:
            target_id, target_label = targets[curr_target_idx]
            safe_print(
                f"[Cycle {cycle + 1}/{max_cycles}] Alternating to "
                f"icon_id {target_id} ({target_label})..."
            )

            _ack, _verified, msg, stop_err = update_icon_once(
                opener, target_id, timeout
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
        safe_print("\nIcon alternation interrupted by user.")
        interrupted = True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        safe_print(f"Unexpected error in alternation loop: {type(exc).__name__}")
        exit_code = EXIT_BLOCKED

    if initial_icon_id is not None:
        safe_print(
            f"Restoring initial icon (id {initial_icon_id})..."
        )
        _r_ack, r_verified, r_msg, _r_err = update_icon_once(
            opener, initial_icon_id, timeout
        )
        if r_verified:
            safe_print(f"Restoration succeeded: icon changed to id {initial_icon_id}")
        else:
            status_desc = "UNVERIFIED" if _r_ack else "FAILED"
            safe_print(f"Restoration {status_desc}: {r_msg}")
    else:
        safe_print("Initial icon id unknown; skipping restoration.")

    if interrupted:
        return EXIT_USAGE
    return exit_code


# ==============================================================================
# Standalone GUI Implementation
# ==============================================================================


class StandaloneIconChangerGUI:
    """Tkinter-based GUI for Standalone Room Icon Changer."""

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
        self.icon_a_var = self.tk.StringVar(value=str(DEFAULT_ICON_A))
        self.icon_b_var = self.tk.StringVar(value=str(DEFAULT_ICON_B))
        self.interval_var = self.tk.StringVar(
            value=str(int(ICON_ALTERNATE_INTERVAL_DEFAULT))
        )
        self.max_cycles_var = self.tk.StringVar(
            value=str(ICON_ALTERNATE_CYCLES_DEFAULT)
        )
        self.confirm_live_var = self.tk.BooleanVar(value=False)
        self.status_text_var = self.tk.StringVar(value="Ready. Default mode: Dry-run")

        self.is_running = False
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()

        self._build_ui()
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
            text="Camfrog Room Icon Alternator",
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

        self.ttk.Label(grid, text="Icon A ID:").grid(
            row=0, column=0, sticky=self.tk.W, pady=2
        )
        self.ttk.Entry(grid, textvariable=self.icon_a_var, width=8).grid(
            row=0, column=1, sticky=self.tk.W, padx=(4, 16), pady=2
        )

        self.ttk.Label(grid, text="Icon B ID:").grid(
            row=0, column=2, sticky=self.tk.W, pady=2
        )
        self.ttk.Entry(grid, textvariable=self.icon_b_var, width=8).grid(
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
            icon_a = int(self.icon_a_var.get().strip())
            icon_b = int(self.icon_b_var.get().strip())
            interval = parse_icon_alternate_interval(self.interval_var.get().strip())
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
                run_icon_alternator(
                    opener,
                    nickname,
                    icon_a=icon_a,
                    icon_b=icon_b,
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
            target=worker, name="icon-alternator-worker", daemon=True
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
        description="Standalone Camfrog Room Icon Changer (GUI or CLI)."
    )
    parser.add_argument("--gui", action="store_true", help="force open GUI")
    parser.add_argument("--login", required=False, help="Camfrog nickname")
    parser.add_argument(
        "--cookies-file", help="Netscape cookies file exported from browser"
    )
    parser.add_argument("--cookie", help="raw PHPSESSID cookie string for session")
    parser.add_argument(
        "--alternate-icons",
        action="store_true",
        help="alternate room icon between icon A and B",
    )
    parser.add_argument(
        "--icon-a",
        type=int,
        default=DEFAULT_ICON_A,
        help=f"icon ID for icon A (default: {DEFAULT_ICON_A})",
    )
    parser.add_argument(
        "--icon-b",
        type=int,
        default=DEFAULT_ICON_B,
        help=f"icon ID for icon B (default: {DEFAULT_ICON_B})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=ICON_ALTERNATE_INTERVAL_DEFAULT,
        help="alternation interval in seconds (default: 30, min: 10)",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=ICON_ALTERNATE_CYCLES_DEFAULT,
        help="maximum number of icon switches (default: 4)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="explicit live consent for icon alternation (default is dry-run)",
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
        app = StandaloneIconChangerGUI()
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

    return run_icon_alternator(
        opener,
        login,
        icon_a=args.icon_a,
        icon_b=args.icon_b,
        interval=args.interval,
        max_cycles=args.max_cycles,
        confirm=args.confirm,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
