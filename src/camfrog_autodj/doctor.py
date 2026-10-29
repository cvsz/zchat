"""Setup verification checks."""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config
from .library import find_ytdlp

log = logging.getLogger("autodj")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


def run_checks(cfg: Config) -> list[CheckResult]:
    out = []

    # mpv
    mpv_ok = shutil.which(cfg.mpv_path) is not None
    out.append(CheckResult("mpv", mpv_ok,
                           f"mpv_path = {cfg.mpv_path!r}" if mpv_ok else f"mpv not found at {cfg.mpv_path!r}",
                           "Install mpv and set mpv_path in config.toml"))

    # yt-dlp
    ytdlp = find_ytdlp(cfg.ytdlp_path, cfg.mpv_path)
    out.append(CheckResult("yt-dlp", bool(ytdlp),
                           f"found at {ytdlp}" if ytdlp else "not found (next to mpv, on PATH, or bundled)",
                           "Put yt-dlp.exe next to mpv.exe, add to PATH, or `pip install yt-dlp`"))

    # config file
    cfg_exists = Path(cfg).is_file() if isinstance(cfg, (str, Path)) else True
    out.append(CheckResult("config.toml", cfg_exists,
                           "found" if cfg_exists else "missing (using defaults)",
                           "Copy config.example.toml to config.toml"))

    # music directories
    if cfg.music_dirs:
        all_ok = True
        details = []
        for d in cfg.music_dirs:
            p = Path(d)
            ok = p.is_dir()
            all_ok = all_ok and ok
            details.append(f"{d} {'OK' if ok else 'MISSING'}")
        out.append(CheckResult("music_dirs", all_ok, "; ".join(details),
                               "Set music_dirs to existing folders"))

    # autoplay sources
    if cfg.autoplay_sources:
        out.append(CheckResult("autoplay_sources", True,
                               f"{len(cfg.autoplay_sources)} source(s) configured",
                               ""))

    # playlist file
    pf = getattr(cfg, "playlist_file", "")
    if pf:
        p = Path(pf)
        out.append(CheckResult("playlist_file", p.parent.exists(),
                               f"{pf} {'exists' if p.exists() else 'will be created'}",
                               "Ensure parent folder exists"))

    # Camfrog window (UIA only)
    if "uia" in (cfg.chat_reader, cfg.chat_sender):
        from .windows import resolve
        c = resolve(cfg.window_title_re, cfg.window_exclude_re, cfg.room, cfg.window_index, 5, cfg.process_re)
        out.append(CheckResult("Camfrog window", c is not None,
                               f"found: {c.title}" if c else f"no window matches {cfg.window_title_re!r}",
                               "Open the room window (not minimized to tray)"))

    return out


def format_report(results: list[CheckResult]) -> str:
    lines = ["AutoDJ setup check", "=" * 40]
    for r in results:
        status = "OK" if r.ok else "FAIL"
        lines.append(f"  [{status}] {r.name}: {r.detail}")
        if r.fix and not r.ok:
            lines.append(f"       Fix: {r.fix}")
    return "\n".join(lines)


def has_failures(results: list[CheckResult]) -> bool:
    return any(not r.ok for r in results)