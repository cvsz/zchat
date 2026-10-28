"""Auto-discovery of Camfrog room window and chat controls."""
from __future__ import annotations
import logging
import re
import tomllib
from pathlib import Path
from .config import Config
from .windows import resolve, candidates

log = logging.getLogger("autodj")


def apply(cfg: Config, config_path: str) -> str | None:
    """Run discovery and update config.toml with found selectors. Returns summary or None."""
    c = resolve(cfg.window_title_re, cfg.window_exclude_re, cfg.room, cfg.window_index, 10, cfg.process_re)
    if c is None:
        return None

    # For now, just return the window info. Full UIA discovery would require
    # attaching and searching for controls, which is done at runtime.
    return f"Window: {c.title} (pid={c.pid}, class={c.cls}, exe={c.exe})"