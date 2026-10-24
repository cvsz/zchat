"""Read-only Camfrog public-widget presence evidence collector.

This module records what the public status-image endpoint returned. It does
not infer online state, room membership, recipient type, or subscription.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError


CAMFROG_STATUS_URL = "https://www.camfrog.com/getstatus.php"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_RESPONSE_BYTES = 262_144
MAX_NICKNAME_CHARS = 256
MAX_GIF_FRAMES = 128
MAX_GIF_TOTAL_PIXELS = 16_000_000
MAX_CACHE_AGE_SECONDS = 31_536_000
DATABASE_FILENAME = "presence-evidence.sqlite3"
DATABASE_SCHEMA_VERSION = 2


class PresenceState(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class EntityKind(str, Enum):
    USER = "user"
    ROOM = "room"
    UNKNOWN = "unknown"


class ProbeOutcome(str, Enum):
    STATUS_IMAGE = "status_image"
    HTTP_ERROR = "http_error"
    NON_GIF_RESPONSE = "non_gif_response"
    INVALID_GIF = "invalid_gif"
    RESPONSE_TOO_LARGE = "response_too_large"
    NETWORK_ERROR = "network_error"


@dataclass(frozen=True)
class GifFingerprint:
    raw_sha256: str
    normalized_sha256: str | None
    width: int | None
    height: int | None
    frames: int | None
    image_error: str | None = None


@dataclass(frozen=True)
class PresenceEvidence:
    nickname: str
    state: PresenceState
    entity_kind: EntityKind
    outcome: ProbeOutcome
    http_status: int | None
    content_type: str | None
    content_length: int | None
    bytes_received: int
    cache_control: str | None
    cache_max_age: int | None
    etag: str | None
    last_modified: str | None
    retry_after: str | None
    raw_sha256: str | None
    normalized_sha256: str | None
    width: int | None
    height: int | None
    frames: int | None
    observed_at: str
    attempts: int
    error: str | None = None
    image_error: str | None = None
    _raw_gif: bytes = field(default=b"", repr=False, compare=False)

    def to_dict(self) -> dict:
        """Return JSON-safe evidence without embedding the raw GIF bytes."""
        return {
            "nickname": self.nickname,
            "state": self.state.value,
            "entity_kind": self.entity_kind.value,
            "outcome": self.outcome.value,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "content_length": self.content_length,
            "bytes_received": self.bytes_received,
            "cache_control": self.cache_control,
            "cache_max_age": self.cache_max_age,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "retry_after": self.retry_after,
            "raw_sha256": self.raw_sha256,
            "normalized_sha256": self.normalized_sha256,
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "observed_at": self.observed_at,
            "attempts": self.attempts,
            "error": self.error,
            "image_error": self.image_error,
        }


class _SameOriginHttpsRedirectHandler(HTTPRedirectHandler):
    """Keep nickname-bearing requests on Camfrog's HTTPS origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_safe_camfrog_url(newurl):
            raise HTTPError(
                req.full_url, code,
                "Blocked redirect outside the Camfrog HTTPS status endpoint.",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_safe_camfrog_url(value: str) -> bool:
    try:
        target = urlsplit(value)
        port = target.port
    except (TypeError, ValueError):
        return False
    return (target.scheme.lower() == "https" and
            (target.hostname or "").lower() == "www.camfrog.com" and
            port in (None, 443) and target.username is None and
            target.password is None)


def validate_nickname(value: str) -> str:
    """Normalize a public nickname input and reject empty/control text."""
    nickname = str(value or "").strip()
    if not nickname:
        raise ValueError("Enter a Camfrog nickname.")
    if len(nickname) > MAX_NICKNAME_CHARS:
        raise ValueError("Camfrog nickname is longer than the 256-character safety limit.")
    if any(ord(char) < 32 or ord(char) == 127 for char in nickname):
        raise ValueError("Camfrog nickname cannot contain control characters.")
    return nickname


def _utc_timestamp(value: datetime | None = None) -> str:
    stamp = value or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def _safe_header(headers, name: str, limit: int = 1024) -> str | None:
    value = headers.get(name) if headers is not None else None
    if value is None:
        return None
    return str(value).strip()[:limit]


def _header_int(headers, name: str) -> int | None:
    raw = _safe_header(headers, name, 32)
    if raw is None or not raw.isdecimal():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _content_type(headers) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get_content_type()
    except (AttributeError, TypeError, ValueError):
        value = _safe_header(headers, "Content-Type", 128)
        value = value.split(";", 1)[0].strip().lower() if value else None
    return str(value).lower()[:128] if value else None


def _cache_max_age(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(?:^|,)\s*max-age\s*=\s*\"?(\d+)\"?", value, re.I)
    if not match:
        return None
    raw_age = match.group(1)
    if len(raw_age) > 10:
        return MAX_CACHE_AGE_SECONDS
    return min(int(raw_age), MAX_CACHE_AGE_SECONDS)


def fingerprint_gif(data: bytes) -> GifFingerprint:
    """Fingerprint GIF bytes and normalized RGBA pixels for every frame.

    The caller must enforce a response-size bound before passing untrusted
    network content here. Pixel and frame limits also cap decoded work.
    """
    raw_sha256 = hashlib.sha256(data).hexdigest()
    if not data.startswith((b"GIF87a", b"GIF89a")):
        return GifFingerprint(raw_sha256, None, None, None, None,
                              "Response does not have a GIF87a/GIF89a header.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format != "GIF":
                    raise ValueError("Pillow did not identify the response as GIF.")
                width, height = image.size
                frames = int(getattr(image, "n_frames", 1))
                if (width <= 0 or height <= 0 or frames <= 0 or
                        frames > MAX_GIF_FRAMES or
                        width * height * frames > MAX_GIF_TOTAL_PIXELS):
                    raise ValueError("GIF dimensions or frame count exceed safety limits.")

                normalized = hashlib.sha256()
                normalized.update(b"camfrog-presence-rgba-frames-v1\0")
                normalized.update(width.to_bytes(4, "big"))
                normalized.update(height.to_bytes(4, "big"))
                normalized.update(frames.to_bytes(4, "big"))
                for frame_index in range(frames):
                    image.seek(frame_index)
                    rgba = image.convert("RGBA")
                    normalized.update(frame_index.to_bytes(4, "big"))
                    normalized.update(rgba.tobytes())
                return GifFingerprint(raw_sha256, normalized.hexdigest(), width,
                                      height, frames)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning,
            UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        return GifFingerprint(raw_sha256, None, None, None, None,
                              (str(exc) or type(exc).__name__)[:240])


def classify_presence(_evidence: PresenceEvidence) -> PresenceState:
    """Fail closed until controlled online/offline samples are mapped."""
    return PresenceState.UNKNOWN


def default_database_path() -> Path:
    """Return the per-user location for local-only presence evidence."""
    override = os.environ.get("CAMFROG_PRESENCE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser() / DATABASE_FILENAME
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Camfrog" / "Presence" / DATABASE_FILENAME
    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / "camfrog" / "presence" / DATABASE_FILENAME
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / "Camfrog" / "Presence" / DATABASE_FILENAME
    return Path.home() / ".local" / "share" / "camfrog" / "presence" / DATABASE_FILENAME


class RawPresenceProvider:
    """Fetch a single public Camfrog status image without account credentials."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
                 opener=None, sleep_fn=time.sleep):
        if not 0 < float(timeout) <= 30:
            raise ValueError("Timeout must be between 0 and 30 seconds.")
        if not 0 <= int(max_retries) <= 2:
            raise ValueError("At most two retries are allowed.")
        if not 1 <= int(max_response_bytes) <= DEFAULT_MAX_RESPONSE_BYTES:
            raise ValueError("Response limit must be between 1 and 262144 bytes.")
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.max_response_bytes = int(max_response_bytes)
        self.opener = opener or build_opener(_SameOriginHttpsRedirectHandler())
        self.sleep_fn = sleep_fn

    def _error_evidence(self, nickname: str, outcome: ProbeOutcome,
                        observed_at: str, attempts: int, error: str,
                        status: int | None = None, headers=None) -> PresenceEvidence:
        cache_control = _safe_header(headers, "Cache-Control")
        return PresenceEvidence(
            nickname=nickname,
            state=PresenceState.UNKNOWN,
            entity_kind=EntityKind.UNKNOWN,
            outcome=outcome,
            http_status=status,
            content_type=_content_type(headers),
            content_length=_header_int(headers, "Content-Length"),
            bytes_received=0,
            cache_control=cache_control,
            cache_max_age=_cache_max_age(cache_control),
            etag=_safe_header(headers, "ETag"),
            last_modified=_safe_header(headers, "Last-Modified"),
            retry_after=_safe_header(headers, "Retry-After", 128),
            raw_sha256=None,
            normalized_sha256=None,
            width=None,
            height=None,
            frames=None,
            observed_at=observed_at,
            attempts=attempts,
            error=error[:512],
        )

    def probe(self, value: str, now: datetime | None = None) -> PresenceEvidence:
        """Fetch one status image; every classification remains UNKNOWN."""
        nickname = validate_nickname(value)
        observed_at = _utc_timestamp(now)
        url = CAMFROG_STATUS_URL + "?" + urlencode({"nick": nickname})
        request = Request(url, headers={
            "Accept": "image/gif",
            "User-Agent": "CamfrogPresenceProbe/1.0",
        }, method="GET")

        for attempt in range(1, self.max_retries + 2):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    geturl = getattr(response, "geturl", None)
                    if callable(geturl):
                        if not _is_safe_camfrog_url(geturl()):
                            return self._error_evidence(
                                nickname, ProbeOutcome.HTTP_ERROR, observed_at, attempt,
                                "Status response redirected outside the Camfrog HTTPS origin.",
                                int(getattr(response, "status", 0) or response.getcode()),
                                getattr(response, "headers", None))
                    return self._process_response(nickname, response, observed_at, attempt)
            except HTTPError as exc:
                retryable = (exc.code in {500, 502, 503, 504} and
                             not _safe_header(exc.headers, "Retry-After", 128))
                if retryable and attempt <= self.max_retries:
                    self.sleep_fn(min(0.5 * (2 ** (attempt - 1)), 2.0))
                    continue
                return self._error_evidence(
                    nickname, ProbeOutcome.HTTP_ERROR, observed_at, attempt,
                    "Camfrog returned HTTP {0}.".format(exc.code), exc.code,
                    exc.headers)
            except (URLError, TimeoutError, OSError) as exc:
                if attempt <= self.max_retries:
                    self.sleep_fn(min(0.5 * (2 ** (attempt - 1)), 2.0))
                    continue
                reason = getattr(exc, "reason", None)
                detail = str(reason if reason is not None else exc)[:400]
                return self._error_evidence(
                    nickname, ProbeOutcome.NETWORK_ERROR, observed_at, attempt,
                    "Camfrog status request failed: {0}".format(detail))

        raise RuntimeError("Presence retry loop ended without a result.")

    def _process_response(self, nickname: str, response, observed_at: str,
                          attempts: int) -> PresenceEvidence:
        headers = getattr(response, "headers", None)
        status = int(getattr(response, "status", None) or response.getcode())
        content_type = _content_type(headers)
        content_length = _header_int(headers, "Content-Length")
        cache_control = _safe_header(headers, "Cache-Control")
        header_values = {
            "cache_control": cache_control,
            "cache_max_age": _cache_max_age(cache_control),
            "etag": _safe_header(headers, "ETag"),
            "last_modified": _safe_header(headers, "Last-Modified"),
            "retry_after": _safe_header(headers, "Retry-After", 128),
        }

        def response_evidence(outcome, *, bytes_received=0, raw_sha256=None,
                              normalized_sha256=None, width=None, height=None,
                              frames=None, error=None, image_error=None,
                              raw_gif=b""):
            return PresenceEvidence(
                nickname=nickname,
                state=PresenceState.UNKNOWN,
                entity_kind=EntityKind.UNKNOWN,
                outcome=outcome,
                http_status=status,
                content_type=content_type,
                content_length=content_length,
                bytes_received=bytes_received,
                observed_at=observed_at,
                attempts=attempts,
                raw_sha256=raw_sha256,
                normalized_sha256=normalized_sha256,
                width=width,
                height=height,
                frames=frames,
                error=error,
                image_error=image_error,
                _raw_gif=raw_gif,
                **header_values,
            )

        if status != 200:
            return response_evidence(
                ProbeOutcome.HTTP_ERROR, error="Camfrog returned HTTP {0}.".format(status))
        if content_type != "image/gif":
            return response_evidence(
                ProbeOutcome.NON_GIF_RESPONSE,
                error="Camfrog did not return an image/gif response.")
        if content_length is not None and content_length > self.max_response_bytes:
            return response_evidence(
                ProbeOutcome.RESPONSE_TOO_LARGE,
                error="GIF response exceeds the configured size limit.")

        data = response.read(self.max_response_bytes + 1)
        if len(data) > self.max_response_bytes:
            return response_evidence(
                ProbeOutcome.RESPONSE_TOO_LARGE,
                bytes_received=len(data),
                error="GIF response exceeds the configured size limit.")

        fingerprint = fingerprint_gif(data)
        valid_image = fingerprint.normalized_sha256 is not None
        return response_evidence(
            ProbeOutcome.STATUS_IMAGE if valid_image else ProbeOutcome.INVALID_GIF,
            bytes_received=len(data),
            raw_sha256=fingerprint.raw_sha256,
            normalized_sha256=fingerprint.normalized_sha256,
            width=fingerprint.width,
            height=fingerprint.height,
            frames=fingerprint.frames,
            image_error=fingerprint.image_error,
            error=None if valid_image else "Camfrog returned an invalid GIF payload.",
            raw_gif=data,
        )


class EvidenceStore:
    """SQLite history for metadata and deduplicated raw GIF samples."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path or default_database_path()).expanduser()
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > DATABASE_SCHEMA_VERSION:
                raise RuntimeError("Presence evidence database schema is newer than this app.")
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS presence_gifs ("
                " raw_sha256 TEXT PRIMARY KEY CHECK(length(raw_sha256)=64),"
                " content BLOB NOT NULL,"
                " byte_count INTEGER NOT NULL CHECK(byte_count >= 0),"
                " first_seen_at TEXT NOT NULL"
                ");"
                "CREATE TABLE IF NOT EXISTS presence_observations ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " nickname TEXT NOT NULL,"
                " state TEXT NOT NULL DEFAULT 'unknown' CHECK(state='unknown'),"
                " entity_kind TEXT NOT NULL DEFAULT 'unknown' CHECK(entity_kind='unknown'),"
                " outcome TEXT NOT NULL,"
                " http_status INTEGER,"
                " content_type TEXT,"
                " content_length INTEGER,"
                " bytes_received INTEGER NOT NULL CHECK(bytes_received >= 0),"
                " cache_control TEXT,"
                " cache_max_age INTEGER,"
                " etag TEXT,"
                " last_modified TEXT,"
                " retry_after TEXT,"
                " raw_sha256 TEXT REFERENCES presence_gifs(raw_sha256),"
                " normalized_sha256 TEXT,"
                " width INTEGER,"
                " height INTEGER,"
                " frames INTEGER,"
                " observed_at TEXT NOT NULL,"
                " attempts INTEGER NOT NULL CHECK(attempts > 0),"
                " error TEXT,"
                " image_error TEXT"
                ");"
                "CREATE INDEX IF NOT EXISTS presence_observations_nick_time "
                "ON presence_observations(nickname, observed_at DESC);"
                "CREATE INDEX IF NOT EXISTS presence_observations_raw_hash "
                "ON presence_observations(raw_sha256);"
            )
            columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(presence_observations)")}
            if "retry_after" not in columns:
                connection.execute(
                    "ALTER TABLE presence_observations ADD COLUMN retry_after TEXT")
            connection.execute("PRAGMA user_version=2")
            connection.commit()
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def record(self, evidence: PresenceEvidence) -> int:
        """Persist one observation and its bounded GIF bytes, if available."""
        if validate_nickname(evidence.nickname) != evidence.nickname:
            raise ValueError("Evidence nickname must already be normalized.")
        if evidence.state is not PresenceState.UNKNOWN:
            raise ValueError("The raw provider may persist UNKNOWN state only.")
        if evidence.entity_kind is not EntityKind.UNKNOWN:
            raise ValueError("The raw provider may persist UNKNOWN entity kind only.")
        raw_gif = evidence._raw_gif
        if raw_gif:
            if len(raw_gif) > DEFAULT_MAX_RESPONSE_BYTES:
                raise ValueError("GIF evidence exceeds the maximum stored response size.")
            digest = hashlib.sha256(raw_gif).hexdigest()
            if digest != evidence.raw_sha256:
                raise ValueError("GIF evidence does not match its SHA-256 fingerprint.")
        elif evidence.raw_sha256 is not None:
            raise ValueError("A raw GIF fingerprint cannot be stored without its GIF bytes.")

        with self._connection() as connection:
            with connection:
                if raw_gif:
                    connection.execute(
                        "INSERT OR IGNORE INTO presence_gifs "
                        "(raw_sha256, content, byte_count, first_seen_at) VALUES (?, ?, ?, ?)",
                        (evidence.raw_sha256, sqlite3.Binary(raw_gif), len(raw_gif),
                         evidence.observed_at))
                    existing = connection.execute(
                        "SELECT content FROM presence_gifs WHERE raw_sha256=?",
                        (evidence.raw_sha256,)).fetchone()
                    if existing is None or bytes(existing["content"]) != raw_gif:
                        raise sqlite3.IntegrityError("SHA-256 collision in presence GIF store.")

                cursor = connection.execute(
                    "INSERT INTO presence_observations ("
                    "nickname, state, entity_kind, outcome, http_status, content_type, "
                    "content_length, bytes_received, cache_control, cache_max_age, etag, "
                    "last_modified, retry_after, raw_sha256, normalized_sha256, width, height, frames, "
                    "observed_at, attempts, error, image_error"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (evidence.nickname, evidence.state.value, evidence.entity_kind.value,
                     evidence.outcome.value, evidence.http_status, evidence.content_type,
                     evidence.content_length, evidence.bytes_received, evidence.cache_control,
                     evidence.cache_max_age, evidence.etag, evidence.last_modified,
                     evidence.retry_after,
                     evidence.raw_sha256, evidence.normalized_sha256, evidence.width,
                     evidence.height, evidence.frames, evidence.observed_at, evidence.attempts,
                     evidence.error, evidence.image_error))
                return int(cursor.lastrowid)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PresenceEvidence:
        return PresenceEvidence(
            nickname=row["nickname"],
            state=PresenceState(row["state"]),
            entity_kind=EntityKind(row["entity_kind"]),
            outcome=ProbeOutcome(row["outcome"]),
            http_status=row["http_status"],
            content_type=row["content_type"],
            content_length=row["content_length"],
            bytes_received=row["bytes_received"],
            cache_control=row["cache_control"],
            cache_max_age=row["cache_max_age"],
            etag=row["etag"],
            last_modified=row["last_modified"],
            retry_after=row["retry_after"],
            raw_sha256=row["raw_sha256"],
            normalized_sha256=row["normalized_sha256"],
            width=row["width"],
            height=row["height"],
            frames=row["frames"],
            observed_at=row["observed_at"],
            attempts=row["attempts"],
            error=row["error"],
            image_error=row["image_error"],
        )

    def latest(self, nickname: str) -> PresenceEvidence | None:
        nickname = validate_nickname(nickname)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM presence_observations WHERE nickname=? "
                "ORDER BY observed_at DESC, id DESC LIMIT 1", (nickname,)).fetchone()
        return self._from_row(row) if row is not None else None

    def recent(self, nickname: str | None = None, limit: int = 100) -> list[PresenceEvidence]:
        if not 1 <= int(limit) <= 10_000:
            raise ValueError("Limit must be between 1 and 10000.")
        with self._connection() as connection:
            if nickname is None:
                rows = connection.execute(
                    "SELECT * FROM presence_observations "
                    "ORDER BY observed_at DESC, id DESC LIMIT ?", (int(limit),)).fetchall()
            else:
                nickname = validate_nickname(nickname)
                rows = connection.execute(
                    "SELECT * FROM presence_observations WHERE nickname=? "
                    "ORDER BY observed_at DESC, id DESC LIMIT ?",
                    (nickname, int(limit))).fetchall()
        return [self._from_row(row) for row in rows]

    def get_raw_gif(self, raw_sha256: str) -> bytes | None:
        if not re.fullmatch(r"[0-9a-f]{64}", str(raw_sha256 or "")):
            raise ValueError("Invalid GIF SHA-256 fingerprint.")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT content FROM presence_gifs WHERE raw_sha256=?",
                (raw_sha256,)).fetchone()
        if row is None:
            return None
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != raw_sha256:
            raise sqlite3.DatabaseError("Stored GIF does not match its SHA-256 fingerprint.")
        return content

    def prune_before(self, timestamp: str) -> int:
        """Explicitly remove older observations and GIFs no longer referenced."""
        try:
            cutoff = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Cutoff must be an ISO-8601 timestamp with a timezone.") from exc
        if cutoff.tzinfo is None:
            raise ValueError("Cutoff timestamp must include a timezone.")
        cutoff_text = _utc_timestamp(cutoff)
        with self._connection() as connection:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM presence_observations WHERE observed_at < ?", (cutoff_text,))
                connection.execute(
                    "DELETE FROM presence_gifs WHERE NOT EXISTS ("
                    "SELECT 1 FROM presence_observations "
                    "WHERE presence_observations.raw_sha256=presence_gifs.raw_sha256)")
                return int(cursor.rowcount)
def _json_lines(values) -> str:
    return json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect and store public Camfrog status-image evidence; classification stays UNKNOWN.")
    parser.add_argument("--database", type=Path, default=default_database_path(),
                        help="SQLite evidence database path.")
    commands = parser.add_subparsers(dest="command", required=True)

    probe_parser = commands.add_parser("probe", help="Fetch one public status image and store evidence.")
    probe_parser.add_argument("nickname")

    recent_parser = commands.add_parser("recent", help="Show stored evidence metadata.")
    recent_parser.add_argument("--nickname")
    recent_parser.add_argument("--limit", type=int, default=20)

    export_parser = commands.add_parser("export-gif", help="Export a previously stored raw GIF sample.")
    export_parser.add_argument("raw_sha256")
    export_parser.add_argument("output", type=Path)

    prune_parser = commands.add_parser("prune", help="Delete evidence older than an ISO-8601 cutoff.")
    prune_parser.add_argument("--before", required=True)

    args = parser.parse_args(argv)
    store = EvidenceStore(args.database)
    if args.command == "probe":
        evidence = RawPresenceProvider().probe(args.nickname)
        observation_id = store.record(evidence)
        output = evidence.to_dict()
        output["observation_id"] = observation_id
        output["gif_stored"] = evidence.raw_sha256 is not None
        print(_json_lines(output))
        return 0 if evidence.outcome is ProbeOutcome.STATUS_IMAGE else 1
    if args.command == "recent":
        print(_json_lines([item.to_dict() for item in store.recent(args.nickname, args.limit)]))
        return 0
    if args.command == "export-gif":
        content = store.get_raw_gif(args.raw_sha256)
        if content is None:
            parser.error("No GIF sample has that SHA-256 fingerprint.")
        with args.output.open("xb") as stream:
            stream.write(content)
        print("Exported {0} bytes to {1}".format(len(content), args.output))
        return 0
    if args.command == "prune":
        print("Removed {0} old observations.".format(store.prune_before(args.before)))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
