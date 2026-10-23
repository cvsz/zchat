"""Regression tests for the audit fixes (offline, no Windows)."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camfrog_auto as c  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    x = c.load_cfg(ROOT / "config.example.json")
    x["dry_run"] = (
        True  # tests must not depend on the user's live settings in the shipped config
    )
    x["status"]["enabled"] = True
    x["autoreply"]["enabled"] = True
    x["status"]["enabled"] = True
    x["autoreply"]["enabled"] = True
    x["autoreply"]["own_nickname"] = ""
    x["autoreply"]["rules"] = [{"pattern": "hello", "reply": "Hi {sender}"}]
    x["autoreply"]["skip_patterns"] = ["สมัครเว็บพนันฟรี", r"free money(?!less)"]
    return x


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    monkeypatch.setattr(
        c.time, "sleep", lambda *_a: None
    )  # no real reply delays in tests
    c.LANG = "en"


def test_full_build_source_clean_marker_fails_closed_and_checks_status_file():
    batch = (ROOT / "full-build.bat").read_text(encoding="utf-8")

    assert 'set "BUILD_SOURCE_CLEAN=false"' in batch
    assert (
        'git status --porcelain --untracked-files=normal > "%BUILD_STATUS_FILE%" 2>nul'
        in batch
    )
    assert "if errorlevel 1 goto :build_source_status_done" in batch
    assert (
        'for %%A in ("%BUILD_STATUS_FILE%") do if %%~zA EQU 0 set "BUILD_SOURCE_CLEAN=true"'
        in batch
    )
    assert 'for /f "delims=" %%i in (\'git status --porcelain' not in batch


def test_history_keeps_newcomer_when_full(tmp_path):
    h = c.StatusHistory(tmp_path / "h.json", 5)
    for i in range(5):
        h.record(f"s{i}")
    h.record("brand new")
    assert h.find("brand new")["count"] == 1
    assert len(h.items) == 5


def test_skip_patterns_catch_embedded_thai_and_english(cfg):
    sk = c.compile_skips(cfg["autoreply"])
    assert c.is_skipped("สมัครเว็บพนันฟรี", sk, True) == "pattern"
    assert c.is_skipped("get FREE MONEY now", sk, True) == "pattern"
    assert c.is_skipped("freemoneyless", sk, True) is None


def test_selector_clash_warns_in_dry_and_errors_live(cfg):
    cfg["status"]["edit"] = dict(
        cfg["autoreply"]["history"]
    )  # force a clash (new defaults are distinct)
    assert c.selector_clashes(cfg) == [("status.edit", "autoreply.history")]
    errs, warns = c.validate(cfg)
    assert not errs and any("identical" in w for w in warns)
    live = copy.deepcopy(cfg)
    live["dry_run"] = False
    errs, _ = c.validate(live)
    assert any("identical" in e for e in errs)
    live["autoreply"]["history"] = {"control_type": "Edit", "auto_id": "9", "index": 0}
    live["autoreply"]["input"] = {"control_type": "Edit", "auto_id": "8", "index": 0}
    assert c.selector_clashes(live) == []


@pytest.mark.parametrize(
    "path,val",
    [
        (("poll_seconds",), 0),
        (("poll_seconds",), -1),
        (("poll_seconds",), "x"),
        (("safety", "max_consecutive_failures"), 0),
        (("status", "max_length"), 0),
        (("autoreply", "max_reply_length"), 0),
        (("poll_seconds",), True),
    ],
)
def test_numeric_ranges(cfg, path, val):
    cfg = copy.deepcopy(cfg)
    d = cfg
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = val
    errs, _ = c.validate(cfg)
    assert errs


def test_clip_never_splits_thai_cluster():
    assert c.clip("เก", 1) == ""  # lead vowel + consonant stay together
    assert (
        c.clip("กี่กี่", 3) == "กี่"
    )  # consonant + vowel + tone (3 code points) stay together
    assert c.clip("กี่กี่", 2) == ""
    assert c.clip("abc", 2) == "ab" and c.clip("abc", 9) == "abc"
    assert c.safe_reply("เก" * 5, "x", 3) == "เก"


class _Ctrl:
    def __init__(self, text=""):
        self.t, self.keys = text, []

    def set_edit_text(self, v):
        self.t = v

    def window_text(self):
        return self.t

    def set_focus(self):
        pass

    def type_keys(self, k):
        self.keys.append(k)


class _Win:
    handle = 111

    def set_focus(self):
        pass


class _User32:
    def __init__(self, seq):
        self.seq = list(seq)

    def GetForegroundWindow(self):
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]


class _Dll:
    def __init__(self, seq):
        self.user32 = _User32(seq)


def test_commit_aborts_if_focus_stolen_before_enter(monkeypatch):
    # fg ok during foreground_ok(), stolen right before the keystroke
    # Simulate verified idle input; CI has no interactive Windows desktop.
    monkeypatch.setattr(c.USER_GUARD, "wait_until_idle", lambda: True)
    monkeypatch.setattr(c.ctypes, "windll", _Dll([111, 999]), raising=False)
    ctrl = _Ctrl()
    assert c.commit(_Win(), ctrl, "hi", False, True) is False
    assert ctrl.keys == []


def test_commit_sends_when_focus_stable(monkeypatch):
    # The desktop activity preflight is tested separately with injected clocks.
    monkeypatch.setattr(c.USER_GUARD, "wait_until_idle", lambda: True)
    monkeypatch.setattr(c.ctypes, "windll", _Dll([111]), raising=False)
    ctrl = _Ctrl()
    assert c.commit(_Win(), ctrl, "hi", False, True) is True
    assert ctrl.keys == ["{ENTER}"]


def test_pid_reuse_guard(monkeypatch):
    monkeypatch.setattr(c.os, "name", "nt")
    monkeypatch.setattr(c, "pid_creation_time", lambda _pid: 123456)
    assert c.pid_is_ours(1, r"C:\Windows\explorer.exe") is False
    monkeypatch.setattr(c.sys, "executable", r"C:\x\camfrog-auto.exe")
    assert c.pid_is_ours(1, r"C:\x\camfrog-auto.exe", 123456) is True
    monkeypatch.setattr(c.sys, "executable", "room-control.exe")
    assert c.pid_is_ours(1, "room-control.exe", 123456) is True
    monkeypatch.setattr(c.sys, "executable", r"C:\venv\Scripts\python.exe")
    monkeypatch.setattr(
        c.sys, "_base_executable", r"C:\Python313\python.exe", raising=False
    )
    assert c.pid_is_ours(1, r"C:\venv\Scripts\pythonw.exe", 123456) is True
    assert c.pid_is_ours(1, r"C:\Python313\pythonw.exe", 123456) is True
    assert c.pid_is_ours(1, r"C:\Python312\pythonw.exe", 123456) is False
    assert c.pid_is_ours(1, r"C:\venv\Scripts\python.exe", 654321) is False
    assert c.pid_is_ours(1, r"C:\venv\Scripts\python.exe") is False
    assert c.pid_is_ours(1, "") is False  # unknown -> fail closed


def test_running_pid_rejects_legacy_and_reused_pid_files(monkeypatch, tmp_path):
    cfg = {"safety": {"pid_file": "worker.pid"}}
    pidfile = tmp_path / "worker.pid"
    pidfile.write_text('{"pid":4242,"creation_time":100}', encoding="utf-8")
    monkeypatch.setattr(c, "BASE", tmp_path)
    monkeypatch.setattr(c.os, "name", "nt")
    monkeypatch.setattr(c.sys, "executable", r"C:\venv\Scripts\python.exe")
    monkeypatch.setattr(c, "pid_alive", lambda _pid: True)
    monkeypatch.setattr(c, "pid_image", lambda _pid: r"C:\venv\Scripts\python.exe")
    monkeypatch.setattr(c, "pid_creation_time", lambda _pid: 200)
    assert (
        c.running_pid(cfg) is None
    )  # same interpreter path, different process lifetime

    pidfile.write_text("4242", encoding="ascii")
    assert c.read_pid(cfg) == 4242
    assert c.running_pid(cfg) is None  # legacy records do not authorize a kill

    pidfile.write_text('{"pid":4242,"creation_time":200}', encoding="utf-8")
    assert c.running_pid(cfg) == 4242


class _ApiFunction:
    def __init__(self, result):
        self.result = result
        self.argtypes = None
        self.restype = None
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if callable(self.result):
            return self.result(*args)
        return self.result


def test_pid_alive_uses_pointer_sized_windows_handles(monkeypatch):
    import ctypes
    from ctypes import wintypes

    handle = 0x1234567887654321
    process_api = type("FakeKernel32", (), {})()
    process_api.OpenProcess = _ApiFunction(handle)

    def get_exit_code(actual_handle, code_pointer):
        assert actual_handle == handle
        ctypes.cast(code_pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = 259
        return 1

    process_api.GetExitCodeProcess = _ApiFunction(get_exit_code)
    process_api.GetProcessTimes = _ApiFunction(1)
    process_api.QueryFullProcessImageNameW = _ApiFunction(1)
    process_api.CloseHandle = _ApiFunction(1)
    monkeypatch.setattr(c.os, "name", "nt")
    monkeypatch.setattr(
        c.ctypes, "WinDLL", lambda *args, **kwargs: process_api, raising=False
    )

    assert c.pid_alive(4242) is True
    assert process_api.OpenProcess.restype is wintypes.HANDLE
    assert process_api.GetExitCodeProcess.calls[0][0] == handle
    assert process_api.CloseHandle.calls == [(handle,)]


def test_stop_does_not_force_kill_when_process_identity_changes(
    cfg, monkeypatch, tmp_path
):
    (tmp_path / cfg["safety"]["pid_file"]).write_text("4242")
    monkeypatch.setattr(c, "pid_alive", lambda p: True)
    checks = iter([True, False, False])
    monkeypatch.setattr(c, "pid_is_ours", lambda p, image=None: next(checks, False))
    called = []
    monkeypatch.setattr(c.subprocess, "run", lambda *a, **k: called.append(a))
    assert c.cmd_stop(cfg) == 0
    assert called == []


def test_stop_ignores_stranger_pid(cfg, monkeypatch, tmp_path):
    (tmp_path / cfg["safety"]["pid_file"]).write_text("4242")
    monkeypatch.setattr(c, "pid_alive", lambda p: True)
    monkeypatch.setattr(c, "pid_is_ours", lambda p, image=None: False)
    called = []
    monkeypatch.setattr(c.subprocess, "run", lambda *a, **k: called.append(a))
    assert c.cmd_stop(cfg) == 0
    assert called == []  # nothing killed
    assert not (tmp_path / cfg["safety"]["pid_file"]).exists()


def _runner(cfg, monkeypatch=None):
    r = c.Runner(cfg)
    r.history = _Ctrl()
    r.input = _Ctrl()
    r.win = _Win()
    r.dry = True
    return r


def test_backlog_burst_is_not_answered(cfg):
    r = _runner(cfg)
    r.own = "me"
    r.history.t = "\n".join(f"u{i}: hello" for i in range(c.RESET_BURST + 5))
    r.do_chat(True)
    assert r.sent == []
    r.history.t += "\nzed: hello"
    r.do_chat(True)
    assert len(r.sent) == 1


def test_hot_reload_keeps_rule_cooldown(cfg):
    r = _runner(cfg)
    r.rules[0]["last"] = 123.0
    r.apply_cfg(copy.deepcopy(cfg))
    assert r.rules[0]["last"] == 123.0
