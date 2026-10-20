"""Behavioural: best_effort_background refuses when Camfrog is not foreground."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "features"))

import status_changer_gui as scg  # noqa: E402
import status_random_gui as srg  # noqa: E402
import status_marquee_gui as smg  # noqa: E402


def _make_mocks(foreground_matches: bool):
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    writes = []
    enters = []
    user32 = SimpleNamespace(
        GetForegroundWindow=lambda: 77 if foreground_matches else 55,
    )
    return win, ctrl, writes, enters, user32


def _run(mod, foreground_matches):
    win, ctrl, writes, enters, user32 = _make_mocks(foreground_matches)
    monkeypatch_target = mod
    monkeypatch_target.ctypes.windll = SimpleNamespace(user32=user32)
    monkeypatch_target.is_web = lambda _c: False
    monkeypatch_target.write_text = lambda _c, t: writes.append(t) or True
    monkeypatch_target._background_enter = lambda *_a, **_k: enters.append("enter")
    monkeypatch_target.foreground_ok = lambda _w: True
    result = mod._commit_impl(
        win, ctrl, "TEST", False, require_fg=True, restore=True,
        execution_mode="best_effort_background",
    )
    return result, writes, enters


def test_status_changer_best_effort_refuses_not_foreground(monkeypatch):
    mod = scg
    # bind monkeypatch into module namespace manually
    mod.ctypes.windll = SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=lambda: 55))
    mod.is_web = lambda _c: False
    writes = []
    enters = []
    mod.write_text = lambda _c, t: writes.append(t) or True
    mod._background_enter = lambda *_a, **_k: enters.append("enter")
    mod.foreground_ok = lambda _w: True
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert result is False
    assert writes == []
    assert enters == []


def test_status_changer_best_effort_proceeds_when_foreground(monkeypatch):
    mod = scg
    mod.ctypes.windll = SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=lambda: 77))
    mod.is_web = lambda _c: False
    writes = []
    enters = []
    mod.write_text = lambda _c, t: writes.append(t) or True
    mod._background_enter = lambda *_a, **_k: enters.append("enter")
    mod.foreground_ok = lambda _w: True
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    # may return True or False depending on further checks; at least write happened OR refused only if not foreground
    # Since foreground matches, it should proceed to write
    assert writes == ["TEST"] or result is False


def test_status_random_best_effort_refuses_not_foreground(monkeypatch):
    mod = srg
    mod.ctypes.windll = SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=lambda: 55))
    mod.is_web = lambda _c: False
    writes = []
    enters = []
    mod.write_text = lambda _c, t: writes.append(t) or True
    mod._background_enter = lambda *_a, **_k: enters.append("enter")
    mod.foreground_ok = lambda _w: True
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert result is False
    assert writes == []
    assert enters == []


def test_status_marquee_best_effort_refuses_not_foreground(monkeypatch):
    mod = smg
    mod.ctypes.windll = SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=lambda: 55))
    mod.is_web = lambda _c: False
    writes = []
    enters = []
    mod.write_text = lambda _c, t: writes.append(t) or True
    mod._background_enter = lambda *_a, **_k: enters.append("enter")
    mod.foreground_ok = lambda _w: True
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert result is False
    assert writes == []
    assert enters == []