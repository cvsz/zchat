"""Data models and exceptions."""
from __future__ import annotations
from dataclasses import dataclass


class UserError(Exception):
    """Message safe to show in the room."""


class YtdlpMissing(UserError):
    """No way to look up YouTube metadata (neither the yt_dlp module nor a yt-dlp executable)."""


def fmt_time(sec: float | None) -> str:
    if not sec:
        return "?"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Track:
    source: str
    title: str
    duration: float | None = None
    requester: str = "AutoDJ"

    @property
    def dur(self) -> str:
        return fmt_time(self.duration)

    def fields(self) -> dict:
        return {"title": self.title, "duration": self.dur, "requester": self.requester}