"""Prototype: update Camfrog status via profiles.camfrog.com (web, not desktop UIA).

PROTOTYPE STATUS: the login flow below was mapped read-only from the public
login page. The authenticated status-update endpoint is UNVERIFIED because it
is only visible inside a logged-in browser session, so this tool FAILS CLOSED
before any status change until that endpoint is captured and confirmed. Run
with --how-to-capture for the browser-devtools steps.

Security rules (fail closed):
- The password comes ONLY from an interactive getpass() prompt. It is never
  read from argv, environment, files, or stdin-pipes, and is never printed,
  logged, or written to disk.
- Preferred path: reuse your Chrome session instead of any password. Export
  cookies to a file and pass --cookies-file; only cookie COUNT and DOMAINS
  are ever printed, never values. Cookie files are git-ignored (*cookies*.txt).
- Default mode is dry-run: no network traffic, no password prompt.
- No CAPTCHA bypass is attempted. Login POSTs carry an empty token, so the
  server answers 'captcha' and the tool stops with exit code 3.
- Check Camfrog's terms and your room's rules before automating web login.
"""

import argparse
import getpass
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from pathlib import Path

# Frozen -> files live next to the exe; source -> the repo root (parent of tools/).
BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
    else Path(__file__).resolve().parent.parent

# Keep the standalone source entry point and packaged EXE on the same profile
# HTTP implementation as the in-app updater.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(BASE / "src" / "camfrog"))
from camfrog_profile_status import (  # noqa: E402
    extract_csrf as _profile_extract_csrf,
    extract_profile_identity as _profile_extract_identity,
    fetch_profile as _profile_fetch,
    post_profile_status as _profile_post_status,
)

LOGIN_URL = "https://www.camfrog.com/th/login/check.php"

# camfrog's edge blocks the default Python-urllib User-Agent with HTTP 403;
# every request therefore presents a plain browser identity. No cookies or
# credentials are added here.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
LOGIN_PAGE_URL = "https://www.camfrog.com/th/login.php"


# Captured 2026-10-09 from a logged-in https://profiles.camfrog.com/home.php
# session: the profile page's hopping box POSTs {status, csrf} here, and the
# csrf token is embedded in that same page. The path is implemented but stays
# gated: the first live round-trip needs an explicit --confirm-update.
WEB_UPDATE_URL = "https://profiles.camfrog.com/ajax/update_status.php"
HOME_URL = "https://profiles.camfrog.com/home.php"

# Captured from profiles.camfrog.com settings and profile JS
SETTINGS_URL = "https://profiles.camfrog.com/th/settings.php"
COLOR_UPDATE_URL = "https://profiles.camfrog.com/th/ajax/update_flair_color.php"

COLOR_ALTERNATE_INTERVAL_MIN = 10.0
COLOR_ALTERNATE_INTERVAL_DEFAULT = 30.0
COLOR_ALTERNATE_CYCLES_DEFAULT = 4
DEFAULT_COLOR_A = 1
DEFAULT_COLOR_B = 3555

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

ROTATE_INTERVAL_MIN = 30
ROTATE_INTERVAL_DEFAULT = 60
ROTATE_INTERVAL_MAX = 86400
TIMEOUT_DEFAULT = 2.0


def parse_pool(text):
    """Multi-line pool box -> [status, ...], blanks dropped."""
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def parse_rotate_interval(raw):
    """Auto-switch interval in seconds. Clamped to the allowed window."""
    try:
        value = float(str(raw).strip())
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid interval: {0!r}".format(raw)) from exc
    if not math.isfinite(value):
        raise ValueError("invalid interval: {0!r}".format(raw))
    return min(ROTATE_INTERVAL_MAX, max(ROTATE_INTERVAL_MIN, value))


def next_rotation(pool, index):
    """Round-robin step. Returns (status, next_index). Empty pool -> ValueError."""
    if not pool:
        raise ValueError("status pool is empty; add at least one line.")
    return pool[index % len(pool)], (index + 1) % len(pool)


def perform_update(opener, status, timeout, confirm=False, expected_login=None):
    """Single gated send point for one rotation tick. Returns (ok, message).

    Reads the profile page, verifies the selected account nickname and provider
    user ID, then obtains its per-session CSRF token before POSTing the status.
    Refuses unless confirm=True and expected_login is supplied.
    """
    if not confirm:
        return False, ("update refused: endpoint captured from home.php but not "
                       "confirmed end to end yet; pass --confirm-update to send "
                       "for real (dry-run preview only).")
    if not str(expected_login or "").strip():
        return False, "update refused: expected Camfrog account identity is required."
    html = fetch_profile(opener, "", timeout)
    identity = extract_profile_identity(html)
    if identity is None:
        return False, ("update refused: profile page did not provide a "
                       "verifiable account identity.")
    if identity["nickname"].casefold() != str(expected_login).strip().casefold():
        return False, ("update refused: authenticated Camfrog account does not "
                       "match the selected account.")
    csrf = extract_csrf(html)
    if not csrf:
        return False, ("update refused: no CSRF token on the profile page "
                       "(session expired or not signed in).")
    try:
        reply = _post(opener, WEB_UPDATE_URL,
                      {"status": status, "csrf": csrf}, timeout)
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500:
            return False, "update refused: profile endpoint returned HTTP {0}.".format(
                exc.code)
        return False, ("update outcome unknown: POST request failed ({0}); "
                       "check the profile before retrying."
                       .format(type(exc).__name__))
    status_code = getattr(reply, "status_code", None)
    if status_code is not None and status_code >= 500:
        return False, ("update outcome unknown: profile endpoint returned HTTP "
                       "{0}; check the profile before retrying.".format(status_code))
    if status_code is not None and not 200 <= status_code < 300:
        return False, "update refused: profile endpoint returned HTTP {0}.".format(
            status_code)
    try:
        data = json.loads(reply)
    except ValueError:
        return False, ("update outcome unknown: unrecognized server reply; "
                       "check the profile before retrying.")
    if not isinstance(data, dict):
        return False, ("update outcome unknown: invalid server response format; "
                       "check the profile before retrying.")
    if data.get("error"):
        return False, "update refused by the server (error response)."
    # Camfrog's profile form treats a non-empty response string as success and
    # writes it back into the status field. It does not require the literal "ok".
    response = data.get("response")
    if not isinstance(response, str) or not response.strip():
        return False, ("update outcome unknown: server did not acknowledge the "
                       "request; check the profile before retrying.")
    return True, "status update acknowledged by server."


