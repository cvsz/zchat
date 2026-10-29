"""Window discovery and resolution helpers."""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("autodj")


@dataclass
class Candidate:
    handle: int
    title: str
    cls: str
    pid: int
    exe: str


def _get_desktop():
    """Get UIA Desktop, only available on Windows with pywinauto."""
    try:
        from pywinauto import Desktop
        return Desktop(backend="uia")
    except ImportError:
        raise RuntimeError("pywinauto not available (Windows only)")


def list_all() -> list[Candidate]:
    out = []
    desktop = _get_desktop()
    for w in desktop.windows():
        try:
            pid = w.process_id()
            import psutil
            exe = psutil.Process(pid).name()
        except Exception:
            exe = ""
        out.append(Candidate(
            handle=int(w.handle),
            title=w.window_text(),
            cls=w.class_name(),
            pid=pid,
            exe=exe,
        ))
    return out


def candidates(title_re: str, visible_only: bool = True) -> list[Candidate]:
    rx = re.compile(title_re)
    res = []
    for c in list_all():
        if visible_only and not c.title:
            continue
        if rx.search(c.title):
            res.append(c)
    return res


def resolve(
    title_re: str,
    exclude_re: str,
    room: str,
    index: int,
    timeout: int,
    process_re: str,
) -> Optional[Candidate]:
    """Find the best matching window, retrying for up to `timeout` seconds."""
    import psutil
    import time

    title_rx = re.compile(title_re)
    exclude_rx = re.compile(exclude_re) if exclude_re else None
    proc_rx = re.compile(process_re) if process_re else None

    def score(c: Candidate) -> int:
        s = 0
        if room and room.lower() in c.title.lower():
            s += 100
        if proc_rx and proc_rx.search(c.exe):
            s += 10
        if c.title:
            s += 1
        return s

    end = time.time() + timeout
    while time.time() < end:
        cs = [c for c in list_all() if title_rx.search(c.title)]
        if exclude_rx:
            cs = [c for c in cs if not exclude_rx.search(c.title)]
        if proc_rx:
            cs = [c for c in cs if proc_rx.search(c.exe)]
        if cs:
            cs.sort(key=score, reverse=True)
            if 0 <= index < len(cs):
                return cs[index]
            return cs[0]
        time.sleep(0.5)
    return None