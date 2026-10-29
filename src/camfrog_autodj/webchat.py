"""Web chat pane helpers for Camfrog's embedded browser (UIA)."""
from __future__ import annotations
import logging
import time

log = logging.getLogger("autodj")


def find_web_panes(win, view_title: str, input_title: str):
    """Find the chat history pane and message input pane by their title substrings.
    Returns (view_control, input_control) or (None, None) if not found."""
    try:
        panes = win.descendants(control_type="Pane")
    except Exception:
        return None, None
    view = inp = None
    for p in panes:
        try:
            name = p.window_text()
        except Exception:
            continue
        if view_title in name and view is None:
            view = p
        if input_title in name and inp is None:
            inp = p
        if view and inp:
            break
    return view, inp


def read_web(view, strict: bool = True) -> list[str]:
    """Extract chat lines from the web history pane."""
    try:
        children = view.descendants(control_type="Text")
    except Exception:
        return []
    out = []
    for c in children:
        try:
            t = c.window_text()
        except Exception:
            continue
        if not t:
            continue
        if strict and not (":" in t and len(t.split(":", 1)[0]) <= 32):
            continue
        out.append(t)
    return out