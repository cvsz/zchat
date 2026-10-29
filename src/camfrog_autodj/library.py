"""Local library scan + YouTube metadata (via yt-dlp)."""
from __future__ import annotations
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from .models import Track, YtdlpMissing

log = logging.getLogger("autodj")

AUDIO = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac"}
VIDEO = {".mp4", ".mkv", ".webm", ".avi", ".mov"}
MEDIA = AUDIO | VIDEO


def is_url(s: str) -> bool:
    return s.lower().startswith(("http://", "https://"))


def host_ok(url: str, allowed: list[str]) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return any(h == a or h.endswith("." + a) for a in allowed)


def local_track(path, requester: str = "AutoDJ") -> Track:
    p = Path(path)
    title, dur = p.stem, None
    try:
        import mutagen
        f = mutagen.File(p, easy=True)
        if f is not None:
            dur = getattr(f.info, "length", None)
            t = (f.get("title") or [None])[0]
            a = (f.get("artist") or [None])[0]
            if t:
                title = f"{a} - {t}" if a else t
    except Exception:
        pass
    return Track(str(p), title, dur, requester)


def scan_folder(folder, requester: str = "AutoDJ") -> list[Track]:
    files = sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in MEDIA)
    return [local_track(p, requester) for p in files]


def find_ytdlp(ytdlp_path: str = "", mpv_path: str = "") -> str:
    """The yt-dlp executable to use, '' if there is none. Order: the configured path, next to mpv, then PATH."""
    if ytdlp_path:
        return ytdlp_path if (shutil.which(ytdlp_path) or Path(ytdlp_path).is_file()) else ""
    names = ("yt-dlp.exe", "yt-dlp") if os.name == "nt" else ("yt-dlp",)
    mpv = shutil.which(mpv_path) if mpv_path else None
    if mpv:
        for n in names:
            cand = Path(mpv).resolve().parent / n
            if cand.is_file():
                return str(cand)
    return shutil.which("yt-dlp") or ""


def parse_ytdlp_info(info: dict, requester: str = "AutoDJ") -> list[Track]:
    """yt-dlp's info dict (single video, playlist or search result) -> tracks."""
    out = []
    for e in (info.get("entries") or [info]):
        if not e:
            continue
        url = e.get("webpage_url") or e.get("url") or ""
        if not is_url(url):
            if not e.get("id"):
                continue
            url = f"https://www.youtube.com/watch?v={e['id']}"
        out.append(Track(url, e.get("title") or url, e.get("duration"), requester))
    return out


def _extract_exe(exe: str, target: str, playlist: bool, limit: int) -> dict:
    cmd = [exe, "--dump-single-json", "--flat-playlist", "--no-warnings", "--skip-download",
           "--yes-playlist" if playlist else "--no-playlist", "--playlist-end", str(limit), target]
    kw: dict = dict(capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        raise RuntimeError("yt-dlp failed: " + (tail[-1] if tail else f"exit code {r.returncode}"))
    return json.loads(r.stdout)


def _extract_module(target: str, playlist: bool, limit: int) -> dict:
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "noplaylist": not playlist, "playlistend": limit}
    with yt_dlp.YoutubeDL(opts) as y:
        return y.extract_info(target, download=False)


def remote_tracks(query: str, requester: str = "AutoDJ", playlist: bool = False, limit: int = 200,
                  ytdlp_path: str = "", mpv_path: str = "",
                  search_limit: int = 1) -> list[Track]:
    """YouTube URL, playlist URL, or plain search text -> tracks (metadata only).
    Uses the yt-dlp executable when there is one (see find_ytdlp), else the yt_dlp Python module.

    *search_limit*: for plain-text queries, how many YouTube results to fetch (default 1 keeps
    backward-compatible behaviour for non-admin !play).  Admins can pass a higher value to get a
    playlist of search results.
    """
    n = max(1, int(search_limit))
    target = query if is_url(query) else f"ytsearch{n}:{query}"
    exe = find_ytdlp(ytdlp_path, mpv_path)
    info = None
    if exe:
        try:
            info = _extract_exe(exe, target, playlist, limit)
        except OSError as ex:  # cannot even start it: try the module instead
            log.warning("cannot run %s (%s): trying the yt_dlp module", exe, ex)
    if info is None:
        try:
            info = _extract_module(target, playlist, limit)
        except ImportError:
            raise YtdlpMissing(
                "yt-dlp not found: there is no yt-dlp executable (next to mpv.exe or on PATH) and this build has no "
                "yt_dlp Python module. Put yt-dlp.exe next to mpv.exe (or set ytdlp_path in config.toml), "
                "or `pip install yt-dlp` and rebuild AutoDJ.exe.") from None
    return parse_ytdlp_info(info, requester)


class LocalIndex:
    def __init__(self, dirs):
        self.dirs = [Path(d).resolve() for d in dirs]
        self.tracks: list[Track] = []

    def scan(self):
        self.tracks = [t for d in self.dirs if d.is_dir() for t in scan_folder(d)]

    def find(self, query: str, requester: str) -> Track | None:
        words = query.lower().split()
        for t in self.tracks:
            hay = f"{t.title} {Path(t.source).name}".lower()
            if words and all(w in hay for w in words):
                return Track(t.source, t.title, t.duration, requester)
        return None

    def contains(self, path) -> bool:
        p = Path(path).resolve()
        return any(p.is_relative_to(d) for d in self.dirs)