"""Shared HTTP primitives for Camfrog's web profile status field.

This is an observed, undocumented web flow. Callers must require explicit live
consent and must treat a non-empty server acknowledgement as acknowledgement,
not independent proof that the profile changed.
"""

from __future__ import annotations

import re
import json
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum


HOME_URL = "https://profiles.camfrog.com/home.php"
WEB_UPDATE_URL = "https://profiles.camfrog.com/ajax/update_status.php"
PROFILE_REFERER = "https://www.camfrog.com/th/login.php"
PROFILE_COOKIE_NAME = "PHPSESSID"
MAX_STATUS_LENGTH = 120
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


class ProfileStatusOutcome(str, Enum):
    """Evidence state for one profile write; acknowledgement is not read-back."""

    REFUSED = "REFUSED"
    REJECTED = "REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    ACKNOWLEDGED_UNVERIFIED = "ACKNOWLEDGED_UNVERIFIED"
    EFFECT_UNKNOWN = "EFFECT_UNKNOWN"


@dataclass(frozen=True)
class ProfileStatusResult:
    outcome: ProfileStatusOutcome
    error_code: str | None = None
    http_status: int | None = None
    retry_after: str | None = None


def parse_profile_cookie(raw: str) -> tuple[str, str]:
    """Accept exactly one PHPSESSID pair; never place it in diagnostics."""
    value = str(raw or "").strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    if ";" in value or "\r" in value or "\n" in value:
        raise ValueError("profile session must contain exactly one cookie")
    name, sep, cookie_value = value.partition("=")
    if not sep or name.strip() != PROFILE_COOKIE_NAME or not cookie_value.strip():
        raise ValueError("profile session must contain one PHPSESSID cookie")
    cookie_value = cookie_value.strip()
    if any(char.isspace() for char in cookie_value):
        raise ValueError("profile session cookie is malformed")
    return PROFILE_COOKIE_NAME, cookie_value


def build_profile_opener(raw_cookie: str):
    """Build a session opener scoped only to profiles.camfrog.com."""
    name, value = parse_profile_cookie(raw_cookie)
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(
        http.cookiejar.Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain="profiles.camfrog.com",
            domain_specified=False,
            domain_initial_dot=False,
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
    )
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def submit_profile_status(
    opener,
    status: str,
    expected_nickname: str,
    *,
    confirmed: bool,
    timeout: float = 15.0,
) -> ProfileStatusResult:
    """Submit after fresh identity/CSRF checks and report acknowledgement honestly.

    The current profile page does not expose a read-back value. This helper never
    returns a verified-success outcome, and never retries an uncertain POST.
    """
    text = str(status)
    expected = str(expected_nickname or "").strip()
    if not confirmed:
        return ProfileStatusResult(
            ProfileStatusOutcome.REFUSED, "CONFIRMATION_REQUIRED"
        )
    if not expected:
        return ProfileStatusResult(ProfileStatusOutcome.REFUSED, "ACCOUNT_REQUIRED")
    if (
        not text.strip()
        or len(text) > MAX_STATUS_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in text)
    ):
        return ProfileStatusResult(ProfileStatusOutcome.REFUSED, "INVALID_STATUS")
    if timeout <= 0:
        return ProfileStatusResult(ProfileStatusOutcome.REFUSED, "INVALID_TIMEOUT")

    try:
        html = fetch_profile(opener, timeout)
    except Exception:
        return ProfileStatusResult(ProfileStatusOutcome.REJECTED, "PROFILE_READ_FAILED")
    identity = extract_profile_identity(html)
    if identity is None:
        return ProfileStatusResult(
            ProfileStatusOutcome.REJECTED, "IDENTITY_UNAVAILABLE"
        )
    if identity["nickname"].casefold() != expected.casefold():
        return ProfileStatusResult(ProfileStatusOutcome.REJECTED, "ACCOUNT_MISMATCH")
    csrf = extract_csrf(html)
    if not csrf:
        return ProfileStatusResult(ProfileStatusOutcome.REJECTED, "CSRF_UNAVAILABLE")

    try:
        reply = post_profile_status(opener, text, csrf, timeout)
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if status_code == 429:
            return ProfileStatusResult(
                ProfileStatusOutcome.RATE_LIMITED,
                "PROVIDER_RATE_LIMITED",
                status_code,
                retry_after,
            )
        if 400 <= status_code < 500:
            return ProfileStatusResult(
                ProfileStatusOutcome.REJECTED, "PROVIDER_REJECTED", status_code
            )
        return ProfileStatusResult(
            ProfileStatusOutcome.EFFECT_UNKNOWN, "POST_OUTCOME_UNKNOWN", status_code
        )
    except Exception:
        return ProfileStatusResult(
            ProfileStatusOutcome.EFFECT_UNKNOWN, "POST_OUTCOME_UNKNOWN"
        )

    status_code = reply.status_code
    if status_code == 429:
        return ProfileStatusResult(
            ProfileStatusOutcome.RATE_LIMITED,
            "PROVIDER_RATE_LIMITED",
            status_code,
            reply.retry_after,
        )
    if status_code is not None and 400 <= status_code < 500:
        return ProfileStatusResult(
            ProfileStatusOutcome.REJECTED, "PROVIDER_REJECTED", status_code
        )
    if status_code is not None and status_code >= 500:
        return ProfileStatusResult(
            ProfileStatusOutcome.EFFECT_UNKNOWN, "POST_OUTCOME_UNKNOWN", status_code
        )
    if status_code is not None and not 200 <= status_code < 300:
        return ProfileStatusResult(
            ProfileStatusOutcome.REJECTED, "PROVIDER_REJECTED", status_code
        )
    try:
        payload = json.loads(reply)
    except (TypeError, ValueError):
        return ProfileStatusResult(
            ProfileStatusOutcome.EFFECT_UNKNOWN,
            "ACKNOWLEDGEMENT_UNPARSEABLE",
            status_code,
        )
    if not isinstance(payload, dict):
        return ProfileStatusResult(
            ProfileStatusOutcome.EFFECT_UNKNOWN,
            "ACKNOWLEDGEMENT_UNPARSEABLE",
            status_code,
        )
    if payload.get("error"):
        return ProfileStatusResult(
            ProfileStatusOutcome.REJECTED, "PROVIDER_REJECTED", status_code
        )
    if isinstance(payload.get("response"), str) and payload["response"].strip():
        return ProfileStatusResult(
            ProfileStatusOutcome.ACKNOWLEDGED_UNVERIFIED,
            "READBACK_UNAVAILABLE",
            status_code,
        )
    return ProfileStatusResult(
        ProfileStatusOutcome.EFFECT_UNKNOWN, "ACKNOWLEDGEMENT_MISSING", status_code
    )