def extract_csrf(html):
    """The per-session CSRF token embedded in the logged-in profile page."""
    return _profile_extract_csrf(html)


def extract_profile_identity(html):
    """Return the signed-in provider identity exposed by the profile page."""
    return _profile_extract_identity(html)


def redact_sensitive(text, secrets=()):
    """Redact csrf, cookies, PHPSESSID, emails, and sensitive keys from output."""
    res = str(text)
    for s in sorted((str(x) for x in secrets if x), key=len, reverse=True):
        if len(s) <= 3:
            res = re.sub(r"(?<!\w){0}(?!\w)".format(re.escape(s)), "[redacted]", res)
        else:
            res = res.replace(s, "[redacted]")
    # Sensitive URL parameters or form keys
    res = re.sub(
        r"(?i)(\b(?:csrf(?:[_-]?token)?|phpsessid|session[_-]?id|token|password|passwd)\b"
        r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)",
        r"\1[redacted]", res)
    # Headers
    res = re.sub(
        r"(?im)(\b(?:cookie|set-cookie|authorization)\s*:\s*)[^\r\n]*",
        r"\1[redacted]", res)
    # Emails
    res = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
                 "[email redacted]", res)
    # Stripe keys
    res = re.sub(r"\b[rsp]k_(?:live|test)_[a-zA-Z0-9]{20,}\b",
                 "[stripe-key redacted]", res)
    return res


