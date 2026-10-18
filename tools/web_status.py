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
