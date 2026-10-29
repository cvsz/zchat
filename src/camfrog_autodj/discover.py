"""Auto-discovery of Camfrog room window and chat controls."""
from __future__ import annotations
import logging
import re
import tomllib
import tomli_w
from pathlib import Path
from .config import Config
from .windows import resolve, candidates

log = logging.getLogger("autodj")


def _find_controls(win, ctype):
    """Find all controls of a given type."""
    try:
        return win.descendants(control_type=ctype)
    except Exception as e:
        log.debug("failed to enumerate %s controls: %s", ctype, e)
        return []


def _score_view_control(ctrl):
    """Score a control for suitability as chat history view."""
    try:
        text = ctrl.window_text()
        if not text:
            return 0
        # Prefer controls with substantial text content
        score = min(len(text), 100)
        # Prefer controls that look like chat logs (have timestamps/nicks)
        if re.search(r'\d{1,2}:\d{2}.*:', text):
            score += 50
        return score
    except Exception:
        return 0


def _score_input_control(ctrl):
    """Score a control for suitability as message input box."""
    try:
        vp = ctrl.iface_value
        if not ctrl.iface.CurrentIsEnabled:
            return 0
        if vp.CurrentIsReadOnly:
            return 0
        # Prefer enabled, writable Edit controls
        return 100
    except Exception:
        return 0


def discover_controls(cfg: Config) -> dict | None:
    """Discover chat controls in the Camfrog window."""
    from pywinauto import Desktop

    c = resolve(cfg.window_title_re, cfg.window_exclude_re, cfg.room, cfg.window_index, 10, cfg.process_re)
    if c is None:
        log.warning("no window matches for discovery")
        return None

    win = Desktop(backend="uia").window(handle=c.handle)

    # Discover view/history controls
    view_controls = _find_controls(win, cfg.view_type)
    if not view_controls:
        log.warning("no %s controls found for view", cfg.view_type)
        return None

    best_view_idx = None
    best_view_score = -1
    for i, ctrl in enumerate(view_controls):
        score = _score_view_control(ctrl)
        if score > best_view_score:
            best_view_score = score
            best_view_idx = i

    if best_view_idx is None:
        log.warning("no suitable view control found")
        return None

    # Discover input controls
    input_controls = _find_controls(win, cfg.input_type)
    if not input_controls:
        log.warning("no %s controls found for input", cfg.input_type)
        return None

    best_input_idx = None
    best_input_score = -1
    for i, ctrl in enumerate(input_controls):
        score = _score_input_control(ctrl)
        if score > best_input_score:
            best_input_score = score
            best_input_idx = i

    if best_input_idx is None:
        log.warning("no suitable input control found")
        return None

    return {
        "view_type": cfg.view_type,
        "view_index": best_view_idx,
        "input_type": cfg.input_type,
        "input_index": best_input_idx,
    }


def apply(cfg: Config, config_path: str) -> str | None:
    """Run discovery and update config.toml with found selectors. Returns summary or None."""
    c = resolve(cfg.window_title_re, cfg.window_exclude_re, cfg.room, cfg.window_index, 10, cfg.process_re)
    if c is None:
        return None

    discovered = discover_controls(cfg)
    if discovered is None:
        return "discovery failed: no suitable controls found"

    # Update config.toml
    path = Path(config_path)
    if not path.exists():
        log.warning("config file %s does not exist, creating from defaults", config_path)
        data = {}
    else:
        try:
            data = tomllib.loads(path.read_text("utf-8-sig"))
        except Exception as e:
            log.error("failed to parse config.toml: %s", e)
            data = {}

    data.update(discovered)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")

    return (f"Discovered controls: view={discovered['view_type']}[{discovered['view_index']}], "
            f"input={discovered['input_type']}[{discovered['input_index']}]. "
            f"Updated {config_path}")