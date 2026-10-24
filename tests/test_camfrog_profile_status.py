"""Offline tests for shared Camfrog profile status HTTP primitives."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "camfrog"))
import camfrog_profile_status as profile  # noqa: E402


class FakeResponse:
    status = 200

    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        return self.response


class SequenceOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_identity_and_csrf_require_observed_profile_markers():
    html = "var _user_id = '42'; var nick = 'Example'; var csrf = '{0}';".format(
        "a" * 32)
    assert profile.extract_profile_identity(html) == {
        "user_id": "42", "nickname": "Example"
    }
    assert profile.extract_csrf(html) == "a" * 32
    assert profile.extract_profile_identity("var nick = 'Example';") is None
    assert profile.extract_csrf("var csrf = 'short';") == ""


def test_fetch_profile_uses_profile_home_and_decodes_replacement():
    opener = FakeOpener(FakeResponse(b"profile\xff"))
    assert profile.fetch_profile(opener, timeout=7) == "profile\ufffd"
    request, timeout = opener.calls[0]
    assert request.full_url == profile.HOME_URL
    assert request.get_header("Referer") == profile.HOME_URL
    assert timeout == 7


def test_post_status_encodes_fields_and_returns_sanitizable_http_metadata():
    opener = FakeOpener(FakeResponse(
        b'{"response":"accepted"}',
        {"Content-Type": "application/json", "Retry-After": "10"},
    ))
    reply = profile.post_profile_status(opener, "hello world", "a" * 32, 9)
    request, timeout = opener.calls[0]
    assert request.full_url == profile.WEB_UPDATE_URL
    assert request.data == b"status=hello+world&csrf=" + b"a" * 32
    assert request.get_header("Referer") == profile.PROFILE_REFERER
    assert timeout == 9
    assert reply == '{"response":"accepted"}'
    assert (reply.status_code, reply.content_type, reply.retry_after) == (
        200, "application/json", "10"
    )


def test_profile_cookie_is_single_and_scoped_to_profile_host():
    assert profile.parse_profile_cookie("PHPSESSID=opaque") == (
        "PHPSESSID", "opaque"
    )
    for invalid in ("", "PHPSESSID=one; cloudbot=two", "cloudbot=one", "PHPSESSID="):
        try:
            profile.parse_profile_cookie(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid profile cookie accepted")

    opener = profile.build_profile_opener("PHPSESSID=opaque")
    processor = next(
        handler for handler in opener.handlers
        if isinstance(handler, __import__("urllib.request").request.HTTPCookieProcessor)
    )
    cookie, = list(processor.cookiejar)
    assert cookie.name == "PHPSESSID"
    assert cookie.domain == "profiles.camfrog.com"
    assert cookie.secure is True


def _profile_html(nickname="Example"):
    return (
        "var _user_id = '42'; var nick = '{0}'; var csrf = '{1}';".format(
            nickname, "a" * 32
        )
    ).encode()


def test_submit_requires_explicit_confirmation_and_account_identity():
    opener = SequenceOpener(FakeResponse(_profile_html("Other")))
    refused = profile.submit_profile_status(
        opener, "new status", "Example", confirmed=False
    )
    assert refused.outcome is profile.ProfileStatusOutcome.REFUSED
    assert opener.calls == []

    mismatch = profile.submit_profile_status(
        opener, "new status", "Example", confirmed=True
    )
    assert mismatch.outcome is profile.ProfileStatusOutcome.REJECTED
    assert mismatch.error_code == "ACCOUNT_MISMATCH"
    assert len(opener.calls) == 1


def test_submit_returns_acknowledged_unverified_without_claiming_readback():
    opener = SequenceOpener(
        FakeResponse(_profile_html()),
        FakeResponse(
            b'{"response":"accepted"}',
            {"Content-Type": "application/json"},
        ),
    )
    result = profile.submit_profile_status(
        opener, "new status", "Example", confirmed=True, timeout=8
    )
    assert result.outcome is profile.ProfileStatusOutcome.ACKNOWLEDGED_UNVERIFIED
    assert result.error_code == "READBACK_UNAVAILABLE"
    assert result.http_status == 200
    assert len(opener.calls) == 2  # profile GET + one POST; no automatic retry/read-back
    assert opener.calls[1][0].full_url == profile.WEB_UPDATE_URL
    assert opener.calls[1][1] == 8


def test_submit_keeps_post_timeout_as_unknown_without_retry():
    opener = SequenceOpener(
        FakeResponse(_profile_html()), TimeoutError("private detail must not escape")
    )
    result = profile.submit_profile_status(
        opener, "new status", "Example", confirmed=True
    )
    assert result.outcome is profile.ProfileStatusOutcome.EFFECT_UNKNOWN
    assert result.error_code == "POST_OUTCOME_UNKNOWN"
    assert len(opener.calls) == 2


def test_submit_reports_provider_rate_limit_without_retry():
    error = __import__("urllib.error").error.HTTPError(
        profile.WEB_UPDATE_URL, 429, "private detail", {"Retry-After": "10"}, None
    )
    opener = SequenceOpener(FakeResponse(_profile_html()), error)
    result = profile.submit_profile_status(
        opener, "new status", "Example", confirmed=True
    )
    assert result.outcome is profile.ProfileStatusOutcome.RATE_LIMITED
    assert result.retry_after == "10"
    assert len(opener.calls) == 2
