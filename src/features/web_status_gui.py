"""Camfrog web status updater - self-contained (updater logic + GUI inlined, no shared imports)."""
import argparse
import datetime
import email.utils
import getpass
import hashlib
import hmac
import http.client
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import base64
import ipaddress
import random
import socket
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from pathlib import Path
from dataclasses import dataclass, field
from html.parser import HTMLParser
import queue
import threading
import unicodedata


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


# ============================================================
# Inlined updater logic (tools/web_status.py), main -> cli_main.
# ============================================================



# Frozen -> files live next to the exe; source -> the repo root (parent of tools/).
BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
    else Path(__file__).resolve().parent.parent

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(BASE / "camfrog"))
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
BOT_MANAGER_BASE_URL = "https://www.camfrog.com"
BOT_MANAGER_HOST = "www.camfrog.com"

# Captured 2026-10-09 from a logged-in https://profiles.camfrog.com/home.php
# session: the profile page's hopping box POSTs {status, csrf} here, and the
# csrf token is embedded in that same page. The path is implemented but stays
# gated: the first live round-trip needs an explicit --confirm-update.
WEB_UPDATE_URL = "https://profiles.camfrog.com/ajax/update_status.php"
HOME_URL = "https://profiles.camfrog.com/home.php"

DJ_CONTROL_PROTOCOL = 1
DJ_CONTROL_REQUEST_TTL_SECONDS = 30
LOCAL_MEDIA_SETTINGS = "web_status_media.json"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

ROTATE_INTERVAL_MIN = 30
ROTATE_INTERVAL_DEFAULT = 60
ROTATE_INTERVAL_MAX = 86400
LIVE_ROTATE_INTERVAL_MIN = 10.0

WEB_MARQUEE_STEP_SECONDS_DEFAULT = 0.5
WEB_MARQUEE_STEP_SECONDS_MIN = 0.5
WEB_MARQUEE_STEP_SECONDS_MAX = 3600.0
WEB_MARQUEE_STEP_SECONDS = WEB_MARQUEE_STEP_SECONDS_DEFAULT
WEB_STATUS_LOOP_COUNT_DEFAULT = 0  # zero means infinite
WEB_STATUS_LOOP_COUNT_MAX = 10000
# Keep aliases for existing source-level integrations; the GUI no longer has
# a Web Status Marquee mode.
WEB_MARQUEE_LOOP_COUNT_DEFAULT = WEB_STATUS_LOOP_COUNT_DEFAULT
WEB_MARQUEE_LOOP_COUNT_MAX = WEB_STATUS_LOOP_COUNT_MAX
LIVE_RETRY_MAX_ATTEMPTS = 5
LIVE_RATE_LIMIT_RETRY_BASE_SECONDS = 30.0
LIVE_TIMEOUT_RETRY_BASE_SECONDS = LIVE_ROTATE_INTERVAL_MIN
LIVE_RETRY_MAX_DELAY_SECONDS = 120.0
LIVE_RETRY_MAX_SERVER_WAIT_SECONDS = 86400.0
WEB_STATUS_THEME = {
    "background": "#f4f1e8",
    "surface": "#fffdfa",
    "surface_high": "#ebe5d9",
    "entry": "#ffffff",
    "border": "#d5c8ac",
    "gold": "#8b672b",
    "gold_hover": "#76541f",
    "teal": "#d6e9e8",
    "text": "#283648",
    "muted": "#626d7a",
    "danger": "#9f3740",
    "success": "#28744c",
    "error": "#a3303a",
    "log_text": "#2c3b4d",
}
WEB_MARQUEE_WIDTH = 28
WEB_MARQUEE_STRIDE = 2
WEB_MARQUEE_SEPARATOR = "   \u2022   "
WEB_MARQUEE_MAX_FRAMES = 80

WEB_STATUS_LOG_NAME = "web_status.log"
WEB_STATUS_LOG_MAX_BYTES = 512 * 1024
WEB_STATUS_LOG_BACKUPS = 3
WEB_STATUS_LOG_GUI_LINES = 200
WEB_STATUS_LOG_DETAIL_MAX_CHARS = 1000
WEB_STATUS_LOG_FAILURE_EVENTS = frozenset((
    "session_verification_failed",
    "status_update_failed",
    "status_update_unknown",
))
WEB_STATUS_LOG_DETAIL_EVENTS = WEB_STATUS_LOG_FAILURE_EVENTS | frozenset((
    "dry_run_rotation_started",
    "live_rotation_started",
    "live_retry_scheduled",
))
WEB_STATUS_LOG_EVENTS = {
    "session_verification_started": "Session verification started",
    "session_verification_succeeded": "Session verification succeeded",
    "session_verification_failed": "Session verification failed",
    "browser_login_started": "Browser login started",
    "browser_session_captured": "Browser session captured",
    "status_lines_saved": "Status lines saved",
    "dry_run_rotation_started": "Dry-run rotation started",
    "live_rotation_started": "Live rotation started",
    "marquee_completed": "Marquee completed configured rounds",
    "rotation_completed": "Rotation completed configured rounds",
    "status_previewed": "Dry-run status previewed",
    "status_update_succeeded": "Server acknowledged the live status update",
    "status_update_failed": "Live status update failed",
    "status_update_unknown": "Live status update outcome unknown",
    "live_retry_scheduled": "Live update retry scheduled",
    "rotation_stopped": "Rotation stopped",
}
WEB_STATUS_LOG_LABELS_TH = {
    "Session verification started": "เริ่มตรวจสอบเซสชัน",
    "Session verification succeeded": "ตรวจสอบเซสชันสำเร็จ",
    "Session verification failed": "ตรวจสอบเซสชันไม่สำเร็จ",
    "Browser login started": "เริ่มเข้าสู่ระบบผ่านเบราว์เซอร์",
    "Browser session captured": "รับเซสชันจากเบราว์เซอร์แล้ว",
    "Status lines saved": "บันทึกข้อความสถานะแล้ว",
    "Dry-run rotation started": "เริ่มวนสถานะแบบทดลอง",
    "Live rotation started": "เริ่มวนสถานะจริง",
    "Marquee completed configured rounds": "วนข้อความครบตามจำนวนรอบ",
    "Rotation completed configured rounds": "วนสถานะครบตามจำนวนรอบ",
    "Dry-run status previewed": "แสดงตัวอย่างสถานะแบบทดลอง",
    "Server acknowledged the live status update": "เซิร์ฟเวอร์ยืนยันรับคำขออัปเดตสถานะ",
    "Live status update failed": "อัปเดตสถานะจริงล้มเหลว",
    "Live status update outcome unknown": "ไม่ทราบผลการอัปเดตสถานะจริง",
    "Live update retry scheduled": "ตั้งเวลาลองอัปเดตสถานะจริงอีกครั้ง",
    "Rotation stopped": "หยุดการวนสถานะ",
}
WEB_STATUS_LOG_LABELS_EN = {
    thai: english for english, thai in WEB_STATUS_LOG_LABELS_TH.items()
}
_WEB_STATUS_LOG_LOCK = threading.Lock()
_GUI_CALLBACK_MARKER = object()


def _sanitize_web_status_detail(value, secrets=()):
    """Bound diagnostics and remove cookie, token, and caller-known secret values."""
    text = str(value)
    for secret in sorted((str(item) for item in secrets if item),
                         key=len, reverse=True):
        if len(secret) <= 3:
            text = re.sub(r"(?<!\w){0}(?!\w)".format(re.escape(secret)),
                          "[redacted]", text)
        else:
            text = text.replace(secret, "[redacted]")
    text = re.sub(
        r"(?im)(\b(?:cookie|set-cookie|authorization)\s*:\s*)[^\r\n]*",
        r"\1[redacted]", text)
    text = re.sub(
        r"(?i)(\b(?:php\s*session\s*id|phpsessid|session[_-]?id|csrf(?:[_-]?token)?|"
        r"access[_-]?token|auth[_-]?token|authorization|token|password|passwd|secret)\b"
        r"[\"']?\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^&\s,;}}\]]+)",
        r"\1[redacted]", text)
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
                  "[email redacted]", text)
    text = re.sub(r"(?<!\w)\+?\d[\d().\s-]{6,}\d(?!\w)",
                  "[number redacted]", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).replace("|", "/").strip()
    if len(text) > WEB_STATUS_LOG_DETAIL_MAX_CHARS:
        text = text[:WEB_STATUS_LOG_DETAIL_MAX_CHARS - 14].rstrip() + " [truncated]"
    return text


def _web_status_response_keys(data):
    """Return a small, safe summary of untrusted JSON object keys."""
    safe = []
    for key in data:
        if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,39}", key):
            if re.search(r"(?i)cookie|csrf|token|password|secret|session", key):
                safe.append("[sensitive-key]")
            else:
                safe.append(key)
        else:
            safe.append("[other-key]")
    return ",".join(sorted(set(safe))[:12]) or "none"


def _web_status_value_summary(value, secrets=()):
    if isinstance(value, str):
        return "string({0} chars): {1}".format(
            len(value), _sanitize_web_status_detail(value, secrets=secrets))
    if isinstance(value, (bool, int, float)) or value is None:
        return "{0}: {1}".format(type(value).__name__, value)
    if isinstance(value, (dict, list, tuple)):
        return "{0}({1} items)".format(type(value).__name__, len(value))
    return type(value).__name__


class _VisibleResponseText(HTMLParser):
    """Collect visible response text without HTML attributes or script contents."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style", "noscript"):
            self._hidden_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style", "noscript") and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if not self._hidden_depth and data.strip():
            self.parts.append(data.strip())


def _web_status_error_response_summary(value, secrets=()):
    """Summarize visible server error text while excluding markup and common PII."""
    if not isinstance(value, str):
        return _web_status_value_summary(value, secrets=secrets)
    parser = _VisibleResponseText()
    try:
        parser.feed(value)
        parser.close()
        visible = " ".join(parser.parts)
    except Exception:
        visible = value
    visible = _sanitize_web_status_detail(visible, secrets=secrets)
    if len(visible) > 300:
        visible = visible[:286].rstrip() + " [truncated]"
    return "string({0} chars); visible_text={1}".format(len(value), visible or "empty")


def _retry_after_seconds(value):
    """Parse Retry-After delta-seconds or HTTP-date without shortening it."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        try:
            delay = float(raw)
        except (ValueError, OverflowError):
            return None
    else:
        try:
            retry_at = email.utils.parsedate_to_datetime(raw)
            if retry_at is None:
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            delay = (retry_at.astimezone(datetime.timezone.utc) - now).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(delay):
        return None
    return max(0.0, delay)


def _session_cookie_redaction_values(cookie):
    if not cookie:
        return ()
    value = str(cookie).strip()
    result = [value]
    if "=" in value:
        raw = value.split("=", 1)[1].strip()
        if raw:
            result.append(raw)
    return tuple(result)


def _web_status_request_error(stage, exc, secrets=()):
    """Describe a failed HTTP request without recording response headers or secrets."""
    if isinstance(exc, urllib.error.HTTPError):
        parts = ["{0}: HTTPError HTTP {1}".format(stage, exc.code)]
        if exc.reason:
            parts.append("reason={0}".format(exc.reason))
        headers = exc.headers or {}
        value = headers.get("Content-Type")
        if value:
            parts.append("content_type={0}".format(value))
        retry_after = _retry_after_seconds(headers.get("Retry-After"))
        if retry_after is not None:
            parts.append("retry_after={0:g}".format(retry_after))
        try:
            body = exc.read(4096).decode("utf-8", "replace")
            if body:
                try:
                    parsed = json.loads(body)
                except ValueError:
                    parts.append("response_body={0} chars (not JSON)".format(len(body)))
                else:
                    if isinstance(parsed, dict):
                        parts.append("response_keys={0}".format(
                            _web_status_response_keys(parsed)))
                        if retry_after is None:
                            body_retry_after = _retry_after_seconds(
                                parsed.get("retry_after"))
                            if body_retry_after is not None:
                                parts.append("retry_after={0:g}".format(
                                    body_retry_after))
                        if parsed.get("error"):
                            parts.append("server_error={0}".format(
                                _web_status_value_summary(
                                    parsed.get("error"), secrets=secrets)))
                    else:
                        parts.append("response_json_type={0}".format(
                            type(parsed).__name__))
        except Exception:
            parts.append("response_body=unavailable")
        return _sanitize_web_status_detail("; ".join(parts), secrets=secrets)
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        description = "{0}: URLError reason={1}".format(stage, reason)
    else:
        description = "{0}: {1}: {2}".format(
            stage, type(exc).__name__, exc)
    return _sanitize_web_status_detail(description, secrets=secrets)


def web_status_log_path():
    return BASE / WEB_STATUS_LOG_NAME


def _web_status_log_entry(event, now=None, detail=None, secrets=(), language="en"):
    try:
        label = WEB_STATUS_LOG_EVENTS[event]
    except (KeyError, TypeError) as exc:
        raise ValueError("unknown activity") from exc
    if language == "th":
        label = WEB_STATUS_LOG_LABELS_TH.get(label, label)
    timestamp = now or datetime.datetime.now().astimezone()
    entry = "{0:%Y-%m-%d %H:%M:%S %z} | {1}".format(timestamp, label)
    if detail is not None:
        if event not in WEB_STATUS_LOG_DETAIL_EVENTS:
            raise ValueError(
                "details are only allowed for diagnostic or rotation events")
        sanitized = _sanitize_web_status_detail(detail, secrets=secrets)
        if sanitized:
            entry += " | " + sanitized
    return entry


def load_web_status_log(path=None):
    """Load only the bounded, most recent activity lines for the GUI."""
    log_path = Path(path) if path is not None else web_status_log_path()
    if not log_path.exists():
        return []
    with log_path.open("r", encoding="utf-8", errors="replace") as stream:
        return [line.rstrip("\r\n") for line in stream.readlines()[-WEB_STATUS_LOG_GUI_LINES:]
                if line.strip()]


def append_web_status_log(event, path=None, now=None, detail=None, secrets=(),
                          language="en"):
    """Append one allowlisted event with bounded, redacted details."""
    entry = _web_status_log_entry(
        event, now, detail=detail, secrets=secrets, language=language)
    log_path = Path(path) if path is not None else web_status_log_path()
    encoded = (entry + "\n").encode("utf-8")
    with _WEB_STATUS_LOG_LOCK:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            current_size = log_path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        if current_size and current_size + len(encoded) > WEB_STATUS_LOG_MAX_BYTES:
            for backup in range(WEB_STATUS_LOG_BACKUPS, 1, -1):
                source = Path(str(log_path) + ".{0}".format(backup - 1))
                target = Path(str(log_path) + ".{0}".format(backup))
                if source.exists():
                    os.replace(source, target)
            os.replace(log_path, Path(str(log_path) + ".1"))
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(entry + "\n")
    return entry


_WEB_MARQUEE_LEAD_VOWELS = set("\u0e40\u0e41\u0e42\u0e43\u0e44")


def _web_status_clusters(text):
    """Keep Thai marks, emoji modifiers, and leading vowels with their base."""
    out, prefix = [], ""
    for char in text:
        if char in _WEB_MARQUEE_LEAD_VOWELS:
            prefix += char
            continue
        attach = (unicodedata.category(char) in ("Mn", "Mc", "Me")
                  or char in "\u200d\ufe0f\ufe0e"
                  or (out and out[-1].endswith("\u200d")))
        if out and not prefix and attach:
            out[-1] += char
        else:
            out.append(prefix + char)
            prefix = ""
    if prefix:
        out.append(prefix)
    return out


def web_status_frames(text, enabled=True):
    """Build a bounded ticker for one status slot, then settle on its full text."""
    value = str(text).strip()
    if not value:
        return []
    if not enabled:
        return [value]
    clusters = _web_status_clusters(value)
    if len(clusters) <= WEB_MARQUEE_WIDTH:
        return [value]
    ticker = clusters + _web_status_clusters(WEB_MARQUEE_SEPARATOR)
    steps = min(WEB_MARQUEE_MAX_FRAMES,
                max(1, (len(ticker) + WEB_MARQUEE_STRIDE - 1) // WEB_MARQUEE_STRIDE))
    frames = []
    for frame_no in range(steps):
        start = (frame_no * WEB_MARQUEE_STRIDE) % len(ticker)
        frame = "".join(ticker[(start + offset) % len(ticker)]
                        for offset in range(WEB_MARQUEE_WIDTH)).strip()
        if frame and (not frames or frame != frames[-1]):
            frames.append(frame)
    if not frames or frames[-1] != value:
        frames.append(value)
    return frames


def default_music_dj_executable(base=None):
    """Find the sibling Music DJ package produced by build.bat."""
    root = Path(base or BASE)
    candidates = (
        root / "music-dj.exe",
        root.parent / "music-dj" / "music-dj.exe",
        root / "dist" / "music-dj" / "music-dj.exe",
    )
    return next((path.resolve() for path in candidates if path.is_file()), None)


def music_dj_control_request(executable, action, timeout=4.0, clock=None,
                             sleep=None, text=None):
    """Send a one-shot command to the Music DJ worker through its private data folder."""
    if action not in {"status", "skip", "clear", "speak"}:
        raise ValueError("Unsupported Music DJ control action.")
    if action == "speak":
        text = str(text or "").strip()
        if not text or len(text) > 240 or any(ord(char) < 32 or ord(char) == 127
                                              for char in text):
            raise ValueError("Voice text must be 1 to 240 printable characters.")
    elif text is not None:
        raise ValueError("Voice text is only valid for the speak action.")
    executable = Path(executable).expanduser().resolve()
    if executable.name.casefold() != "music-dj.exe":
        raise ValueError("Select the packaged music-dj.exe application.")
    directory = executable.parent / "dj-control"
    request_id = uuid.uuid4().hex
    request_path = directory / (request_id + ".request.json")
    response_path = directory / (request_id + ".response.json")
    now = time.time()
    payload = {
        "protocol": DJ_CONTROL_PROTOCOL,
        "request_id": request_id,
        "action": action,
        "created_at": now,
    }
    if action == "speak":
        payload["text"] = text
    directory.mkdir(parents=True, exist_ok=True)
    temp_path = directory / (request_id + ".tmp")
    try:
        temp_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n",
                             encoding="utf-8")
        os.replace(temp_path, request_path)
        deadline = (time.monotonic if clock is None else clock)() + timeout
        wait = time.sleep if sleep is None else sleep
        while (time.monotonic if clock is None else clock)() < deadline:
            try:
                raw = response_path.read_text(encoding="utf-8")
                response = json.loads(raw)
                if (not isinstance(response, dict) or
                        response.get("protocol") != DJ_CONTROL_PROTOCOL or
                        response.get("request_id") != request_id):
                    raise RuntimeError("Music DJ returned an invalid control response.")
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error") or
                                           "Music DJ rejected the command."))
                return response
            except FileNotFoundError:
                wait(0.1)
        raise TimeoutError("Music DJ is not running or did not answer the request.")
    finally:
        temp_path.unlink(missing_ok=True)
        request_path.unlink(missing_ok=True)
        response_path.unlink(missing_ok=True)


def music_dj_worker_state(executable, stale_after=4.0):
    """Return a recent worker snapshot, or None when no fresh worker is visible."""
    path = Path(executable).expanduser().resolve().parent / "dj-control" / "state.json"
    try:
        raw = path.read_text(encoding="utf-8")
        if len(raw) > 128_000:
            return None
        state = json.loads(raw)
        if not isinstance(state, dict):
            return None
        age = time.time() - float(state.get("updated_at", 0))
        if (state.get("protocol") != DJ_CONTROL_PROTOCOL or
                not state.get("running") or age < -5 or age > stale_after):
            return None
        return state
    except (OSError, ValueError, TypeError):
        return None


