import hashlib
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError

from PIL import Image
import pytest

import camfrog_presence as presence


def gif_bytes(color=(220, 30, 40), size=(3, 2), comment=None):
    image = Image.new("RGB", size, color)
    if comment is not None:
        image.info["comment"] = comment
    output = BytesIO()
    image.save(output, format="GIF", comment=comment)
    return output.getvalue()


def animated_gif_bytes():
    frames = [Image.new("RGB", (2, 2), color)
              for color in ((255, 0, 0), (0, 255, 0))]
    output = BytesIO()
    frames[0].save(output, format="GIF", save_all=True,
                   append_images=frames[1:], duration=100, loop=0)
    return output.getvalue()


class FakeResponse(BytesIO):
    def __init__(self, body, status=200, headers=None, final_url=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or Message()
        self.final_url = final_url

    def getcode(self):
        return self.status

    def geturl(self):
        return self.final_url or presence.CAMFROG_STATUS_URL

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def gif_headers(body, extra=None):
    headers = Message()
    headers["Content-Type"] = "image/gif"
    headers["Content-Length"] = str(len(body))
    headers["Cache-Control"] = "private, max-age=600"
    headers["ETag"] = '"sample-v1"'
    headers["Last-Modified"] = "Wed, 14 Oct 2026 00:00:00 GMT"
    for name, value in (extra or {}).items():
        headers[name] = value
    return headers


class FakeOpener:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def test_fingerprint_normalizes_pixels_across_gif_metadata():
    plain = gif_bytes()
    with_comment = gif_bytes(comment=b"non-visual metadata")

    first = presence.fingerprint_gif(plain)
    second = presence.fingerprint_gif(with_comment)

    assert first.raw_sha256 == hashlib.sha256(plain).hexdigest()
    assert second.raw_sha256 == hashlib.sha256(with_comment).hexdigest()
    assert first.raw_sha256 != second.raw_sha256
    assert first.normalized_sha256 == second.normalized_sha256
    assert (first.width, first.height, first.frames) == (3, 2, 1)


def test_fingerprint_covers_all_animated_frames():
    fingerprint = presence.fingerprint_gif(animated_gif_bytes())

    assert fingerprint.normalized_sha256
    assert (fingerprint.width, fingerprint.height, fingerprint.frames) == (2, 2, 2)


def test_provider_fetches_bounded_public_image_and_never_classifies_online():
    body = gif_bytes()
    response = FakeResponse(body, headers=gif_headers(body))
    opener = FakeOpener(response)
    provider = presence.RawPresenceProvider(opener=opener, max_retries=0)

    evidence = provider.probe("  Test Nick  ", now=datetime(
        2026, 10, 14, tzinfo=timezone.utc))

    request, timeout = opener.requests[0]
    assert request.full_url == (
        "https://www.camfrog.com/getstatus.php?nick=Test+Nick")
    assert {key.lower(): value for key, value in request.header_items()}["accept"] == "image/gif"
    assert "cookie" not in {key.lower() for key, _ in request.header_items()}
    assert timeout == 5.0
    assert evidence.state is presence.PresenceState.UNKNOWN
    assert evidence.entity_kind is presence.EntityKind.UNKNOWN
    assert evidence.outcome is presence.ProbeOutcome.STATUS_IMAGE
    assert evidence.http_status == 200
    assert evidence.content_type == "image/gif"
    assert evidence.content_length == len(body)
    assert evidence.bytes_received == len(body)
    assert evidence.cache_control == "private, max-age=600"
    assert evidence.cache_max_age == 600
    assert evidence.etag == '"sample-v1"'
    assert evidence.last_modified == "Wed, 14 Oct 2026 00:00:00 GMT"
    assert evidence.retry_after is None
    assert evidence.raw_sha256 == hashlib.sha256(body).hexdigest()
    assert evidence.normalized_sha256
    assert evidence.observed_at == "2026-10-14T00:00:00.000Z"
    assert presence.classify_presence(evidence) is presence.PresenceState.UNKNOWN


def test_provider_retries_network_errors_with_bounded_backoff():
    body = gif_bytes()
    opener = FakeOpener(URLError("timed out"), URLError("reset"),
                        FakeResponse(body, headers=gif_headers(body)))
    delays = []
    provider = presence.RawPresenceProvider(
        opener=opener, sleep_fn=delays.append, max_retries=2)

    evidence = provider.probe("TestNick")

    assert evidence.outcome is presence.ProbeOutcome.STATUS_IMAGE
    assert evidence.attempts == 3
    assert delays == [0.5, 1.0]
    assert len(opener.requests) == 3


def test_provider_does_not_retry_rate_limit_responses():
    headers = Message()
    headers["Retry-After"] = "600"
    opener = FakeOpener(HTTPError(
        presence.CAMFROG_STATUS_URL, 429, "Too Many Requests", headers, BytesIO()))
    delays = []
    provider = presence.RawPresenceProvider(opener=opener, sleep_fn=delays.append)

    evidence = provider.probe("TestNick")

    assert evidence.state is presence.PresenceState.UNKNOWN
    assert evidence.outcome is presence.ProbeOutcome.HTTP_ERROR
    assert evidence.http_status == 429
    assert evidence.attempts == 1
    assert delays == []


def test_provider_respects_retry_after_on_temporary_server_errors():
    headers = Message()
    headers["Retry-After"] = "600"
    opener = FakeOpener(HTTPError(
        presence.CAMFROG_STATUS_URL, 503, "Unavailable", headers, BytesIO()))
    delays = []
    provider = presence.RawPresenceProvider(opener=opener, sleep_fn=delays.append)

    evidence = provider.probe("TestNick")

    assert evidence.outcome is presence.ProbeOutcome.HTTP_ERROR
    assert evidence.http_status == 503
    assert evidence.retry_after == "600"
    assert evidence.attempts == 1
    assert delays == []


def test_provider_records_non_gif_as_unknown_without_storing_response_body():
    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"
    body = b"not a status image"
    opener = FakeOpener(FakeResponse(body, headers=headers))

    evidence = presence.RawPresenceProvider(opener=opener, max_retries=0).probe("TestNick")

    assert evidence.state is presence.PresenceState.UNKNOWN
    assert evidence.outcome is presence.ProbeOutcome.NON_GIF_RESPONSE
    assert evidence.raw_sha256 is None
    assert evidence._raw_gif == b""


def test_provider_caps_response_even_when_content_length_is_missing():
    headers = Message()
    headers["Content-Type"] = "image/gif"
    body = b"x" * 20
    opener = FakeOpener(FakeResponse(body, headers=headers))

    evidence = presence.RawPresenceProvider(
        opener=opener, max_retries=0, max_response_bytes=10).probe("TestNick")

    assert evidence.outcome is presence.ProbeOutcome.RESPONSE_TOO_LARGE
    assert evidence.bytes_received == 11
    assert evidence.raw_sha256 is None
    assert evidence._raw_gif == b""


def test_malformed_gif_is_stored_as_unknown_evidence(tmp_path):
    body = b"GIF89a"
    evidence = presence.RawPresenceProvider(max_retries=0)._process_response(
        "OwnedAccount", FakeResponse(body, headers=gif_headers(body)),
        "2026-10-14T00:00:00.000Z", 1)
    store = presence.EvidenceStore(tmp_path / "presence.sqlite3")

    assert evidence.state is presence.PresenceState.UNKNOWN
    assert evidence.outcome is presence.ProbeOutcome.INVALID_GIF
    assert evidence.raw_sha256 == hashlib.sha256(body).hexdigest()
    assert evidence.normalized_sha256 is None
    assert store.get_raw_gif(evidence.raw_sha256) is None
    store.record(evidence)
    assert store.get_raw_gif(evidence.raw_sha256) == body


def test_provider_rejects_external_redirects():
    handler = presence._SameOriginHttpsRedirectHandler()
    with pytest.raises(HTTPError):
        handler.redirect_request(
            presence.Request(presence.CAMFROG_STATUS_URL), None, 302, "Found",
            Message(), "https://example.net/steal-nickname")


def test_provider_rejects_custom_opener_cross_origin_final_url():
    body = gif_bytes()
    response = FakeResponse(
        body, headers=gif_headers(body), final_url="https://example.net/collect")
    opener = FakeOpener(response)

    evidence = presence.RawPresenceProvider(
        opener=opener, max_retries=0).probe("TestNick")

    assert evidence.outcome is presence.ProbeOutcome.HTTP_ERROR
    assert evidence.state is presence.PresenceState.UNKNOWN
    assert evidence.raw_sha256 is None


def test_store_persists_observations_and_deduplicates_raw_gifs(tmp_path):
    body = gif_bytes()
    provider = presence.RawPresenceProvider(max_retries=0)
    first = provider._process_response(
        "OwnedAccount", FakeResponse(body, headers=gif_headers(body)),
        "2026-10-14T00:00:00.000Z", 1)
    second = replace(first, observed_at="2026-10-14T00:20:00.000Z")
    database = tmp_path / "presence.sqlite3"
    store = presence.EvidenceStore(database)

    first_id = store.record(first)
    second_id = store.record(second)

    assert second_id > first_id
    assert store.latest("OwnedAccount").observed_at == second.observed_at
    assert len(store.recent("OwnedAccount")) == 2
    assert store.get_raw_gif(first.raw_sha256) == body
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM presence_gifs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM presence_observations").fetchone()[0] == 2

    assert store.prune_before("2026-10-14T00:10:00Z") == 1
    assert store.get_raw_gif(first.raw_sha256) == body
    assert store.prune_before("2026-10-15T00:00:00Z") == 1
    assert store.get_raw_gif(first.raw_sha256) is None


def test_store_rejects_non_unknown_evidence(tmp_path):
    body = gif_bytes()
    evidence = presence.RawPresenceProvider(max_retries=0)._process_response(
        "OwnedAccount", FakeResponse(body, headers=gif_headers(body)),
        "2026-10-14T00:00:00.000Z", 1)

    with pytest.raises(ValueError, match="UNKNOWN state"):
        presence.EvidenceStore(tmp_path / "presence.sqlite3").record(
            replace(evidence, state=presence.PresenceState.ONLINE))


def test_store_migrates_schema_without_retry_after_column(tmp_path):
    database = tmp_path / "presence.sqlite3"
    presence.EvidenceStore(database)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE presence_observations DROP COLUMN retry_after")
        connection.execute("PRAGMA user_version=1")

    presence.EvidenceStore(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(presence_observations)")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert "retry_after" in columns
    assert version == presence.DATABASE_SCHEMA_VERSION


def test_default_database_path_supports_override_and_user_scoped_locations(monkeypatch, tmp_path):
    monkeypatch.setenv("CAMFROG_PRESENCE_DATA_DIR", str(tmp_path / "override"))
    assert presence.default_database_path() == (
        tmp_path / "override" / presence.DATABASE_FILENAME)

    monkeypatch.delenv("CAMFROG_PRESENCE_DATA_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert presence.default_database_path() == (
        tmp_path / "local" / "Camfrog" / "Presence" / presence.DATABASE_FILENAME)


def test_nickname_validation_rejects_control_chars_and_empty_input():
    with pytest.raises(ValueError, match="Enter"):
        presence.validate_nickname(" \t ")
    with pytest.raises(ValueError, match="control"):
        presence.validate_nickname("Name\nInjected")