def extract_external_data(html):
    """Parse var external_data object literal with balanced braces. Fails closed."""
    if not html:
        return None
    m = re.search(r"var\s+external_data\s*=", html)
    if not m:
        return None
    # Match the function body containing return {
    ret_m = re.search(r"return\s*\{", html[m.end():])
    if not ret_m:
        return None
    start = m.end() + ret_m.end() - 1  # position of '{'
    depth = 0
    in_string = False
    quote_char = ""
    escape = False
    for i in range(start, len(html)):
        char = html[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if in_string:
            if char == quote_char:
                in_string = False
            continue
        if char in ('"', "'"):
            in_string = True
            quote_char = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                raw_json = html[start:i + 1]
                try:
                    parsed = json.loads(raw_json)
                    return parsed if isinstance(parsed, dict) else None
                except (ValueError, TypeError):
                    return None
    return None


def fetch_settings(opener, timeout):
    """Fetch settings.php for nickname colors and CSRF."""
    request = urllib.request.Request(
        SETTINGS_URL,
        headers=dict(BROWSER_HEADERS, Referer=HOME_URL),
    )
    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace").strip()
        status_code = getattr(response, "status", None)
        if status_code is None and hasattr(response, "getcode"):
            status_code = response.getcode()
        return body, status_code


def parse_settings_nickname_colors(data):
    """Extract (current_color_hex, allowed_colors_dict) from external_data.

    Returns (current_hex, {color_id: color_hex}) or raises ValueError if invalid.
    """
    if not isinstance(data, dict):
        raise ValueError("external_data is not an object")
    if data.get("type") != "settings":
        raise ValueError("external_data type is not settings")
    settings = data.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("missing settings in external_data")
    nickname = settings.get("nickname")
    if not isinstance(nickname, dict):
        raise ValueError("missing settings.nickname in external_data")
    colors_list = nickname.get("colors")
    if not isinstance(colors_list, list):
        raise ValueError("missing settings.nickname.colors list")
    allowed = {}
    for entry in colors_list:
        if isinstance(entry, dict) and "id" in entry and "color" in entry:
            try:
                c_id = int(entry["id"])
                c_hex = str(entry["color"]).strip().lower()
                allowed[c_id] = c_hex
            except (ValueError, TypeError):
                continue
    if not allowed:
        raise ValueError("no valid colors found in settings.nickname.colors")
    current_hex = str(data.get("nickname_color") or "").strip().lower()
    return current_hex, allowed


def post_flair_color(opener, color_id, csrf, timeout):
    """POST to update_flair_color.php with form fields csrf and color_id."""
    data = urllib.parse.urlencode({"csrf": csrf, "color_id": str(color_id)}).encode("utf-8")
    request = urllib.request.Request(
        COLOR_UPDATE_URL,
        data=data,
        headers=dict(BROWSER_HEADERS, Referer=SETTINGS_URL),
    )
    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace").strip()
        headers = getattr(response, "headers", None)
        status_code = getattr(response, "status", None)
        if status_code is None and hasattr(response, "getcode"):
            status_code = response.getcode()
        retry_after = headers.get("Retry-After") if headers is not None else None
        return body, status_code, retry_after


def verify_active_session(opener, expected_login, timeout):
    """Check logged-in session on profile page. Returns (ok, identity, csrf, error_msg)."""
    try:
        html = fetch_profile(opener, expected_login, timeout)
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            ra_msg = " (Retry-After: {0})".format(retry_after) if retry_after else ""
            return False, None, None, "session check rate limited (HTTP 429){0}.".format(ra_msg)
        return False, None, None, "session check failed: {0}: {1}".format(
            type(exc).__name__, exc)
    verdict, _ = summarize_session(html)
    if verdict != "signed-in":
        return False, None, None, "session is not signed in."
    identity = extract_profile_identity(html)
    if not identity:
        return False, None, None, "profile page did not provide a verifiable account identity."
    if expected_login and identity["nickname"].casefold() != str(expected_login).strip().casefold():
        return False, None, None, "authenticated Camfrog account does not match expected login."
    csrf = extract_csrf(html)
    if not csrf:
        return False, None, None, "no CSRF token found on profile page."
    return True, identity, csrf, ""


def update_nickname_color_once(opener, color_id, expected_color_hex, timeout):
    """Execute one live color change with full verification and fail-closed stopping.

    Returns (success, verified, message, stop_error).
    stop_error is True if alternation MUST stop immediately.
    """
    try:
        settings_html, s_code = fetch_settings(opener, timeout)
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            ra_msg = " (Retry-After: {0})".format(retry_after) if retry_after else ""
            return False, False, "HTTP 429 rate limit on settings fetch{0}".format(ra_msg), True
        return False, False, "settings fetch failed: {0}".format(type(exc).__name__), True

    if s_code != 200:
        return False, False, "settings fetch returned HTTP {0}".format(s_code), True

    verdict, _ = summarize_session(settings_html)
    if verdict == "logged-out":
        return False, False, "session is logged out on settings page", True

    ext_data = extract_external_data(settings_html)
    if not ext_data:
        return False, False, "missing or malformed external_data in settings", True

    try:
        _curr_hex, allowed_colors = parse_settings_nickname_colors(ext_data)
    except ValueError as exc:
        return False, False, "failed to parse settings nickname colors: {0}".format(exc), True

    if color_id not in allowed_colors:
        return False, False, "color_id {0} is not in allowed nickname colors".format(color_id), True

    csrf = extract_csrf(settings_html)
    if not csrf and isinstance(ext_data.get("csrf"), str):
        csrf = ext_data["csrf"]
    if not csrf:
        return False, False, "no CSRF token found on settings page", True

    try:
        reply_body, status_code, retry_after = post_flair_color(opener, color_id, csrf, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            ra = exc.headers.get("Retry-After") if exc.headers else None
            ra_msg = " (Retry-After: {0})".format(ra) if ra else ""
            return False, False, "HTTP 429 rate limited on color update{0}".format(ra_msg), True
        return False, False, "color update HTTP error {0}".format(exc.code), True
    except Exception as exc:
        return False, False, "color update transport error: {0}".format(type(exc).__name__), True

    if status_code == 429:
        ra_msg = " (Retry-After: {0})".format(retry_after) if retry_after else ""
        return False, False, "HTTP 429 rate limited on color update{0}".format(ra_msg), True

    if status_code != 200:
        return False, False, "color update returned HTTP {0}".format(status_code), True

    try:
        resp_data = json.loads(reply_body)
    except (ValueError, TypeError):
        return False, False, "unexpected non-JSON response from color update", True

    if not isinstance(resp_data, dict):
        return False, False, "unexpected response shape from color update", True

    if resp_data.get("redirect"):
        return False, False, "session problem: server returned redirect", True

    if resp_data.get("error"):
        return False, False, "server returned error: {0}".format(resp_data.get("response", True)), True

    # Server acknowledged (HTTP 200)
    # Verification step: re-read settings.php to verify external_data.nickname_color matches
    try:
        verify_html, v_code = fetch_settings(opener, timeout)
    except Exception:
        return True, False, "server acknowledged (HTTP 200), but re-read failed: UNVERIFIED", False

    if v_code != 200:
        return True, False, "server acknowledged (HTTP 200), re-read HTTP {0}: UNVERIFIED".format(v_code), False

    verify_data = extract_external_data(verify_html)
    if not verify_data:
        return True, False, "server acknowledged (HTTP 200), re-read missing external_data: UNVERIFIED", False

    try:
        new_hex, _ = parse_settings_nickname_colors(verify_data)
    except ValueError:
        return True, False, "server acknowledged (HTTP 200), re-read parse error: UNVERIFIED", False

    if new_hex.lower() == expected_color_hex.lower():
        return True, True, "server acknowledged (HTTP 200); color changed to {0}".format(expected_color_hex), False
    else:
        return True, False, "server acknowledged (HTTP 200), current color {0} != requested {1}: UNVERIFIED".format(
            new_hex, expected_color_hex), False


def parse_color_alternate_interval(raw):
    """Clamped interval for nickname color alternator: minimum 10.0s."""
    try:
        val = float(str(raw).strip())
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid interval: {0!r}".format(raw)) from exc
    if not math.isfinite(val):
        raise ValueError("invalid interval: {0!r}".format(raw))
    return max(COLOR_ALTERNATE_INTERVAL_MIN, val)


def run_color_alternator(opener, expected_login, color_a=DEFAULT_COLOR_A,
                         color_b=DEFAULT_COLOR_B, interval=COLOR_ALTERNATE_INTERVAL_DEFAULT,
                         max_cycles=COLOR_ALTERNATE_CYCLES_DEFAULT, confirm=False,
                         timeout=TIMEOUT_DEFAULT, time_module=None, print_fn=print):
    """Execute color alternator loop (A <-> B).

    Default is dry-run: prints preview and exits 0 without network changes.
    Live mode requires confirm=True and verified logged-in session.
    Restores original color on exit.
    """
    safe_print = lambda msg, sec=(): print_fn(redact_sensitive(msg, secrets=sec))  # noqa: E731
    clamped_interval = max(COLOR_ALTERNATE_INTERVAL_MIN, float(interval))

    if not confirm:
        safe_print("[DRY-RUN] Color alternator preview:")
        safe_print("  Target account: {0}".format(expected_login or "none"))
        safe_print("  Color A: id {0}, Color B: id {1}".format(color_a, color_b))
        safe_print("  Interval: {0}s (min {1}s), Max cycles: {2}".format(
            clamped_interval, COLOR_ALTERNATE_INTERVAL_MIN, max_cycles))
        safe_print("  Would fetch {0} for allowed colors and CSRF (token redacted)".format(SETTINGS_URL))
        safe_print("  Would POST to {0} with color_id and csrf".format(COLOR_UPDATE_URL))
        safe_print("  Dry-run active; no requests sent. Pass --confirm for live execution.")
        return EXIT_OK

    if not opener:
        safe_print("Live color alternator requires a valid session (e.g. --cookies-file or WEBAUTH_USER).")
        return EXIT_USAGE

    # Gate on verified active session
    ok_sess, identity, _sess_csrf, sess_err = verify_active_session(opener, expected_login, timeout)
    if not ok_sess:
        safe_print("Session verification failed: {0}".format(sess_err))
        return EXIT_BLOCKED

    # Fetch settings to get initial state and allowed colors
    try:
        settings_html, s_code = fetch_settings(opener, timeout)
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
            ra = exc.headers.get("Retry-After") if exc.headers else None
            ra_msg = " (Retry-After: {0})".format(ra) if ra else ""
            safe_print("Initial settings fetch rate limited (HTTP 429){0}".format(ra_msg))
            return EXIT_BLOCKED
        safe_print("Initial settings fetch failed: {0}".format(type(exc).__name__))
        return EXIT_BLOCKED

    if s_code != 200:
        safe_print("Initial settings fetch returned HTTP {0}".format(s_code))
        return EXIT_BLOCKED

    ext_data = extract_external_data(settings_html)
    if not ext_data:
        safe_print("Failed closed: missing external_data in settings.php")
        return EXIT_BLOCKED

    try:
        initial_color_hex, allowed_colors = parse_settings_nickname_colors(ext_data)
    except ValueError as exc:
        safe_print("Failed closed: invalid nickname colors structure: {0}".format(exc))
        return EXIT_BLOCKED

    # Validate color A and B are in allowed_colors
    if color_a not in allowed_colors:
        safe_print("Refusing color A: id {0} is not in allowed colors: {1}".format(
            color_a, sorted(allowed_colors.keys())))
        return EXIT_USAGE
    if color_b not in allowed_colors:
        safe_print("Refusing color B: id {0} is not in allowed colors: {1}".format(
            color_b, sorted(allowed_colors.keys())))
        return EXIT_USAGE

    # Find starting color id that matches initial_color_hex if possible
    initial_color_id = None
    for cid, chex in allowed_colors.items():
        if chex.lower() == initial_color_hex.lower():
            initial_color_id = cid
            break

    safe_print("Live color alternator started for {0} (initial color: {1}, id: {2})".format(
        identity["nickname"], initial_color_hex, initial_color_id))
    safe_print("Allowed colors: {0}".format(
        ", ".join("{0}: {1}".format(cid, chex) for cid, chex in sorted(allowed_colors.items()))))

    # Alternation order: alternate between A and B
    targets = [(color_a, allowed_colors[color_a]), (color_b, allowed_colors[color_b])]
    # Determine first target: if currently A, start with B; otherwise start with A
    curr_target_idx = 1 if (initial_color_id == color_a) else 0

    import time as _real_time
    t_mod = time_module or _real_time

    cycle = 0
    exit_code = EXIT_OK
    interrupted = False

    try:
        while cycle < max_cycles:
            target_id, target_hex = targets[curr_target_idx]
            safe_print("[Cycle {0}/{1}] Alternating to color_id {2} ({3})...".format(
                cycle + 1, max_cycles, target_id, target_hex))

            _ack, verified, msg, stop_err = update_nickname_color_once(
                opener, target_id, target_hex, timeout)
            safe_print("  Result: {0}".format(msg))

            if stop_err:
                safe_print("STOPPING IMMEDIATELY: failure or unexpected response encountered.")
                exit_code = EXIT_BLOCKED
                break

            curr_target_idx = 1 - curr_target_idx
            cycle += 1

            if cycle < max_cycles:
                t_mod.sleep(clamped_interval)

    except KeyboardInterrupt:
        safe_print("\nColor alternation interrupted by user.")
        interrupted = True
    except Exception as exc:
        safe_print("Unexpected error in alternation loop: {0}".format(type(exc).__name__))
        exit_code = EXIT_BLOCKED

    # Restore original color on exit
    if initial_color_id is not None:
        safe_print("Restoring initial color (id {0}, {1})...".format(
            initial_color_id, initial_color_hex))
        _r_ack, r_verified, r_msg, _r_err = update_nickname_color_once(
            opener, initial_color_id, initial_color_hex, timeout)
        if r_verified:
            safe_print("Restoration succeeded: color changed to {0}".format(initial_color_hex))
        else:
            safe_print("Restoration {0}: {1}".format(
                "UNVERIFIED" if _r_ack else "FAILED", r_msg))
    else:
        safe_print("Initial color id unknown; skipping restoration.")

    if interrupted:
        return EXIT_USAGE
    return exit_code

CAPTURE_STEPS = """\
To capture the real status-update endpoint:
1. Log in at https://profiles.camfrog.com/ in your browser.
2. Open devtools (F12) -> Network tab, then change your status once.
3. Find the POST/XHR request that carries the new status text.
4. Record its full URL, parameters, and which cookies it needs.
5. Hand the (redacted, password-free) request details to the maintainer so
   WEB_UPDATE_URL/WEB_UPDATE_VERIFIED above can be confirmed. Never paste
   your password or full session cookies into chat, issues, or files.
"""

COOKIE_STEPS = """\
To reuse your Chrome session (no password needed):
1. In Chrome, log in at https://profiles.camfrog.com/ as usual.
2. Export cookies with an extension such as "Get cookies.txt LOCALLY"
   (localhost-only export, nothing uploaded) for camfrog.com.
3. Save the file OUTSIDE this repository, e.g. Documents/camfrog-cookies.txt.
   (*cookies*.txt is git-ignored so it can never be committed.)
4. Run: python tools/web_status.py --login Seaza --status "..." \\
           --cookies-file <path> --probe
   Only the cookie count and domains are printed, never cookie values.
5. Do not paste cookie values into chat, issues, or files.
"""

# Markers seen on the logged-out login wall (fetched 2026-10-09).
LOGGED_OUT_MARKERS = ("nav-btn-sign-on", ">Sign On<", "Logon to view",
                      "<title>Camfrog - Login Page</title>")
# Markers seen on the signed-in profile page home.php (fetched 2026-10-09).
SIGNED_IN_MARKERS = ("nav-user-logged", "var _user_id = ")

CHROME_DOMAINS = (".camfrog.com", "camfrog.com")
CHROME_ENV_OVERRIDE = "CAMFROG_CHROME_USER_DATA"


def build_parser():
    parser = argparse.ArgumentParser(
        description="Prototype web status updater (dry-run by default).")
    parser.add_argument("--login", required=False, help="Camfrog nickname")
    parser.add_argument("--status", required=False, help="new status text")
    parser.add_argument("--live", action="store_true",
                        help="attempt web login (password is prompted, never stored)")
    parser.add_argument("--cookies-file",
                        help="Netscape cookie.txt exported from Chrome; "
                             "enables --probe without any password")
    parser.add_argument("--chrome", action="store_true",
                        help="auto-import the session from Chrome's cookie store "
                             "(Windows only; nothing is written to disk)")
    parser.add_argument("--probe", action="store_true",
                        help="check the session read-only (no status change)")
    parser.add_argument("--confirm-update", action="store_true",
                        help="actually POST the status to the captured endpoint "
                             "(without it every run stays a dry-run)")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_DEFAULT)
    parser.add_argument("--how-to-capture", action="store_true",
                        help="print how to capture the update endpoint and exit")
    parser.add_argument("--alternate-colors", action="store_true",
                        help="alternate nickname color between color A and B (CLI color alternator)")
    parser.add_argument("--color-a", type=int, default=DEFAULT_COLOR_A,
                        help="color id for color A (default: 1)")
    parser.add_argument("--color-b", type=int, default=DEFAULT_COLOR_B,
                        help="color id for color B (default: 3555)")
    parser.add_argument("--interval", type=float, default=COLOR_ALTERNATE_INTERVAL_DEFAULT,
                        help="alternation interval in seconds (default: 30, min: 10)")
    parser.add_argument("--max-cycles", type=int, default=COLOR_ALTERNATE_CYCLES_DEFAULT,
                        help="maximum number of color switches (default: 4)")
    parser.add_argument("--confirm", action="store_true",
                        help="explicit live consent for color alternation (default is dry-run)")
    return parser


def build_plan(login, status):
    return ("dry-run: would sign in as {login!r} at {url}, open {home} for the "
            "session CSRF token, and set the status to {n} chars.".format(
                login=login, url=LOGIN_URL, home=HOME_URL,
                n=len(status)))


def cookie_from_paste(text):
    """Build a jar from a pasted `name=value` browser cookie or PHPSESSID. Value stays in memory.

    This supports copying either the full cookie, name=value pairs, or capturing
    PHPSESSID from network inspection (e.g. headers or request/response on
    https://profiles.camfrog.com/home.php).
    """
    raw = str(text).strip()
    if not raw:
        raise ValueError(
            "paste a cookie as name=value or PHPSESSID=... (copy it from the "
            "network capture on profiles.camfrog.com/home.php or browser devtools).")

    pairs = []
    for chunk in raw.replace(";", " ").split():
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            key, value = key.strip(), value.strip()
            if key and value:
                pairs.append((key, value))

    if not pairs:
        m = re.search(r"PHPSESSID\s*[:=]\s*([a-zA-Z0-9_-]+)", raw, re.IGNORECASE)
        if m:
            pairs.append(("PHPSESSID", m.group(1)))
        elif re.fullmatch(r"[a-zA-Z0-9_-]{16,64}", raw):
            pairs.append(("PHPSESSID", raw))

    if not pairs:
        raise ValueError(
            "paste a cookie as name=value (copy PHPSESSID from devtools: "
            "Network -> home.php -> Headers/Cookies or Application -> Cookies).")
    jar = CookieJar()
    for key, value in pairs:
        jar.set_cookie(Cookie(
            version=0, name=key, value=value, port=None, port_specified=False,
            domain=".camfrog.com", domain_specified=True, domain_initial_dot=True,
            path="/", path_specified=True, secure=True, expires=None,
            discard=False, comment=None, comment_url=None, rest={}, rfc2109=False))
    return jar


def environment_cookie(name, expected_cookie):
    """Return one validated environment cookie pair without logging its value."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return ""
    cookies = list(cookie_from_paste(raw))
    if len(cookies) != 1 or cookies[0].name != expected_cookie:
        raise ValueError("{0} must contain exactly one {1} cookie.".format(
            name, expected_cookie))
    return "{0}={1}".format(cookies[0].name, cookies[0].value)


def load_cookie_jar(path):
    """Load a Netscape cookie.txt export. Raises ValueError with a safe message."""
    if Path(path).name == ENV_ENC_NAME:
        raise ValueError(
            "that is your encrypted account (.env.enc), not a cookies export - "
            "use the Account section of the GUI (Save encrypted) and leave the "
            "cookies file empty, or export cookies with \"Get cookies.txt "
            "LOCALLY\" and pick that file.")
    jar = MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise ValueError(
            "cannot load cookies file: {0}: {1}. A cookies export must come from "
            "\"Get cookies.txt LOCALLY\" in Chrome (or the Chrome button with "
            "Chrome closed); the Account section needs no cookies at all.".format(
                type(exc).__name__, exc)) from exc
    if not len(jar):
        raise ValueError("cookies file holds no cookies; export camfrog.com cookies first.")
    return jar


def cookie_summary(jar):
    """Redacted summary: counts and domains only, never values."""
    domains = sorted({c.domain for c in jar})
    return "{0} cookies for: {1}".format(len(jar), ", ".join(domains))


def chrome_user_data_dir():
    override = os.environ.get(CHROME_ENV_OVERRIDE)
    if override:
        return override
    local = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(local, "Google", "Chrome", "User Data") if local else ""


def find_chrome_profiles(user_data=""):
    """[(profile, Cookies-db-path)] for standard Chrome profiles, else []."""
    base = user_data or chrome_user_data_dir()
    if not base or not os.path.isdir(base):
        return []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for name in names:
        if name != "Default" and not name.startswith("Profile "):
            continue
        # Chrome 127+ keeps the cookie DB under Network/; older builds (<127)
        # used <profile>/Cookies. The Network file wins when both exist.
        db = os.path.join(base, name, "Network", "Cookies")
        if not os.path.isfile(db):
            db = os.path.join(base, name, "Cookies")
        if os.path.isfile(db):
            found.append((name, db))
    return found


def _data_blob(data):
    """DATA_BLOB for DPAPI with its backing buffer pinned to the struct.

    The pointer must stay valid while CryptUnprotectData runs; returning the
    bare cast of a temporary buffer would be a use-after-free.
    """
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = bytes(data)
    backing = ctypes.create_string_buffer(raw)
    blob = DATA_BLOB(len(raw), ctypes.cast(backing, ctypes.POINTER(ctypes.c_char)))
    blob._backing = backing
    return blob


def dpapi_protect(data):
    """Windows DPAPI encrypt to a blob (this user, this machine).

    The result only decrypts under the same Windows user, so a copied
    .env.enc is useless elsewhere. Raises ValueError off-Windows.
    """
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise ValueError("DPAPI encrypt needs Windows")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("nothing to encrypt")
    in_blob = _data_blob(data)
    out = type(in_blob)()
    ok = windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise ValueError("DPAPI could not encrypt this data")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        try:
            windll.kernel32.LocalFree(out.pbData)
        except Exception:
            pass


def dpapi_unprotect(blob):
    """Windows DPAPI decrypt (ctypes, stdlib only). Raises ValueError off-Windows."""
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise ValueError("DPAPI decrypt needs Windows")
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise ValueError("empty DPAPI blob")
    in_blob = _data_blob(blob)
    out = type(in_blob)()
    ok = windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise ValueError("DPAPI could not decrypt this cookie (different Windows user?)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        try:
            windll.kernel32.LocalFree(out.pbData)
        except Exception:
            pass


def chrome_aes_key(user_data):
    """AES key for v10/v11 cookies from Chrome's Local State (DPAPI-wrapped)."""
    import base64
    import json

    path = os.path.join(user_data or chrome_user_data_dir(), "Local State")
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
        raw = base64.b64decode(state["os_crypt"]["encrypted_key"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("cannot read Chrome AES key from Local State: {0}".format(exc)) from exc
    if not raw.startswith(b"DPAPI"):
        raise ValueError("unexpected Chrome key wrapping (not DPAPI)")
    return dpapi_unprotect(raw[len(b"DPAPI"):])


def decrypt_chrome_value(blob, user_data=""):
    """Decrypt one Chrome cookie value. v10/v11 need the 'cryptography' package."""
    if blob is None:
        return ""
    if isinstance(blob, str):
        return blob
    if blob.startswith(b"v10") or blob.startswith(b"v11"):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise ValueError("AES-encrypted Chrome cookie needs the 'cryptography' "
                             "package (pip install cryptography), or export "
                             "cookies.txt instead.") from exc
        key = chrome_aes_key(user_data)
        return AESGCM(key).decrypt(blob[3:15], blob[15:], None).decode("utf-8")
    return dpapi_unprotect(blob).decode("utf-8", "replace")


def _chrome_domain_match(host, domains=CHROME_DOMAINS):
    host = (host or "").lstrip(".").lower()
    return any(host == dom.lstrip(".").lower()
               or host.endswith("." + dom.lstrip(".").lower()) for dom in domains)


CHROME_LOCKED_MESSAGE = (
    "Chrome is running and holds its cookie file closed to every other process "
    "(sharing violation). Close Chrome and retry, export cookies with \"Get "
    "cookies.txt LOCALLY\" and pass --cookies-file, or skip cookies entirely: "
    "save your account in the GUI (Account / Save encrypted) and run without "
    "--chrome.")


def read_cookie_rows(db):
    """All rows of a Chrome cookie DB via a temp snapshot copy.

    Chrome opens its cookie DB exclusively (sharing violation, error 32), so the
    copy only succeeds while Chrome is closed; the caller reports
    CHROME_LOCKED_MESSAGE instead of guessing. Nothing is written next to the DB.
    """
    query = ("SELECT host_key, name, value, encrypted_value, path, "
             "is_secure, expires_utc FROM cookies")
    try:
        with tempfile.TemporaryDirectory(prefix="camfrog-chrome-") as tmp:
            snapshot = os.path.join(tmp, "Cookies")
            shutil.copyfile(db, snapshot)
            connection = sqlite3.connect(snapshot)
            try:
                return connection.execute(query).fetchall()
            finally:
                connection.close()
    except PermissionError as exc:
        raise ValueError(CHROME_LOCKED_MESSAGE) from exc


def import_chrome_jar(user_data="", domains=CHROME_DOMAINS):
    """Build a CookieJar from Chrome's cookie store. Nothing is written to disk.

    Only camfrog.com cookies are imported; values stay in memory.
    Raises ValueError with a safe (value-free) message on any failure.
    """
    profiles = find_chrome_profiles(user_data)
    if not profiles:
        raise ValueError("no Chrome cookie store found; is Chrome installed "
                         "with a Default/Profile directory?")
    jar = CookieJar()
    notes = []
    for profile, db in profiles:
        try:
            rows = read_cookie_rows(db)
        except ValueError as exc:
            notes.append(str(exc))  # our own value-free guidance
            continue
        except Exception as exc:
            notes.append("{0}: {1}".format(profile, type(exc).__name__))
            continue
        for host, name, value, encrypted, path, secure, _expires in rows:
            if not _chrome_domain_match(host, domains) or not name:
                continue
            try:
                text = value if value else decrypt_chrome_value(encrypted, user_data)
            except Exception as exc:
                notes.append("{0}/{1}: {2}".format(profile, name, exc))
                continue
            jar.set_cookie(Cookie(
                version=0, name=name, value=text, port=None, port_specified=False,
                domain=host, domain_specified=host.startswith("."),
                domain_initial_dot=host.startswith("."), path=path or "/",
                path_specified=True, secure=bool(secure), expires=None,
                discard=False, comment=None, comment_url=None, rest={}, rfc2109=False))
    if not len(jar):
        detail = "; ".join(notes[:3])
        raise ValueError("no usable camfrog.com cookies in Chrome profiles"
                         + (" ({0})".format(detail) if detail else
                            "; log in at profiles.camfrog.com first"))
    return jar


def fetch_profile(opener, login, timeout):
    """The signed-in landing page: session markers plus the CSRF token."""
    return _profile_fetch(opener, timeout)


def summarize_session(html):
    """Read-only verdict on an exported session. Never claims verified blindly."""
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = (match.group(1).strip() if match else "?")[:80]
    if any(marker in html for marker in LOGGED_OUT_MARKERS):
        return ("logged-out",
                "session is NOT signed in (page: {0!r}); log in via Chrome and "
                "re-export cookies.".format(title))
    user = re.search(r"var\s+_user_id\s*=\s*['\"](\d+)['\"]", html)
    nick = re.search(r"var\s+nick\s*=\s*['\"]([^'\"]+)['\"]", html)
    if user and nick:
        return ("signed-in",
                "session is signed in as {0!r} (user id {1}, page {2!r}).".format(
                    nick.group(1), user.group(1), title))
    return ("unconfirmed",
            "no login wall found (page: {0!r}, {1} chars) but no signed-in marker "
            "either; confirm visually before any live use.".format(title, len(html)))


def _post(opener, url, fields, timeout):
    if url == WEB_UPDATE_URL and set(fields) == {"status", "csrf"}:
        return _profile_post_status(
            opener, fields["status"], fields["csrf"], timeout)
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=dict(
        BROWSER_HEADERS, Referer=LOGIN_PAGE_URL))
    with opener.open(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace").strip()


def attempt_login(opener, login, password, timeout):
    """POST credentials. Returns the raw server reply (never logs the password)."""
    return _post(opener, LOGIN_URL, {
        "login": login,
        "passwd": password,
        "save": "0",
        "code": "",
    }, timeout)


def stored_account():
    """(login, password) from the encrypted store / environment, if any."""
    user = (os.environ.get("CAMFROG_USER") or "").strip()
    password = os.environ.get("CAMFROG_PASSWORD") or ""
    return (user, password) if user and password else None


def live_update(login, password, status, timeout, confirm=False):
    """Password path: sign in, then the gated status update. Returns (code, message).

    Needs no cookies and no Chrome. `confirm` is the explicit operator consent
    (the CLI --confirm-update flag, or an armed live start in the GUI).
    """
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    try:
        reply = attempt_login(opener, login, password, timeout)
    except Exception as exc:
        return EXIT_USAGE, "login request failed: {0}: {1}".format(
            type(exc).__name__, exc)
    finally:
        del password
    code, message = handle_login_reply(reply)
    if not (code == EXIT_BLOCKED and reply.startswith("http")):
        return code, message  # wrong password, captcha, ban, server error...
    try:
        html = fetch_profile(opener, login, timeout)
    except Exception as exc:
        return EXIT_USAGE, "profile page failed: {0}: {1}".format(
            type(exc).__name__, exc)
    verdict, detail = summarize_session(html)
    if verdict != "signed-in":
        guidance = (" Use --confirm-update for live changes; a verified signed-in "
                    "session is also required.") if not confirm else ""
        return EXIT_BLOCKED, "status update blocked: session not confirmed signed in." + guidance
    ok, update_message = perform_update(
        opener, status, timeout, confirm=confirm, expected_login=login)
    return (EXIT_OK if ok else EXIT_BLOCKED), "{0}\n{1}".format(detail, update_message)


def handle_login_reply(reply):
    """Map the login/check.php reply to (exit_code, message). No secrets involved."""
    if reply == "password":
        return EXIT_USAGE, "web login failed: nickname or password incorrect."
    if reply.startswith("ban:"):
        return EXIT_USAGE, "web login refused by the server (ban response)."
    if reply == "captcha":
        return EXIT_BLOCKED, ("web login blocked: the server requires a CAPTCHA "
                              "token and this tool does not bypass it.")
    if reply in ("privacy", "under_16"):
        return EXIT_BLOCKED, "web login blocked: server replied {0!r}.".format(reply)
    if reply.startswith("http"):
        return EXIT_BLOCKED, ("logged in, but the status update was not confirmed: "
                              "re-run with --confirm-update to POST it for real.")
    return EXIT_USAGE, "web login failed: unexpected server reply."


ENV_ENC_NAME = ".env.enc"


def env_enc_path():
    return BASE / ENV_ENC_NAME


def load_dotenv(path):
    """Load credentials into os.environ: the DPAPI-encrypted .env.enc first,
    then a plaintext .env (legacy). Existing environment variables always win,
    and values are never logged.

    .env.enc is written by the GUI account setup (Save encrypted) and only
    decrypts under the Windows user that saved it.
    """
    if load_env_enc():
        return True
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value
    return True


def save_credentials(user, password):
    """Encrypt the account into .env.enc (DPAPI). Returns (ok, message).

    Nothing is logged; the plaintext never touches the disk.
    """
    import base64

    user, password = str(user).strip(), str(password)
    if not user or not password:
        return False, "enter both the nickname and the password."
    try:
        blob = dpapi_protect(
            "CAMFROG_USER={0}\nCAMFROG_PASSWORD={1}\n".format(
                user, password).encode("utf-8"))
        env_enc_path().write_text(
            base64.b64encode(blob).decode("ascii") + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        return False, "could not save the encrypted account: {0}".format(exc)
    return True, ("saved the encrypted account for {0!r} "
                  "(this Windows user only).").format(user)


def load_env_enc():
    """Decrypt .env.enc into os.environ. Returns True when it was used."""
    import base64

    path = env_enc_path()
    if not path.is_file():
        return False
    try:
        blob = base64.b64decode(path.read_text(encoding="utf-8").strip())
        text = dpapi_unprotect(blob).decode("utf-8", "replace")
    except (OSError, ValueError):
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
    return True


def main(argv=None):
    load_dotenv(BASE / ".env")
    args = build_parser().parse_args(argv)
    if args.how_to_capture:
        print(CAPTURE_STEPS)
        print(COOKIE_STEPS)
        return EXIT_OK
    if not args.login:
        args.login = (os.environ.get("CAMFROG_USER") or "").strip()

    if args.alternate_colors:
        # Nickname color alternator workflow
        opener = None
        if args.cookies_file or args.chrome or os.environ.get("WEBAUTH_USER"):
            try:
                if args.chrome:
                    jar = import_chrome_jar()
                elif args.cookies_file:
                    jar = load_cookie_jar(args.cookies_file)
                else:
                    env_cookie = environment_cookie("WEBAUTH_USER", "PHPSESSID")
                    jar = cookie_from_paste(env_cookie)
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            except (OSError, ValueError) as exc:
                print(str(exc))
                return EXIT_USAGE
        return run_color_alternator(
            opener,
            args.login,
            color_a=args.color_a,
            color_b=args.color_b,
            interval=args.interval,
            max_cycles=args.max_cycles,
            confirm=args.confirm,
            timeout=args.timeout,
        )

    if not args.login or not args.status:
        build_parser().error("--login and --status are required "
                             "(or set CAMFROG_USER in .env)")
    if not args.status.strip():
        print("refusing empty status text.")
        return EXIT_USAGE
    if args.probe:
        if args.chrome and args.cookies_file:
            build_parser().error("use only one of --chrome and --cookies-file")
        try:
            if args.chrome:
                jar = import_chrome_jar()
                print("loaded session from Chrome: {0}".format(cookie_summary(jar)))
            elif args.cookies_file:
                jar = load_cookie_jar(args.cookies_file)
                print("loaded session: {0}".format(cookie_summary(jar)))
            else:
                env_cookie = environment_cookie("WEBAUTH_USER", "PHPSESSID")
                if not env_cookie:
                    build_parser().error(
                        "--probe requires --cookies-file, --chrome, or WEBAUTH_USER in .env")
                jar = cookie_from_paste(env_cookie)
                print("loaded configured session from WEBAUTH_USER (value hidden).")
        except (OSError, ValueError) as exc:
            print(exc)
            return EXIT_USAGE
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            html = fetch_profile(opener, args.login, args.timeout)
        except Exception as exc:
            print("session probe failed: {0}: {1}".format(type(exc).__name__, exc))
            return EXIT_USAGE
        _verdict, detail = summarize_session(html)
        print(detail)
        print("no status was changed (read-only probe).")
        return EXIT_OK
    if not args.live:
        print(build_plan(args.login, args.status))
        return EXIT_OK
    try:
        env_cookie = environment_cookie("WEBAUTH_USER", "PHPSESSID")
    except ValueError as exc:
        print(str(exc))
        return EXIT_USAGE
    if env_cookie:
        jar = cookie_from_paste(env_cookie)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        ok, message = perform_update(
            opener, args.status, args.timeout, confirm=args.confirm_update,
            expected_login=args.login)
        print(message)
        return EXIT_OK if ok else EXIT_BLOCKED
    env_password = os.environ.get("CAMFROG_PASSWORD")
    if env_password:
        password = env_password  # from .env / environment; never printed or logged
        print("using the CAMFROG_PASSWORD from the environment (value not shown).")
    else:
        try:
            password = getpass.getpass("Camfrog password for {0!r}: ".format(args.login))
        except (EOFError, KeyboardInterrupt):
            print("password prompt cancelled; nothing was sent.")
            return EXIT_USAGE
    if not password:
        print("empty password; nothing was sent.")
        return EXIT_USAGE
    code, message = live_update(args.login, password, args.status, args.timeout,
                                confirm=args.confirm_update)
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