def music_dj_speech_summary(state, now=None):
    """Build a short TTS summary from the Music DJ status response."""
    current = state.get("current") if isinstance(state, dict) else None
    if not isinstance(current, dict) or not str(current.get("title", "")).strip():
        raise ValueError("Music DJ has no current song to speak.")

    def clip(value, limit):
        value = " ".join(str(value or "").split())
        value = "".join(char for char in value
                        if ord(char) >= 32 and ord(char) != 127)
        return value if len(value) <= limit else value[:limit - 1] + "…"

    playback = state.get("playback")
    elapsed = duration = None
    if isinstance(playback, dict):
        try:
            age = (time.time() if now is None else float(now)) - float(state.get("updated_at"))
            if 0 <= age <= 10:
                raw_elapsed = playback.get("elapsed_seconds")
                raw_duration = playback.get("duration_seconds")
                elapsed = float(raw_elapsed) + age if raw_elapsed is not None else None
                duration = float(raw_duration) if raw_duration is not None else None
        except (TypeError, ValueError):
            elapsed = duration = None

    def clock(seconds):
        total = max(0, int(seconds))
        minutes, seconds = divmod(total, 60)
        return "{0}:{1:02}".format(minutes, seconds) if minutes < 60 else \
            "{0}:{1:02}:{2:02}".format(minutes // 60, minutes % 60, seconds)

    if elapsed is None:
        timing = "play time unknown"
    elif duration and duration > 0:
        timing = "playback {0} of {1}".format(clock(elapsed), clock(duration))
    else:
        timing = "played {0}".format(clock(elapsed))
    following = (state.get("queue", [])[0]
                 if isinstance(state.get("queue"), list) and state.get("queue") else None)
    next_text = ("{0}, requested by {1}".format(
        clip(following.get("title"), 50), clip(following.get("user"), 24))
        if isinstance(following, dict) else "none")
    return "Now playing {0}, requested by {1}; {2}. Next song: {3}.".format(
        clip(current.get("title"), 55), clip(current.get("user"), 24), timing, next_text)


def run_music_dj_command(executable, action, timeout=30.0):
    """Run only the packaged app's start/stop CLI commands without a shell."""
    if action not in {"start", "stop"}:
        raise ValueError("Unsupported Music DJ command.")
    if os.name != "nt":
        raise OSError("Music DJ process control requires Windows.")
    executable = Path(executable).expanduser().resolve()
    if executable.name.casefold() != "music-dj.exe" or not executable.is_file():
        raise ValueError("Select an existing packaged music-dj.exe application.")
    flags = 0x08000000  # CREATE_NO_WINDOW
    result = subprocess.run(
        [str(executable), action], cwd=str(executable.parent),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True,
        creationflags=flags, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("Music DJ {0} failed with exit code {1}.".format(
            action, result.returncode))
    return True


def normalize_vmix_api_url(value):
    """Allow only a local vMix HTTP API address to avoid remote control requests."""
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Use a local HTTP address such as http://127.0.0.1:8088.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The vMix address cannot contain credentials or extra parameters.")
    try:
        if parsed.hostname.casefold() == "localhost":
            addresses = socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
            local_host = bool(addresses) and all(
                ipaddress.ip_address(item[4][0]).is_loopback for item in addresses)
        else:
            local_host = ipaddress.ip_address(parsed.hostname).is_loopback
    except (OSError, ValueError):
        local_host = False
    if not local_host:
        raise ValueError("For safety, vMix control is limited to this PC.")
    if parsed.path not in ("", "/", "/api", "/api/"):
        raise ValueError("Use the vMix Web Controller origin, without a custom path.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise ValueError("The vMix address has an invalid port.") from None
    if not 1 <= port <= 65535:
        raise ValueError("The vMix address has an invalid port.")
    host = "[::1]" if parsed.hostname == "::1" else parsed.hostname
    return "{0}://{1}:{2}/api/".format(parsed.scheme, host, port)


def vmix_api_request(base_url, parameters=None, timeout=4.0):
    api_url = normalize_vmix_api_url(base_url)
    query = urllib.parse.urlencode(parameters or {})
    request = urllib.request.Request(
        api_url + ("?" + query if query else ""),
        headers={"Accept": "application/xml,text/xml,*/*",
                 "User-Agent": "Camfrog-Web-Status/1"})
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _VmixNoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(1_000_001)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Could not reach the local vMix HTTP API.") from None
    if len(payload) > 1_000_000:
        raise RuntimeError("vMix returned an oversized API response.")
    return payload


class _VmixNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow a vMix API redirect outside the explicitly selected local API."""

    def redirect_request(self, _request, _response, _code, _message, _headers, _new_url):
        return None


def fetch_vmix_inputs(base_url, timeout=4.0):
    try:
        root = ET.fromstring(
            vmix_api_request(base_url, timeout=timeout),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (ET.ParseError, DefusedXmlException):
        raise RuntimeError("vMix returned invalid XML.") from None
    inputs = []
    container = root.find("inputs")
    for item in list(container) if container is not None else []:
        key = item.get("key", "")
        number = item.get("number", "")
        title = item.get("title", "").strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", key) or not number.isdigit():
            continue
        inputs.append({"key": key, "number": int(number),
                       "title": title or "Input {0}".format(number),
                       "type": item.get("type", "")})
    return inputs


def control_vmix_input(base_url, function, input_key, timeout=4.0):
    if function not in {"PreviewInput", "CutDirect"}:
        raise ValueError("Unsupported vMix action.")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", str(input_key or "")):
        raise ValueError("Select a valid input returned by vMix.")
    vmix_api_request(base_url, {"Function": function, "Input": input_key}, timeout)
    return fetch_vmix_inputs(base_url, timeout)

WEB_STATUS_THEME = {
    "background": "#f4f1e8",
    "surface": "#fffdfa",
    "surface_high": "#ebe5d9",
    "entry": "#ffffff",
    "border": "#d5c8ac",
    "accent": "#8b672b",
    "accent_hover": "#76541f",
    "gold": "#8b672b",
    "gold_hover": "#76541f",
    "teal": "#d6e9e8",
    "button_text": "#283648",
    "button_active": "#d5c8ac",
    "button_pressed": "#c2b392",
    "text": "#283648",
    "muted": "#626d7a",
    "danger": "#9f3740",
    "success": "#28744c",
    "error": "#a3303a",
    "log_text": "#2c3b4d",
}

WEB_STATUS_GUI_TEXT_TH = {
    "ZChat Web Status": "สถานะเว็บ ZChat",
    "Web Status": "สถานะเว็บ",
    "Setup": "ตั้งค่า",
    "Account & Session": "บัญชีและเซสชัน",
    "Session": "เซสชัน",
    "Bot Manager": "ตัวจัดการบอท",
    "Bot": "บอท",
    "Music DJ": "ดีเจเพลง",
    "Local Music DJ": "Music DJ ในเครื่องนี้",
    "Local ZeaZDev room Music DJ": "ZeaZDev room Music DJ ในเครื่องนี้",
    "Select music-dj.exe": "เลือก music-dj.exe",
    "Select music-dj.exe to connect to the local DJ.":
        "เลือก music-dj.exe เพื่อเชื่อมต่อกับ DJ ในเครื่อง",
    "Select an existing packaged music-dj.exe application.":
        "เลือกไฟล์ music-dj.exe ที่แพ็กไว้แล้ว",
    "Opened the Music DJ window.": "เปิดหน้าต่าง Music DJ แล้ว",
    "DJ worker connected; queue read from the Music DJ owner.":
        "เชื่อมต่อกับบอท DJ แล้ว อ่านคิวจากโปรแกรม Music DJ",
    "Music DJ worker stopped.": "หยุดบอท Music DJ แล้ว",
    "Now playing": "กำลังเล่น",
    "Song": "เพลง",
    "Requested by": "ผู้ขอ",
    "Local vMix control": "ควบคุม vMix ในเครื่อง",
    "Refresh vMix inputs and select one first.":
        "โหลดอินพุต vMix แล้วเลือกอินพุตก่อน",
    "Send this input directly to the live vMix output?":
        "ส่งอินพุตนี้ไปยังภาพออกอากาศ vMix ทันทีหรือไม่?",
    "vMix connected; {0} inputs returned by the local API.":
        "เชื่อมต่อ vMix แล้ว API ในเครื่องส่งอินพุตกลับมา {0} รายการ",
    "vMix accepted {0}; confirm program output in vMix and Camfrog.":
        "vMix รับคำสั่ง {0} แล้ว ตรวจภาพออกอากาศใน vMix และ Camfrog",
    "For safety, vMix control is limited to this PC.":
        "เพื่อความปลอดภัย ควบคุม vMix ได้เฉพาะเครื่องนี้",
    "Could not reach the local vMix HTTP API.":
        "เชื่อมต่อ vMix HTTP API ในเครื่องไม่ได้",
    "Browse...": "เลือกไฟล์...",
    "Open Music DJ": "เปิด Music DJ",
    "Start DJ worker": "เริ่มบอท DJ",
    "Stop DJ worker": "หยุดบอท DJ",
    "Refresh queue": "โหลดคิวใหม่",
    "Skip current": "ข้ามเพลงปัจจุบัน",
    "Clear queue": "ล้างคิว",
    "Voice message": "ข้อความเสียง",
    "Speak to room": "พูดเข้าห้อง",
    "Speak current song": "พูดข้อมูลเพลงปัจจุบัน",
    "Voice text: up to 240 characters. Route Windows output to the Camfrog mic.":
        "ข้อความเสียงไม่เกิน 240 ตัวอักษร ต่อเอาต์พุต Windows เข้าไมค์ Camfrog",
    "Voice request accepted by Music DJ.": "Music DJ รับคำขอเสียงแล้ว",
    "Music DJ executable": "ไฟล์โปรแกรม Music DJ",
    "vMix HTTP API": "vMix HTTP API",
    "Refresh inputs": "โหลดอินพุตใหม่",
    "Preview input": "ดูตัวอย่างอินพุต",
    "Cut to output": "ตัดภาพออกอากาศ",
    "Restore 800x600": "คืนขนาด 800x600",
    "Tray": "ถาดระบบ",
    "Status rotation": "การสลับสถานะ",
    "Nickname": "ชื่อเล่น",
    "Status lines": "ข้อความสถานะ",
    "Save to webtext.db": "บันทึกลง webtext.db",
    "Switch every": "สลับทุก",
    "s (min 30)": "วินาที (ขั้นต่ำ 30)",
    "Timeout": "หมดเวลา",
    "Send live (otherwise dry-run preview only)":
        "ส่งสถานะจริง (ปิดไว้ = ทดลองเท่านั้น)",
    ("Dry-run is on by default; enable Send live before status updates are sent."):
        "ค่าเริ่มต้นเป็นโหมดทดลอง เปิดส่งสถานะจริงก่อนอัปเดตสถานะ",
    "Session (PHPSESSID via iframe / web login)":
        "เซสชัน (PHPSESSID ผ่าน iframe / เว็บ)",
    ("Log in in your browser (via iframe/web login). In devtools Network tab, "
     "inspect https://profiles.camfrog.com/home.php to get PHPSESSID=..., "
     "paste below, then press 'Login' to verify and arm automation."):
        ("เข้าสู่ระบบในเบราว์เซอร์ แล้วเปิดแท็บ Network ใน devtools "
         "ตรวจ https://profiles.camfrog.com/home.php เพื่อหา PHPSESSID=... "
         "วางค่าในช่องด้านล่าง แล้วกด Login เพื่อตรวจสอบเซสชัน"),
    "Open login page": "เปิดหน้าเข้าสู่ระบบ",
    "Login": "เข้าสู่ระบบ",
    "Login in browser": "เข้าสู่ระบบผ่านเบราว์เซอร์",
    "Preview plan": "ดูตัวอย่างแผน",
    "Preview": "ดูแผน",
    "Start": "เริ่ม",
    "Stop": "หยุด",
    "Probe session": "ตรวจสอบเซสชัน",
    "Probe": "เช็ก",
    "Update": "อัปเดต",
    "How to capture": "วิธีรับ PHPSESSID",
    "Help": "ช่วย",
    "More": "เพิ่มเติม",
    "cloudbot": "cloudbot",
    "Dry-run is on. Nothing has been sent.":
        "โหมดทดลองเปิดอยู่ ยังไม่มีการส่งสถานะ",
    "System tray is unavailable; minimized to the taskbar.":
        "ไม่พบถาดระบบ จึงย่อหน้าต่างไปที่แถบงาน",
    "Loading pages...": "กำลังโหลดหน้า...",
    "Camfrog Bot Manager": "ตัวจัดการบอท Camfrog",
    "cloudbot parameter": "พารามิเตอร์ cloudbot",
    "Connect": "เชื่อมต่อ",
    "Disconnect": "ตัดการเชื่อมต่อ",
    "Stop bot": "หยุดบอท",
    "Log out": "ออกจากระบบ",
    "Disconnected": "ยังไม่ได้เชื่อมต่อ",
    "Connected": "เชื่อมต่อแล้ว",
    "Connect to Camfrog Bot Manager to load Room, Profile, and Plugins.":
        "เชื่อมต่อ Camfrog Bot Manager เพื่อโหลด Room, Profile และ Plugins",
    "Room": "ห้อง",
    "Profile": "โปรไฟล์",
    "Plugins": "ปลั๊กอิน",
    "Activity": "กิจกรรม",
    "Plugin catalog": "รายการปลั๊กอิน",
    "Find": "ค้นหา",
    "Refresh": "โหลดใหม่",
    "Apply changes": "ใช้การเปลี่ยนแปลง",
    "No plugins loaded.": "ยังไม่ได้โหลดปลั๊กอิน",
    "Reload settings": "โหลดการตั้งค่าใหม่",
    "Save settings": "บันทึกการตั้งค่า",
    "Save": "บันทึก",
    "Reload": "โหลดใหม่",
    "Choose file": "เลือกไฟล์",
    "No file selected": "ยังไม่ได้เลือกไฟล์",
    "Options": "ตัวเลือก",
    "No options available from Camfrog.": "Camfrog ไม่มีตัวเลือกให้แสดง",
    "No roles returned from Camfrog.": "Camfrog ไม่ได้ส่งรายการบทบาทกลับมา",
    "No editable settings were returned.": "ไม่พบการตั้งค่าที่แก้ไขได้",
    "Message formatting": "รูปแบบข้อความ",
    "Font": "แบบอักษร",
    "Size": "ขนาด",
    "Color": "สี",
    "Auto Loop Color": "สลับสีอัตโนมัติ",
    "Choose": "เลือก",
    "Bold": "ตัวหนา",
    "Italic": "ตัวเอียง",
    "Underline": "ขีดเส้นใต้",
    "Strikeout": "ขีดทับ",
    "8 ball": "8 บอล",
    "IM control": "ควบคุม IM",
    "Messenger": "เมสเซนเจอร์",
    "Greeter": "ระบบต้อนรับ",
    "Math trivia": "เกมตอบคำถามคณิตศาสตร์",
    "Bad words": "คำต้องห้าม",
    "Trivia": "เกมตอบคำถาม",
    "Word trivia": "เกมทายคำศัพท์",
    "Roulette": "รูเล็ต",
    "Ghost": "โกสต์",
    "Who banned": "ใครแบน",
    "Talkback": "ตอบกลับ",
    "Dice": "ลูกเต๋า",
    "DJ Manager": "จัดการดีเจ",
    "Auto Moderator": "ผู้ดูแลอัตโนมัติ",
    "Enabled": "เปิดใช้งาน",
    "Disabled": "ปิดใช้งาน",
    "Enable": "เปิดใช้งาน",
    "Disable": "ปิดใช้งาน",
    "Yes": "ใช่",
    "No": "ไม่ใช่",
    "On": "เปิด",
    "Off": "ปิด",
    "True": "จริง",
    "False": "เท็จ",
    "None": "ไม่มี",
    "Any": "ทุกแบบ",
    "All": "ทั้งหมด",
    "Default": "ค่าเริ่มต้น",
    "Basic": "พื้นฐาน",
    "Simple": "ง่าย",
    "Everyone": "ทุกคน",
    "Everybody": "ทุกคน",
    "User": "ผู้ใช้",
    "Users": "ผู้ใช้",
    "Owner": "เจ้าของ",
    "Owners": "เจ้าของ",
    "Administrator": "ผู้ดูแลระบบ",
    "Administrators": "ผู้ดูแลระบบ",
    "Admin": "ผู้ดูแลระบบ",
    "Moderator": "ผู้ดูแลห้อง",
    "Moderators": "ผู้ดูแลห้อง",
    "Member": "สมาชิก",
    "Members": "สมาชิก",
    "Guest": "ผู้เยี่ยมชม",
    "Guests": "ผู้เยี่ยมชม",
    "Male": "ชาย",
    "Female": "หญิง",
    "Both": "ทั้งสองเพศ",
    "Unknown": "ไม่ทราบ",
    "Public": "สาธารณะ",
    "Private": "ส่วนตัว",
    "In the room": "ในห้อง",
    "In chat": "ในแชท",
    "Chat": "แชท",
    "Room message": "ข้อความในห้อง",
    "Notify everyone": "แจ้งเตือนทุกคน",
    "Notify moderators only": "แจ้งเตือนผู้ดูแลเท่านั้น",
    "Easy": "ง่าย",
    "Medium": "ปานกลาง",
    "Hard": "ยาก",
    "Addition": "บวก",
    "Subtraction": "ลบ",
    "Multiplication": "คูณ",
    "Division": "หาร",
    "Second": "วินาที",
    "Seconds": "วินาที",
    "Minute": "นาที",
    "Minutes": "นาที",
    "Hour": "ชั่วโมง",
    "Hours": "ชั่วโมง",
    "Day": "วัน",
    "Days": "วัน",
    "Kick": "เตะออก",
    "Ban": "แบน",
    "Kick and ban": "เตะออกและแบน",
    "Regular expression": "นิพจน์ทั่วไป",
    "Regex": "Regex",
    "Show answer": "แสดงคำตอบ",
    "Hide answer": "ซ่อนคำตอบ",
    "Notify": "แจ้งเตือน",
    "Do not notify": "ไม่ต้องแจ้งเตือน",
    "4-sided": "4 หน้า",
    "6-sided": "6 หน้า",
    "8-sided": "8 หน้า",
    "10-sided": "10 หน้า",
    "12-sided": "12 หน้า",
    "20-sided": "20 หน้า",
    "Bot name": "ชื่อบอท",
    "Response message": "ข้อความตอบกลับ",
    "Owner list": "รายชื่อเจ้าของ",
    "Message interval": "ช่วงเวลาส่งข้อความ",
    "Question interval": "ช่วงเวลาคำถาม",
    "Time limit": "เวลาจำกัด",
    "Violation count": "จำนวนการละเมิด",
    "Number range": "ช่วงตัวเลข",
    "Operator level": "ระดับตัวดำเนินการ",
    "Dice type": "ชนิดลูกเต๋า",
    "Number of dice": "จำนวนลูกเต๋า",
    "Microphone turn duration": "ระยะเวลารับไมโครโฟน",
    "Allow moderators only": "อนุญาตเฉพาะผู้ดูแล",
    "Reports before penalty": "จำนวนรายงานก่อนลงโทษ",
    "Loading settings:": "กำลังโหลดการตั้งค่า:",
    "Loading settings for": "กำลังโหลดการตั้งค่าของ",
    "Settings:": "การตั้งค่า:",
    "Loaded settings for": "โหลดการตั้งค่าของ",
    "Could not load settings:": "โหลดการตั้งค่าไม่สำเร็จ:",
    "Select a plugin card to open its settings.":
        "เลือกปลั๊กอินเพื่อเปิดการตั้งค่า",
    "Connect to load the installed plugins.":
        "เชื่อมต่อเพื่อโหลดรายการปลั๊กอินที่ติดตั้ง",
    "No plugins match this search.": "ไม่พบปลั๊กอินที่ตรงกับคำค้น",
    "Camfrog returned no installed plugins.":
        "Camfrog ไม่พบปลั๊กอินที่ติดตั้งไว้",
    "No installed plugin settings are available.":
        "ไม่มีการตั้งค่าของปลั๊กอินที่ติดตั้ง",
    "The selected plugin is no longer in Camfrog's list.":
        "ปลั๊กอินที่เลือกไม่อยู่ในรายการของ Camfrog แล้ว",
}


class _WindowsTrayIcon:
    """Native notification-area icon driven by Tk's existing message loop."""

    WM_TRAY = 0x8000 + 41
    ID_SHOW = 1001
    ID_EXIT = 1002

    def __init__(self, root, title, on_show, on_exit, icon_path=None):
        self.root = root
        self.on_show = on_show
        self.on_exit = on_exit
        self.title = str(title)[:127]
        self.active = False
        self._class_atom = None
        self.hwnd = None
        self.icon = None
        if os.name != "nt":
            return
        try:
            import win32con
            import win32gui

            self.win32con = win32con
            self.win32gui = win32gui
            self.hinstance = win32gui.GetModuleHandle(None)
            self.class_name = "CamfrogWebStatusTray_{0}_{1:x}".format(
                os.getpid(), id(self))
            window_class = win32gui.WNDCLASS()
            window_class.hInstance = self.hinstance
            window_class.lpszClassName = self.class_name
            window_class.lpfnWndProc = self._window_proc
            self._class_atom = win32gui.RegisterClass(window_class)
            self.hwnd = win32gui.CreateWindowEx(
                0, self.class_name, self.title, 0,
                0, 0, 0, 0, 0, 0, self.hinstance, None)
            self.icon = self._load_icon(icon_path)
            flags = (win32gui.NIF_ICON | win32gui.NIF_MESSAGE |
                     win32gui.NIF_TIP)
            data = (self.hwnd, 1, flags, self.WM_TRAY, self.icon, self.title)
            # pywin32 returns None for a successful Shell_NotifyIcon call.
            # Only an explicit False indicates that Windows rejected the icon.
            if win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, data) is False:
                raise RuntimeError("Windows rejected the tray icon.")
            self.active = True
            self.root.after(100, self._pump)
        except Exception:
            self.shutdown()

    def _load_icon(self, icon_path):
        gui, con = self.win32gui, self.win32con
        candidates = []
        if icon_path:
            candidates.append(Path(icon_path))
        frozen_dir = getattr(__import__("sys"), "_MEIPASS", None)
        if frozen_dir:
            candidates.append(Path(frozen_dir) / "app.ico")
        candidates.append(Path(__import__("sys").executable).resolve().parent /
                          "app.ico")
        candidates.append(Path(__file__).resolve().with_name("app.ico"))
        for path in candidates:
            try:
                if path.is_file():
                    icon = gui.LoadImage(
                        0, str(path), con.IMAGE_ICON, 0, 0,
                        con.LR_LOADFROMFILE | con.LR_DEFAULTSIZE)
                    if icon:
                        return icon
            except Exception:
                continue
        return gui.LoadIcon(0, con.IDI_APPLICATION)

    def _window_proc(self, hwnd, message, wparam, lparam):
        gui, con = self.win32gui, self.win32con
        if message == self.WM_TRAY:
            if lparam in (con.WM_LBUTTONUP, con.WM_LBUTTONDBLCLK):
                self.on_show()
            elif lparam == con.WM_RBUTTONUP:
                self._show_menu()
            return 0
        if message == con.WM_COMMAND:
            command = wparam & 0xFFFF
            if command == self.ID_SHOW:
                self.on_show()
            elif command == self.ID_EXIT:
                self.on_exit()
            return 0
        return gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _show_menu(self):
        gui, con = self.win32gui, self.win32con
        menu = gui.CreatePopupMenu()
        gui.AppendMenu(menu, con.MF_STRING, self.ID_SHOW, "Open window")
        gui.AppendMenu(menu, con.MF_SEPARATOR, 0, None)
        gui.AppendMenu(menu, con.MF_STRING, self.ID_EXIT, "Exit GUI")
        x, y = gui.GetCursorPos()
        gui.SetForegroundWindow(self.hwnd)
        gui.TrackPopupMenu(menu, con.TPM_RIGHTBUTTON, x, y, 0, self.hwnd, None)
        gui.PostMessage(self.hwnd, con.WM_NULL, 0, 0)
        gui.DestroyMenu(menu)

    def _pump(self):
        if not self.active:
            return
        try:
            self.win32gui.PumpWaitingMessages()
        except Exception:
            self.shutdown()
            return
        if self.active:
            try:
                self.root.after(100, self._pump)
            except Exception:
                self.shutdown()

    def shutdown(self):
        if not self.active and not self.hwnd and not self._class_atom:
            return
        gui = getattr(self, "win32gui", None)
        if gui:
            try:
                if self.hwnd:
                    gui.Shell_NotifyIcon(gui.NIM_DELETE, (self.hwnd, 1))
            except Exception:
                pass
            try:
                if self.hwnd:
                    gui.DestroyWindow(self.hwnd)
            except Exception:
                pass
            try:
                if self._class_atom:
                    gui.UnregisterClass(self.class_name, self.hinstance)
            except Exception:
                pass
        self.active = False
        self.hwnd = None
        self._class_atom = None


@dataclass
class BotManagerOption:
    value: str
    label: str
    selected: bool = False


@dataclass
class BotManagerField:
    tag: str
    name: str = ""
    identifier: str = ""
    kind: str = ""
    value: str = field(default="", repr=False)
    checked: bool = False
    label: str = ""
    options: list = field(default_factory=list, repr=False)
    attributes: dict = field(default_factory=dict, repr=False)


@dataclass
class BotManagerPage:
    path: str
    method: str
    action: str
    enctype: str
    fields: list = field(default_factory=list, repr=False)
    links: list = field(default_factory=list, repr=False)
    plugin_states: list = field(default_factory=list, repr=False)
    headings: list = field(default_factory=list)


class _BotManagerPageParser(HTMLParser):
    """Read the remote manager's current form without retaining rendered markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.method = "GET"
        self.action = ""
        self.enctype = "application/x-www-form-urlencoded"
        self.fields = []
        self.labels = {}
        self.links = []
        self.plugin_states = []
        self.headings = []
        self._form_seen = False
        self._label = None
        self._link = None
        self._heading = None
        self._textarea = None
        self._select = None
        self._option = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and not self._form_seen:
            self._form_seen = True
            self.method = attributes.get("method", "GET").upper()
            self.action = attributes.get("action", "")
            self.enctype = attributes.get(
                "enctype", "application/x-www-form-urlencoded")
        elif tag == "input":
            kind = attributes.get("type", "text").lower()
            if kind not in ("button", "submit", "reset", "image"):
                if self._label is not None and not self._label[0]:
                    self._label[0] = attributes.get("id", "")
                self.fields.append(BotManagerField(
                    tag="input", name=attributes.get("name", ""),
                    identifier=attributes.get("id", ""), kind=kind,
                    value=attributes.get("value", ""),
                    checked="checked" in attributes,
                    attributes=attributes))
        elif tag == "textarea":
            self._textarea = BotManagerField(
                tag="textarea", name=attributes.get("name", ""),
                identifier=attributes.get("id", ""), kind="textarea",
                attributes=attributes)
            self.fields.append(self._textarea)
        elif tag == "select":
            self._select = BotManagerField(
                tag="select", name=attributes.get("name", ""),
                identifier=attributes.get("id", ""), kind="select",
                attributes=attributes)
            self.fields.append(self._select)
        elif tag == "option" and self._select is not None:
            self._option = BotManagerOption(
                value=attributes.get("value", ""), label="",
                selected="selected" in attributes)
        elif tag == "label":
            self._label = [attributes.get("for", ""), []]
        elif tag == "a":
            self._link = [attributes.get("href", ""), [],
                          attributes.get("class", "")]
        plugin_link = re.fullmatch(
            r"plugin_link_(\d+)", attributes.get("id", ""))
        if plugin_link:
            classes = set(attributes.get("class", "").split())
            self.plugin_states.append((
                int(plugin_link.group(1)),
                "on" in classes or "active-on" in classes))
        elif tag in ("h1", "h2", "h3", "h4"):
            self._heading = [tag, []]

    def handle_endtag(self, tag):
        if tag == "option" and self._option is not None:
            label = " ".join("".join(self._option.label).split())
            self._option.label = label
            if not self._option.value:
                self._option.value = label
            self._select.options.append(self._option)
            self._option = None
        elif tag == "textarea" and self._textarea is not None:
            self._textarea.value = "".join(self._textarea.value).strip("\r\n")
            self._textarea = None
        elif tag == "select":
            if (self._select is not None and
                    "multiple" not in self._select.attributes and
                    self._select.options and not any(
                        option.selected for option in self._select.options)):
                self._select.options[0].selected = True
            self._select = None
        elif tag == "label" and self._label is not None:
            label = " ".join("".join(self._label[1]).split())
            if self._label[0] and label:
                self.labels[self._label[0]] = label
            self._label = None
        elif tag == "a" and self._link is not None:
            label = " ".join("".join(self._link[1]).split())
            self.links.append((self._link[0], label))
            self._link = None
        elif tag in ("h1", "h2", "h3", "h4") and self._heading is not None:
            heading = " ".join("".join(self._heading[1]).split())
            if heading:
                self.headings.append(heading)
            self._heading = None

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea.value += data
        if self._option is not None:
            self._option.label += data
        if self._label is not None:
            self._label[1].append(data)
        if self._link is not None:
            self._link[1].append(data)
        if self._heading is not None:
            self._heading[1].append(data)

    def make_page(self, path):
        for item in self.fields:
            item.label = self.labels.get(item.identifier, "")
        return BotManagerPage(
            path=path, method=self.method, action=self.action,
            enctype=self.enctype, fields=self.fields, links=self.links,
            plugin_states=self.plugin_states, headings=self.headings)


class _BotManagerSameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname != BOT_MANAGER_HOST:
            raise urllib.error.HTTPError(
                newurl, code, "Camfrog redirected outside its manager host",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class CamfrogBotManagerClient:
    """In-memory cloudbot-cookie client for Camfrog's Bot Manager forms."""

    def __init__(self, token, timeout=20):
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", str(token or "")):
            raise ValueError("Invalid cloudbot parameter format.")
        self._token = str(token)
        self.timeout = timeout
        self._rpc_id = 0
        self._opener = urllib.request.build_opener(_BotManagerSameHostRedirect())

    def clear(self):
        self._token = ""
        self._opener = None

    def _request(self, path, data=None, content_type=None, allow_signon=False,
                 referer=None):
        if not self._token or self._opener is None:
            raise RuntimeError("Bot Manager is disconnected.")
        parsed_path = urllib.parse.urlparse(path)
        if (parsed_path.scheme or parsed_path.netloc or
                not parsed_path.path.startswith("/bot/")):
            raise ValueError("Invalid Bot Manager path.")
        url = BOT_MANAGER_BASE_URL + path
        headers = dict(BROWSER_HEADERS)
        headers["Accept"] = "text/html,application/xhtml+xml"
        headers["Cookie"] = "cloudbot=" + self._token
        if referer:
            headers["Referer"] = BOT_MANAGER_BASE_URL + referer
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                response_path = urllib.parse.urlparse(response.geturl()).path
                content = response.read(3_000_000)
                encoding = response.headers.get_content_charset() or "utf-8"
        except urllib.error.HTTPError as exc:
            raise RuntimeError("Camfrog returned HTTP {0}.".format(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError("Could not reach Camfrog Bot Manager.") from None
        if (not allow_signon and
                (response_path == "/bot/signon" or
                 response_path.startswith("/bot/signon/"))):
            raise RuntimeError("Camfrog did not accept this cloudbot session.")
        return content.decode(encoding, "replace"), response_path

    def fetch_page(self, path):
        html, final_path = self._request(path)
        parser = _BotManagerPageParser()
        parser.feed(html)
        page = parser.make_page(final_path)
        if not parser._form_seen:
            raise RuntimeError("Camfrog returned an unsupported Bot Manager page.")
        if final_path == "/bot/signon":
            raise RuntimeError("Camfrog did not accept this cloudbot session.")
        return page

    def plugin_catalog(self, page):
        result = []
        seen = set()
        states = dict(page.plugin_states)
        navigation_labels = {"plugins", "plugin settings", "ตั้งค่าปลั๊กอิน"}
        for href, label in page.links:
            path = urllib.parse.urlparse(urllib.parse.urljoin(
                BOT_MANAGER_BASE_URL, href)).path
            match = re.fullmatch(r"/bot/plugins/(\d+)", path)
            if not match or not label or label.strip().lower() in navigation_labels:
                continue
            plugin_id = int(match.group(1))
            if plugin_id not in seen:
                if plugin_id not in states:
                    raise RuntimeError(
                        "Camfrog did not return the plugin activation state.")
                result.append((plugin_id, label.strip(), states[plugin_id]))
                seen.add(plugin_id)
        return result

    def set_plugin_enabled(self, plugin_id, enabled):
        """Call Camfrog's plugin-switch JSON endpoint for one installed plugin."""
        self._rpc_id += 1
        payload = json.dumps({
            "id": self._rpc_id,
            "method": "switch_plugin",
            "params": [int(plugin_id), bool(enabled)],
        }, separators=(",", ":")).encode("utf-8")
        self._request("/bot/ajax/", data=payload,
                      content_type="application/json", referer="/bot/plugins/1")

    def save_page(self, page, values, role_state=None, font_state=None):
        pairs = []
        files = []
        role_state = role_state or {}
        font_state = font_state or {}
        for index, item in enumerate(page.fields):
            if not item.name:
                continue
            value = values.get(index, item.value)
            if item.kind == "radio":
                selected_value = values.get(
                    index, item.value if item.checked else None)
                if selected_value is not None and str(selected_value) == item.value:
                    pairs.append((item.name, item.value))
                continue
            if item.kind == "checkbox":
                if values.get(index, item.checked):
                    pairs.append((item.name, item.value or "on"))
                continue
            if item.tag == "select" and "multiple" in item.attributes:
                selected_values = values.get(
                    index, [option.value for option in item.options
                            if option.selected])
                selected_values = {str(value) for value in selected_values}
                pairs.extend((item.name, option.value)
                             for option in item.options
                             if option.value in selected_values)
                continue
            if item.kind == "file":
                filename = values.get(index, "")
                if filename:
                    path = Path(filename)
                    files.append((item.name, path.name, path.read_bytes()))
                continue
            if item.name == "font_picker" and font_state:
                value = json.dumps(font_state, ensure_ascii=False,
                                   separators=(",", ":"))
            if item.identifier == "_1" and item.name in ("_3", "_4") and role_state:
                value = json.dumps(role_state, ensure_ascii=False,
                                   separators=(",", ":"))
            if item.kind == "password" and index not in values:
                continue
            pairs.append((item.name, str(value)))

        action = urllib.parse.urljoin(BOT_MANAGER_BASE_URL + page.path,
                                      page.action or page.path)
        target = urllib.parse.urlparse(action)
        if target.scheme != "https" or target.hostname != BOT_MANAGER_HOST or not target.path.startswith("/bot/"):
            raise ValueError("Camfrog returned an invalid form action.")
        target_path = target.path
        if target.query:
            target_path += "?" + target.query
        if page.method != "POST":
            raise ValueError("This Bot Manager form cannot be saved safely.")
        if "multipart/form-data" in page.enctype.lower() or files:
            boundary = "----CamfrogBotManagerBoundary" + str(random.getrandbits(64))
            chunks = []
            for name, value in pairs:
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
                chunks.extend((
                    ("--" + boundary + "\r\n").encode("ascii"),
                    (f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n').encode("ascii"),
                    str(value).encode("utf-8"), b"\r\n"))
            for name, filename, content in files:
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
                safe_filename = Path(filename).name.replace('"', "_").replace("\r", "_").replace("\n", "_")
                chunks.extend((
                    ("--" + boundary + "\r\n").encode("ascii"),
                    (f'Content-Disposition: form-data; name="{safe_name}"; filename="{safe_filename}"\r\n').encode("utf-8"),
                    b"Content-Type: application/octet-stream\r\n\r\n",
                    content, b"\r\n"))
            chunks.append(("--" + boundary + "--\r\n").encode("ascii"))
            body = b"".join(chunks)
            content_type = "multipart/form-data; boundary=" + boundary
        else:
            body = urllib.parse.urlencode(pairs).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        self._request(target_path, data=body, content_type=content_type,
                      referer=page.path)
        return self.fetch_page(page.path)


def parse_rotate_interval(raw):
    """Auto-switch interval in seconds. Clamped to the allowed window."""
    try:
        value = float(str(raw).strip())
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid interval: {0!r}".format(raw)) from exc
    if not math.isfinite(value):
        raise ValueError("invalid interval: {0!r}".format(raw))
    return min(ROTATE_INTERVAL_MAX, max(ROTATE_INTERVAL_MIN, value))


def parse_marquee_step_seconds(raw):
    """Frame delay in seconds. Clamp numeric values to the GUI's supported range."""
    try:
        value = float(str(raw).strip())
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid marquee frame interval: {0!r}".format(raw)) from exc
    if not math.isfinite(value):
        raise ValueError("invalid marquee frame interval: {0!r}".format(raw))
    return min(WEB_MARQUEE_STEP_SECONDS_MAX,
               max(WEB_MARQUEE_STEP_SECONDS_MIN, value))


def web_status_rotation_delay(interval, live, marquee=False,
                              frame_seconds=WEB_MARQUEE_STEP_SECONDS_DEFAULT):
    """Return the delay before the next frame or status submission.

    Camfrog's web status endpoint rate-limits live updates. Apply that minimum
    to both live rotations and live marquee frames; dry-run previews retain
    their configured cadence.
    """
    configured_delay = frame_seconds if marquee else interval
    return (max(configured_delay, LIVE_ROTATE_INTERVAL_MIN)
            if live else configured_delay)


def parse_loop_count(raw):
    """Return a status-pool pass count; zero means no limit."""
    value = str(raw).strip()
    if not re.fullmatch(r"\d+", value):
        raise ValueError("loop rounds must be a whole number from 0 to {0}.".format(
            WEB_STATUS_LOOP_COUNT_MAX))
    count = int(value)
    if count > WEB_STATUS_LOOP_COUNT_MAX:
        raise ValueError("loop rounds must be a whole number from 0 to {0}.".format(
            WEB_STATUS_LOOP_COUNT_MAX))
    return count


parse_marquee_loop_count = parse_loop_count


def _web_status_retry_policy(detail, retry_attempt):
    """Return (reason, delay) for safe retries; otherwise return None.

    A 429 or Camfrog's hopping_too_fast response explicitly refused the update.
    A profile GET timeout is safe to retry because the status POST has not begun.
    POST timeouts are deliberately not retried because the server may have applied
    the status before the connection timed out.
    """
    message = str(detail)
    lowered = message.casefold()
    rate_limited = ("hopping_too_fast" in lowered
                    or re.search(r"\bHTTP\s+429\b", message, re.IGNORECASE))
    if rate_limited:
        retry_after = re.search(
            r"\bretry_after\s*=\s*([^\s;,]+)", message, re.IGNORECASE)
        if retry_after:
            delay = _retry_after_seconds(retry_after.group(1))
            if delay is not None:
                # A very long server cooldown must stop automation, not be
                # shortened to our local retry cap and retried prematurely.
                if delay > LIVE_RETRY_MAX_SERVER_WAIT_SECONDS:
                    return None
                return "rate_limit", max(LIVE_ROTATE_INTERVAL_MIN, delay)
        delay = LIVE_RATE_LIMIT_RETRY_BASE_SECONDS * (2 ** retry_attempt)
        return "rate_limit", min(
            LIVE_RETRY_MAX_DELAY_SECONDS,
            max(LIVE_ROTATE_INTERVAL_MIN, delay))
    profile_get = "get /home.php" in lowered
    profile_timeout = (profile_get
                       and ("timeouterror" in lowered or "timed out" in lowered))
    profile_transient_error = (profile_get and (
        "urlerror" in lowered
        or re.search(r"\bHTTPError\s+HTTP\s+5\d\d\b", message, re.IGNORECASE)))
    if profile_timeout or profile_transient_error:
        delay = LIVE_TIMEOUT_RETRY_BASE_SECONDS * (2 ** retry_attempt)
        return ("profile_timeout" if profile_timeout else "profile_request"), min(
            LIVE_RETRY_MAX_DELAY_SECONDS, delay)
    return None


def parse_pool(text):
    """Parse legacy multi-line pool text; the GUI uses ten numbered slots."""
    return [line.strip() for line in str(text).splitlines() if line.strip()]


WEB_TEXT_DB_NAME = "webtext.db"
WEB_TEXT_SLOT_COUNT = 10


def web_text_db_path():
    return BASE / WEB_TEXT_DB_NAME


def _web_text_lines(lines):
    values = [str(value) for value in list(lines)[:WEB_TEXT_SLOT_COUNT]]
    return values + [""] * (WEB_TEXT_SLOT_COUNT - len(values))


def load_web_text_lines(path=None):
    """Load the ten GUI status slots from the local webtext database."""
    database = Path(path) if path is not None else web_text_db_path()
    values = [""] * WEB_TEXT_SLOT_COUNT
    connection = sqlite3.connect(str(database), timeout=5.0)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS webtext ("
            "line_no INTEGER PRIMARY KEY CHECK(line_no BETWEEN 1 AND 10), "
            "text TEXT NOT NULL)")
        rows = connection.execute(
            "SELECT line_no, text FROM webtext ORDER BY line_no").fetchall()
        connection.commit()
    finally:
        connection.close()
    for line_no, text in rows:
        if 1 <= line_no <= WEB_TEXT_SLOT_COUNT:
            values[line_no - 1] = text
    return values


def load_web_text_slots(path=None):
    """Load GUI status slots, falling back without modifying an unreadable DB."""
    try:
        return load_web_text_lines(path), ""
    except (OSError, sqlite3.Error) as exc:
        return ([""] * WEB_TEXT_SLOT_COUNT,
                "Could not load webtext.db ({0}); status fields are empty. "
                "The existing database was left unchanged.".format(type(exc).__name__))


def save_web_text_lines(lines, path=None):
    """Save exactly ten single-line status slots to the local database."""
    database = Path(path) if path is not None else web_text_db_path()
    values = _web_text_lines(lines)
    connection = sqlite3.connect(str(database), timeout=5.0)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS webtext ("
            "line_no INTEGER PRIMARY KEY CHECK(line_no BETWEEN 1 AND 10), "
            "text TEXT NOT NULL)")
        with connection:
            connection.executemany(
                "INSERT OR REPLACE INTO webtext (line_no, text) VALUES (?, ?)",
                [(line_no, text) for line_no, text in enumerate(values, 1)])
    finally:
        connection.close()
    return WEB_TEXT_SLOT_COUNT


def next_rotation(pool, index):
    """Round-robin step. Returns (status, next_index). Empty pool -> ValueError."""
    if not pool:
        raise ValueError("status pool is empty; add at least one line.")
    return pool[index % len(pool)], (index + 1) % len(pool)


TIMEOUT_MIN = 2.0
TIMEOUT_DEFAULT = 15.0
TIMEOUT_MAX = 120.0


def parse_timeout(raw):
    """Network timeout in seconds, clamped to the allowed window."""
    try:
        value = float(str(raw).strip())
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid timeout: {0!r}".format(raw)) from exc
    if not math.isfinite(value):
        raise ValueError("invalid timeout: {0!r}".format(raw))
    return min(TIMEOUT_MAX, max(TIMEOUT_MIN, value))


class WebStatusUpdateCancelled(Exception):
    """The operator stopped rotation before the status POST was sent."""


def _raise_if_update_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise WebStatusUpdateCancelled(
            "update cancelled before the status POST was sent")


def perform_update(opener, status, timeout, confirm=False, cancelled=None,
                   on_post_started=None, expected_login=None):
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
    _raise_if_update_cancelled(cancelled)
    try:
        html = fetch_profile(opener, "", timeout)
    except WebStatusUpdateCancelled:
        raise
    except Exception as exc:
        return False, _web_status_request_error(
            "GET /home.php", exc, secrets=(status,))
    _raise_if_update_cancelled(cancelled)
    identity = extract_profile_identity(html)
    if identity is None:
        return False, ("update refused: profile page did not provide a verifiable "
                       "account identity.")
    if identity["nickname"].casefold() != str(expected_login).strip().casefold():
        return False, ("update refused: authenticated Camfrog account does not "
                       "match the selected account.")
    csrf = extract_csrf(html)
    if not csrf:
        return False, ("update refused: no CSRF token on the profile page "
                       "(session expired or not signed in); profile_chars={0}.".format(
                           len(html)))
    _raise_if_update_cancelled(cancelled)
    if on_post_started is not None:
        on_post_started()
    try:
        reply = _post(opener, WEB_UPDATE_URL,
                      {"status": status, "csrf": csrf}, timeout)
    except Exception as exc:
        detail = _web_status_request_error(
            "POST /ajax/update_status.php", exc, secrets=(status, csrf))
        if isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500:
            return False, "update refused by profile endpoint; {0}.".format(detail)
        return False, ("update outcome unknown: {0}; check the profile before "
                       "retrying.").format(detail)
    response_context = []
    status_code = getattr(reply, "status_code", None)
    content_type = getattr(reply, "content_type", None)
    if status_code is not None:
        response_context.append("http_status={0}".format(status_code))
    if content_type:
        response_context.append("content_type={0}".format(content_type))
    retry_after = _retry_after_seconds(getattr(reply, "retry_after", None))
    if retry_after is not None:
        response_context.append("retry_after={0:g}".format(retry_after))
    if response_context:
        response_context = ["response " + ", ".join(response_context)]
    response_context.append("response_chars={0}".format(len(reply)))
    context = "; ".join(response_context)
    if status_code is not None and status_code >= 500:
        return False, ("update outcome unknown: profile endpoint returned "
                       "HTTP status 5xx; {0}; check the profile before retrying."
                       .format(context))
    if status_code is not None and not 200 <= status_code < 300:
        return False, "update refused: profile endpoint returned HTTP {0}; {1}.".format(
            status_code,
            context)
    try:
        data = json.loads(reply)
    except (TypeError, ValueError) as exc:
        parser_detail = type(exc).__name__
        if isinstance(exc, json.JSONDecodeError):
            parser_detail += " ({0}, line={1}, column={2}, char={3})".format(
                exc.msg, exc.lineno, exc.colno, exc.pos)
        return False, "update outcome unknown: invalid JSON server reply; {0}; {1}.".format(
            parser_detail, context)
    if not isinstance(data, dict):
        return False, ("update outcome unknown: expected a JSON object but "
                       "received {0}; {1}; check the profile before retrying.").format(
                           type(data).__name__, context)
    if retry_after is None:
        retry_after = _retry_after_seconds(data.get("retry_after"))
        if retry_after is not None:
            context = "{0}; retry_after={1:g}".format(context, retry_after)
    if data.get("error"):
        response_summary = _web_status_error_response_summary(
            data.get("response", "missing"), secrets=(status, csrf))
        return False, ("update refused by the server; response_keys={0}; "
                       "error={1}; server_response={2}; {3}.").format(
                           _web_status_response_keys(data),
                           _web_status_value_summary(
                               data.get("error"), secrets=(status, csrf)),
                           response_summary, context)
    # Camfrog's profile form treats a non-empty response string as success and
    # writes it back into the status field. It does not require the literal "ok".
    response = data.get("response")
    if not isinstance(response, str) or not response.strip():
        response_type = "missing" if "response" not in data else type(response).__name__
        response_chars = (len(response) if isinstance(response, str) else "n/a")
        return False, ("update outcome unknown: server did not acknowledge "
                       "success; check the profile before retrying; "
                       "response_type={0}; response_chars={1}; response_keys={2}; "
                       "{3}.").format(
                           response_type, response_chars,
                           _web_status_response_keys(data), context)
    return True, "status update acknowledged by server."


def extract_csrf(html):
    """The per-session CSRF token embedded in the logged-in profile page."""
    match = re.search(r"var\s+csrf\s*=\s*['\"]([0-9a-fA-F]{16,})['\"]", html)
    return match.group(1) if match else ""

CAPTURE_STEPS = """\
To capture PHPSESSID from https://profiles.camfrog.com/home.php:
1. Log in at https://profiles.camfrog.com/ or https://www.camfrog.com/th/login.php.
2. Open devtools (F12) -> Network tab (preserve log / filter for "home.php").
3. Navigate to https://profiles.camfrog.com/home.php.
4. Click the 'home.php' request -> Headers -> Request Headers (Cookie) or Response Headers (set-cookie).
5. Copy the PHPSESSID value (e.g. PHPSESSID=xxxx... or just the token).
6. Paste into the Cookie/PHPSESSID box below and press 'Login' to arm automation.
"""

COOKIE_STEPS = """\
To capture the real status-update endpoint:
1. On https://profiles.camfrog.com/home.php, change your status once.
2. In devtools Network tab, find POST /ajax/update_status.php with {status, csrf}.
3. The session uses your authenticated PHPSESSID cookie.
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
    # If the user pasted something like 'PHPSESSID=xyz' directly or in cookie string
    for chunk in raw.replace(";", " ").split():
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            key, value = key.strip(), value.strip()
            if key and value:
                pairs.append((key, value))

    # Also handle if user pasted just the raw PHPSESSID hash or 'PHPSESSID: xyz'
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
    identity = extract_profile_identity(html)
    if identity:
        return ("signed-in",
                "session is signed in as {0!r} (user id {1}, page {2!r}).".format(
                    identity["nickname"], identity["user_id"], title))
    return ("unconfirmed",
            "no login wall found (page: {0!r}, {1} chars) but no signed-in marker "
            "either; confirm visually before any live use.".format(title, len(html)))


def extract_profile_identity(html):
    """Return the signed-in provider identity exposed by the profile page."""
    return _profile_extract_identity(html)


def _post(opener, url, fields, timeout):
    if url == WEB_UPDATE_URL and set(fields) == {"status", "csrf"}:
        return _profile_post_status(
            opener, fields["status"], fields["csrf"], timeout)
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=dict(
        BROWSER_HEADERS, Referer=LOGIN_PAGE_URL))
    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace").strip()
        headers = getattr(response, "headers", None)
        content_type = headers.get("Content-Type") if headers is not None else None
        retry_after = headers.get("Retry-After") if headers is not None else None
        return WebStatusReply(
            body, status_code=getattr(response, "status", None)
            or (response.getcode() if hasattr(response, "getcode") else None),
            content_type=content_type, retry_after=retry_after)


def extract_csrf(html):
    """The per-session CSRF token embedded in the logged-in profile page."""
    return _profile_extract_csrf(html)


class WebStatusReply(str):
    """Text response with only non-sensitive HTTP metadata used for diagnostics."""

    def __new__(cls, value, status_code=None, content_type=None, retry_after=None):
        reply = super().__new__(cls, value)
        reply.status_code = status_code
        reply.content_type = content_type
        reply.retry_after = retry_after
        return reply


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
    # Load encrypted values first; plaintext .env only fills keys that are not
    # already present, so the protected store remains authoritative.
    encrypted_loaded = load_env_enc()
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return encrypted_loaded
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


def environment_cookie(name, expected_cookie):
    """Return one validated cookie pair from the environment, without logging it."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return ""
    jar = cookie_from_paste(raw)
    cookies = list(jar)
    if len(cookies) != 1 or cookies[0].name != expected_cookie:
        raise ValueError("{0} must contain exactly one {1} cookie.".format(
            name, expected_cookie))
    return "{0}={1}".format(cookies[0].name, cookies[0].value)


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


def cli_main(argv=None):
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





# ============================================================
# GUI (from web_status_gui.py).
# ============================================================






def wants_gui(argv):
    """No CLI args -> GUI mode. Headless-safe (no tkinter needed)."""
    return not list(argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if wants_gui(argv):
        manager = WebStatusManager()
        manager.root.mainloop()
        return 0
    if getattr(sys, "stdout", None) is None:
        return run_cli_headless(argv)  # frozen --windowed: no console to print to
    return cli_main(argv)


def run_cli_headless(argv, show=None):
    """Run the CLI with output captured into a dialog (windowed exe, no console)."""
    import contextlib
    import io

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = cli_main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:  # never a silent no-op
        code = 2
        buf.write("failed: {0}: {1}".format(type(exc).__name__, exc))
    text = buf.getvalue().strip() or "(no output)"
    if show is None:
        from tkinter import messagebox

        show = messagebox.showinfo
    show("Web Status", text)
    return code


_CLIPBOARD_WIDGET_CLASSES = {
    "Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text",
}
_CLIPBOARD_EDITABLE_CLASSES = {
    "Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text",
}


def _windows_open_clipboard(clipboard, attempts=20, interval=0.025):
    """Retry briefly while another Windows process holds the clipboard."""
    last_error = None
    for attempt in range(attempts):
        try:
            clipboard.OpenClipboard(None)
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(interval)
    raise OSError("Could not open the Windows clipboard: {0}".format(
        type(last_error).__name__ if last_error else "unknown error")) from last_error


def _read_clipboard_text(root):
    """Read text through Win32 first, with Tk as a portable fallback."""
    clipboard_error = None
    if os.name == "nt":
        try:
            import win32clipboard
            import win32con
        except ImportError:
            pass
        else:
            try:
                _windows_open_clipboard(win32clipboard)
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        value = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                        return value if isinstance(value, str) else str(value)
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                        value = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                        if isinstance(value, bytes):
                            value = value.decode("mbcs", errors="replace")
                        return value if isinstance(value, str) else str(value)
                finally:
                    win32clipboard.CloseClipboard()
            except Exception as exc:
                clipboard_error = exc

    try:
        return str(root.clipboard_get())
    except Exception as exc:
        if clipboard_error is not None:
            raise OSError("Could not read the system clipboard ({0}).".format(
                type(clipboard_error).__name__)) from clipboard_error
        raise OSError("Could not read text from the system clipboard.") from exc


def _write_clipboard_text(root, value):
    """Write text through Win32 first, with Tk as a portable fallback."""
    text = str(value)
    clipboard_error = None
    if os.name == "nt":
        try:
            import win32clipboard
            import win32con
        except ImportError:
            pass
        else:
            try:
                _windows_open_clipboard(win32clipboard)
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                finally:
                    win32clipboard.CloseClipboard()
                return
            except Exception as exc:
                clipboard_error = exc

    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update_idletasks()
    except Exception as exc:
        if clipboard_error is not None:
            raise OSError("Could not write the Windows clipboard ({0}).".format(
                type(clipboard_error).__name__)) from clipboard_error
        raise OSError("Could not write text to the system clipboard.") from exc


def _clipboard_widget_class(widget):
    try:
        return widget.winfo_class()
    except Exception:
        return ""


def _clipboard_widget_editable(widget):
    if _clipboard_widget_class(widget) not in _CLIPBOARD_EDITABLE_CLASSES:
        return False
    try:
        return str(widget.cget("state")) not in {"disabled", "readonly"}
    except Exception:
        return True


class WebStatusClipboardController:
    """Bind layout-independent copy, select-all, and paste shortcuts to GUI fields."""

    def __init__(self, root, on_error=None, translate=None):
        import tkinter as tk

        self.root = root
        self.on_error = on_error
        self.translate = translate or (lambda key: key)
        self.target = None
        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label=self.translate("clipboard_cut"), command=self.cut_target)
        self.menu.add_command(label=self.translate("clipboard_copy"), command=self.copy_target)
        self.menu.add_command(label=self.translate("clipboard_paste"), command=self.paste_target)
        self.menu.add_separator()
        self.menu.add_command(label=self.translate("clipboard_select_all"),
                              command=self.select_all_target)

    def install(self, parent):
        for widget in parent.winfo_children():
            if _clipboard_widget_class(widget) in _CLIPBOARD_WIDGET_CLASSES:
                if _clipboard_widget_class(widget) == "Text":
                    try:
                        widget.configure(
                            background=WEB_STATUS_THEME["entry"],
                            foreground=WEB_STATUS_THEME["log_text"],
                            insertbackground=WEB_STATUS_THEME["gold"],
                            selectbackground=WEB_STATUS_THEME["border"],
                            selectforeground=WEB_STATUS_THEME["text"])
                    except Exception:
                        pass
                for sequence in ("<Control-v>", "<Control-V>", "<Shift-Insert>"):
                    widget.bind(sequence, self.handle, add="+")
                # Some Windows layouts (including Thai) report Ctrl shortcuts
                # with keysym "??". Match their control characters or VK codes.
                widget.bind("<Control-KeyPress>", self.handle_control_key, add="+")
                widget.bind("<Button-3>", self.show_menu, add="+")
            self.install(widget)

    def handle(self, event):
        self.target = event.widget
        self.paste(self.target)
        return "break"

    def handle_control_key(self, event):
        """Handle Ctrl+A/C/V/X when the active layout changes its keysyms."""
        keysym = str(getattr(event, "keysym", "")).lower()
        char = str(getattr(event, "char", "") or "")
        try:
            keycode = int(getattr(event, "keycode", -1))
        except (TypeError, ValueError):
            keycode = -1
        shortcuts = {
            "a": ({"a", "\x01"}, 65),
            "c": ({"c", "\x03"}, 67),
            "v": ({"v", "\x16"}, 86),
            "x": ({"x", "\x18"}, 88),
        }
        shortcut = next((name for name, (chars, code) in shortcuts.items()
                         if keysym == name or char in chars or keycode == code), None)
        if shortcut is None:
            return None

        self.target = event.widget
        if shortcut == "a":
            self.select_all(self.target)
        elif shortcut == "v":
            self.paste(self.target)
        elif shortcut == "x":
            self.cut(self.target)
        else:
            self.copy(self.target)
        return "break"

    def select_all(self, widget):
        if _clipboard_widget_class(widget) not in _CLIPBOARD_WIDGET_CLASSES:
            return False
        try:
            widget.focus_set()
            if _clipboard_widget_class(widget) == "Text":
                widget.tag_remove("sel", "1.0", "end")
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
                widget.see("insert")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
            return True
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
            return False

    def copy(self, widget):
        if _clipboard_widget_class(widget) not in _CLIPBOARD_WIDGET_CLASSES:
            return False
        try:
            if _clipboard_widget_class(widget) == "Text":
                if not widget.tag_ranges("sel"):
                    return False
                value = widget.get("sel.first", "sel.last")
            else:
                if not widget.selection_present():
                    return False
                start = widget.index("sel.first")
                end = widget.index("sel.last")
                value = widget.get()[start:end]
            if not value:
                return False
            _write_clipboard_text(self.root, value)
            return True
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
            return False

    def cut(self, widget):
        """Copy and then remove the current selection from an editable field."""
        if not _clipboard_widget_editable(widget):
            return False
        try:
            widget_class = _clipboard_widget_class(widget)
            has_selection = (bool(widget.tag_ranges("sel")) if widget_class == "Text"
                             else bool(widget.selection_present()))
            if not has_selection or not self.copy(widget):
                return False
            widget.delete("sel.first", "sel.last")
            return True
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
            return False

    def cut_target(self):
        self.cut(self.target)

    def copy_target(self):
        self.copy(self.target)

    def select_all_target(self):
        self.select_all(self.target)

    def paste_target(self):
        self.paste(self.target)

    def paste(self, widget):
        if widget is None or not _clipboard_widget_editable(widget):
            return False
        try:
            value = _read_clipboard_text(self.root)
            if not value:
                raise ValueError("The system clipboard does not contain text.")
            widget_class = _clipboard_widget_class(widget)
            if widget_class == "Text":
                if widget.tag_ranges("sel"):
                    widget.delete("sel.first", "sel.last")
                widget.insert("insert", value)
            else:
                if widget.selection_present():
                    widget.delete("sel.first", "sel.last")
                widget.insert("insert", value)
            return True
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
            return False

    def show_menu(self, event):
        self.target = event.widget
        self.target.focus_set()
        for index, key in enumerate(("clipboard_cut", "clipboard_copy", "clipboard_paste")):
            self.menu.entryconfigure(index, label=self.translate(key))
        self.menu.entryconfigure(4, label=self.translate("clipboard_select_all"))
        widget_class = _clipboard_widget_class(self.target)
        try:
            selected = (bool(self.target.tag_ranges("sel")) if widget_class == "Text"
                        else bool(self.target.selection_present()))
        except Exception:
            selected = False
        editable = _clipboard_widget_editable(self.target)
        self.menu.entryconfigure(0, state="normal" if editable and selected else "disabled")
        self.menu.entryconfigure(1, state="normal" if selected else "disabled")
        self.menu.entryconfigure(2, state="normal" if editable else "disabled")
        self.menu.entryconfigure(4, state="normal" if widget_class in _CLIPBOARD_WIDGET_CLASSES
                                 else "disabled")
        try:
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


WEB_STATUS_LANGUAGE_TEXT = {
    "en": {
        "intro": ("Update status via profiles.camfrog.com. Dry-run is the default; "
                  "nothing is sent unless Send live is on. Passwords and cookie values "
                  "are never stored, logged, or shown."),
        "setup": "Setup",
        "account_session": "Account & Session",
        "activity_log": "Activity Log",
        "nickname": "Nickname",
        "status_lines": "Status lines",
        "save_status": "Save to webtext.db",
        "switch_every": "Switch every",
        "seconds_live_min": "s (live min 10)",
        "timeout": "Timeout",
        "send_live": "Send live (otherwise dry-run preview only)",
        "loop_rounds": "Loop rounds",
        "infinite": "0 = infinite",
        "session_title": "Session (PHPSESSID via iframe / web login)",
        "session_help": ("Log in in your browser (via iframe/web login). In devtools Network tab, "
                         "inspect https://profiles.camfrog.com/home.php to get PHPSESSID=..., "
                         "paste below, then press 'Login' to verify and arm automation."),
        "open_login": "Open login page",
        "login": "Login",
        "browser_login": "Login in browser",
        "cloudbot_login": "Connect via cloudbot",
        "cloudbot_param": "Cloudbot parameter",
        "preview_plan": "Preview plan",
        "probe_session": "Probe session",
        "update": "Update",
        "bot_manager": "Bot manager",
        "start": "Start",
        "stop": "Stop",
        "capture_help": "How to capture",
        "clipboard_cut": "Cut",
        "clipboard_copy": "Copy",
        "clipboard_paste": "Paste",
        "clipboard_select_all": "Select all",
        "ready_note": "Dry-run is on. Nothing has been sent.",
        "capture_instructions": CAPTURE_STEPS + COOKIE_STEPS,
        "confirm_title": "Send for real?",
        "confirm_start": ("This will sign in and POST your status to Camfrog for real.\n"
                           "Continue?"),
        "confirm_after_login": ("Logged in. This will POST your status to Camfrog for real.\n"
                                "Continue?"),
    },
    "th": {
        "intro": ("อัปเดตสถานะผ่าน profiles.camfrog.com ค่าเริ่มต้นเป็นโหมดทดลอง "
                  "ระบบจะไม่ส่งข้อมูลจริงจนกว่าจะเปิดส่งสถานะจริง และจะไม่บันทึกรหัสผ่านหรือ cookie"),
        "setup": "ตั้งค่า",
        "account_session": "บัญชีและเซสชัน",
        "activity_log": "บันทึกกิจกรรม",
        "bot_manager": "ตัวจัดการบอท",
        "nickname": "ชื่อเล่น",
        "status_lines": "ข้อความสถานะ",
        "save_status": "บันทึกลง webtext.db",
        "switch_every": "สลับทุก",
        "seconds_live_min": "วินาที (Live ขั้นต่ำ 10)",
        "timeout": "หมดเวลา",
        "send_live": "ส่งสถานะจริง (ปิด = แสดงตัวอย่างเท่านั้น)",
        "loop_rounds": "จำนวนรอบ",
        "infinite": "0 = ไม่จำกัด",
        "session_title": "เซสชัน (PHPSESSID ผ่าน iframe / เว็บ)",
        "session_help": ("เข้าสู่ระบบในเบราว์เซอร์ แล้วเปิดแท็บ Network ในเครื่องมือ devtools "
                         "ตรวจคำขอ https://profiles.camfrog.com/home.php เพื่อหา PHPSESSID=... "
                         "วางค่าในช่องด้านล่าง แล้วกด Login เพื่อตรวจสอบเซสชัน"),
        "open_login": "เปิดหน้าเข้าสู่ระบบ",
        "login": "ตรวจสอบ Login",
        "browser_login": "เข้าสู่ระบบในเบราว์เซอร์",
        "cloudbot_login": "เชื่อมต่อผ่าน cloudbot",
        "cloudbot_param": "พารามิเตอร์ cloudbot",
        "preview_plan": "ดูตัวอย่างแผน",
        "probe_session": "ตรวจสอบเซสชัน",
        "update": "อัปเดต",
        "start": "เริ่ม",
        "stop": "หยุด",
        "capture_help": "วิธีรับ PHPSESSID",
        "clipboard_cut": "ตัด",
        "clipboard_copy": "คัดลอก",
        "clipboard_paste": "วาง",
        "clipboard_select_all": "เลือกทั้งหมด",
        "ready_note": "โหมดทดลองเปิดอยู่ ยังไม่มีการส่งสถานะ",
        "capture_instructions": ("วิธีรับ PHPSESSID จาก https://profiles.camfrog.com/home.php:\n"
                                 "1. เข้าสู่ระบบที่ https://profiles.camfrog.com/ หรือหน้าเข้าสู่ระบบ Camfrog\n"
                                 "2. กด F12 แล้วเปิดแท็บ Network\n"
                                 "3. เปิด https://profiles.camfrog.com/home.php\n"
                                 "4. เลือกคำขอ home.php แล้วดู Headers ใน Request Headers (Cookie) "
                                 "หรือ Response Headers (set-cookie)\n"
                                 "5. คัดลอกค่า PHPSESSID=... หรือคัดลอกเฉพาะ token\n"
                                 "6. วางในช่อง PHPSESSID แล้วกด Login\n\n"
                                 "วิธีตรวจ endpoint อัปเดตสถานะ:\n"
                                 "1. เปลี่ยนสถานะหนึ่งครั้งจากหน้า home.php\n"
                                 "2. ในแท็บ Network หา POST /ajax/update_status.php ที่มี {status, csrf}\n"
                                 "3. คำขอใช้ cookie PHPSESSID ของบัญชีที่เข้าสู่ระบบแล้ว"),
        "confirm_title": "ยืนยันการส่งจริง",
        "confirm_start": "ระบบจะเข้าสู่ระบบและส่งสถานะไปยัง Camfrog จริง\nดำเนินการต่อหรือไม่?",
        "confirm_after_login": "เข้าสู่ระบบแล้ว ระบบจะส่งสถานะไปยัง Camfrog จริง\nดำเนินการต่อหรือไม่?",
    },
}


class WebStatusManager:
    def __init__(self, cookies_default=""):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self._language = "en"
        self.q = queue.Queue()
        self.bot_q = queue.Queue()
        self.busy = False
        self.bot_busy = False
        self.bot_client = None
        self.bot_pages = {}
        self.bot_plugin_catalog = []
        self.bot_plugin_index_page = None
        self.bot_selected_plugin_id = None
        self.bot_generation = 0
        self.switching = False
        self.rotate_job = None
        self.chrome_mode = False
        self.cookie_paste = ""
        self._cookie_from_browser = False
        self.session_ready = False
        self._pending = None
        self._start_generation = 0
        self._clipboard_bound_widgets = set()
        self._language_widgets = []
        self._note_key = None
        self._note_source = ""
        self._paste_state_source = ""
        self._paste_state_color = WEB_STATUS_THEME["muted"]
        load_dotenv(BASE / ".env")  # pick up the encrypted account, if saved
        self.root = self.tk.Tk()
        self.root.title("ZChat Web Status")
        self.root.geometry("400x300")
        self.root.minsize(300, 220)
        self.root.resizable(True, True)
        self._is_fullscreen = False
        self._pre_fullscreen_geometry = "400x300"
        self._tray_icon = None
        self._create_initial_splash()
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass
        style.configure("TFrame", background=WEB_STATUS_THEME["background"])
        style.configure("TLabel", background=WEB_STATUS_THEME["background"],
                        foreground=WEB_STATUS_THEME["text"], font=("Segoe UI", 9))
        style.configure("TCheckbutton", background=WEB_STATUS_THEME["background"],
                        foreground=WEB_STATUS_THEME["text"])
        style.configure("TRadiobutton", background=WEB_STATUS_THEME["background"],
                        foreground=WEB_STATUS_THEME["text"])
        style.configure("TLabelframe", background=WEB_STATUS_THEME["background"],
                        bordercolor=WEB_STATUS_THEME["border"])
        style.configure("TLabelframe.Label", background=WEB_STATUS_THEME["background"],
                        foreground=WEB_STATUS_THEME["accent"],
                        font=("Segoe UI", 9, "bold"))
        style.configure("TButton", padding=(7, 4), font=("Segoe UI", 9),
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["button_text"])
        style.map("TButton", background=[
            ("active", WEB_STATUS_THEME["button_active"]),
            ("pressed", WEB_STATUS_THEME["button_pressed"])])
        style.configure("TEntry", padding=(4, 4),
                        fieldbackground=WEB_STATUS_THEME["entry"],
                        foreground=WEB_STATUS_THEME["text"])
        style.configure("TNotebook", background=WEB_STATUS_THEME["background"])
        style.configure("TNotebook.Tab", padding=(11, 6), font=("Segoe UI", 9))
        style.map("TNotebook.Tab", background=[("selected", WEB_STATUS_THEME["surface"])],
                  foreground=[("selected", WEB_STATUS_THEME["accent_hover"])])
        style.configure("Brand.TLabel", font=("Segoe UI", 13, "bold"),
                        foreground=WEB_STATUS_THEME["accent"])
        style.configure("BrandBar.TFrame",
                        background=WEB_STATUS_THEME["surface_high"])
        style.configure("BrandLogo.TButton", padding=(4, 1), width=2,
                        font=("Segoe UI", 11, "bold"),
                        background=WEB_STATUS_THEME["accent"],
                        foreground=WEB_STATUS_THEME["surface"])
        style.map("BrandLogo.TButton", background=[
            ("active", WEB_STATUS_THEME["accent_hover"]),
            ("pressed", WEB_STATUS_THEME["accent_hover"])])
        style.configure("BrandBar.TLabel",
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["text"],
                        font=("Segoe UI", 9, "bold"))
        style.configure("Loading.TLabel",
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["accent"],
                        font=("Segoe UI", 8))
        style.configure("Hint.TLabel", foreground=WEB_STATUS_THEME["muted"])
        style.configure("BotHeader.TFrame",
                        background=WEB_STATUS_THEME["surface_high"])
        style.configure("BotHeader.TLabel",
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["text"])
        style.configure("BotHeaderTitle.TLabel",
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["accent_hover"],
                        font=("Segoe UI", 13, "bold"))
        style.configure("BotHeaderHint.TLabel",
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["muted"])
        style.configure("BotTab.TButton", padding=(14, 8),
                        font=("Segoe UI", 9), anchor="center",
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["button_text"])
        style.map("BotTab.TButton", background=[
            ("active", WEB_STATUS_THEME["button_active"]),
            ("pressed", WEB_STATUS_THEME["button_pressed"])])
        style.configure("BotTabActive.TButton", padding=(14, 8),
                        font=("Segoe UI", 9, "bold"), anchor="center",
                        background=WEB_STATUS_THEME["surface"],
                        foreground=WEB_STATUS_THEME["accent_hover"])
        style.map("BotTabActive.TButton", background=[
            ("active", WEB_STATUS_THEME["surface"]),
            ("pressed", WEB_STATUS_THEME["surface"])])
        style.configure("BotPrimary.TButton", padding=(12, 6),
                        font=("Segoe UI", 9, "bold"),
                        background=WEB_STATUS_THEME["accent"],
                        foreground=WEB_STATUS_THEME["surface"])
        style.map("BotPrimary.TButton", background=[
            ("active", WEB_STATUS_THEME["accent_hover"]),
            ("pressed", WEB_STATUS_THEME["accent_hover"])])
        style.configure("SafetyBanner.TLabel",
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["muted"],
                        padding=(10, 6), font=("Segoe UI", 9))
        style.configure("AppFooter.TFrame",
                        background=WEB_STATUS_THEME["surface_high"])
        style.configure("AppFooter.TLabel",
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["muted"],
                        font=("Segoe UI", 9))
        style.configure("ActionPrimary.TButton", padding=(12, 6),
                        font=("Segoe UI", 9, "bold"),
                        background=WEB_STATUS_THEME["accent"],
                        foreground=WEB_STATUS_THEME["surface"])
        style.map("ActionPrimary.TButton", background=[
            ("active", WEB_STATUS_THEME["accent_hover"]),
            ("pressed", WEB_STATUS_THEME["accent_hover"])])
        style.configure("ActionStop.TButton", padding=(9, 6),
                        font=("Segoe UI", 9, "bold"),
                        background=WEB_STATUS_THEME["surface_high"],
                        foreground=WEB_STATUS_THEME["danger"])
        style.map("ActionStop.TButton", background=[
            ("active", WEB_STATUS_THEME["button_active"]),
            ("pressed", WEB_STATUS_THEME["button_pressed"])])
        style.configure("BotLink.TButton", padding=(2, 0), borderwidth=0,
                        anchor="w", background=WEB_STATUS_THEME["background"],
                        foreground=WEB_STATUS_THEME["accent_hover"],
                        font=("Segoe UI", 9, "underline"))
        style.map("BotLink.TButton",
                  background=[("active", WEB_STATUS_THEME["background"])],
                  foreground=[("active", WEB_STATUS_THEME["accent"])])
        self.brand_bar = self.ttk.Frame(
            self.root, style="BrandBar.TFrame", padding=(6, 3))
        self.brand_bar.pack(fill="x", padx=10, pady=(5, 3))
        self.logo_button = self.ttk.Button(
            self.brand_bar, text="Z", style="BrandLogo.TButton",
            command=self._toggle_fullscreen)
        self.logo_button.pack(side="left", padx=(0, 6))
        self.brand_title = self.ttk.Label(
            self.brand_bar, text="ZChat Web Status", style="BrandBar.TLabel")
        self.brand_title.pack(side="left")
        self.page_loading_var = self.tk.StringVar(value="Loading pages...")
        self.page_loading_label = self.ttk.Label(
            self.brand_bar, textvariable=self.page_loading_var,
            style="Loading.TLabel")
        self.page_loading_label.pack(side="left", padx=(8, 0))
        self.tray_button = self.ttk.Button(
            self.brand_bar, text="Tray", width=5,
            command=self._minimize_to_tray)
        self.tray_button.pack(side="right", padx=(4, 0))
        self.restore_size_button = self.ttk.Button(
            self.brand_bar, text="800x600", width=7,
            command=self._restore_800x600)
        self.restore_size_button.pack(side="right")
        self.language_button = self.ttk.Button(
            self.brand_bar, text="TH/EN", width=5,
            command=self.switch_language)
        self.language_button.pack(side="right", padx=(4, 0))

        self.safety_banner = self.ttk.Label(self.root, text=(
            "Dry-run is on by default; enable Send live before status updates are sent."
        ), style="SafetyBanner.TLabel", wraplength=350)
        self.safety_banner.pack(fill="x", padx=10, pady=(5, 4))

        self.app_footer = self.ttk.Frame(
            self.root, style="AppFooter.TFrame", padding=(10, 6))
        self.app_footer.pack(side="bottom", fill="x", padx=10, pady=(0, 8))

        self.nb = self.ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        setup_tab = self.ttk.Frame(self.nb, padding=(10, 6, 10, 6))
        session_tab = self.ttk.Frame(self.nb, padding=(10, 6, 10, 6))
        self.nb.add(setup_tab, text="Setup")
        self.nb.add(session_tab, text="Account & Session")
        self.bot_manager_tab = self.ttk.Frame(self.nb, padding=(10, 6, 10, 6))
        self.nb.add(self.bot_manager_tab, text=self._text("bot_manager"))
        self._register_language_widget(self.bot_manager_tab, "bot_manager", notebook_tab=True)
        self._create_bot_manager_content(self.bot_manager_tab)
        self._create_main_ui(setup_tab, session_tab)
        self._install_clipboard_shortcuts(self.root)
        self.nb.bind("<<NotebookTabChanged>>", self._on_main_tab_changed)
        self.root.bind("<Configure>", self._on_root_resize, add="+")
        self.root.bind("<Escape>", self._exit_fullscreen, add="+")
        self.root.bind("<F3>", lambda _e: self.start())
        self.root.bind("<F4>", lambda _e: self.stop())
        self.root.protocol("WM_DELETE_WINDOW", self._close_window)
        self._show_page_loading(900)
        self.splash_frame.lift()
        self.root.after(500, lambda: self._tick_initial_splash(5))
        self._apply_language()

    def _create_initial_splash(self):
        """Show the branded loading page while the rest of the GUI is created."""
        background = "#020a24"
        self.root.configure(background=background)
        self.splash_frame = self.tk.Frame(self.root, background=background)
        self.splash_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        content = self.tk.Frame(self.splash_frame, background=background)
        content.place(relx=0.5, rely=0.5, anchor="center")

        resource_root = Path(getattr(
            sys, "_MEIPASS", Path(__file__).resolve().parent))
        logo_image = None
        try:
            logo_image = self.tk.PhotoImage(
                file=str(resource_root / "assets" / "zttato.png")).subsample(16, 16)
        except (self.tk.TclError, OSError):
            pass
        self._splash_photo = logo_image

        details = self.tk.Frame(content, background=background)
        details.pack(side="left")
        if logo_image is not None:
            self.tk.Label(
                details, image=logo_image, background=background,
                borderwidth=0).pack(anchor="w", pady=(0, 8))
        else:
            self.tk.Label(
                details, text="Z", background=background,
                foreground="#55e4ff", font=("Segoe UI", 28, "bold"),
                borderwidth=0).pack(anchor="w", pady=(0, 8))
        self.splash_title = self.tk.Label(
            details, text="ZChat Web Status", background=background,
            foreground="#ffffff", font=("Segoe UI", 14, "bold"),
            justify="left", wraplength=145)
        self.splash_title.pack(anchor="w", pady=(0, 12))
        self.splash_loading = self.tk.Label(
            details, text="Loading pages...", background=background,
            foreground="#55e4ff",
            font=("Segoe UI", 10, "bold"), anchor="w")
        self.splash_loading.pack(anchor="w", pady=(0, 8))
        self.splash_countdown = self.tk.Label(
            details, text="", background=background,
            foreground="#b4c2dc",
            font=("Segoe UI", 9), anchor="w")
        self.splash_countdown.pack(anchor="w")

        # Paint the splash before constructing the main interface.
        self.root.update_idletasks()
        self.root.update()

    def _tick_initial_splash(self, seconds_left):
        """Count down, then remove the splash to reveal the Setup page."""
        try:
            if not self.splash_frame.winfo_exists():
                return
            if seconds_left <= 0:
                self.splash_frame.destroy()
                self._splash_photo = None
                return
            self.splash_countdown.configure(
                text="Opening the app in {0}...".format(seconds_left))
            self.root.after(
                1000, lambda: self._tick_initial_splash(seconds_left - 1))
        except self.tk.TclError:
            return

    def _toggle_fullscreen(self, _event=None):
        if self._is_fullscreen:
            self._exit_fullscreen()
            return
        try:
            self._pre_fullscreen_geometry = self.root.geometry()
            self.root.attributes("-fullscreen", True)
            self._is_fullscreen = True
            self._show_page_loading(250)
        except self.tk.TclError:
            self._is_fullscreen = False

    def _exit_fullscreen(self, _event=None):
        if not self._is_fullscreen:
            return
        try:
            self.root.attributes("-fullscreen", False)
            self.root.geometry(self._pre_fullscreen_geometry or "400x300")
            self._is_fullscreen = False
        except self.tk.TclError:
            self._is_fullscreen = False

    def _restore_800x600(self):
        try:
            self.root.attributes("-fullscreen", False)
            self._is_fullscreen = False
            self.root.state("normal")
            self.root.geometry("800x600")
            self.root.lift()
            self.root.focus_force()
        except self.tk.TclError:
            pass

    def _minimize_to_tray(self):
        if self._tray_icon is None:
            self._tray_icon = _WindowsTrayIcon(
                self.root, "ZChat Web Status", self._show_from_tray,
                self._close_window, icon_path=BASE / "app.ico")
        if self._tray_icon.active:
            self.root.withdraw()
        else:
            self.note.set("System tray is unavailable; minimized to the taskbar.")
            self.root.iconify()

    def _show_from_tray(self):
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
        except self.tk.TclError:
            pass

    def _show_page_loading(self, duration=300):
        generation = getattr(self, "_page_loading_generation", 0) + 1
        self._page_loading_generation = generation
        self.page_loading_var.set("Loading pages...")
        self.root.after(duration, lambda: self._clear_page_loading(generation))

    def _clear_page_loading(self, generation):
        if generation == getattr(self, "_page_loading_generation", 0):
            self.page_loading_var.set("")

    def _on_root_resize(self, event):
        if event.widget is not self.root:
            return
        width = max(event.width, 300)
        height = max(event.height, 220)
        try:
            bot_active = str(self.nb.select()) == str(self.bot_manager_tab)
        except self.tk.TclError:
            bot_active = False
        if bot_active or height < 330:
            self.safety_banner.pack_forget()
        elif not self.safety_banner.winfo_manager():
            self.safety_banner.pack(fill="x", padx=10, pady=(5, 4), before=self.nb)
        self.safety_banner.configure(wraplength=max(220, width - 46))
        self.page_loading_label.configure(
            wraplength=max(60, width - 225))
        self.brand_title.configure(
            text=self._ui_text(
                "ZChat Web Status" if width >= 380 else "Web Status"))
        self.restore_size_button.configure(
            text=self._ui_text(
                "Restore 800x600" if width >= 460 else "800x600"),
            width=17 if width >= 460 else 7)
        self.tray_button.configure(width=5)
        self.language_button.configure(width=5)
        tab_padding = (14, 8) if width >= 440 else (8, 4)
        style = self.ttk.Style(self.root)
        style.configure("BotTab.TButton", padding=tab_padding)
        style.configure("BotTabActive.TButton", padding=tab_padding)
        self.nb.tab(
            1, text=self._ui_text(
                "Account & Session" if width >= 440 else "Session"))
        self.nb.tab(self.bot_manager_tab,
                    text=self._ui_text(
                        "Bot Manager" if width >= 380 else "Bot"))
        self._layout_action_bar(width)
        self._layout_status_pool(width)
        self._layout_bot_header(width, height)
        if height < 330:
            self.bot_activity_frame.pack_forget()
        elif not self.bot_activity_frame.winfo_manager():
            self.bot_activity_frame.pack(
                side="bottom", fill="x", pady=(6, 0), before=self.bot_page_host)
        self.bot_log_text.configure(height=2 if height >= 320 else 1)
        self.session_help_label.configure(wraplength=max(180, width - 100))
        if hasattr(self, "note_label"):
            self.note_label.configure(wraplength=max(210, width - 48))

    def _on_main_tab_changed(self, _event=None):
        """Give Bot Manager the full window; keep status actions on status tabs."""
        self._show_page_loading(300)
        try:
            bot_manager_active = str(self.nb.select()) == str(self.bot_manager_tab)
        except self.tk.TclError:
            return
        if bot_manager_active:
            self.safety_banner.pack_forget()
            self.app_footer.pack_forget()
            return
        if (self.root.winfo_height() >= 330 and
                not self.safety_banner.winfo_manager()):
            self.safety_banner.pack(fill="x", padx=10, pady=(5, 4), before=self.nb)
        if not self.app_footer.winfo_manager():
            self.app_footer.pack(
                side="bottom", fill="x", padx=10, pady=(0, 8), before=self.nb)

    # ---- Language support
    def _register_language_widget(self, widget, name, notebook_tab=False):
        """Register a widget for language support."""
        # Store widget reference
        if not hasattr(self, '_language_widgets'):
            self._language_widgets = {}
        self._language_widgets[name] = widget

    def _text(self, key):
        """Get translated text for the given key."""
        translations = {
            "bot_manager": {
                "en": "Bot Manager",
                "th": "ตัวจัดการบอท",
            },
            "bot_manager_subtitle": {
                "en": "Manage room, profile, and bot plugins",
                "th": "จัดการห้อง โปรไฟล์ และปลั๊กอินบอท",
            },
        }
        return translations.get(key, {}).get(self._language, key)

    def _ui_text(self, text, language=None):
        """Translate a known UI label while leaving user data untouched."""
        value = str(text)
        english_to_thai = dict(WEB_STATUS_GUI_TEXT_TH)
        english_to_thai.update({
            english: thai for thai, english in self.BOT_LABELS.items()
            if english not in english_to_thai
        })
        language = language or self._language
        if language == "th":
            translated = english_to_thai.get(value)
            if translated is None:
                translated = next((thai for english, thai in
                                   english_to_thai.items()
                                   if english.casefold() == value.casefold()), value)
            return translated
        thai_to_english = {thai: english for english, thai in
                           english_to_thai.items()}
        translated = thai_to_english.get(value)
        if translated is None:
            translated = next((english for thai, english in
                               thai_to_english.items()
                               if thai.casefold() == value.casefold()), value)
        return translated

    def _apply_language(self):
        """Apply the selected language to static labels and current messages."""
        self.root.title(self._ui_text("ZChat Web Status"))

        def visit(widget):
            try:
                widget_class = widget.winfo_class()
            except self.tk.TclError:
                return
            if widget is self.nb:
                for tab_id in widget.tabs():
                    current = widget.tab(tab_id, "text")
                    translated = self._ui_text(current)
                    if translated != current:
                        widget.tab(tab_id, text=translated)
            elif widget_class == "Menu":
                try:
                    last = widget.index("end")
                    for index in range((last if last is not None else -1) + 1):
                        current = widget.entrycget(index, "label")
                        translated = self._ui_text(current)
                        if translated != current:
                            widget.entryconfigure(index, label=translated)
                except self.tk.TclError:
                    pass
            else:
                try:
                    current = widget.cget("text")
                    translated = self._ui_text(current)
                    if translated != current:
                        widget.configure(text=translated)
                except (self.tk.TclError, TypeError):
                    pass

                if widget_class in ("Label", "TLabel"):
                    try:
                        variable_name = widget.cget("textvariable")
                        if variable_name:
                            current = self.root.getvar(variable_name)
                            translated = self._ui_text(current)
                            if translated != current:
                                self.root.setvar(variable_name, translated)
                    except self.tk.TclError:
                        pass

            try:
                children = widget.winfo_children()
            except self.tk.TclError:
                return
            for child in children:
                visit(child)

        visit(self.root)

    def _refresh_bot_plugin_summary(self):
        if not hasattr(self, "bot_plugin_summary_var"):
            return
        if (not getattr(self, "bot_client", None) and
                not getattr(self, "bot_plugin_catalog", [])):
            self.bot_plugin_summary_var.set(
                self._ui_text("Connect to load the installed plugins."))
            return
        enabled = sum(bool(variable.get()) for variable
                      in getattr(self, "bot_plugin_switch_vars", {}).values())
        installed = len(getattr(self, "bot_plugin_catalog", []))
        if self._language == "th":
            summary = "ติดตั้ง {0} รายการ · เปิดใช้งาน {1}".format(
                installed, enabled)
        else:
            summary = "{0} installed · {1} enabled".format(installed, enabled)
        self.bot_plugin_summary_var.set(summary)

    def _refresh_bot_plugin_title(self):
        if (not getattr(self, "bot_selected_plugin_id", None) or
                "plugins" not in getattr(self, "bot_pages", {})):
            return
        selected = next((name for plugin_id, name, _enabled
                         in self.bot_plugin_catalog
                         if plugin_id == self.bot_selected_plugin_id), None)
        if selected:
            self.bot_plugin_selected_var.set(
                "{0} {1}".format(self._ui_text("Settings:"),
                                 self._ui_text(selected)))

    def _refresh_bot_option_widgets(self, previous_language):
        """Refresh translated choices without changing their submitted values."""
        for controls in getattr(self, "bot_form_widgets", {}).values():
            for control in controls.values():
                if control.get("kind") == "multiselect":
                    widget = control.get("widget")
                    options = control.get("options", [])
                    if widget is None or not options:
                        continue
                    selected = set(widget.curselection())
                    widget.delete(0, "end")
                    for option in options:
                        widget.insert("end", self._ui_text(option.label))
                    for index in selected:
                        widget.selection_set(index)
                    continue
                if control.get("kind") not in ("select", "role_select"):
                    continue
                options = control.get("options", [])
                widget = control.get("widget")
                variable = control.get("variable")
                if widget is None or variable is None or not options:
                    continue
                selected_value = control.get("selected_value")
                selected = next((option for option in options
                                 if option.value == selected_value), None)
                if selected is None:
                    current = variable.get()
                    selected = next((option for option in options
                                     if current in (
                                         option.label,
                                         self._ui_text(option.label,
                                                       previous_language),
                                         self._ui_text(option.label))), None)
                widget.configure(values=[self._ui_text(option.label)
                                         for option in options])
                if selected is not None:
                    control["selected_value"] = selected.value
                    variable.set(self._ui_text(selected.label))

    def _bot_option_label(self, option, language=None):
        return self._ui_text(option.label, language=language)

    def _bind_bot_option_selection(self, control):
        """Remember the server value independently of the translated label."""
        def remember(_event=None):
            selected_index = control["widget"].current()
            if 0 <= selected_index < len(control["options"]):
                control["selected_value"] = control["options"][selected_index].value
                return
            selected = control["variable"].get()
            option = next((candidate for candidate in control["options"]
                           if self._bot_option_label(candidate) == selected), None)
            if option is not None:
                control["selected_value"] = option.value

        control["widget"].bind("<<ComboboxSelected>>", remember)

    def _language_button(self, parent, text, command=None, state="normal"):
        """Create a language-aware button."""
        button = self.ttk.Button(parent, text=text, command=command, state=state)
        return button

    def _create_scrollable_tab(self, parent):
        holder = self.ttk.Frame(parent)
        holder.pack(fill="both", expand=True)
        canvas = self.tk.Canvas(
            holder, highlightthickness=0,
            background=WEB_STATUS_THEME["background"])
        scrollbar = self.ttk.Scrollbar(
            holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = self.ttk.Frame(canvas)
        item = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>",
            lambda _event, c=canvas: c.configure(scrollregion=c.bbox("all")))
        canvas.bind(
            "<Configure>",
            lambda event, c=canvas, window=item:
            c.itemconfigure(window, width=event.width))
        canvas.bind("<Enter>", lambda _event, c=canvas:
                    c.bind_all("<MouseWheel>",
                               lambda wheel: self._scroll_canvas(wheel, c)))
        canvas.bind("<Leave>", lambda _event:
                    self.root.unbind_all("<MouseWheel>"))
        self._scrollable_canvases.append(canvas)
        return content

    def _scroll_canvas(self, event, canvas):
        if getattr(event, "delta", 0):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        elif getattr(event, "num", 0) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", 0) == 5:
            canvas.yview_scroll(1, "units")
        return "break"

    def _create_main_ui(self, setup_tab, session_tab):
        """Create the status setup and session controls."""
        pool_lines, pool_load_warning = load_web_text_slots()
        self._scrollable_canvases = []
        setup_content = self._create_scrollable_tab(setup_tab)
        session_content = self._create_scrollable_tab(session_tab)

        form = self.ttk.LabelFrame(
            setup_content, text="Status rotation", padding=(8, 6))
        self.status_form = form
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        self._language_label(form, "nickname").grid(row=0, column=0, sticky="w")
        self.login = self.tk.StringVar(value="Seaza")
        self.ttk.Entry(form, textvariable=self.login).grid(row=0, column=1, sticky="ew", padx=6)
        self.status_lines_label = self.ttk.Label(form, text="Status lines")
        self.status_lines_label.grid(
            row=1, column=0, sticky="nw", pady=(5, 0))
        self.pool_frame = self.ttk.Frame(form)
        self.pool_frame.grid(row=1, column=1, sticky="ew", padx=6, pady=(5, 0))
        self.pool_vars = []
        self.pool_entries = []
        self.pool_cells = []
        for slot in range(WEB_TEXT_SLOT_COUNT):
            cell = self.ttk.Frame(self.pool_frame)
            self.ttk.Label(cell, text="{0}.".format(slot + 1), width=3).pack(side="left")
            variable = self.tk.StringVar(value=pool_lines[slot])
            entry = self.ttk.Entry(cell, textvariable=variable, width=18)
            entry.pack(side="left", fill="x", expand=True)
            self.pool_cells.append(cell)
            self.pool_vars.append(variable)
            self.pool_entries.append(entry)
        self.pool_btns = self.ttk.Frame(form)
        self.btn_save_web_text = self.ttk.Button(
            self.pool_btns, text="Save to webtext.db", command=self.save_web_texts)
        self.btn_save_web_text.pack(fill="x")
        self.switch_label = self.ttk.Label(form, text="Switch every")
        self.switch_label.grid(row=2, column=0, sticky="w", pady=(5, 0))
        self.timing = self.ttk.Frame(form)
        self.timing.grid(row=2, column=1, sticky="w", padx=6, pady=(5, 0))
        self.interval = self.tk.StringVar(value=str(ROTATE_INTERVAL_DEFAULT))
        self.interval_spin = self.ttk.Spinbox(
            self.timing, textvariable=self.interval,
            from_=ROTATE_INTERVAL_MIN, to=ROTATE_INTERVAL_MAX,
            increment=30, width=6)
        self.interval_unit = self.ttk.Label(self.timing, text="s (min 30)")
        self.timeout_label = self.ttk.Label(self.timing, text="Timeout")
        self.timeout = self.tk.StringVar(value=str(TIMEOUT_DEFAULT))
        self.timeout_entry = self.ttk.Entry(
            self.timing, textvariable=self.timeout, width=6)
        self.live = self.tk.BooleanVar(value=False)
        self.live_check = self.ttk.Checkbutton(
            form, text="Send live (otherwise dry-run preview only)",
            variable=self.live)

        browser = self.ttk.LabelFrame(session_content, padding=(8, 6, 8, 6),
                                      text="Session (PHPSESSID via iframe / web login)")
        browser.pack(fill="x")
        self.session_help_label = self.ttk.Label(browser, text=(
            "Log in in your browser (via iframe/web login). In devtools Network tab, "
            "inspect https://profiles.camfrog.com/home.php to get PHPSESSID=..., "
            "paste below, then press 'Login' to verify and arm automation."
        ), wraplength=340, foreground="#555")
        self.session_help_label.pack(anchor="w", pady=(0, 4))
        row2 = self.ttk.Frame(browser)
        row2.pack(fill="x")
        self._language_button(row2, "open_login",
                              command=self.open_login_page).pack(side="left")
        self.cookie_paste_var = self.tk.StringVar(value=environment_cookie(
            "WEBAUTH_USER", "PHPSESSID"))
        self.ttk.Entry(row2, textvariable=self.cookie_paste_var,
                       width=18, show="*").pack(
                           side="left", padx=6, fill="x", expand=True)
        session_actions = self.ttk.Frame(browser)
        session_actions.pack(fill="x", pady=(4, 0))
        self.btn_login = self._language_button(
            session_actions, "login", command=self.login_session)
        self.btn_login.configure(style="Primary.TButton")
        self.btn_login.pack(side="left")
        self._language_button(session_actions, "browser_login",
                              command=self.login_via_browser).pack(
                                  side="left", padx=(6, 0))
        # Add cloudbot connection button
        self._language_button(session_actions, "cloudbot_login",
                              command=self.login_via_cloudbot).pack(
                                  side="left", padx=(6, 0))
        self.paste_state = self.ttk.Label(
            browser, text="", foreground=WEB_STATUS_THEME["muted"])
        self.paste_state.pack(anchor="w", pady=(4, 0))

        actions = self.ttk.Frame(self.app_footer, style="AppFooter.TFrame")
        actions.pack(fill="x")
        self.action_frame = actions
        self.action_buttons = {}
        self.action_specs = (
            ("preview", "Preview plan", "Preview", 7, self.preview),
            ("start", "Start", "Start", 5, self.start),
            ("stop", "Stop", "Stop", 5, self.stop),
            ("probe", "Probe session", "Probe", 5, self.probe),
            ("update", "Update", "Update", 6, self.check_update),
            ("capture", "How to capture", "Help", 5, self.capture),
        )
        for key, label, compact, width_chars, command in self.action_specs:
            button_style = ("ActionPrimary.TButton" if key == "start" else
                            "ActionStop.TButton" if key == "stop" else "TButton")
            button = self.ttk.Button(
                actions, text=label, command=command, style=button_style)
            self.action_buttons[key] = (button, label, compact, width_chars)
        self.btn_start = self.action_buttons["start"][0]
        self.btn_stop = self.action_buttons["stop"][0]
        self.more_menu = self.tk.Menu(self.root, tearoff=False)
        self.more_menu.add_command(label="Preview plan", command=self.preview)
        self.more_menu.add_command(label="Probe session", command=self.probe)
        self.more_menu.add_command(label="Update", command=self.check_update)
        self.more_menu.add_command(label="How to capture", command=self.capture)
        self.more_action_button = self.ttk.Menubutton(
            actions, text="More", menu=self.more_menu)
        self.note = self.tk.StringVar(value="Dry-run is on. Nothing has been sent.")
        if pool_load_warning:
            self.note.set(pool_load_warning)
        self.note_label = self.ttk.Label(
            self.app_footer, textvariable=self.note, style="AppFooter.TLabel",
            anchor="w", wraplength=350)
        self.note_label.pack(fill="x", pady=(5, 0))
        self._layout_status_pool(400)
        self._layout_action_bar(400)
        self._layout_bot_header(400, 300)
        self.root.after(120, self._poll)

    def _layout_action_bar(self, width):
        if not hasattr(self, "action_buttons"):
            return
        for child in self.action_frame.winfo_children():
            child.grid_forget()
        for column in range(6):
            self.action_frame.columnconfigure(column, weight=0, minsize=0)

        compact = width < 560
        if width < 360:
            visible = ("start", "stop")
            for key, (button, label, short, chars) in self.action_buttons.items():
                button.configure(text=self._ui_text(short if compact else label),
                                 width=chars if compact else 0)
                if key not in visible:
                    continue
                index = visible.index(key)
                button.grid(row=0, column=index, sticky="ew", padx=1, pady=1)
            self.more_action_button.configure(width=5)
            self.more_action_button.grid(row=0, column=2, sticky="ew",
                                         padx=1, pady=1)
            for column in range(3):
                self.action_frame.columnconfigure(column, weight=1)
            return

        for column, (key, (button, label, short, chars)) in enumerate(
                self.action_buttons.items()):
            button.configure(
                text=self._ui_text(short if compact else label),
                width=(chars if compact and self._language == "en" else 0))
            button.grid(row=0, column=column, sticky="ew" if compact else "w",
                        padx=1, pady=1)
            if compact:
                self.action_frame.columnconfigure(column, weight=1)

    def _layout_status_pool(self, width):
        if not hasattr(self, "pool_cells"):
            return
        narrow = width < 560
        one_column = width < 400
        for cell in self.pool_cells:
            cell.grid_forget()
        for column in range(2):
            self.pool_frame.columnconfigure(column, weight=0, minsize=0)
        columns = 1 if one_column else 2
        for column in range(columns):
            self.pool_frame.columnconfigure(column, weight=1)
        for slot, cell in enumerate(self.pool_cells):
            row = slot if one_column else slot % 5
            column = 0 if one_column else slot // 5
            cell.grid(row=row, column=column, sticky="ew",
                      padx=(0, 8 if column == 0 and columns == 2 else 0),
                      pady=2)

        self.pool_frame.grid_forget()
        self.pool_btns.grid_forget()
        self.timing.grid_forget()
        if narrow:
            self.pool_frame.grid(row=1, column=1, columnspan=2, sticky="ew",
                                 padx=6, pady=(5, 0))
            self.pool_btns.grid(row=2, column=1, columnspan=2, sticky="w",
                                padx=6, pady=(3, 0))
            self.switch_label.grid(row=3, column=0, sticky="w", pady=(5, 0))
            self.timing.grid(row=3, column=1, columnspan=2, sticky="w",
                             padx=6, pady=(5, 0))
            self.live_check.grid(row=4, column=0, columnspan=3,
                                 sticky="w", pady=(6, 0))
        else:
            self.pool_frame.grid(row=1, column=1, sticky="ew",
                                 padx=6, pady=(5, 0))
            self.pool_btns.grid(row=1, column=2, sticky="n", pady=(5, 0))
            self.switch_label.grid(row=2, column=0, sticky="w", pady=(5, 0))
            self.timing.grid(row=2, column=1, sticky="w", padx=6, pady=(5, 0))
            self.live_check.grid(row=3, column=0, columnspan=3,
                                 sticky="w", pady=(6, 0))

        for widget in (self.interval_spin, self.interval_unit,
                       self.timeout_label, self.timeout_entry):
            widget.grid_forget()
        if width < 480:
            self.interval_spin.grid(row=0, column=0, sticky="w")
            self.interval_unit.grid(row=0, column=1, sticky="w", padx=(4, 0))
            self.timeout_label.grid(row=1, column=0, sticky="w", pady=(3, 0))
            self.timeout_entry.grid(row=1, column=1, sticky="w",
                                    padx=(3, 0), pady=(3, 0))
        else:
            self.interval_spin.grid(row=0, column=0, sticky="w")
            self.interval_unit.grid(row=0, column=1, sticky="w", padx=(4, 0))
            self.timeout_label.grid(row=0, column=2, sticky="w", padx=(10, 0))
            self.timeout_entry.grid(row=0, column=3, sticky="w", padx=(3, 0))

    def _layout_bot_header(self, width, height):
        if not hasattr(self, "bot_header_widgets"):
            return
        (label, entry, connect, disconnect, stop, logout, status) = (
            self.bot_header_widgets)
        for widget in self.bot_header_widgets:
            widget.grid_forget()
        if width < 440:
            self.bot_header_brand.grid_forget()
        elif not self.bot_header_brand.winfo_manager():
            self.bot_header_brand.grid(
                row=0, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        if width >= 560:
            label.configure(text=self._ui_text("cloudbot parameter"))
            entry.configure(width=28)
            connect.configure(width=0, text=self._ui_text("Connect"))
            disconnect.configure(width=0, text=self._ui_text("Disconnect"))
            stop.configure(width=0, text=self._ui_text("Stop bot"))
            logout.configure(width=0, text=self._ui_text("Log out"))
            label.grid(row=1, column=0, sticky="w")
            entry.grid(row=1, column=1, sticky="ew", padx=6)
            connect.grid(row=1, column=2, padx=3)
            disconnect.grid(row=1, column=3, padx=3)
            stop.grid(row=2, column=0, sticky="w", padx=(0, 3), pady=(5, 0))
            logout.grid(row=2, column=1, sticky="w", padx=3, pady=(5, 0))
            status.grid(row=2, column=2, columnspan=2, sticky="w",
                        padx=6, pady=(5, 0))
        else:
            label.configure(text=self._ui_text(
                "cloudbot" if width >= 340 else ""))
            entry.configure(width=8)
            connect.configure(width=6, text=self._ui_text("Connect"))
            disconnect.configure(width=9, text=self._ui_text("Disconnect"))
            stop.configure(width=6, text=self._ui_text("Stop"))
            logout.configure(width=7, text=self._ui_text("Log out"))
            if width < 340:
                entry.grid(row=1, column=0, sticky="ew", padx=(0, 2))
                connect.grid(row=1, column=1, sticky="w", padx=2)
                disconnect.grid(row=1, column=2, sticky="w", padx=2)
                stop.grid(row=2, column=0, sticky="w", padx=(0, 2), pady=(3, 0))
                logout.grid(row=2, column=1, sticky="w", padx=2, pady=(3, 0))
                status_row = 3
            else:
                label.grid(row=1, column=0, sticky="w")
                entry.grid(row=1, column=1, sticky="ew", padx=3)
                connect.grid(row=1, column=2, sticky="w", padx=2)
                disconnect.grid(row=1, column=3, sticky="w", padx=2)
                stop.grid(row=2, column=0, sticky="w", padx=(0, 2), pady=(3, 0))
                logout.grid(row=2, column=1, sticky="w", padx=2, pady=(3, 0))
                status_row = 2
            status_column = 2 if width >= 340 else 0
            status_span = 2 if width >= 340 else 3
            status.grid(row=status_row, column=status_column,
                        columnspan=status_span, sticky="w",
                        padx=(3, 0) if width >= 340 else (0, 3), pady=(3, 0))
            status.configure(wraplength=max(190, width - 55))

    def _install_clipboard_shortcuts(self, parent):
        """Install layout-independent editing shortcuts on Tk text controls."""
        supported = {
            "Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text",
            "Listbox",
        }
        for widget in parent.winfo_children():
            try:
                widget_class = widget.winfo_class()
                widget_path = str(widget)
            except self.tk.TclError:
                continue
            if (widget_class in supported and
                    widget_path not in self._clipboard_bound_widgets):
                widget.bind(
                    "<Control-KeyPress>", self._handle_clipboard_control_key,
                    add="+")
                self._clipboard_bound_widgets.add(widget_path)
            self._install_clipboard_shortcuts(widget)

    def _handle_clipboard_control_key(self, event):
        """Recognize Ctrl shortcuts by keysym, control char, or Windows keycode."""
        keysym = str(getattr(event, "keysym", "")).lower()
        char = str(getattr(event, "char", "") or "")
        try:
            keycode = int(getattr(event, "keycode", -1))
        except (TypeError, ValueError):
            keycode = -1
        shortcuts = {
            "a": ({"a", "\x01"}, 65, "select_all"),
            "c": ({"c", "\x03"}, 67, "copy"),
            "v": ({"v", "\x16"}, 86, "paste"),
            "x": ({"x", "\x18"}, 88, "cut"),
        }
        action = next((shortcut for name, (chars, code, shortcut)
                       in shortcuts.items()
                       if keysym == name or char in chars or keycode == code), None)
        if action is None:
            return None
        self._perform_clipboard_action(event.widget, action)
        return "break"

    def _perform_clipboard_action(self, widget, action):
        try:
            widget_class = widget.winfo_class()
        except self.tk.TclError:
            return False
        supported = {
            "Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text",
            "Listbox",
        }
        editable = supported - {"Listbox"}
        if widget_class not in supported:
            return False

        if action == "select_all":
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
                return True
            except (self.tk.TclError, AttributeError):
                return False

        try:
            if widget_class == "Text":
                has_selection = bool(widget.tag_ranges("sel"))
                selection = ("sel.first", "sel.last")
            elif widget_class == "Listbox":
                selected = widget.curselection()
                has_selection = bool(selected)
                selection = selected
            else:
                has_selection = bool(widget.selection_present())
                selection = ("sel.first", "sel.last")
        except (self.tk.TclError, AttributeError):
            has_selection = False
            selection = None

        if action in {"copy", "cut"}:
            if (not has_selection or
                    (action == "cut" and (
                        widget_class not in editable or
                        not self._clipboard_widget_editable(widget)))):
                return False
            # A masked token/password field must accept paste but never export
            # its contents through Ctrl+C or Ctrl+X.
            try:
                if str(widget.cget("show")):
                    return False
            except (self.tk.TclError, AttributeError):
                pass
            try:
                if widget_class == "Text":
                    value = widget.get(*selection)
                elif widget_class == "Listbox":
                    value = "\n".join(widget.get(index) for index in selection)
                else:
                    content = widget.get()
                    start, end = (int(widget.index(index)) for index in selection)
                    value = content[start:end]
                if not self._write_clipboard_text(value):
                    return False
                if action == "cut":
                    widget.delete(*selection)
                return True
            except (self.tk.TclError, AttributeError, TypeError, ValueError):
                return False

        if action == "paste":
            if widget_class not in editable or not self._clipboard_widget_editable(widget):
                return False
            value = self._read_clipboard_text()
            if not value:
                return False
            try:
                if has_selection:
                    widget.delete(*selection)
                widget.insert("insert", value)
                return True
            except (self.tk.TclError, AttributeError, TypeError):
                return False
        return False

    def _clipboard_widget_editable(self, widget):
        try:
            return str(widget.cget("state")) not in {"disabled", "readonly"}
        except (self.tk.TclError, AttributeError):
            return True

    def _write_clipboard_text(self, value):
        value = str(value)
        if os.name == "nt":
            try:
                import win32clipboard
                import win32con

                for attempt in range(20):
                    try:
                        win32clipboard.OpenClipboard(None)
                        break
                    except Exception:
                        if attempt == 19:
                            raise
                        time.sleep(0.025)
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(value, win32con.CF_UNICODETEXT)
                finally:
                    win32clipboard.CloseClipboard()
                return True
            except Exception:
                pass
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            if os.name != "nt":
                self.root.update()
            return True
        except self.tk.TclError:
            return False

    def _read_clipboard_text(self):
        if os.name == "nt":
            try:
                import win32clipboard
                import win32con

                for attempt in range(20):
                    try:
                        win32clipboard.OpenClipboard(None)
                        break
                    except Exception:
                        if attempt == 19:
                            raise
                        time.sleep(0.025)
                try:
                    if not win32clipboard.IsClipboardFormatAvailable(
                            win32con.CF_UNICODETEXT):
                        if not win32clipboard.IsClipboardFormatAvailable(
                                win32con.CF_TEXT):
                            return ""
                        value = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                        if isinstance(value, bytes):
                            value = value.decode("mbcs", errors="replace")
                    else:
                        value = win32clipboard.GetClipboardData(
                            win32con.CF_UNICODETEXT)
                    return value if isinstance(value, str) else str(value)
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass
        try:
            return str(self.root.clipboard_get())
        except self.tk.TclError:
            return ""

    # ---- helpers
    def _text(self, key):
        return WEB_STATUS_LANGUAGE_TEXT[getattr(self, "_language", "en")][key]

    def _register_language_widget(self, widget, key, notebook_tab=False):
        self._language_widgets.append((widget, key, notebook_tab))
        return widget

    def _language_label(self, parent, key, **kwargs):
        return self._register_language_widget(
            self.ttk.Label(parent, text=self._text(key), **kwargs), key)

    def _language_button(self, parent, key, **kwargs):
        return self._register_language_widget(
            self.ttk.Button(parent, text=self._text(key), **kwargs), key)

    def _language_checkbutton(self, parent, key, **kwargs):
        return self._register_language_widget(
            self.ttk.Checkbutton(parent, text=self._text(key), **kwargs), key)

    def _language_labelframe(self, parent, key, **kwargs):
        return self._register_language_widget(
            self.ttk.LabelFrame(parent, text=self._text(key), **kwargs), key)

    def switch_language(self):
        previous_language = self._language
        self._language = "th" if self._language == "en" else "en"
        for widget, key, notebook_tab in self._language_widgets:
            if notebook_tab:
                self.nb.tab(widget, text=self._text(key))
            else:
                widget.configure(text=self._text(key))
        if self._note_key:
            self.note.set(self._text(self._note_key))
        else:
            self.note.set(self._translate_message(self._note_source))
        if hasattr(self, "paste_state"):
            self.paste_state.configure(
                text=self._translate_message(self._paste_state_source),
                foreground=self._paste_state_color)
        if hasattr(self, "_log_source_lines"):
            self._show_log_lines(self._log_source_lines)
        self._refresh_bot_option_widgets(previous_language)
        self._refresh_bot_plugin_summary()
        self._refresh_bot_plugin_title()
        self._layout_action_bar(self.root.winfo_width())

    def _set_note(self, text, key=None):
        self._note_source = str(text)
        self._note_key = key
        rendered = self._text(key) if key else self._translate_message(self._note_source)
        self.note.set(rendered)

    def _set_paste_state(self, text, foreground=None):
        foreground = foreground or WEB_STATUS_THEME["muted"]
        self._paste_state_source = str(text)
        self._paste_state_color = foreground
        self.paste_state.configure(
            text=self._translate_message(self._paste_state_source),
            foreground=foreground)

    def _translate_message(self, text):
        if getattr(self, "_language", "en") != "th":
            return text
        exact = {
            "Dry-run is on. Nothing has been sent.": "โหมดทดลองเปิดอยู่ ยังไม่มีการส่งสถานะ",
            "Working...": "กำลังทำงาน...",
            "Saved status lines to webtext.db.": "บันทึกข้อความสถานะลง webtext.db แล้ว",
            "No Chrome or Edge found on this computer.": "ไม่พบ Chrome หรือ Edge ในคอมพิวเตอร์เครื่องนี้",
            "Browser opened - sign in there; this window confirms by itself.":
                "เปิดเบราว์เซอร์แล้ว กรุณาเข้าสู่ระบบ หน้าต่างนี้จะตรวจสอบให้อัตโนมัติ",
            "browser opened": "เปิดเบราว์เซอร์แล้ว กำลังรอเข้าสู่ระบบ...",
            "Login page opened. After signing in, capture PHPSESSID on "
            "https://profiles.camfrog.com/home.php, paste it, then press Login.":
                "เปิดหน้า Login แล้ว หลังเข้าสู่ระบบ ให้รับค่า PHPSESSID จาก "
                "https://profiles.camfrog.com/home.php วางค่า แล้วกดตรวจสอบ Login",
            "PHPSESSID ready in memory. Click Login to verify.":
                "เก็บ PHPSESSID ไว้ในหน่วยความจำแล้ว กดตรวจสอบ Login",
            "Session captured from browser.": "รับเซสชันจากเบราว์เซอร์แล้ว",
            "Verifying PHPSESSID...": "กำลังตรวจสอบ PHPSESSID...",
            "Logged in via PHPSESSID - automation armed.":
                "ตรวจสอบ PHPSESSID แล้ว พร้อมใช้งานระบบอัตโนมัติ",
            "Logged in - automation armed.": "เข้าสู่ระบบแล้ว พร้อมใช้งานระบบอัตโนมัติ",
            "Live start cancelled. Nothing was sent.": "ยกเลิกการเริ่มส่งจริงแล้ว ไม่มีการส่งข้อมูล",
            "Stop requested. The current request may finish; no new updates will be scheduled.":
                "ส่งคำขอหยุดแล้ว คำขอที่กำลังทำงานอาจเสร็จก่อน จากนั้นจะไม่ส่งรายการใหม่",
            "Stop the live rotation before starting another network task.":
                "กรุณาหยุดการวนสถานะจริงก่อนเริ่มงานเครือข่ายอื่น",
            "Wait for the current request to finish before starting.":
                "รอให้คำขอปัจจุบันเสร็จก่อนเริ่มใหม่",
            "Enter a nickname first.": "กรุณากรอกชื่อเล่นก่อน",
            "Enter at least one status line.": "กรุณากรอกข้อความสถานะอย่างน้อยหนึ่งบรรทัด",
            "Enter or paste PHPSESSID first (Session tab).":
                "กรุณากรอกหรือวาง PHPSESSID ก่อน (แท็บบัญชีและเซสชัน)",
        }
        if text in exact:
            return exact[text]
        match = re.fullmatch(r"Rotation completed (\d+) round\(s\)\.", text)
        if match:
            return "วนครบ {0} รอบแล้ว".format(match.group(1))
        match = re.fullmatch(
            r"Temporary Live error; retrying in ([0-9.]+) seconds \((\d+)/(\d+)\)\.",
            text)
        if match:
            return "พบข้อผิดพลาดชั่วคราว กำลังลองอีกครั้งใน {0} วินาที ({1}/{2})".format(
                *match.groups())
        match = re.fullmatch(
            r"Live update failed after (\d+) retries; see Activity Log\.", text)
        if match:
            return "อัปเดต Live ไม่สำเร็จหลังลองซ้ำ {0} ครั้ง ดูรายละเอียดในบันทึกกิจกรรม".format(
                match.group(1))
        return text

    def _clipboard_error(self, exc):
        if hasattr(self, "note"):
            self._set_note("Paste failed: {0}: {1}".format(
                type(exc).__name__, exc))

    def chrome_import(self):
        """Auto-import the session from Chrome's store (nothing saved to disk)."""
        def do():
            jar = import_chrome_jar()
            return "loaded session from Chrome: {0}".format(cookie_summary(jar))

        self._run_bg(do, on_done=lambda: setattr(self, "chrome_mode", True))

    # ---- Bot Manager functionality
    BOT_LABELS = {
        "ห้อง": "Room",
        "กุญแจเข้าห้อง": "Room password",
        "ภาษาบอท": "Bot language",
        "หยุดบอท (ห้ามเข้าห้อง)": "Pause bot (do not enter the room)",
        "รหัสผ่านปัจจุบัน": "Current password",
        "รหัสผ่านใหม่": "New password",
        "อีเมล์ใหม่": "New email",
        "ชื่อบอท": "Bot name",
        "ข้อความ": "Message",
        "รายการโอเนอร์": "Owner list",
        "ช่วงเวลาที่จอแสดงผล (วินาที)": "Display interval (seconds)",
        "บทบาท": "Role",
        "ยินดี": "Greeting",
        "เปิดใช้งานบทบาทในการตรวจสอบ": "Enable role matching",
        "เปิดการใช้งานในการตรวจสอบเพศ": "Enable gender matching",
        "ระยะเวลาคำถาม": "Question duration",
        "หมดเวลา": "Time limit",
        "แจ้งเเตือนผู้ใช้เมื่อเกมไม่สามารถใช้งาน": "Notify users when the game is unavailable",
        "แสดงคำตอบที่ถูกต้องหากไม่ได้รับคำตอบ": "Show the answer when no one responds",
        "จำนวนแทนค่าที่กำหนด": "Number range",
        "คลาสตัวดำเนินการ": "Operator level",
        "เปิดเงื่อนไขการใช้งาน": "Enable addition",
        "เปิดการใช้งานในการลบ": "Enable subtraction",
        "เปิดการใช้งานการเพิ่มจำนวน": "Enable multiplication",
        "กฎกติกา": "Rules",
        "จำนวนของการละเมิด": "Violation count",
        "เตะช่อง/เหตุผลที่ทำโทษ": "Kick or ban reason",
        "โหมด Regexp": "Regular expression mode",
        "เตะ/ทำโทษ": "Kick or ban",
        "ระดับเวลาที่เลือก": "Question interval",
        "เริ่มเกมรูเล็ตทุกครั้ง": "Start roulette every",
        "จับเวลาการใช้งาน": "Enable timer",
        "แจ้งผู้ใช้รายอื่นในการแชท": "Notify other chat users",
        "แจ้งเตือนผู้ดูแลเท่านั้น": "Notify moderators only",
        "วันในการจัดเก็บบันทึก": "Keep records for days",
        "ชนิดลูกเต๋า": "Dice type",
        "นับลูกเต๋า": "Number of dice",
        "เวลาในการรับไมค์": "Microphone turn duration",
        "อนุญาตเฉพาะผู้ดูแลเท่านั้นที่สามารถรับไมค์ได้": "Allow moderators only",
        "แจ้งเตือนผู้ใช้เมื่อมีการใช้งาน": "Notify users when active",
        "ลงโทษ ชื่อเล่น อัตโนมัติ": "Automatically penalize nicknames",
        "รายงานเพื่อลงโทษผู้ใช้": "Reports before penalty",
    }

    def _create_bot_manager_content(self, parent):
        header = self.ttk.Frame(parent, style="BotHeader.TFrame", padding=8)
        header.pack(fill="x", pady=(0, 6))
        header.columnconfigure(1, weight=1)
        brand = self.ttk.Frame(header, style="BotHeader.TFrame")
        brand.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        self.bot_header_brand = brand
        self.ttk.Label(brand, text="Camfrog Bot Manager",
                       style="BotHeaderTitle.TLabel").pack(anchor="w")

        cloudbot_label = self.ttk.Label(
            header, text="cloudbot parameter", style="BotHeader.TLabel")
        cloudbot_label.grid(row=1, column=0, sticky="w")
        self.bot_cloudbot_var = self.tk.StringVar(value=environment_cookie(
            "WEBAUTH_BOT_USER", "cloudbot"))
        self.bot_cloudbot_entry = self.ttk.Entry(
            header, textvariable=self.bot_cloudbot_var, show="*", width=28)
        self.bot_cloudbot_entry.grid(row=1, column=1, sticky="ew", padx=6)
        self.bot_connect_button = self.ttk.Button(
            header, text="Connect", command=self.connect_bot)
        self.bot_connect_button.grid(row=1, column=2, padx=3)
        self.bot_disconnect_button = self.ttk.Button(
            header, text="Disconnect", command=self.disconnect_bot,
            state="disabled")
        self.bot_disconnect_button.grid(row=1, column=3, padx=3)
        self.bot_stop_button = self.ttk.Button(
            header, text="Stop bot", command=self.stop_bot,
            state="disabled")
        self.bot_stop_button.grid(row=2, column=0, sticky="w", padx=(0, 3), pady=(5, 0))
        self.bot_logout_button = self.ttk.Button(
            header, text="Log out", command=self.logout_bot,
            state="disabled")
        self.bot_logout_button.grid(row=2, column=1, sticky="w", padx=3, pady=(5, 0))
        self.bot_status_var = self.tk.StringVar(value="Disconnected")
        status_label = self.ttk.Label(
            header, textvariable=self.bot_status_var,
            style="BotHeaderHint.TLabel", wraplength=500)
        status_label.grid(row=2, column=2, columnspan=2, sticky="w",
                          padx=6, pady=(5, 0))
        self.bot_header_widgets = (
            cloudbot_label, self.bot_cloudbot_entry, self.bot_connect_button,
            self.bot_disconnect_button, self.bot_stop_button,
            self.bot_logout_button, status_label)

        navigation = self.ttk.Frame(parent)
        navigation.pack(fill="x", pady=(0, 4), before=header)
        for column in range(4):
            navigation.columnconfigure(column, weight=1)
        self.bot_page_host = self.ttk.Frame(parent)
        self.bot_page_host.pack(fill="both", expand=True)
        self.bot_tabs = {}
        self.bot_section_buttons = {}
        self.bot_active_tab = None
        self.bot_page_hosts = {}
        self.bot_save_buttons = {}
        self.bot_reload_buttons = {}
        self.bot_form_widgets = {}
        self.bot_form_context = {}
        self.bot_pages = {}
        self.bot_disconnected_label = self.ttk.Label(
            self.bot_page_host,
            text="Connect to Camfrog Bot Manager to load Room, Profile, and Plugins.",
            style="Hint.TLabel", wraplength=600, justify="left")
        self.bot_disconnected_label.pack(anchor="nw", padx=16, pady=16)

        for column, (key, title) in enumerate((
                ("room", "Room"), ("profile", "Profile"),
                ("plugins", "Plugins"), ("music_dj", "Music DJ"))):
            button = self.ttk.Button(
                navigation, text=self._ui_text(title), style="BotTab.TButton",
                state="normal" if key == "music_dj" else "disabled",
                command=lambda page_key=key: self._show_bot_tab(page_key))
            button.grid(row=0, column=column, sticky="ew", padx=2)
            self.bot_section_buttons[key] = button
            tab = self.ttk.Frame(self.bot_page_host, padding=6)
            self.bot_tabs[key] = tab
            if key == "music_dj":
                self._create_music_dj_controls(tab)
            elif key == "plugins":
                panes = self.ttk.Panedwindow(tab, orient="horizontal")
                panes.pack(fill="both", expand=True)
                catalog_panel = self.ttk.Frame(panes)
                settings_panel = self.ttk.Frame(panes)
                panes.add(catalog_panel, weight=2)
                panes.add(settings_panel, weight=3)
                self.bot_plugin_panes = panes
                self._create_plugin_selector(catalog_panel)
                self._create_bot_page_panel(settings_panel, key)
                panes.bind(
                    "<Configure>",
                    lambda _event, widget=panes: self._position_plugin_pane(widget))
            else:
                self._create_bot_page_panel(tab, key)

        log_frame = self.ttk.LabelFrame(
            parent, text=self._ui_text("Activity"), padding=5)
        self.bot_activity_frame = log_frame
        log_frame.pack(side="bottom", fill="x", pady=(6, 0),
                       before=self.bot_page_host)
        self.bot_log_text = self.tk.Text(
            log_frame, height=2, wrap="word", state="disabled",
            background=WEB_STATUS_THEME["entry"],
            foreground=WEB_STATUS_THEME["log_text"], font=("Consolas", 8))
        self.bot_log_text.pack(fill="x")

    def _show_bot_tab(self, key):
        tab = self.bot_tabs.get(key)
        if tab is None:
            return
        self._show_page_loading(250)
        self.bot_disconnected_label.pack_forget()
        for page in self.bot_tabs.values():
            page.pack_forget()
        tab.pack(fill="both", expand=True)
        self.bot_active_tab = key
        for page_key, button in self.bot_section_buttons.items():
            button.configure(style=("BotTabActive.TButton" if page_key == key
                                    else "BotTab.TButton"))

    def _create_music_dj_controls(self, parent):
        from tkinter import ttk

        settings = self._load_local_media_settings()
        executable = settings.get("music_dj_executable") or ""
        if not executable:
            found = default_music_dj_executable()
            executable = str(found) if found else ""
        self.music_dj_exe_var = self.tk.StringVar(value=executable)
        self.music_dj_speech_var = self.tk.StringVar()
        self.music_dj_status_var = self.tk.StringVar(
            value=self._ui_text("Select music-dj.exe to connect to the local DJ."))

        app_panel = self.ttk.LabelFrame(
            parent, text=self._ui_text("Local ZeaZDev room Music DJ"), padding=6)
        app_panel.pack(fill="both", expand=True, pady=(0, 6))
        app_panel.columnconfigure(0, weight=1)
        path_row = self.ttk.Frame(app_panel)
        path_row.grid(row=0, column=0, sticky="ew")
        path_row.columnconfigure(1, weight=1)
        self.ttk.Label(path_row, text=self._ui_text("Music DJ executable")).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self.ttk.Entry(path_row, textvariable=self.music_dj_exe_var).grid(
            row=0, column=1, sticky="ew")
        browse_button = self.ttk.Button(
            path_row, text=self._ui_text("Browse..."), command=self._browse_music_dj)
        browse_button.grid(row=0, column=2, padx=(5, 0))

        action_row = self.ttk.Frame(app_panel)
        action_row.grid(row=1, column=0, sticky="ew", pady=(5, 2))
        actions = (
            ("Open Music DJ", self._open_music_dj),
            ("Start DJ worker", self._start_music_dj),
            ("Stop DJ worker", self._stop_music_dj),
            ("Refresh queue", self._refresh_music_dj),
            ("Skip current", self._skip_music_dj),
            ("Clear queue", self._clear_music_dj),
        )
        self.music_dj_action_buttons = []
        for index, (label, command) in enumerate(actions):
            button = self.ttk.Button(
                action_row, text=self._ui_text(label), command=command)
            column = index % 3
            row = index // 3
            button.grid(row=row, column=column, padx=2, pady=2, sticky="ew")
            action_row.columnconfigure(column, weight=1)
            self.music_dj_action_buttons.append(button)

        voice_row = self.ttk.Frame(app_panel)
        voice_row.grid(row=2, column=0, sticky="ew", pady=(2, 4))
        voice_row.columnconfigure(0, weight=1)
        self.ttk.Entry(voice_row, textvariable=self.music_dj_speech_var).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        self.ttk.Button(
            voice_row, text=self._ui_text("Speak to room"),
            command=self._speak_music_dj).grid(row=0, column=1, sticky="ew")
        self.ttk.Button(
            voice_row, text=self._ui_text("Speak current song"),
            command=self._speak_current_music_dj).grid(
                row=0, column=2, sticky="ew", padx=(5, 0))
        self.ttk.Label(app_panel, text=self._ui_text(
            "Voice text: up to 240 characters. Route Windows output to the Camfrog mic.")).grid(
                row=3, column=0, sticky="w", pady=(0, 3))
        self.ttk.Label(app_panel, textvariable=self.music_dj_status_var,
                       style="Hint.TLabel", wraplength=700).grid(
            row=4, column=0, sticky="w", pady=(2, 4))
        queue_frame = self.ttk.Frame(app_panel)
        queue_frame.grid(row=5, column=0, sticky="nsew")
        app_panel.rowconfigure(5, weight=1)
        self.music_dj_tree = self.ttk.Treeview(
            queue_frame, columns=("position", "title", "user"),
            show="headings", height=5)
        for key, title, width in (("position", "#", 50),
                                  ("title", "Song", 360),
                                  ("user", "Requested by", 140)):
            self.music_dj_tree.heading(key, text=self._ui_text(title))
            self.music_dj_tree.column(key, width=width, anchor="w")
        scrollbar = self.ttk.Scrollbar(
            queue_frame, orient="vertical", command=self.music_dj_tree.yview)
        self.music_dj_tree.configure(yscrollcommand=scrollbar.set)
        self.music_dj_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        vmix_panel = self.ttk.LabelFrame(
            parent, text=self._ui_text("Local vMix control"), padding=6)
        vmix_panel.pack(fill="x")
        vmix_panel.columnconfigure(1, weight=1)
        self.vmix_url_var = self.tk.StringVar(
            value=settings.get("vmix_url") or "http://127.0.0.1:8088")
        self.ttk.Label(vmix_panel, text=self._ui_text("vMix HTTP API")).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self.ttk.Entry(vmix_panel, textvariable=self.vmix_url_var).grid(
            row=0, column=1, sticky="ew")
        refresh_inputs = self.ttk.Button(
            vmix_panel, text=self._ui_text("Refresh inputs"),
            command=self._refresh_vmix_inputs)
        refresh_inputs.grid(row=0, column=2, padx=(5, 0))
        self.vmix_input_var = self.tk.StringVar()
        self.vmix_input_picker = self.ttk.Combobox(
            vmix_panel, textvariable=self.vmix_input_var, state="readonly")
        self.vmix_input_picker.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.vmix_preview_button = self.ttk.Button(
            vmix_panel, text=self._ui_text("Preview input"),
            command=lambda: self._control_vmix("PreviewInput"))
        self.vmix_preview_button.grid(row=1, column=2, padx=(5, 2), pady=(5, 0))
        self.vmix_cut_button = self.ttk.Button(
            vmix_panel, text=self._ui_text("Cut to output"),
            command=lambda: self._control_vmix("CutDirect"))
        self.vmix_cut_button.grid(row=1, column=3, padx=(2, 0), pady=(5, 0))
        self.vmix_inputs = []
        self.vmix_action_buttons = [refresh_inputs, self.vmix_preview_button,
                                    self.vmix_cut_button]
        self.vmix_status_var = self.tk.StringVar(value="")
        self.ttk.Label(vmix_panel, textvariable=self.vmix_status_var,
                       style="Hint.TLabel", wraplength=700).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(3, 0))
        self._save_local_media_settings()

    def _load_local_media_settings(self):
        try:
            raw = (BASE / LOCAL_MEDIA_SETTINGS).read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return {key: str(data.get(key) or "") for key in
                        ("music_dj_executable", "vmix_url")}
        except (OSError, ValueError):
            pass
        return {}

    def _save_local_media_settings(self):
        if not hasattr(self, "music_dj_exe_var"):
            return
        data = {
            "music_dj_executable": self.music_dj_exe_var.get().strip(),
            "vmix_url": (self.vmix_url_var.get().strip()
                         if hasattr(self, "vmix_url_var") else
                         "http://127.0.0.1:8088"),
        }
        path = BASE / LOCAL_MEDIA_SETTINGS
        temporary = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _browse_music_dj(self):
        from tkinter import filedialog
        selected = filedialog.askopenfilename(
            parent=self.root, title=self._ui_text("Select music-dj.exe"),
            filetypes=(("Music DJ", "music-dj.exe"), ("Executable", "*.exe")))
        if selected:
            self.music_dj_exe_var.set(selected)
            self._save_local_media_settings()

    def _music_dj_executable(self):
        raw = self.music_dj_exe_var.get().strip()
        if not raw:
            found = default_music_dj_executable()
            if found:
                self.music_dj_exe_var.set(str(found))
                raw = str(found)
        path = Path(raw).expanduser().resolve() if raw else None
        if path is None or path.name.casefold() != "music-dj.exe" or not path.is_file():
            raise FileNotFoundError(self._ui_text(
                "Select an existing packaged music-dj.exe application."))
        self._save_local_media_settings()
        return path

    def _open_music_dj(self):
        try:
            executable = self._music_dj_executable()
            if os.name != "nt":
                raise OSError("Music DJ applications run on Windows.")
            subprocess.Popen([str(executable)], cwd=str(executable.parent),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             creationflags=0x08000000)
            self.music_dj_status_var.set(self._ui_text("Opened the Music DJ window."))
        except (OSError, ValueError) as exc:
            self.music_dj_status_var.set(str(exc))

    def _start_music_dj_task(self, action, work, done):
        if self.bot_busy:
            self.music_dj_status_var.set(self._ui_text(
                "Wait for the current Bot Manager request to finish."))
            return
        self.bot_busy = True
        self._set_bot_request_busy(True)

        def worker():
            try:
                result, error = work(), ""
            except Exception as exc:
                result, error = None, str(exc)
            self.bot_q.put((result, error, done))

        self.music_dj_status_var.set(self._ui_text(action))
        threading.Thread(target=worker, daemon=True).start()

    def _render_music_dj_state(self, state):
        self.music_dj_tree.delete(*self.music_dj_tree.get_children())
        current = state.get("current")
        if isinstance(current, dict):
            self.music_dj_tree.insert("", "end", iid="current", values=(
                self._ui_text("Now playing"), current.get("title", ""),
                current.get("user", "")))
        for index, item in enumerate(state.get("queue", [])[:50], 1):
            if isinstance(item, dict):
                self.music_dj_tree.insert("", "end", iid="q{0}".format(index),
                                          values=(index, item.get("title", ""),
                                                  item.get("user", "")))
        self.music_dj_status_var.set(self._ui_text(
            "DJ worker connected; queue read from the Music DJ owner."))

    def _refresh_music_dj(self):
        try:
            executable = self._music_dj_executable()
        except (OSError, ValueError) as exc:
            self.music_dj_status_var.set(str(exc))
            return

        def done(state, error):
            if error:
                snapshot = music_dj_worker_state(executable)
                if snapshot is not None:
                    self._render_music_dj_state(snapshot)
                else:
                    self.music_dj_tree.delete(*self.music_dj_tree.get_children())
                    self.music_dj_status_var.set(self._ui_text(error))
                return
            self._render_music_dj_state(state)

        self._start_music_dj_task(
            "Reading the Music DJ queue...",
            lambda: music_dj_control_request(executable, "status"), done)

    def _start_music_dj(self):
        try:
            executable = self._music_dj_executable()
        except (OSError, ValueError) as exc:
            self.music_dj_status_var.set(str(exc))
            return

        def work():
            run_music_dj_command(executable, "start")
            return music_dj_control_request(executable, "status", timeout=8.0)

        def done(state, error):
            if error:
                self.music_dj_status_var.set(self._ui_text(error))
            else:
                self._render_music_dj_state(state)

        self._start_music_dj_task("Starting the Music DJ worker...", work, done)

    def _stop_music_dj(self):
        try:
            executable = self._music_dj_executable()
        except (OSError, ValueError) as exc:
            self.music_dj_status_var.set(str(exc))
            return

        def done(_result, error):
            self.music_dj_tree.delete(*self.music_dj_tree.get_children())
            self.music_dj_status_var.set(self._ui_text(
                error or "Music DJ worker stopped."))

        self._start_music_dj_task(
            "Stopping the Music DJ worker...",
            lambda: run_music_dj_command(executable, "stop"), done)

    def _skip_music_dj(self):
        self._music_dj_request("skip")

    def _clear_music_dj(self):
        from tkinter import messagebox
        if not messagebox.askyesno(
                self._ui_text("Clear queue"),
                self._ui_text("Stop the current track and remove every queued song?"),
                parent=self.root):
            return
        self._music_dj_request("clear")

    def _speak_music_dj(self):
        self._music_dj_request("speak", self.music_dj_speech_var.get())

    def _speak_current_music_dj(self):
        try:
            executable = self._music_dj_executable()
        except (OSError, ValueError) as exc:
            self.music_dj_status_var.set(str(exc))
            return

        def work():
            state = music_dj_control_request(executable, "status")
            text = music_dj_speech_summary(state)
            return music_dj_control_request(executable, "speak", text=text)

        def done(_result, error):
            self.music_dj_status_var.set(self._ui_text(
                error or "Voice request accepted by Music DJ."))

        self._start_music_dj_task(
            "Preparing the current song voice summary...", work, done)

    def _music_dj_request(self, action, text=None):
        try:
            executable = self._music_dj_executable()
        except (OSError, ValueError) as exc:
            self.music_dj_status_var.set(str(exc))
            return

        def done(state, error):
            if error:
                self.music_dj_status_var.set(self._ui_text(error))
            else:
                self._render_music_dj_state(state)
                if action == "speak":
                    self.music_dj_speech_var.set("")
                    self.music_dj_status_var.set(self._ui_text(
                        "Voice request accepted by Music DJ."))

        self._start_music_dj_task(
            "Sending {0} to Music DJ...".format(action),
            lambda: music_dj_control_request(executable, action, text=text), done)

    def _refresh_vmix_inputs(self):
        address = self.vmix_url_var.get().strip()
        self._save_local_media_settings()

        def done(inputs, error):
            if error:
                self.vmix_status_var.set(self._ui_text(error))
                return
            self.vmix_inputs = inputs
            labels = ["{0}: {1}".format(item["number"], item["title"])
                      for item in inputs]
            self.vmix_input_picker.configure(values=labels)
            if labels and self.vmix_input_var.get() not in labels:
                self.vmix_input_var.set(labels[0])
            self.vmix_status_var.set(self._ui_text(
                "vMix connected; {0} inputs returned by the local API.").format(len(inputs)))

        self._start_music_dj_task(
            "Reading local vMix inputs...",
            lambda: fetch_vmix_inputs(address), done)

    def _control_vmix(self, function):
        label = self.vmix_input_var.get()
        item = next((entry for entry in self.vmix_inputs
                     if label == "{0}: {1}".format(entry["number"], entry["title"])), None)
        if item is None:
            self.vmix_status_var.set(self._ui_text("Refresh vMix inputs and select one first."))
            return
        if function == "CutDirect":
            from tkinter import messagebox
            if not messagebox.askyesno(
                    self._ui_text("Cut to output"),
                    self._ui_text("Send this input directly to the live vMix output?"),
                    parent=self.root):
                return
        address = self.vmix_url_var.get().strip()

        def done(inputs, error):
            if error:
                self.vmix_status_var.set(self._ui_text(error))
                return
            self.vmix_inputs = inputs
            labels = ["{0}: {1}".format(value["number"], value["title"])
                      for value in inputs]
            self.vmix_input_picker.configure(values=labels)
            self.vmix_input_var.set(next((value for value in labels
                                          if value.startswith(str(item["number"]) + ":")), ""))
            self.vmix_status_var.set(self._ui_text(
                "vMix accepted {0}; confirm program output in vMix and Camfrog.").format(
                    "preview" if function == "PreviewInput" else "cut"))

        self._start_music_dj_task(
            "Sending a command to local vMix...",
            lambda: control_vmix_input(address, function, item["key"]), done)

    def _show_bot_disconnected(self):
        for page in self.bot_tabs.values():
            page.pack_forget()
        self.bot_active_tab = None
        self.bot_disconnected_label.pack(anchor="nw", padx=16, pady=16)
        for button in self.bot_section_buttons.values():
            button.configure(style="BotTab.TButton")

    def _position_plugin_pane(self, panes):
        try:
            if getattr(panes, "_default_sash_positioned", False):
                return
            width = panes.winfo_width()
            if width >= 480:
                panes.sashpos(0, int(width * 0.36))
                panes._default_sash_positioned = True
        except self.tk.TclError:
            pass

    def _create_plugin_selector(self, parent):
        catalog = self.ttk.LabelFrame(
            parent, text=self._ui_text("Plugin catalog"), padding=6)
        catalog.pack(fill="both", expand=True)
        toolbar = self.ttk.Frame(catalog)
        toolbar.pack(fill="x", pady=(0, 5))
        search_row = self.ttk.Frame(toolbar)
        search_row.pack(fill="x")
        self.bot_plugin_filter_var = self.tk.StringVar(value="")
        filter_entry = self.ttk.Entry(
            search_row, textvariable=self.bot_plugin_filter_var, width=24)
        self.ttk.Label(search_row, text=self._ui_text("Find")).pack(side="left")
        filter_entry.pack(side="left", fill="x", expand=True, padx=(5, 0))
        self.bot_plugin_filter_var.trace_add(
            "write", lambda *_args: self._layout_plugin_cards())
        action_row = self.ttk.Frame(toolbar)
        action_row.pack(fill="x", pady=(4, 0))
        self.bot_plugin_refresh_button = self.ttk.Button(
            action_row, text=self._ui_text("Refresh"),
            command=self.refresh_bot_plugins,
            state="disabled")
        self.bot_plugin_refresh_button.pack(side="left", padx=(0, 4))
        self.bot_plugin_activation_button = self.ttk.Button(
            action_row, text=self._ui_text("Apply changes"),
            command=self.apply_plugin_activation,
            state="disabled")
        self.bot_plugin_activation_button.pack(side="left")

        self.bot_plugin_summary_var = self.tk.StringVar(
            value="Connect to load the installed plugins.")
        self.ttk.Label(catalog, textvariable=self.bot_plugin_summary_var,
                       style="Hint.TLabel").pack(anchor="w", pady=(0, 4))
        body = self.ttk.Frame(catalog)
        body.pack(fill="both", expand=True)
        self.bot_plugin_canvas = self.tk.Canvas(
            body, highlightthickness=0, background=WEB_STATUS_THEME["background"])
        scrollbar = self.ttk.Scrollbar(
            body, orient="vertical", command=self.bot_plugin_canvas.yview)
        self.bot_plugin_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.bot_plugin_canvas.pack(side="left", fill="both", expand=True)
        self.bot_plugin_switch_frame = self.ttk.Frame(
            self.bot_plugin_canvas, padding=(2, 2))
        self.bot_plugin_window = self.bot_plugin_canvas.create_window(
            (0, 0), window=self.bot_plugin_switch_frame, anchor="nw")
        self.bot_plugin_switch_frame.bind(
            "<Configure>", lambda _event: self.bot_plugin_canvas.configure(
                scrollregion=self.bot_plugin_canvas.bbox("all")))
        self.bot_plugin_canvas.bind("<Configure>", self._layout_plugin_cards)
        self.bot_plugin_switch_vars = {}
        self.bot_plugin_switch_original = {}
        self.bot_plugin_cards = {}
        self.bot_plugin_configure_buttons = {}
        self.bot_plugin_empty_label = self.ttk.Label(
            self.bot_plugin_switch_frame,
            text=self._ui_text("No plugins loaded."),
            style="Hint.TLabel")
        self.bot_plugin_selected_var = self.tk.StringVar(
            value="Select a plugin card to open its settings.")

    def _layout_plugin_cards(self, event=None):
        if not hasattr(self, "bot_plugin_canvas"):
            return
        width = (event.width if event is not None and hasattr(event, "width")
                 else self.bot_plugin_canvas.winfo_width())
        width = max(1, width)
        self.bot_plugin_canvas.itemconfigure(self.bot_plugin_window, width=width)
        query = self.bot_plugin_filter_var.get().strip().casefold()
        visible = [
            (plugin_id, name, card)
            for plugin_id, name, _enabled in self.bot_plugin_catalog
            if query in str(plugin_id).casefold()
            or query in str(name).casefold()
            or query in self._ui_text(name).casefold()
            for card in (self.bot_plugin_cards.get(plugin_id),)
            if card is not None
        ]
        columns = max(1, min(len(visible) or 1, width // 250))
        for column in range(max(columns, getattr(self, "bot_plugin_grid_columns", 0))):
            self.bot_plugin_switch_frame.grid_columnconfigure(
                column, weight=1 if column < columns else 0)
        self.bot_plugin_grid_columns = columns
        for card in self.bot_plugin_cards.values():
            card.grid_forget()
        self.bot_plugin_empty_label.grid_forget()
        if not visible:
            text = ("No plugins match this search." if self.bot_plugin_catalog
                    else "Camfrog returned no installed plugins.")
            self.bot_plugin_empty_label.configure(text=self._ui_text(text))
            self.bot_plugin_empty_label.grid(row=0, column=0, sticky="w", padx=5, pady=5)
            return
        for index, (_plugin_id, _name, card) in enumerate(visible):
            card.grid(row=index // columns, column=index % columns,
                      sticky="nsew", padx=4, pady=4)

    def _render_plugin_activation(self, catalog):
        for child in self.bot_plugin_switch_frame.winfo_children():
            if child is not self.bot_plugin_empty_label:
                child.destroy()
        self.bot_plugin_catalog = list(catalog)
        self.bot_plugin_switch_vars = {}
        self.bot_plugin_switch_original = {}
        self.bot_plugin_cards = {}
        self.bot_plugin_configure_buttons = {}
        known_plugin_ids = {plugin_id for plugin_id, _name, _enabled in catalog}
        if (self.bot_selected_plugin_id is not None and
                self.bot_selected_plugin_id not in known_plugin_ids):
            self.bot_selected_plugin_id = None
            self.bot_plugin_selected_var.set(
                "No installed plugin settings are available.")
            self._clear_bot_plugin_settings(
                "The selected plugin is no longer in Camfrog's list.")
        for plugin_id, name, enabled in catalog:
            variable = self.tk.BooleanVar(value=bool(enabled))
            self.bot_plugin_switch_vars[plugin_id] = variable
            self.bot_plugin_switch_original[plugin_id] = bool(enabled)
            card = self.ttk.Frame(self.bot_plugin_switch_frame, padding=(4, 2))
            self.ttk.Checkbutton(card, variable=variable).pack(side="left")
            configure = self.ttk.Button(
                card, text=self._ui_text(name), style="BotLink.TButton",
                command=lambda selected_id=plugin_id: self.load_bot_plugin(selected_id),
                state="disabled" if self.bot_client is None or self.bot_busy else "normal")
            configure.pack(side="left", fill="x", expand=True, anchor="w")
            self.bot_plugin_cards[plugin_id] = card
            self.bot_plugin_configure_buttons[plugin_id] = configure
        self._refresh_bot_plugin_summary()
        self.bot_plugin_empty_label.lift()
        self._layout_plugin_cards()

    def apply_plugin_activation(self):
        if self.bot_client is None:
            self.bot_status_var.set("Connect to Bot Manager first.")
            return
        changes = [(plugin_id, bool(variable.get()))
                   for plugin_id, variable in self.bot_plugin_switch_vars.items()
                   if bool(variable.get()) !=
                   self.bot_plugin_switch_original.get(plugin_id)]
        if not changes:
            self.bot_status_var.set("No plugin activation changes to apply.")
            return
        from tkinter import messagebox
        if not messagebox.askyesno(
                "Apply plugin activation",
                "Send {0} plugin activation change(s) to Camfrog?".format(
                    len(changes)), parent=self.root):
            return
        client = self.bot_client
        generation = self.bot_generation
        self.bot_status_var.set("Applying plugin activation changes...")

        def work():
            for plugin_id, enabled in changes:
                client.set_plugin_enabled(plugin_id, enabled)
            page = client.fetch_page("/bot/plugins/1")
            return page, client.plugin_catalog(page)

        def done(result, error):
            if generation != self.bot_generation:
                return
            if error:
                self.bot_status_var.set(
                    "Activation may be partial. Refresh the plugin list to verify.")
                self._bot_log("Plugin activation request needs read-back verification.")
                return
            index_page, catalog = result
            self.bot_plugin_index_page = index_page
            self._render_plugin_activation(catalog)
            read_back = {plugin_id: enabled for plugin_id, _name, enabled
                         in self.bot_plugin_catalog}
            if all(read_back.get(plugin_id) == enabled
                   for plugin_id, enabled in changes):
                self.bot_status_var.set(
                    "Camfrog read back the requested plugin activation states.")
            else:
                self.bot_status_var.set(
                    "Camfrog returned different plugin states. Review before retrying.")
            self._bot_log("Read back plugin activation states.")

        self._start_bot_task(work, done)

    def refresh_bot_plugins(self):
        if self.bot_client is None:
            self.bot_status_var.set("Connect to Bot Manager first.")
            return
        client = self.bot_client
        generation = self.bot_generation
        self.bot_status_var.set("Refreshing installed plugins...")

        def done(page, error):
            if generation != self.bot_generation:
                return
            if error:
                self.bot_status_var.set(error)
                return
            catalog = client.plugin_catalog(page)
            self.bot_plugin_index_page = page
            self._render_plugin_activation(catalog)
            self.bot_status_var.set(
                "Refreshed {0} plugin states from Camfrog.".format(len(catalog)))
            self._bot_log("Refreshed plugin list and activation states.")

        self._start_bot_task(lambda: client.fetch_page("/bot/plugins/1"), done)

    def _create_bot_page_panel(self, parent, key):
        actions = self.ttk.Frame(parent)
        actions.pack(fill="x", pady=(0, 5))
        reload_button = self.ttk.Button(
            actions, text=self._ui_text(
                "Reload settings" if key == "plugins" else "Reload"),
            command=lambda page_key=key:
            self.reload_bot_page(page_key), state="disabled")
        reload_button.pack(side="left")
        if key == "plugins":
            self.ttk.Label(actions, textvariable=self.bot_plugin_selected_var,
                           style="Hint.TLabel").pack(side="left", padx=8)
        self.bot_reload_buttons[key] = reload_button

        body = self.ttk.Frame(parent)
        body.pack(fill="both", expand=True)
        canvas = self.tk.Canvas(
            body, highlightthickness=1,
            highlightbackground=WEB_STATUS_THEME["border"],
            highlightcolor=WEB_STATUS_THEME["border"],
            background=WEB_STATUS_THEME["surface"])
        scrollbar = self.ttk.Scrollbar(
            body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = self.ttk.Frame(canvas, padding=8)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _event, c=canvas:
                   c.configure(scrollregion=c.bbox("all")))
        canvas.bind("<Configure>", lambda event, c=canvas, w=window:
                    c.itemconfigure(w, width=event.width))
        self.bot_page_hosts[key] = inner

        save_footer = self.ttk.Frame(parent)
        save_footer.pack(fill="x", pady=(6, 0))
        save_button = self.ttk.Button(
            save_footer, text=self._ui_text("Save settings"),
            command=lambda page_key=key:
            self.save_bot_page(page_key), state="disabled",
            style="BotPrimary.TButton")
        save_button.pack(anchor="center")
        self.bot_save_buttons[key] = save_button

    def _connect_bot_manager(self):
        if self.bot_busy:
            self.bot_status_var.set("Wait for the current Bot Manager request to finish.")
            return
        if self.bot_client is not None:
            self.bot_status_var.set("Disconnect before connecting another session.")
            return
        raw = self.bot_cloudbot_var.get().strip()
        self.bot_cloudbot_var.set("")
        raw = raw.rstrip(";").strip()
        match = re.fullmatch(r"cloudbot=([A-Za-z0-9_-]{16,128})", raw)
        if not match:
            self.bot_status_var.set(
                "Enter a valid cloudbot=<token> parameter.")
            return
        try:
            client = CamfrogBotManagerClient(match.group(1))
        except ValueError as exc:
            self.bot_status_var.set(str(exc))
            return
        self.bot_generation += 1
        generation = self.bot_generation
        self.bot_status_var.set("Connecting and loading manager pages...")
        self.bot_connect_button.configure(state="disabled")

        def work():
            room = client.fetch_page("/bot/room")
            profile = client.fetch_page("/bot/profile")
            plugin_index = client.fetch_page("/bot/plugins/1")
            catalog = client.plugin_catalog(plugin_index)
            plugin_page = plugin_index
            selected_id = catalog[0][0] if catalog else None
            if selected_id is not None:
                expected_path = "/bot/plugins/{0}".format(selected_id)
                if plugin_index.path != expected_path:
                    plugin_page = client.fetch_page(expected_path)
            return room, profile, plugin_index, plugin_page, catalog, selected_id

        def done(result, error):
            if generation != self.bot_generation:
                client.clear()
                return
            if error:
                client.clear()
                self.bot_connect_button.configure(state="normal")
                self.bot_status_var.set(error)
                return
            self.bot_client = client
            room, profile, plugin_index, plugin_page, catalog, selected_id = result
            self.bot_plugin_catalog = catalog
            self.bot_plugin_index_page = plugin_index
            self.bot_selected_plugin_id = selected_id
            self.bot_pages = {
                "room": room,
                "profile": profile,
                "plugins": plugin_page,
            }
            for key in ("room", "profile", "plugins"):
                self.bot_section_buttons[key].configure(state="normal")
                self.bot_reload_buttons[key].configure(state="normal")
                self.bot_save_buttons[key].configure(state="normal")
            self.bot_disconnect_button.configure(state="normal")
            self.bot_stop_button.configure(state="normal")
            self.bot_logout_button.configure(state="normal")
            self.bot_plugin_refresh_button.configure(state="normal")
            self.bot_plugin_activation_button.configure(state="normal")
            if selected_id is not None:
                plugin_name = next(name for plugin_id, name, _enabled in catalog
                                   if plugin_id == selected_id)
                self.bot_plugin_selected_var.set(
                    "{0} {1}".format(self._ui_text("Settings:"),
                                     self._ui_text(plugin_name)))
            else:
                self.bot_plugin_selected_var.set(
                    "No installed plugin settings are available.")
            self._render_plugin_activation(catalog)
            self.bot_status_var.set(
                "Connected. Room, profile, and {0} plugins loaded.".format(
                    len(catalog)))
            self._render_bot_page("room", room)
            self._render_bot_page("profile", profile)
            if selected_id is not None:
                self._render_bot_page("plugins", plugin_page)
            else:
                self._clear_bot_plugin_settings(
                    "Camfrog returned no installed plugin settings.")
            self._show_bot_tab("room")
            self._bot_log("Connected to Camfrog Bot Manager.")
            self._bot_log("Loaded room, profile, and plugin catalog.")

        self._start_bot_task(work, done)

    def connect_bot(self):
        self._connect_bot_manager()

    def _start_bot_task(self, work, callback):
        if self.bot_busy:
            self.bot_status_var.set("A Bot Manager request is already running.")
            return
        self.bot_busy = True
        self._set_bot_request_busy(True)

        def worker():
            try:
                result = work()
                error = ""
            except (RuntimeError, ValueError) as exc:
                result, error = None, str(exc)
            except Exception as exc:
                result, error = None, "Request failed ({0}).".format(
                    type(exc).__name__)
            self.bot_q.put((result, error, callback))

        threading.Thread(target=worker, daemon=True).start()

    def _set_bot_request_busy(self, busy):
        connected = self.bot_client is not None
        self.bot_connect_button.configure(
            state="disabled" if busy or connected else "normal")
        for button in (self.bot_disconnect_button, self.bot_stop_button,
                       self.bot_logout_button):
            button.configure(state="disabled" if busy or not connected else "normal")
        for key, button in self.bot_reload_buttons.items():
            available = connected and key in self.bot_pages
            button.configure(state="disabled" if busy or not available else "normal")
        for key, button in self.bot_save_buttons.items():
            available = connected and key in self.bot_pages
            button.configure(state="disabled" if busy or not available else "normal")
        if hasattr(self, "bot_plugin_activation_button"):
            self.bot_plugin_activation_button.configure(
                state="disabled" if busy or not connected else "normal")
        if hasattr(self, "bot_plugin_refresh_button"):
            self.bot_plugin_refresh_button.configure(
                state="disabled" if busy or not connected else "normal")
        for button in getattr(self, "bot_plugin_configure_buttons", {}).values():
            button.configure(state="disabled" if busy or not connected else "normal")
        for button in getattr(self, "music_dj_action_buttons", ()):
            button.configure(state="disabled" if busy else "normal")
        for button in getattr(self, "vmix_action_buttons", ()):
            button.configure(state="disabled" if busy else "normal")

    def _bot_page_path(self, key):
        if key == "room":
            return "/bot/room"
        if key == "profile":
            return "/bot/profile"
        if key == "plugins":
            plugin_id = self.bot_selected_plugin_id
            if plugin_id is None or plugin_id not in {
                    item[0] for item in self.bot_plugin_catalog}:
                raise ValueError("The selected plugin is not in the server list.")
            return "/bot/plugins/{0}".format(plugin_id)
        raise ValueError("Unknown Bot Manager page.")

    def reload_bot_page(self, key):
        if self.bot_client is None:
            self.bot_status_var.set("Connect to Bot Manager first.")
            return
        try:
            path = self._bot_page_path(key)
        except ValueError as exc:
            self.bot_status_var.set(str(exc))
            return
        self.bot_status_var.set("Reloading {0} settings...".format(key))
        client = self.bot_client
        generation = self.bot_generation

        def done(page, error):
            if generation != self.bot_generation:
                return
            if error:
                self.bot_status_var.set(error)
                return
            self.bot_pages[key] = page
            self._render_bot_page(key, page)
            self.bot_status_var.set("Loaded {0} settings.".format(key))
            self._bot_log("Loaded {0} settings.".format(key))

        self._start_bot_task(lambda: client.fetch_page(path), done)

    def load_bot_plugin(self, plugin_id=None):
        if self.bot_client is None:
            self.bot_status_var.set("Connect to Bot Manager first.")
            return
        if plugin_id is not None:
            try:
                selected_id = int(plugin_id)
            except (TypeError, ValueError):
                self.bot_status_var.set("Choose a plugin from the loaded catalog.")
                return
            if selected_id not in {item[0] for item in self.bot_plugin_catalog}:
                self.bot_status_var.set("That plugin is not in Camfrog's list.")
                return
            self.bot_selected_plugin_id = selected_id
        try:
            path = self._bot_page_path("plugins")
        except ValueError as exc:
            self.bot_status_var.set(str(exc))
            return
        selected = next((name for candidate_id, name, _enabled
                         in self.bot_plugin_catalog
                         if candidate_id == self.bot_selected_plugin_id), "plugin")
        display_name = self._ui_text(selected)
        self.bot_plugin_selected_var.set(
            "{0} {1}".format(self._ui_text("Loading settings:"), display_name))
        self._clear_bot_plugin_settings(
            "{0} {1}…".format(self._ui_text("Loading settings for"), display_name))
        self.bot_status_var.set(
            "{0} {1}…".format(self._ui_text("Loading settings for"), display_name))
        client = self.bot_client
        generation = self.bot_generation

        def done(page, error):
            if generation != self.bot_generation:
                return
            if error:
                self.bot_plugin_selected_var.set(
                    "{0} {1}".format(
                        self._ui_text("Could not load settings:"),
                        self._ui_text(selected)))
                self._clear_bot_plugin_settings(
                    "Could not load settings for {0}. Use Refresh and try again.".format(
                        self._ui_text(selected)))
                self.bot_status_var.set(error)
                return
            self.bot_pages["plugins"] = page
            self._render_bot_page("plugins", page)
            self.bot_plugin_selected_var.set(
                "{0} {1}".format(self._ui_text("Settings:"),
                                 self._ui_text(selected)))
            self.bot_status_var.set(
                "{0} {1}.".format(self._ui_text("Loaded settings for"),
                                  self._ui_text(selected)))
            self._bot_log("Loaded settings for plugin {0}.".format(selected))

        self._start_bot_task(lambda: client.fetch_page(path), done)

    def _render_bot_page(self, key, page):
        host = self.bot_page_hosts[key]
        for child in host.winfo_children():
            child.destroy()
        self.bot_form_widgets[key] = {}
        context = {
            "role_state": {},
            "role_field": None,
            "role_text": None,
            "role_var": None,
            "role_active": None,
            "font_state": {},
            "font_field": None,
            "font_controls": {},
        }
        self.bot_form_context[key] = context
        host.columnconfigure(1, weight=1)

        rendered_radio_groups = set()
        for index, item in enumerate(page.fields):
            if item.kind == "hidden":
                if item.name == "font_picker":
                    context["font_field"] = index
                    try:
                        state = json.loads(item.value or "{}")
                        if not isinstance(state, dict):
                            state = {}
                    except (TypeError, ValueError):
                        state = {}
                    context["font_state"] = {
                        "color": str(state.get("color") or "#000000"),
                        "name": str(state.get("name") or "Tahoma"),
                        "size": int(state.get("size") or 12),
                        "bold": bool(state.get("bold", False)),
                        "italic": bool(state.get("italic", False)),
                        "underline": bool(state.get("underline", False)),
                        "strikeout": bool(state.get("strikeout", False)),
                    }
                elif item.identifier == "_1" and item.name in ("_3", "_4"):
                    context["role_field"] = index
                    try:
                        state = json.loads(item.value or "{}")
                        if isinstance(state, dict):
                            context["role_state"] = state
                    except (TypeError, ValueError):
                        context["role_state"] = {}
                continue

            if item.kind == "radio":
                group_key = item.name or "__radio_{0}".format(index)
                if group_key in rendered_radio_groups:
                    continue
                rendered_radio_groups.add(group_key)
                group = [(field_index, candidate)
                         for field_index, candidate in enumerate(page.fields)
                         if candidate.kind == "radio" and
                         (candidate.name or "__radio_{0}".format(field_index))
                         == group_key]
                self._render_bot_radio_group(key, host, index, group)
                continue

            if item.tag == "select" and item.identifier in (
                    "fnamefont_picker", "fsizefont_picker"):
                continue
            if item.tag == "select" and item.identifier == "rolesel":
                self._render_bot_role_select(key, host, index, item, context)
                continue
            if item.tag == "textarea" and item.identifier == "rolegreet":
                self._render_bot_role_text(key, host, index, item, context)
                continue
            self._render_bot_field(key, host, index, item)

        if context["font_field"] is not None:
            self._render_bot_font_picker(key, host, page, context)
        if not page.fields:
            self.ttk.Label(
                host, text=self._ui_text(
                    "No editable settings were returned.")).grid(
                row=0, column=0, sticky="w")
        self._install_clipboard_shortcuts(host)
        self.bot_save_buttons[key].configure(state="normal")
        self.bot_reload_buttons[key].configure(state="normal")

    def _clear_bot_plugin_settings(self, message):
        host = self.bot_page_hosts.get("plugins")
        if host is not None:
            for child in host.winfo_children():
                child.destroy()
            self.ttk.Label(
                host, text=self._ui_text(message), style="Hint.TLabel").grid(
                row=0, column=0, sticky="w")
        self.bot_pages.pop("plugins", None)
        self.bot_save_buttons["plugins"].configure(state="disabled")
        self.bot_reload_buttons["plugins"].configure(state="disabled")

    def _bot_label(self, item):
        if item.kind == "file":
            return self._ui_text("Quiz file")
        label = (item.label or item.name or item.identifier or
                 item.kind or "Setting").strip().rstrip(":：")
        return self._ui_text(self.BOT_LABELS.get(label, label))

    def _render_bot_field(self, key, host, index, item):
        row = self.ttk.Frame(host)
        row.grid(row=index, column=0, columnspan=2, sticky="ew", pady=3)
        row.columnconfigure(1, weight=1)
        label = self._bot_label(item)
        if item.kind == "checkbox":
            variable = self.tk.BooleanVar(value=item.checked)
            widget = self.ttk.Checkbutton(row, text=label, variable=variable)
            widget.grid(row=0, column=0, columnspan=2, sticky="w")
            self.bot_form_widgets[key][index] = {
                "kind": "checkbox", "variable": variable}
            return
        self.ttk.Label(row, text=label, wraplength=230).grid(
            row=0, column=0, sticky="nw", padx=(0, 10))
        if item.tag == "textarea":
            widget = self.tk.Text(row, height=5, width=65, wrap="word")
            widget.insert("1.0", item.value)
            widget.grid(row=0, column=1, sticky="nsew")
            self.bot_form_widgets[key][index] = {
                "kind": "textarea", "widget": widget}
        elif item.tag == "select":
            options = item.options
            if not options:
                self.ttk.Label(row, text=self._ui_text(
                    "No options available from Camfrog.")).grid(
                    row=0, column=1, sticky="w")
                return
            if "multiple" in item.attributes:
                widget = self.tk.Listbox(
                    row, height=min(max(len(options), 2), 7),
                    selectmode="extended", exportselection=False)
                for option_index, option in enumerate(options):
                    widget.insert("end", self._bot_option_label(option))
                    if option.selected:
                        widget.selection_set(option_index)
                widget.grid(row=0, column=1, sticky="ew")
                self.bot_form_widgets[key][index] = {
                    "kind": "multiselect", "widget": widget,
                    "options": options}
                return
            labels = [self._bot_option_label(option) for option in options]
            selected_option = next(
                (option for option in options if option.selected), options[0])
            variable = self.tk.StringVar(
                value=self._bot_option_label(selected_option))
            widget = self.ttk.Combobox(
                row, textvariable=variable, values=labels, state="readonly")
            widget.grid(row=0, column=1, sticky="ew")
            control = {
                "kind": "select", "variable": variable, "options": options,
                "widget": widget, "selected_value": selected_option.value}
            self.bot_form_widgets[key][index] = control
            self._bind_bot_option_selection(control)
        elif item.kind == "file":
            variable = self.tk.StringVar(value="")
            widget = self.ttk.Label(
                row, text=self._ui_text("No file selected"))
            def choose_file(var=variable, target=widget):
                from tkinter import filedialog
                selected = filedialog.askopenfilename(parent=self.root)
                if selected:
                    var.set(selected)
                    target.configure(text=Path(selected).name)
            self.ttk.Button(row, text=self._ui_text("Choose file"),
                            command=choose_file).grid(
                row=0, column=1, sticky="w")
            self.bot_form_widgets[key][index] = {
                "kind": "file", "variable": variable}
        else:
            variable = self.tk.StringVar(value=item.value)
            show = "*" if item.kind == "password" else ""
            widget = self.ttk.Entry(row, textvariable=variable, show=show)
            widget.grid(row=0, column=1, sticky="ew")
            self.bot_form_widgets[key][index] = {
                "kind": item.kind or "text", "variable": variable}

    def _render_bot_radio_group(self, key, host, index, group):
        if not group:
            return
        first = group[0][1]
        row = self.ttk.Frame(host)
        row.grid(row=index, column=0, columnspan=2, sticky="ew", pady=3)
        row.columnconfigure(1, weight=1)
        group_label = (first.attributes.get("aria-label") or first.name or
                       first.label or "Options")
        self.ttk.Label(row, text=self._ui_text(group_label),
                       wraplength=230).grid(
            row=0, column=0, sticky="nw", padx=(0, 10))
        selected = next((item.value for _field_index, item in group
                         if item.checked), "")
        variable = self.tk.StringVar(value=selected)
        options_frame = self.ttk.Frame(row)
        options_frame.grid(row=0, column=1, sticky="w")
        for option_index, (field_index, item) in enumerate(group):
            label = (item.label or item.value or item.name).strip()
            label = self._ui_text(self.BOT_LABELS.get(label, label))
            self.ttk.Radiobutton(
                options_frame, text=label, value=item.value,
                variable=variable).grid(
                    row=option_index, column=0, sticky="w")
            self.bot_form_widgets[key][field_index] = {
                "kind": "radio", "variable": variable,
                "option_value": item.value}

    def _render_bot_role_select(self, key, host, index, item, context):
        options = list(item.options)
        role_state = context["role_state"]
        if not options and role_state:
            options = [BotManagerOption(str(value), str(value))
                       for value in role_state]
        row = self.ttk.Frame(host)
        row.grid(row=index, column=0, columnspan=2, sticky="ew", pady=3)
        row.columnconfigure(1, weight=1)
        self.ttk.Label(row, text=self._bot_label(item)).grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        if not options:
            self.ttk.Label(row, text=self._ui_text(
                "No roles returned from Camfrog.")).grid(
                row=0, column=1, sticky="w")
            return
        labels = [self._bot_option_label(option) for option in options]
        selected_option = next(
            (option for option in options if option.selected), options[0])
        variable = self.tk.StringVar(
            value=self._bot_option_label(selected_option))
        widget = self.ttk.Combobox(
            row, textvariable=variable, values=labels, state="readonly")
        widget.grid(row=0, column=1, sticky="ew")
        context["role_field_options"] = options
        context["role_var"] = variable
        context["role_text"] = None
        control = {
            "kind": "role_select", "variable": variable, "options": options,
            "widget": widget, "selected_value": selected_option.value}
        self.bot_form_widgets[key][index] = control
        context["role_control"] = control
        self._bind_bot_option_selection(control)
        widget.bind(
            "<<ComboboxSelected>>",
            lambda _event, role_context=context:
            self._sync_bot_role_editor(role_context), add="+")

    def _render_bot_role_text(self, key, host, index, item, context):
        row = self.ttk.Frame(host)
        row.grid(row=index, column=0, columnspan=2, sticky="ew", pady=3)
        row.columnconfigure(1, weight=1)
        self.ttk.Label(row, text=self._bot_label(item)).grid(
            row=0, column=0, sticky="nw", padx=(0, 10))
        widget = self.tk.Text(row, height=5, width=65, wrap="word")
        selected_role = self._selected_bot_role(context)
        widget.insert("1.0", str(context["role_state"].get(selected_role, "")))
        widget.grid(row=0, column=1, sticky="nsew")
        context["role_text"] = widget
        context["role_active"] = (
            str(selected_role) if selected_role is not None else None)
        self.bot_form_widgets[key][index] = {
            "kind": "role_text", "widget": widget}

    def _selected_bot_role(self, context):
        variable = context.get("role_var")
        options = context.get("role_field_options", [])
        if variable is None:
            return None
        label = variable.get()
        return next((option.value for option in options
                     if label in (option.label,
                                  self._bot_option_label(option))), label)

    def _sync_bot_role_editor(self, context):
        """Save the current role text and load the newly selected greeting."""
        widget = context.get("role_text")
        if widget is None:
            return
        previous_role = context.get("role_active")
        if previous_role is not None:
            context["role_state"][str(previous_role)] = widget.get(
                "1.0", "end-1c")
        selected_role = self._selected_bot_role(context)
        if selected_role is None:
            return
        selected_role = str(selected_role)
        if selected_role != previous_role:
            widget.delete("1.0", "end")
            widget.insert(
                "1.0", str(context["role_state"].get(selected_role, "")))
            context["role_active"] = selected_role

    def _render_bot_font_picker(self, key, host, page, context):
        row = self.ttk.LabelFrame(
            host, text=self._ui_text("Message formatting"), padding=6)
        row.grid(row=len(page.fields) + 1, column=0, columnspan=2,
                 sticky="ew", pady=6)
        row.columnconfigure(1, weight=1)
        state = context["font_state"]
        name_options = next((field.options for field in page.fields
                             if field.identifier == "fnamefont_picker"), [])
        size_options = next((field.options for field in page.fields
                             if field.identifier == "fsizefont_picker"), [])
        font_names = [option.value for option in name_options]
        if not font_names:
            font_names = [state["name"]]
        sizes = [option.value for option in size_options] or [
            str(state["size"])]
        current_name = state["name"] if state["name"] in font_names else font_names[0]
        current_size = str(state["size"])
        if current_size not in sizes:
            sizes.append(current_size)

        self.ttk.Label(row, text=self._ui_text("Font")).grid(
            row=0, column=0, sticky="w")
        font_var = self.tk.StringVar(value=current_name)
        self.ttk.Combobox(row, textvariable=font_var, values=font_names,
                          state="readonly", width=26).grid(
            row=0, column=1, sticky="ew", padx=4, pady=2)
        self.ttk.Label(row, text=self._ui_text("Size")).grid(
            row=0, column=2, sticky="w")
        size_var = self.tk.StringVar(value=current_size)
        self.ttk.Combobox(row, textvariable=size_var, values=sizes,
                          state="readonly", width=5).grid(
            row=0, column=3, padx=4, pady=2)
        color_var = self.tk.StringVar(value=state["color"])
        self.ttk.Label(row, text=self._ui_text("Color")).grid(
            row=0, column=4, sticky="w")
        def choose_color():
            from tkinter import colorchooser
            chosen = colorchooser.askcolor(
                initialcolor=color_var.get(), parent=self.root)[1]
            if chosen:
                color_var.set(chosen)
        self.ttk.Button(row, text=self._ui_text("Choose"),
                        command=choose_color).grid(
            row=0, column=5, padx=4, pady=2)
        color_preview_label = self.ttk.Label(row, textvariable=color_var, width=10)
        color_preview_label.grid(row=0, column=6, sticky="w")

        color_loop_var = self.tk.BooleanVar(value=False)
        colors_palette = ["#FF0000", "#FF7F00", "#FFFF00", "#00FF00", "#0000FF", "#4B0082", "#9400D3", "#000000"]
        context["_color_loop_idx"] = 0
        context["_color_loop_job"] = None

        def step_color_loop():
            if not color_loop_var.get():
                return
            idx = context.get("_color_loop_idx", 0)
            next_color = colors_palette[idx % len(colors_palette)]
            context["_color_loop_idx"] = idx + 1
            color_var.set(next_color)
            context["_color_loop_job"] = self.root.after(3000, step_color_loop)

        def toggle_color_loop():
            if color_loop_var.get():
                step_color_loop()
            else:
                job = context.get("_color_loop_job")
                if job:
                    try:
                        self.root.after_cancel(job)
                    except Exception:
                        pass
                    context["_color_loop_job"] = None

        self.ttk.Checkbutton(
            row, text=self._ui_text("Auto Loop Color"),
            variable=color_loop_var, command=toggle_color_loop).grid(
            row=0, column=7, padx=(6, 2), pady=2, sticky="w")
        toggles = {}
        for column, key_name, label in (
                (0, "bold", "Bold"), (1, "italic", "Italic"),
                (2, "underline", "Underline"), (3, "strikeout", "Strikeout")):
            variable = self.tk.BooleanVar(value=bool(state[key_name]))
            self.ttk.Checkbutton(
                row, text=self._ui_text(label), variable=variable).grid(
                row=1, column=column, sticky="w", padx=2, pady=(4, 0))
            toggles[key_name] = variable
        context["font_controls"] = {
            "name": font_var, "size": size_var, "color": color_var,
            "toggles": toggles}

    def _collect_bot_form(self, key):
        page = self.bot_pages[key]
        controls = self.bot_form_widgets.get(key, {})
        values = {}
        for index, item in enumerate(page.fields):
            control = controls.get(index)
            if control is None:
                continue
            kind = control["kind"]
            if kind == "checkbox":
                values[index] = bool(control["variable"].get())
            elif kind in ("textarea", "role_text"):
                values[index] = control["widget"].get("1.0", "end-1c")
            elif kind == "radio":
                values[index] = control["variable"].get()
            elif kind == "multiselect":
                values[index] = [control["options"][int(option_index)].value
                                 for option_index in
                                 control["widget"].curselection()]
            elif kind == "file":
                values[index] = control["variable"].get()
            elif kind in ("select", "role_select"):
                selected = control["variable"].get()
                option = next((opt for opt in control["options"]
                               if opt.value == control.get("selected_value") and
                               self._bot_option_label(opt) == selected), None)
                if option is None:
                    option = next((opt for opt in control["options"]
                                   if self._bot_option_label(opt) == selected), None)
                if option is not None and item.name:
                    control["selected_value"] = option.value
                    values[index] = option.value
                elif control.get("selected_value") is not None and item.name:
                    values[index] = control["selected_value"]
            else:
                values[index] = control["variable"].get()

        context = self.bot_form_context.get(key, {})
        self._sync_bot_role_editor(context)
        role_state = dict(context.get("role_state", {}))
        role = self._selected_bot_role(context)
        role_text = context.get("role_text")
        if role is not None and role_text is not None:
            role_state[str(role)] = role_text.get("1.0", "end-1c")
        font_state = dict(context.get("font_state", {}))
        font_controls = context.get("font_controls", {})
        if font_controls:
            font_state["name"] = font_controls["name"].get()
            try:
                font_state["size"] = int(font_controls["size"].get())
            except (ValueError, TypeError):
                font_state["size"] = 12
            font_state["color"] = font_controls["color"].get()
            for name, variable in font_controls["toggles"].items():
                font_state[name] = bool(variable.get())
        return values, role_state, font_state

    def save_bot_page(self, key):
        if self.bot_client is None or key not in self.bot_pages:
            self.bot_status_var.set("Connect to Bot Manager first.")
            return
        from tkinter import messagebox
        label = "selected plugin" if key == "plugins" else key
        if not messagebox.askyesno(
                "Save Camfrog settings",
                "Send the current {0} settings to Camfrog?".format(label),
                parent=self.root):
            return
        page = self.bot_pages[key]
        values, role_state, font_state = self._collect_bot_form(key)
        client = self.bot_client
        generation = self.bot_generation
        self.bot_status_var.set("Saving {0} settings...".format(label))

        def done(updated_page, error):
            if generation != self.bot_generation:
                return
            if error:
                self.bot_status_var.set(error)
                self._bot_log("Save failed for {0} settings.".format(label))
                return
            self.bot_pages[key] = updated_page
            self._render_bot_page(key, updated_page)
            self.bot_status_var.set(
                "Camfrog returned the {0} form. Review the displayed values.".format(label))
            self._bot_log("Submitted {0} settings to Camfrog.".format(label))

        self._start_bot_task(
            lambda: client.save_page(page, values, role_state, font_state), done)

    def stop_bot(self):
        if self.bot_client is None:
            self.bot_status_var.set("Connect to Bot Manager first.")
            return
        from tkinter import messagebox
        if not messagebox.askyesno(
                "Stop Camfrog bot",
                "Send the Stop Bot command to Camfrog now?",
                parent=self.root):
            return
        client = self.bot_client
        generation = self.bot_generation
        self.bot_status_var.set("Sending Stop Bot command...")

        def work():
            client._request("/bot/stop")
            return client.fetch_page("/bot/room")

        def done(page, error):
            if generation != self.bot_generation:
                return
            if error:
                self.bot_status_var.set(error)
                return
            self.bot_pages["room"] = page
            self._render_bot_page("room", page)
            self.bot_status_var.set(
                "Stop request sent. Check the room settings page for bot state.")
            self._bot_log("Sent Stop Bot command.")

        self._start_bot_task(work, done)

    def logout_bot(self):
        if self.bot_client is None:
            self.disconnect_bot()
            return
        from tkinter import messagebox
        if not messagebox.askyesno(
                "Log out of Camfrog Bot Manager",
                "End this Camfrog Bot Manager session?",
                parent=self.root):
            return
        client = self.bot_client
        generation = self.bot_generation
        self.bot_status_var.set("Logging out...")

        def work():
            client._request("/bot/logout", allow_signon=True)
            return True

        def done(_result, error):
            if generation != self.bot_generation:
                return
            if error:
                self.bot_status_var.set(error)
                return
            self.disconnect_bot()
            self.bot_status_var.set("Logged out of Camfrog Bot Manager.")

        self._start_bot_task(work, done)

    def disconnect_bot(self):
        self.bot_generation += 1
        if self.bot_client is not None:
            self.bot_client.clear()
        self.bot_client = None
        self.bot_pages = {}
        self.bot_plugin_catalog = []
        self.bot_plugin_index_page = None
        self.bot_selected_plugin_id = None
        self.bot_status_var.set("Disconnected. Cloudbot session cleared from memory.")
        self.bot_connect_button.configure(state="normal")
        self.bot_disconnect_button.configure(state="disabled")
        self.bot_stop_button.configure(state="disabled")
        self.bot_logout_button.configure(state="disabled")
        self.bot_plugin_filter_var.set("")
        self.bot_plugin_selected_var.set("Select a plugin card to open its settings.")
        self.bot_plugin_refresh_button.configure(state="disabled")
        self._render_plugin_activation([])
        self.bot_plugin_activation_button.configure(state="disabled")
        for key in ("room", "profile", "plugins"):
            self.bot_section_buttons[key].configure(state="disabled")
            self.bot_save_buttons[key].configure(state="disabled")
            self.bot_reload_buttons[key].configure(state="disabled")
            for child in self.bot_page_hosts[key].winfo_children():
                child.destroy()
            self.bot_form_widgets[key] = {}
            self.bot_form_context[key] = {}
        self.bot_section_buttons["music_dj"].configure(state="normal")
        self._show_bot_disconnected()
        self._set_bot_request_busy(self.bot_busy)
        self._bot_log("Disconnected and cleared the local session.")

    def _bot_log(self, message):
        widget = getattr(self, "bot_log_text", None)
        if widget is None:
            return
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        widget.configure(state="normal")
        widget.insert(self.tk.END, "[{0}] {1}\n".format(timestamp, message))
        widget.see(self.tk.END)
        widget.configure(state="disabled")

    def _close_window(self):
        if self.bot_client is not None:
            self.bot_client.clear()
            self.bot_client = None
        if self._tray_icon is not None:
            self._tray_icon.shutdown()
            self._tray_icon = None
        self.root.destroy()

    def _set_note(self, message):
        self.note.set(message)

    def _run_bg(self, fn, force=False, on_done=None, on_error=None):
        if self.busy and not force:
            return
        self.busy = True
        self._set_note("Working...")
        worker = threading.Thread(
            target=self._worker, args=(fn, on_done, on_error), daemon=True)
        try:
            worker.start()
        except RuntimeError:
            self.busy = False
            self._set_note("Could not start the background task.")
            return False
        return True

    def _worker(self, fn, on_done, on_error):
        try:
            text, ok = fn(), True
        except Exception as exc:  # shown in the GUI, never a traceback window
            text, ok = "failed: {0}: {1}".format(type(exc).__name__, exc), False
        self.q.put((ok, text, on_done, on_error))

    def _queue_ui_call(self, callback):
        """Queue a Tk operation for the main thread instead of calling Tk from a worker."""
        if not getattr(self, "_closed", False):
            self.q.put((_GUI_CALLBACK_MARKER, callback))

    def _queue_ui_status(self, text):
        self._queue_ui_call(
            lambda text=str(text): self._set_note(text))

    def _show_log_lines(self, lines):
        if not hasattr(self, "log_view"):
            return
        self._log_source_lines = list(lines[-WEB_STATUS_LOG_GUI_LINES:])
        display_lines = [self._translate_log_line(line)
                         for line in self._log_source_lines]
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.insert("end", "\n".join(display_lines))
        if display_lines:
            self.log_view.insert("end", "\n")
        self.log_view.configure(state="disabled")
        self.log_view.see("end")

    def _append_log_line(self, entry):
        if not hasattr(self, "log_view"):
            return
        self._show_log_lines(
            getattr(self, "_log_source_lines", []) + [entry])

    def _translate_log_line(self, line):
        parts = line.split(" | ", 2)
        if len(parts) < 2:
            return line
        if getattr(self, "_language", "en") == "th":
            parts[1] = WEB_STATUS_LOG_LABELS_TH.get(parts[1], parts[1])
        else:
            parts[1] = WEB_STATUS_LOG_LABELS_EN.get(parts[1], parts[1])
        return " | ".join(parts)

    def _record_activity(self, event, detail=None, secrets=()):
        try:
            entry = append_web_status_log(
                event, detail=detail, secrets=secrets,
                language=getattr(self, "_language", "en"))
        except (OSError, ValueError) as exc:
            try:
                entry = _web_status_log_entry(
                    event, detail=detail, secrets=secrets,
                    language=getattr(self, "_language", "en"))
            except ValueError:
                return
            if hasattr(self, "note"):
                self._set_note("Could not write web_status.log ({0}).".format(
                    type(exc).__name__))
        self._append_log_line(entry)

    def _rotation_settings_detail(self, interval, timeout, live, pool,
                                  marquee=False, frame_seconds=None):
        delay_source = "marquee_frame" if marquee else "switch_every"
        delay = frame_seconds if marquee and frame_seconds is not None else interval
        effective_delay = web_status_rotation_delay(
            interval, live, marquee=marquee,
            frame_seconds=delay if marquee else WEB_MARQUEE_STEP_SECONDS_DEFAULT)
        loop_count = getattr(self, "_cycle_loop_count", 0)
        loop_label = "{0} (infinite)".format(loop_count) if loop_count == 0 \
            else str(loop_count)
        return (
            "mode={0}; delay_source={1}; configured_delay_s={2}; "
            "effective_delay_s={3}; loop_rounds={4}; timeout_s={5}; "
            "populated_slots={6}"
        ).format(
            "live" if live else "dry-run",
            delay_source,
            delay,
            effective_delay,
            loop_label,
            timeout,
            len(pool),
        )

    def _poll(self):
        if getattr(self, "_closed", False):
            return
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] is _GUI_CALLBACK_MARKER:
                    try:
                        item[1]()
                    except Exception as exc:
                        self._set_note("UI update failed ({0}).".format(
                            type(exc).__name__))
                    continue
                ok, text, on_done, on_error = item
                self.busy = False
                if text:
                    self._set_note(text)
                callback = on_done if ok else on_error
                if callback is not None:
                    try:
                        callback()
                    except Exception as exc:
                        self._set_note("{0} ({1})".format(text, exc))
        except queue.Empty:
            pass
        if getattr(self, "_closed", False):
            return
        try:
            while True:
                result, error, callback = self.bot_q.get_nowait()
                self.bot_busy = False
                try:
                    callback(result, error)
                except Exception as exc:
                    self.bot_status_var.set(
                        "Could not update the Bot Manager view ({0}).".format(
                            type(exc).__name__))
                self._set_bot_request_busy(False)
        except queue.Empty:
            pass
        try:
            self.root.after(120, self._poll)
        except Exception:
            pass

    def close(self):
        """Cancel queued work and prevent any new POST when the window closes."""
        if getattr(self, "_closed", False):
            return
        self.stop()
        self._closed = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def _login_name(self):
        login = self.login.get().strip()
        if not login:
            raise ValueError("Enter a nickname first.")
        return login

    def _pool(self):
        pool = self._pool_values()
        if not pool:
            raise ValueError("Enter at least one status line.")
        return pool

    def _pool_values(self):
        return [value for variable in self.pool_vars
                if (value := variable.get().strip())]

    def _save_web_texts(self, notify=True):
        try:
            save_web_text_lines([variable.get() for variable in self.pool_vars])
        except (OSError, sqlite3.Error) as exc:
            self._set_note("Could not save status lines to webtext.db: {0}".format(
                type(exc).__name__))
            return False
        self._record_activity("status_lines_saved")
        if notify:
            self._set_note("Saved status lines to webtext.db.")
        return True

    def save_web_texts(self):
        self._save_web_texts()

    def _fields(self):
        return self._login_name(), self._pool()

    # ---- actions (network runs in worker threads; secrets never leave memory)
    def open_login_page(self):
        import webbrowser
        webbrowser.open(LOGIN_PAGE_URL)
        self._set_paste_state(
            "Login page opened. After signing in, capture PHPSESSID on "
            "https://profiles.camfrog.com/home.php, paste it, then press Login.")

    def use_pasted_cookie(self):
        try:
            self._sync_pasted_cookie()
        except ValueError as exc:
            self._set_paste_state(
                str(exc), foreground=WEB_STATUS_THEME["error"])
            return
        self._set_paste_state(
            "PHPSESSID ready in memory. Click Login to verify.")

    def _sync_pasted_cookie(self):
        """Validate the current entry and use it instead of any stale cached paste."""
        text = self.cookie_paste_var.get().strip()
        if not text:
            if self.cookie_paste and self._cookie_from_browser:
                return self.cookie_paste
            if self.cookie_paste or self.session_ready:
                self.cookie_paste = ""
                self._cookie_from_browser = False
                self.session_ready = False
                self._invalidate_pending_start()
            raise ValueError("Enter or paste PHPSESSID first (Session tab).")
        changed = text != self.cookie_paste
        if changed:
            self.session_ready = False
            self._invalidate_pending_start()
        self._cookie_from_browser = False
        try:
            cookie_from_paste(text)
        except ValueError:
            if changed:
                self.cookie_paste = ""
                self._cookie_from_browser = False
            raise
        self.cookie_paste = text
        self._cookie_from_browser = False
        return text

    def _session_jar(self):
        """The cookie jar for probe/rotation: single flow using pasted PHPSESSID or cloudbot."""
        if not self.cookie_paste:
            raise ValueError("Enter or paste PHPSESSID or cloudbot first (Session tab).")
        return cookie_from_paste(self.cookie_paste)

    def login_via_cloudbot(self):
        """Connect via cloudbot parameter (UI action)."""
        if self.busy:
            self._set_note("Wait for the current request to finish before starting.")
            return
        if self.switching:
            self._set_note("Stop the live rotation before starting another network task.")
            return

        cloudbot_param = self.cloudbot_param_var.get().strip()
        login = self.login.get().strip()
        timeout = float(self.timeout.get().strip()) if self.timeout.get().strip() else TIMEOUT_DEFAULT

        try:
            if not cloudbot_param:
                raise ValueError("Enter cloudbot parameter first (Session tab).")
            if not login:
                raise ValueError("Enter nickname first (Setup tab).")

            # Parse cloudbot parameter (format: cloudbot=<token>)
            if not cloudbot_param.startswith("cloudbot="):
                raise ValueError("Invalid cloudbot parameter format. Use: cloudbot=<token>")

            cloudbot_token = cloudbot_param.split("=", 1)[1]
            if not cloudbot_token:
                raise ValueError("Empty cloudbot token.")

            # Use the _connect_via_cloudbot method
            self._connect_via_cloudbot(login, cloudbot_param, timeout,
                                      on_done=lambda: setattr(self, "session_ready", True))
        except ValueError as exc:
            self._set_note("Cloudbot connection failed: {0}".format(exc))

    def _connect_via_cloudbot(self, login, cloudbot_param, timeout, on_done):
        """Connect via cloudbot parameter for Camfrogbot control."""
        if not cloudbot_param.strip():
            raise ValueError("Enter cloudbot parameter first (Session tab).")
        if not login.strip():
            raise ValueError("Enter nickname first (Setup tab).")

        def do():
            import urllib.request

            # Parse cloudbot parameter (format: cloudbot=<token>)
            if not cloudbot_param.startswith("cloudbot="):
                raise ValueError("Invalid cloudbot parameter format. Use: cloudbot=<token>")

            cloudbot_token = cloudbot_param.split("=", 1)[1]
            if not cloudbot_token:
                raise ValueError("Empty cloudbot token.")

            # Create a cookie jar with the cloudbot token
            jar = CookieJar()
            jar.set_cookie(Cookie(
                version=0, name="cloudbot", value=cloudbot_token,
                port=None, port_specified=False,
                domain=".camfrog.com", domain_specified=True,
                domain_initial_dot=True, path="/", path_specified=True,
                secure=True, expires=None, discard=False,
                comment=None, comment_url=None, rest={}, rfc2109=False))

            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar))

            # Try to fetch the profile page with cloudbot token
            try:
                html = fetch_profile(opener, login, timeout)
                verdict, detail = summarize_session(html)

                if verdict != "signed-in":
                    raise ValueError(f"Cloudbot connection failed: {detail}")

                return f"Cloudbot connection successful. {detail}"
            except Exception as exc:
                diagnostic = _web_status_request_error(
                    "cloudbot connection", exc,
                    secrets=(login, cloudbot_token))
                raise RuntimeError(diagnostic) from exc

        self._record_activity("browser_login_started")

        def success():
            self.session_ready = True
            self._set_note("Connected via cloudbot. Automation armed.")

        def error(exc):
            self._set_note("Cloudbot connection failed: {0}".format(exc))

        self._run_bg(do, on_done=success, on_error=error)

    def _verify_session(self, login, timeout, on_done):
        """Probe PHPSESSID session in a worker; on_done runs on success."""
        if self.busy:
            self._set_note("Wait for the current request to finish before starting.")
            return
        if self.switching:
            self._set_note("Stop the live rotation before starting another network task.")
            return
        failure_detail = []
        cookie_secrets = _session_cookie_redaction_values(
            getattr(self, "cookie_paste", ""))

        def do():
            import urllib.request

            try:
                jar = self._session_jar()
                opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(jar))
                html = fetch_profile(opener, login, timeout)
                verdict, detail = summarize_session(html)
                if verdict != "signed-in":
                    raise ValueError(detail)
                return detail
            except Exception as exc:
                diagnostic = _web_status_request_error(
                    "session verification GET /home.php", exc,
                    secrets=(login,) + cookie_secrets)
                failure_detail[:] = [diagnostic]
                raise RuntimeError(diagnostic) from exc

        self._record_activity("session_verification_started")

        def verified():
            self._record_activity("session_verification_succeeded")
            on_done()

        self._run_bg(
            do, on_done=verified,
            on_error=lambda: self._record_activity(
                "session_verification_failed",
                detail=failure_detail[0] if failure_detail else
                "session verification worker failed without diagnostic details",
                secrets=cookie_secrets))

    def login_via_browser(self):
        """One-click login: the real browser solves the CAPTCHA and the
        HttpOnly session cookie comes back through DevTools."""
        if self.busy:
            self._set_note("Wait for the current request to finish before starting.")
            return
        if self.switching:
            self._set_note("Stop the live rotation before starting another network task.")
            return
        if find_browser() is None:
            self._set_note("No Chrome or Edge found on this computer.")
            return
        self._record_activity("browser_login_started")
        self._set_note("Browser opened - sign in there; this window confirms by itself.")

        captured = []

        def work():
            cookies, _profile = browser_login(
                on_status=self._queue_ui_status)
            captured.append(cookies)
            return "done"

        self._run_bg(
            work,
            on_done=lambda: self._browser_login_done(captured[0]) if captured else None)

    def _browser_login_done(self, cookies):
        try:
            cookie_from_paste(cookies)  # validate before adopting
        except ValueError as exc:
            self._set_note(str(exc))
            return
        self.cookie_paste = cookies
        self._cookie_from_browser = True
        self.cookie_paste_var.set("")
        self.session_ready = False
        self._invalidate_pending_start()
        self._record_activity("browser_session_captured")
        self._set_paste_state("Session captured from browser.")
        self._verify_session(self._login_name(),
                             self._timeout_value() or 20.0, self._on_logged_in)

    def login_session(self):
        """Verify the captured session live, arm automation on success."""
        if getattr(self, "busy", False):
            self._set_note("Wait for the current request to finish before starting.")
            return
        if getattr(self, "switching", False):
            self._set_note("Stop the live rotation before starting another network task.")
            return
        try:
            login = self._login_name()
            self._sync_pasted_cookie()
        except ValueError as exc:
            self._set_note(str(exc))
            return
        timeout = self._timeout_value()
        if timeout is None:
            return
        self.session_ready = False
        self._set_paste_state("Verifying PHPSESSID...")
        self._verify_session(login, timeout, self._on_logged_in)

    def _on_logged_in(self):
        self.session_ready = True
        self._set_paste_state(
            "Logged in via PHPSESSID - automation armed.",
            foreground=WEB_STATUS_THEME["success"])

    def preview(self):
        try:
            login, pool = self._fields()
        except ValueError as exc:
            self._set_note(str(exc))
            return
        live = self.live.get()
        self._set_note(build_plan(login, pool[0])
                      + (" LIVE ARMED (still gated)." if live else ""))

    def check_update(self):
        def do():
            state, message = self_update("web-status.exe")
            return "{0}\n{1}".format(
                message, "restart the app to run the new version."
                if state == "ready" else "")
        self._run_bg(do)

    def _timeout_value(self):
        try:
            return parse_timeout(self.timeout.get())
        except ValueError as exc:
            self._set_note(str(exc))
            return None

    def probe(self):
        try:
            login = self._login_name()
            self._sync_pasted_cookie()
        except ValueError as exc:
            self._set_note(str(exc))
            return
        timeout = self._timeout_value()
        if timeout is None:
            return

        def do():
            import urllib.request

            jar = self._session_jar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            html = fetch_profile(opener, login, timeout)
            _verdict, detail = summarize_session(html)
            return "loaded session: {0}. {1}".format(cookie_summary(jar), detail)

        self._run_bg(do)

    def capture(self):
        self._set_note("", key="capture_instructions")

    # ---- one-click auto-switch rotation (same fail-closed gates as one-shot ops)
    def start(self):
        if self.switching:
            return
        if getattr(self, "busy", False):
            self._set_note("Wait for the current request to finish before starting.")
            return
        try:
            login, pool = self._fields()
            interval = parse_rotate_interval(self.interval.get())
            timeout = parse_timeout(self.timeout.get())
            loop_setting = getattr(self, "loop_count", None)
            if loop_setting is not None:
                loop_count = parse_loop_count(loop_setting.get())
            else:
                infinity_setting = getattr(self, "infinity_loop", None)
                infinity = (infinity_setting.get()
                            if infinity_setting is not None else True)
                loop_count = 0 if infinity else 1
        except ValueError as exc:
            self._set_note(str(exc))
            return
        self._cycle_marquee = False
        self._cycle_marquee_step_seconds = WEB_MARQUEE_STEP_SECONDS_DEFAULT
        self._cycle_loop_count = loop_count
        self._cycle_rounds_completed = 0
        if not self._save_web_texts(notify=False):
            return
        live = self.live.get()
        if live:
            try:
                self._sync_pasted_cookie()
            except ValueError as exc:
                self._set_note(str(exc))
                return
        if live and not getattr(self, "session_ready", False):
            # Single login: verify the session first, then chain into rotation.
            generation = self._invalidate_pending_start()
            self._pending = (login, pool, interval, timeout, generation)
            self._verify_session(
                login, timeout,
                lambda: self._after_login(generation))
            return
        self._invalidate_pending_start()
        if live and not getattr(self, "_live_confirmed", False):
            from tkinter import messagebox
            ok = messagebox.askyesno(
                self._text("confirm_title"), self._text("confirm_start"),
                parent=self.root)
            if not ok:
                self._set_note("Live start cancelled. Nothing was sent.")
                return
            self._live_confirmed = True
        self.switching = True
        event = "live_rotation_started" if live else "dry_run_rotation_started"
        self._record_activity(
            event,
            detail=self._rotation_settings_detail(
                interval, timeout, live, pool))
        self._cycle(login, pool, interval, timeout, live, 0)

    def _invalidate_pending_start(self):
        """Invalidate queued login callbacks before Stop or another Start."""
        self._start_generation = getattr(self, "_start_generation", 0) + 1
        self._pending = None
        return self._start_generation

    def _after_login(self, generation):
        """Continue a pending live Start after the session verified."""
        pending = getattr(self, "_pending", None)
        if not pending or pending[4] != generation:
            return
        self._pending = None
        self.session_ready = True
        self._set_paste_state(
            "Logged in - automation armed.",
            foreground=WEB_STATUS_THEME["success"])
        login, pool, interval, timeout = pending[:4]
        from tkinter import messagebox
        ok = messagebox.askyesno(
            self._text("confirm_title"), self._text("confirm_after_login"),
            parent=self.root)
        if not ok:
            self._set_note("Live start cancelled. Nothing was sent.")
            return
        self._live_confirmed = True
        self.switching = True
        self._record_activity(
            "live_rotation_started",
            detail=self._rotation_settings_detail(
                interval, timeout, True, pool))
        self._cycle(login, pool, interval, timeout, True, 0)

    def stop(self):
        was_active = self.switching or self._pending is not None
        self._invalidate_pending_start()
        self.switching = False
        self.session_ready = False  # next Start re-verifies the session
        self.chrome_mode = False  # Stop also drops an imported Chrome session
        if self.rotate_job is not None:
            try:
                self.root.after_cancel(self.rotate_job)
            except Exception:
                pass
            self.rotate_job = None
        self._set_note(
            "Stop requested. The current request may finish; no new updates will be scheduled.")
        if was_active:
            self._record_activity("rotation_stopped")

    def _cycle(self, login, pool, interval, timeout, live, index, frame_index=0,
               generation=None, retry_attempt=0):
        if getattr(self, "_closed", False) or not self.switching:
            return
        current_generation = getattr(self, "_start_generation", 0)
        if generation is None:
            generation = current_generation
        if generation != current_generation:
            return
        if getattr(self, "busy", False):
            self.rotate_job = self.root.after(
                250, lambda: self._cycle(
                    login, pool, interval, timeout, live, index, frame_index,
                    generation=generation, retry_attempt=retry_attempt))
            return
        try:
            pool = self._pool_values() or pool
            if not pool:
                raise ValueError("status pool is empty; add at least one line.")
            status = pool[index % len(pool)]
        except ValueError as exc:
            self._set_note(str(exc))
            self.stop()
            return
        marquee_setting = getattr(self, "marquee_mode", None)
        marquee = bool(getattr(
            self, "_cycle_marquee",
            marquee_setting.get() if marquee_setting is not None else False))
        frame_seconds = float(getattr(
            self, "_cycle_marquee_step_seconds", WEB_MARQUEE_STEP_SECONDS_DEFAULT))
        loop_count = getattr(self, "_cycle_loop_count", None)
        if loop_count is None:
            infinity_setting = getattr(self, "infinity_loop", None)
            infinity = bool(getattr(
                self, "_cycle_infinity_loop",
                infinity_setting.get() if infinity_setting is not None else True))
            loop_count = 0 if infinity else 1
        frames = web_status_frames(status, enabled=marquee)
        frame_index = min(frame_index, len(frames) - 1)
        frame = frames[frame_index]
        slot_index = index % len(pool)
        step_delay = web_status_rotation_delay(
            interval, live, marquee=marquee, frame_seconds=frame_seconds)
        cookie_secrets = _session_cookie_redaction_values(
            getattr(self, "cookie_paste", ""))
        diagnostic_secrets = (frame, login) + cookie_secrets
        failure_detail = []
        retry_context = []
        post_started = []
        unknown_outcome = []

        def cancelled():
            return (getattr(self, "_closed", False)
                    or not self.switching
                    or generation != getattr(self, "_start_generation", 0))

        def do():
            if not live:
                if cancelled():
                    return ""
                return "cycle #{0}/{1} (dry-run): {2}".format(
                    index + 1, len(pool), frame)
            if cancelled():
                return ""
            try:
                import urllib.request

                jar = self._session_jar()
                opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(jar))
                ok, message = perform_update(
                    opener, frame, timeout, confirm=True, cancelled=cancelled,
                    on_post_started=lambda: post_started.append(True),
                    expected_login=login)
                if not ok:
                    diagnostic = _sanitize_web_status_detail(
                        message, secrets=diagnostic_secrets)
                    failure_detail[:] = [diagnostic]
                    if message.startswith("update outcome unknown:"):
                        unknown_outcome[:] = [True]
                    retry_policy = _web_status_retry_policy(
                        diagnostic, retry_attempt)
                    if retry_policy:
                        retry_context[:] = [retry_policy]
                        return "Live update retry scheduled."
                    raise RuntimeError(diagnostic)
            except WebStatusUpdateCancelled:
                return ""
            except Exception as exc:
                diagnostic = _web_status_request_error(
                    "live status update", exc, secrets=diagnostic_secrets)
                failure_detail[:] = [diagnostic]
                retry_policy = _web_status_retry_policy(
                    diagnostic, retry_attempt)
                if retry_policy:
                    retry_context[:] = [retry_policy]
                    return "Live update retry scheduled."
                raise RuntimeError(diagnostic) from exc
            return "cycle #{0}/{1}: {2}".format(index + 1, len(pool), message)

        def advance():
            if (not self.switching
                    or generation != getattr(self, "_start_generation", 0)):
                return
            if marquee and frame_index + 1 < len(frames):
                next_index = slot_index
                next_frame = frame_index + 1
            elif slot_index + 1 < len(pool):
                next_index = slot_index + 1
                next_frame = 0
            else:
                rounds_completed = getattr(self, "_cycle_rounds_completed", 0) + 1
                self._cycle_rounds_completed = rounds_completed
                if loop_count and rounds_completed >= loop_count:
                    self.switching = False
                    self.rotate_job = None
                    self.session_ready = False
                    self.chrome_mode = False
                    self._invalidate_pending_start()
                    self._record_activity(
                        "marquee_completed" if marquee else "rotation_completed")
                    self._set_note("{0} completed {1} round(s).".format(
                        "Marquee" if marquee else "Rotation", rounds_completed))
                    return
                next_index = 0
                next_frame = 0
            self.rotate_job = self.root.after(
                max(1, int(round(step_delay * 1000))), self._cycle,
                login, pool, interval, timeout, live, next_index, next_frame,
                generation)

        def failed():
            if live:
                detail = failure_detail[0] if failure_detail else \
                    "live status update worker failed without diagnostic details"
                if retry_attempt:
                    detail = "retry_attempts={0}; {1}".format(
                        retry_attempt, detail)
                detail += "; " + self._rotation_settings_detail(
                    interval, timeout, live, pool,
                    marquee=marquee, frame_seconds=frame_seconds)
                self._record_activity(
                    "status_update_unknown" if unknown_outcome
                    else "status_update_failed",
                    detail=detail,
                    secrets=diagnostic_secrets)
                if unknown_outcome:
                    self._set_note(
                        "Status update outcome is unknown. Check the Camfrog "
                        "profile before sending again.")
            else:
                self._record_activity("rotation_stopped")
            if generation != getattr(self, "_start_generation", 0):
                return
            self.switching = False
            self.rotate_job = None
            self.session_ready = False
            self.chrome_mode = False
            self._invalidate_pending_start()

        def completed():
            if (not self.switching
                    or generation != getattr(self, "_start_generation", 0)):
                if live and post_started:
                    self._record_activity("status_update_succeeded")
                return
            if retry_context:
                if retry_attempt >= LIVE_RETRY_MAX_ATTEMPTS:
                    failed()
                    self._set_note(
                        "Live update failed after {0} retries; see Activity Log."
                        .format(LIVE_RETRY_MAX_ATTEMPTS))
                    return
                reason, retry_delay = retry_context[0]
                retry_detail = failure_detail[0] if failure_detail else \
                    "retryable live update failure without diagnostic details"
                detail = (
                    "reason={0}; attempt={1}/{2}; retry_in_s={3}; cause={4}; {5}"
                ).format(
                    reason,
                    retry_attempt + 1,
                    LIVE_RETRY_MAX_ATTEMPTS,
                    retry_delay,
                    retry_detail,
                    self._rotation_settings_detail(
                        interval, timeout, live, pool,
                        marquee=marquee, frame_seconds=frame_seconds),
                )
                self._record_activity(
                    "live_retry_scheduled",
                    detail=detail,
                    secrets=diagnostic_secrets)
                self._set_note(
                    "Temporary Live error; retrying in {0:g} seconds ({1}/{2})."
                    .format(
                        retry_delay, retry_attempt + 1,
                        LIVE_RETRY_MAX_ATTEMPTS))
                self.rotate_job = self.root.after(
                    max(1, int(round(retry_delay * 1000))),
                    lambda: self._cycle(
                        login, pool, interval, timeout, live, index, frame_index,
                        generation=generation, retry_attempt=retry_attempt + 1))
                return
            self._record_activity("status_update_succeeded" if live
                                  else "status_previewed")
            advance()

        self._run_bg(
            do, force=True, on_done=completed, on_error=failed)


# ============================================================
# Inlined browser login (tools/browser_login.py): real-browser login +
# DevTools cookie harvest. tools/browser_login.py stays the tested copy.
# ============================================================

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


# ---------- self-update from the GitHub release ----------
# Read-only GitHub API (no token needed for public repos, stdlib only). Each
# packaged app checks the same repo's latest release and downloads only its
# own executable asset, verified against the release's SHA256SUMS.txt.
APP_VERSION = "2.19.3"
GITHUB_REPO = os.environ.get("CAMFROG_UPDATE_REPO", "cvsz/zchat")
UPDATE_ASSET_SUMS = "SHA256SUMS.txt"


def _version_key(text):
    parts = [int(p) for p in re.findall(r"\d+", str(text))[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def version_newer(latest, current=APP_VERSION):
    """True when `latest` sorts above `current` (2.19.0 > 2.18.3)."""
    return _version_key(latest) > _version_key(current)


def _validate_github_release_url(url):
    """Accept only HTTPS URLs on GitHub's release and asset hosts."""
    allowed_hosts = {
        "github.com", "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
    try:
        parsed = urllib.parse.urlsplit(str(url))
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid GitHub release URL") from exc
    if (parsed.scheme.casefold() != "https"
            or (parsed.hostname or "").casefold() not in allowed_hosts
            or parsed.username or parsed.password
            or port not in (None, 443)):
        raise ValueError("update URL must use HTTPS on a GitHub release host")
    return str(url)


def _validate_github_response_url(response, requested_url):
    geturl = getattr(response, "geturl", None)
    final_url = geturl() if callable(geturl) else requested_url
    _validate_github_release_url(final_url)


def github_latest_release(timeout=15.0):
    """(tag, notes, {asset_name: url}) for the newest release, or None."""
    request = urllib.request.Request(
        "https://api.github.com/repos/{0}/releases/latest".format(GITHUB_REPO),
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "camfrog-auto/{0}".format(APP_VERSION)})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    tag = str(data.get("tag_name") or "").lstrip("v")
    if not tag:
        return None
    assets = {}
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name and url:
            assets[name] = url
    return tag, str(data.get("body") or ""), assets


def check_for_update(asset_name, timeout=15.0):
    """(tag, url, notes) when a newer release carries this asset, else None."""
    found = check_release_assets(asset_name, timeout)
    return None if found is None else found[:3]


def check_release_assets(asset_name, timeout=15.0):
    """Return verified GitHub release URLs only when the asset has checksums."""
    release = github_latest_release(timeout)
    if release is None:
        return None
    tag, notes, assets = release
    url = assets.get(asset_name)
    if not url or not version_newer(tag):
        return None
    sums_url = assets.get(UPDATE_ASSET_SUMS, "")
    if not sums_url:  # e.g. release-SHA256SUMS.txt alongside SHA256SUMS.txt
        sums_url = next((value for name, value in sorted(assets.items())
                         if name.endswith("SHA256SUMS.txt")), "")
    if not sums_url:
        return None
    try:
        _validate_github_release_url(url)
        _validate_github_release_url(sums_url)
    except ValueError:
        return None
    return tag, url, sums_url, notes


def self_update(asset_name, confirm=None, timeout=15.0):
    """Check -> confirm -> download -> verify -> stage the swap.

    Returns (state, message) with state in
    ("no-update", "declined", "ready", "failed"); "ready" means the new file is
    staged as <asset>.new and camfrog-update.cmd will swap it after we exit.
    """
    found = check_release_assets(asset_name, timeout)
    if found is None:
        return "no-update", "no update available (running v{0}).".format(APP_VERSION)
    tag, url, sums_url, _notes = found
    if confirm is not None and not confirm(tag):
        return "declined", "update to v{0} declined.".format(tag)
    ok, message = stage_and_swap_update(url, asset_name, sums_url or None)
    return ("ready" if ok else "failed"), message


def _sha256_file(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def download_update(url, target, timeout=60.0):
    """Stream a GitHub release asset to `target`. Returns the bytes written."""
    _validate_github_release_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": "camfrog-auto/{0}".format(APP_VERSION)})
    written = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        _validate_github_response_url(response, url)
        with open(target, "wb") as handle:
            while True:
                block = response.read(1 << 16)
                if not block:
                    break
                handle.write(block)
                written += len(block)
    return written


def verify_against_sums(sums_url, asset_name, local_path, timeout=30.0):
    """True when local_path matches the GitHub release's SHA256SUMS entry."""
    _validate_github_release_url(sums_url)
    request = urllib.request.Request(
        sums_url, headers={"User-Agent": "camfrog-auto/{0}".format(APP_VERSION)})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        _validate_github_response_url(response, sums_url)
        text = response.read().decode("utf-8", "replace")
    want = ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name = parts[1].lstrip("*")
        # Release sums may be flat or dist-relative; match either, first hit wins.
        if name == asset_name or name.endswith("/" + asset_name):
            want = parts[0].lower()
            break
    return bool(want) and want == _sha256_file(local_path)


def stage_and_swap_update(url, asset_name, sums_url=None):
    """Download the new exe beside this one, then swap it in after we exit.

    A running .exe cannot be overwritten on Windows, so the new file lands as
    `<asset>.new` and a small `camfrog-update.cmd` moves it into place a few
    seconds after this process exits. Returns (ok, message).
    """
    if not getattr(sys, "frozen", False):
        return False, "self-update only applies to the packaged executable."
    if not sums_url:
        return False, "release checksum is unavailable; update refused."
    if not isinstance(asset_name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,123}\.exe",
            asset_name, flags=re.IGNORECASE):
        return False, "invalid update asset name."
    try:
        _validate_github_release_url(url)
        _validate_github_release_url(sums_url)
    except ValueError as exc:
        return False, str(exc)
    exe = Path(sys.executable).resolve()
    staged = exe.parent / (asset_name + ".new")
    try:
        written = download_update(url, staged)
        if not written:
            raise OSError("empty download")
        if sums_url and not verify_against_sums(sums_url, asset_name, staged):
            staged.unlink(missing_ok=True)
            return False, "checksum mismatch; update refused."
    except Exception as exc:
        staged.unlink(missing_ok=True)
        return False, "download failed: {0}: {1}".format(type(exc).__name__, exc)
    script = exe.parent / "camfrog-update.cmd"
    script.write_text(
        "@echo off\r\n"
        "ping -n 4 127.0.0.1 >nul\r\n"
        'move /Y "{staged}" "{exe}"\r\n'
        'del "%~f0"\r\n'.format(staged=staged, exe=exe), encoding="utf-8")
    command = ["cmd", "/c", str(script)]
    spawn = {"cwd": str(exe.parent)}
    if os.name == "nt":  # CREATE_NO_WINDOW: no console flash during the swap
        spawn["creationflags"] = 0x08000000
    subprocess.Popen(command, close_fds=True, **spawn)
    return True, "update is staged; it will replace {0} after the app exits.".format(asset_name)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

