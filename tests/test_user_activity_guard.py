"""The status/marquee senders must not take over the mouse/keyboard while the user is active."""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "features"))
sys.path.insert(0, str(ROOT / "src" / "camfrog"))

FILES = [
    "src/camfrog/camfrog_auto.py",
    "src/features/camfrog_music_gui.py",
    "src/features/camfrog_status_gui.py",
    "src/features/chat_im_private_gui.py",
    "src/features/im_autoreply_gui.py",
    "src/features/room_control_gui.py",
    "src/features/status_changer_gui.py",
    "src/features/status_marquee_gui.py",
    "src/features/status_random_gui.py",
]
BEGIN, END = "# --- user-activity guard", "# --- end user-activity guard ---"


def _block(path):
    s = (ROOT / path).read_text(encoding="utf-8")
    return s[s.index(BEGIN):s.index(END)]


def test_guard_block_identical_in_every_module():
    """All nine Guard blocks must be semantically identical (same AST).

    Formatting may differ (e.g. Ruff 0.17.2 reformatted camfrog_auto.py
    while the eight standalone GUI modules retain their previous layout),
    but the safety contract must be the same across every module. A raw
    text comparison is too brittle; an AST dump catches any real logic
    divergence while ignoring whitespace/style changes.
    """
    blocks = [_block(p) for p in FILES]
    dumps = [
        ast.dump(ast.parse(b), annotate_fields=False, include_attributes=False)
        for b in blocks
    ]
    assert len(set(dumps)) == 1, (
        "Guard block semantics differ across modules:\n"
        + "\n".join(f"  {p}: {d}" for p, d in zip(FILES, dumps))
    )


def _load_guard_class():
    from user_activity_guard import UserActivityGuard
    return {"UserActivityGuard": UserActivityGuard}


def _stub(real, sink):
    """Replacement for _commit_impl that keeps its signature (commit() binds against it)."""
    import functools

    @functools.wraps(real)
    def fake(*a, **k):
        sink.append((a, k))
        return True
    return fake


class Clock:
    def __init__(self):
        self.now = 10_000
        self.mono = 0.0

    def tick(self):
        return self.now & 0xFFFFFFFF

    def sleep(self, s):
        self.mono += s
        self.now += int(s * 1000)

    def clock(self):
        return self.mono


def make(ns, last, idle_ms=2000, max_wait=5.0):
    c = Clock()
    g = ns["UserActivityGuard"](idle_ms=idle_ms, poll=0.5, max_wait=max_wait, tick=c.tick,
                                last_input=lambda: last(c), sleep=c.sleep, clock=c.clock,
                                os=ns.get("os"))
    return g, c


def test_idle_user_is_not_blocked():
    ns = _load_guard_class()
    g, c = make(ns, lambda c: c.now - 5000)
    assert g.wait_until_idle() is True


def test_waits_until_user_stops_then_proceeds():
    ns = _load_guard_class()
    stop_at = 12_000
    g, c = make(ns, lambda c: c.now if c.now < stop_at else stop_at)
    assert g.wait_until_idle() is True
    assert c.now >= stop_at + 2000


def test_constantly_active_user_times_out_without_injecting():
    ns = _load_guard_class()
    g, c = make(ns, lambda c: c.now)
    assert g.wait_until_idle() is False


def test_own_injection_does_not_count_as_user_activity():
    ns = _load_guard_class()
    g, c = make(ns, lambda c: c.now - 100)  # fresh input every time
    g._user_tick = c.now - 60_000
    g.mark_own(c.now - 300, c.now)
    g._last_input = lambda: c.now - 100  # inside own window
    assert g.user_idle_ms() >= 60_000


def test_tick_wraparound():
    ns = _load_guard_class()
    g, c = make(ns, lambda c: 0xFFFFFFFF - 500)
    c.now = 1500  # wrapped
    assert g.user_idle_ms() == 2001


def test_unmeasurable_activity_is_noop_outside_windows():
    from types import SimpleNamespace

    ns = _load_guard_class()
    ns["os"] = SimpleNamespace(name="posix")
    g, c = make(ns, lambda c: None)
    assert g.wait_until_idle() is True


def test_unmeasurable_activity_fails_closed_on_windows():
    from types import SimpleNamespace

    ns = _load_guard_class()
    ns["os"] = SimpleNamespace(name="nt")
    g, c = make(ns, lambda c: None)
    assert g.wait_until_idle() is False


def test_disabled_with_zero():
    ns = _load_guard_class()
    g, c = make(ns, lambda c: c.now, idle_ms=0)
    assert g.wait_until_idle() is True


@pytest.mark.parametrize("path", FILES)
def test_commit_gates_only_global_input_paths(path):
    import importlib
    name = Path(path).stem
    try:
        mod = importlib.import_module(name)
    except ModuleNotFoundError as exc:  # tkinter missing in headless sandboxes
        pytest.skip(f"cannot import {name}: {exc}")
    calls = []
    waits = []
    mod._commit_impl_real = mod._commit_impl
    mod._commit_impl = _stub(mod._commit_impl_real, calls)

    class G:
        def wait_until_idle(self):
            waits.append(1)
            return True

        def run(self, f, *a, **k):
            return f(*a, **k)

    old = mod.USER_GUARD
    mod.USER_GUARD = G()
    try:
        ctrl = object()
        assert mod.commit(None, ctrl, "x", True)  # dry run: never gated
        assert not waits
        assert mod.commit(None, ctrl, "x", False, True)  # interactive: gated
        assert len(waits) == 1
        assert mod.commit(None, ctrl, "x", False, False)  # background post: not gated
        assert mod.commit(None, ctrl, "x", False, True, None, False, "combo")
        assert len(waits) == 1
    finally:
        mod.USER_GUARD = old
        mod._commit_impl = mod._commit_impl_real


def test_commit_refuses_when_user_stays_active(monkeypatch):
    import importlib
    try:
        mod = importlib.import_module("status_marquee_gui")
    except ModuleNotFoundError as exc:
        pytest.skip(str(exc))
    ran = []
    monkeypatch.setattr(mod, "_commit_impl", _stub(mod._commit_impl, ran))

    class Busy:
        def wait_until_idle(self):
            return False

    monkeypatch.setattr(mod, "USER_GUARD", Busy())
    assert mod.commit(None, object(), "x", False, True) is False
    assert not ran
    # background modes never need the desktop, so they are never deferred
    assert mod.commit(None, object(), "x", False, True, execution_mode="best_effort_background") is True