class ProfileStatusReply(str):
    """Response text plus non-sensitive HTTP metadata for safe diagnostics."""

    def __new__(
        cls,
        value: str,
        status_code: int | None = None,
        content_type: str | None = None,
        retry_after: str | None = None,
    ) -> ProfileStatusReply:
        result = super().__new__(cls, value)
        result.status_code = status_code
        result.content_type = content_type
        result.retry_after = retry_after
        return result


def fetch_profile(opener, timeout: float) -> str:
    """Read the signed-in profile page that carries identity and CSRF markers."""
    request = urllib.request.Request(
        HOME_URL, headers=dict(BROWSER_HEADERS, Referer=HOME_URL)
    )
    with opener.open(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def extract_profile_identity(html: str) -> dict[str, str] | None:
    """Return provider identity only when both the external ID and nickname exist."""
    user = re.search(r"var\s+_user_id\s*=\s*['\"](\d+)['\"]", html)
    nickname = re.search(r"var\s+nick\s*=\s*['\"]([^'\"]+)['\"]", html)
    if not user or not nickname or not nickname.group(1).strip():
        return None
    return {"user_id": user.group(1), "nickname": nickname.group(1).strip()}


def extract_csrf(html: str) -> str:
    """Return the per-session CSRF marker embedded in the profile page."""
    match = re.search(r"var\s+csrf\s*=\s*['\"]([0-9a-fA-F]{16,})['\"]", html)
    return match.group(1) if match else ""


def post_profile_status(
    opener, status: str, csrf: str, timeout: float
) -> ProfileStatusReply:
    """Submit one profile text update using the session-owned CSRF token."""
    data = urllib.parse.urlencode({"status": status, "csrf": csrf}).encode("utf-8")
    request = urllib.request.Request(
        WEB_UPDATE_URL,
        data=data,
        headers=dict(BROWSER_HEADERS, Referer=PROFILE_REFERER),
    )
    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace").strip()
        headers = getattr(response, "headers", None)
        content_type = headers.get("Content-Type") if headers is not None else None
        retry_after = headers.get("Retry-After") if headers is not None else None
        status_code = getattr(response, "status", None)
        if status_code is None and hasattr(response, "getcode"):
            status_code = response.getcode()
        return ProfileStatusReply(body, status_code, content_type, retry_after)
