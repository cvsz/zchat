"""Persistent autoplay playlist (playlist.json).

The file is plain JSON the operator can edit by hand:
    {"version": 1, "tracks": [
        {"source": "https://www.youtube.com/watch?v=...", "title": "...", "duration": 245.0,
         "added_by": "Seaza", "added": "2026-10-28T15:10:00"},
        ...]}

* added at startup to the autoplay pool (requests still play first, then random from the pool, looping)
* every user request (and admin !load) appends its tracks here -- deduplicated by `source`
* missing or corrupt file = empty list, never fatal.
"""
from __future__ import annotations
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from .models import Track

log = logging.getLogger("autodj")


def load_playlist(path: str) -> list[Track]:
    """Read playlist.json -> tracks; missing or broken file = [] (logged, never fatal)."""
    try:
        data = json.loads(Path(path).read_text("utf-8-sig"))
    except FileNotFoundError:
        return []
    except Exception:
        log.warning("cannot read playlist file %r -- starting without it (the file will not be overwritten)", path,
                    exc_info=True)
        return []
    entries = data.get("tracks", []) if isinstance(data, dict) else data
    out = []
    for e in entries if isinstance(entries, list) else []:
        try:
            src = str(e.get("source") or "").strip()
            if not src:
                continue
            dur = e.get("duration")
            t = Track(src, str(e.get("title") or src), dur if isinstance(dur, (int, float)) else None,
                      str(e.get("added_by") or "AutoDJ"))
            t.added = str(e.get("added") or "")  # not a Track field: plain attribute, kept for !last
            out.append(t)
        except Exception:
            continue
    return out


def save_playlist(path: str, tracks: list[Track]) -> None:
    """Write the whole playlist atomically (tmp file + replace)."""
    entries = [{"source": t.source, "title": t.title, "duration": t.duration,
                "added_by": t.requester, "added": getattr(t, "added", "")} for t in tracks]
    tmp = path + ".tmp"
    Path(tmp).write_text(json.dumps({"version": 1, "tracks": entries}, ensure_ascii=False, indent=1), "utf-8")
    Path(tmp).replace(path)


class PlaylistStore:
    """playlist.json behind a lock: read once at startup, appended by requests."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()

    def load(self) -> list[Track]:
        with self.lock:
            return load_playlist(self.path)

    def add(self, tracks) -> int:
        """Append tracks to playlist.json (dedup by source). Returns how many were new.
        Disabled / nothing to add / write failure are all no-ops (never breaks the request)."""
        if not self.path or not tracks:
            return 0
        with self.lock:
            try:
                cur = load_playlist(self.path)
                have = {t.source for t in cur}
                now = datetime.now().isoformat(timespec="seconds")
                fresh = []
                for t in tracks:
                    if t.source not in have:
                        have.add(t.source)
                        t.added = now
                        fresh.append(t)
                if not fresh:
                    return 0
                cur.extend(fresh)
                save_playlist(self.path, cur)
                log.info("playlist: +%d new source(s) -> %s (%d total)", len(fresh), self.path, len(cur))
                return len(fresh)
            except Exception:
                log.exception("cannot update playlist file %r (the request still went through)", self.path)
                return 0