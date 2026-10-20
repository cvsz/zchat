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


def _setup(mod, monkeypatch, foreground_matches):
    writes = []
    enters = []
    user32 = SimpleNamespace(GetForegroundWindow=lambda: 77 if foreground_matches else 55)
    monkeypatch.setattr(mod.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(mod, "is_web", lambda _c: False)
    monkeypatch.setattr(mod, "write_text", lambda _c, t: writes.append(t) or True, raising=False)
    monkeypatch.setattr(mod, "_background_enter", lambda *_a, **_k: enters.append("enter") or None, raising=False)
    monkeypatch.setattr(mod, "foreground_ok", lambda _w: True, raising=False)
    return writes, enters


def test_status_changer_best_effort_refuses_not_foreground(monkeypatch):
    mod = scg
    writes, enters = _setup(mod, monkeypatch, False)
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert result is False
    assert writes == []
    assert enters == []


def test_status_changer_best_effort_proceeds_when_foreground(monkeypatch):
    mod = scg
    writes, enters = _setup(mod, monkeypatch, True)
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert writes == ["TEST"]
    assert enters == ["enter"]


def test_status_random_best_effort_refuses_not_foreground(monkeypatch):
    mod = srg
    writes, enters = _setup(mod, monkeypatch, False)
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert result is False
    assert writes == []
    assert enters == []


def test_status_random_best_effort_proceeds_when_foreground(monkeypatch):
    mod = srg
    writes, enters = _setup(mod, monkeypatch, True)
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert writes == ["TEST"]
    assert enters == ["enter"]


def test_status_marquee_best_effort_refuses_not_foreground(monkeypatch):
    mod = smg
    writes, enters = _setup(mod, monkeypatch, False)
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert result is False
    assert writes == []
    assert enters == []


def test_status_marquee_best_effort_proceeds_when_foreground(monkeypatch):
    mod = smg
    writes, enters = _setup(mod, monkeypatch, True)
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(handle=88)
    result = mod._commit_impl(win, ctrl, "TEST", False, require_fg=True, restore=True, execution_mode="best_effort_background")
    assert writes == ["TEST"]
    assert enters == ["enter"]
