"""Tk-free behavior tests for the compact Camfrog status changer."""
import ctypes
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camfrog_status_gui as gui  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features"))
import status_changer_gui as combined_gui  # noqa: E402


class _WindowsPlatformProxy:
    """Expose the Windows platform name while delegating other OS helpers."""

    def __init__(self, delegate):
        self._delegate = delegate

    @property
    def name(self):
        return "nt"

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def _enable_windows_mutex_api(monkeypatch):
    monkeypatch.setattr(gui, "os", _WindowsPlatformProxy(gui.os))
    monkeypatch.setattr(ctypes, "set_last_error", lambda _error: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)


def test_status_changer_has_ten_slots_and_saves_only_visible_slots():
    assert gui.STATUS_SLOTS == 10
    entries = [f"status {i}" for i in range(1, 11)]
    entries[1] = "ไทย || English"
    entries[3] = "  "
    messages = gui.messages_from_slots(entries)
    assert len(messages) == 9
    assert messages[0] == "status 1"
    assert messages[1] == {"th": "ไทย", "en": "English"}
    assert messages[-1] == "status 10"


def test_status_changer_parses_one_language_without_losing_it():
    assert gui.messages_from_slots([" ไทย || ", " || English "]) == ["ไทย", "English"]


def test_runtime_config_disables_autoreply_and_preserves_foreground_safety():
    base_cfg = {
        "autoreply": {"enabled": True},
        "autoreply_im": {"enabled": True},
        "stats": {"file": "stats.json"},
        "log": {"file": "log.txt"},
        "safety": {"require_foreground": True},
    }
    rt = gui.runtime_config(base_cfg)
    assert rt["autoreply"]["enabled"] is False
    assert rt["autoreply_im"]["enabled"] is False
    assert rt["safety"]["require_foreground"] is True
    assert rt["stats"]["file"] == "camfrog_status_changer_stats.json"
    assert rt["log"]["file"] == "camfrog_status_changer.log"
    # Ensure original config is not mutated
    assert base_cfg["autoreply"]["enabled"] is True
    assert base_cfg["safety"]["require_foreground"] is True


@pytest.mark.parametrize("mode", ["random", "marquee"])
def test_runtime_config_enforces_strict_background_for_each_worker_mode(mode):
    import copy

    for configured_mode in ("interactive", "strict_background"):
        cfg = copy.deepcopy(combined_gui.DEFAULTS)
        cfg["status"]["execution_mode"] = configured_mode
        runtime = combined_gui.runtime_config(cfg, mode)
        assert runtime["status"]["execution_mode"] == "strict_background"
        assert runtime["status"]["random"] is (mode == "random")
        assert runtime["status"]["marquee"]["enabled"] is (mode == "marquee")


def test_status_changer_worker_entry_forces_strict_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(
        combined_gui,
        "cli_main",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or 0,
    )
    assert combined_gui.main(["--worker", "--config", "legacy.json", "run"]) == 0
    assert calls == [
        (["--config", "legacy.json", "run"], {"force_strict_background": True})
    ]


def test_apply_status_text_forces_strict_background_for_legacy_config(tmp_path, monkeypatch):
    config_path = tmp_path / "camfrog-status-config.json"
    config_path.write_text(
        '{"dry_run": false, "safety": {"require_foreground": true}}',
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(gui, "commit_background",
                        lambda text, dry=False: calls.append((text, dry)) or True)

    assert gui.apply_status_text(config_path, "status text") == 0
    assert calls == [("status text", False)]


def test_standalone_defaults_to_strict_background():
    assert gui.DEFAULTS["status"]["execution_mode"] == "strict_background"
    runtime = gui.runtime_config({
        "status": {"execution_mode": "interactive"},
        "autoreply": {}, "stats": {}, "log": {}, "safety": {},
    })
    assert runtime["status"]["execution_mode"] == "strict_background"


def test_live_standalone_worker_fails_before_spawn_when_strict_submit_unverified(
        monkeypatch, tmp_path, capsys):
    _enable_windows_mutex_api(monkeypatch)
    monkeypatch.setattr(gui, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gui, "running_pid", lambda _cfg: None)
    monkeypatch.setitem(sys.modules, "camfrog_status_engine",
                        SimpleNamespace(strict_background_submit_supported=lambda: False))
    monkeypatch.setattr(gui.subprocess, "Popen",
                        lambda *args, **kwargs: pytest.fail("worker must not spawn"))
    cfg = {
        "dry_run": False,
        "status": {"enabled": True},
        "log": {"file": "worker.log"},
        "safety": {"stop_file": "stop", "pid_file": "pid"},
    }
    assert gui.cmd_start_worker(cfg, SimpleNamespace(config=None, lang=None)) == 2
    assert "BACKGROUND_UNSUPPORTED" in capsys.readouterr().out


def test_status_live_toggle_maps_to_dry_run_without_mutating_config():
    original = {"dry_run": True}

    preview = gui.status_config_for_live_send(original, False)
    live = gui.status_config_for_live_send(original, True)

    assert preview["dry_run"] is True
    assert live["dry_run"] is False
    assert original["dry_run"] is True


def test_text_to_message_and_message_to_text():
    assert gui.text_to_message("hello") == "hello"
    assert gui.text_to_message("  ") is None
    assert gui.text_to_message("th || en") == {"th": "th", "en": "en"}
    assert gui.text_to_message("th || ") == "th"
    assert gui.text_to_message(" || en") == "en"

    assert gui.message_to_text({"th": "th", "en": "en"}) == "th || en"
    assert gui.message_to_text("plain") == "plain"


def test_get_window_skips_own_tk_gui():
    """wins[0] may be our own GUI (topmost when Enable is clicked)."""
    from types import SimpleNamespace

    own = SimpleNamespace(class_name=lambda: "TkTopLevel")
    real = SimpleNamespace(class_name=lambda: "#32770")
    assert gui.is_own_gui_window(own) is True
    assert gui.is_own_gui_window(real) is False
    assert gui.is_own_gui_window(SimpleNamespace(class_name=lambda: (_ for _ in ()).throw(OSError()))) is False


def test_status_gui_own_window_titles_cover_random_and_marquee_when_class_lookup_fails():
    for title in (
        "ZChat Status Changer",
        "ZChat Random Status",
        "ZChat Marquee Status",
    ):
        own = SimpleNamespace(
            class_name=lambda: (_ for _ in ()).throw(OSError()),
            window_text=lambda title=title: title,
        )
        assert gui.is_own_gui_window(own) is True


def test_single_instance_guard_first_acquire_succeeds(monkeypatch):
    """First acquire() succeeds when mutex doesn't exist."""
    _enable_windows_mutex_api(monkeypatch)
    mock_kernel32 = type("K32", (), {
        "CreateMutexW": staticmethod(lambda *a, **k: 1),
        "CloseHandle": staticmethod(lambda *a: None),
    })
    mock_kernel32.CreateMutexW.argtypes = ()
    mock_kernel32.CreateMutexW.restype = ctypes.c_void_p
    mock_kernel32.CloseHandle.argtypes = ()
    mock_kernel32.CloseHandle.restype = ctypes.c_int
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: mock_kernel32, raising=False)

    guard = gui.SingleInstanceGuard("TestMutexFirst")
    assert guard.acquire() is True
    guard.release()


def test_single_instance_guard_blocks_second_instance(monkeypatch):
    """Second acquire() returns False when ERROR_ALREADY_EXISTS."""
    _enable_windows_mutex_api(monkeypatch)
    mock_kernel32 = type("K32", (), {
        "CreateMutexW": staticmethod(lambda *a, **k: 1),
        "CloseHandle": staticmethod(lambda *a: None),
    })
    mock_kernel32.CreateMutexW.argtypes = ()
    mock_kernel32.CreateMutexW.restype = ctypes.c_void_p
    mock_kernel32.CloseHandle.argtypes = ()
    mock_kernel32.CloseHandle.restype = ctypes.c_int
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: mock_kernel32, raising=False)
    errors = [0, 183]
    monkeypatch.setattr(ctypes, "get_last_error", lambda: errors.pop(0), raising=False)

    guard = gui.SingleInstanceGuard("TestMutexSecond")
    assert guard.acquire() is True
    assert guard.acquire() is False
    guard.release()


def test_single_instance_guard_non_windows_always_succeeds(monkeypatch):
    """On non-Windows, acquire() always returns True."""
    monkeypatch.setattr(os, "name", "posix")

    guard = gui.SingleInstanceGuard("TestMutexLinux")
    assert guard.acquire() is True
    guard.release()


def test_worker_cmd_runs_the_standalone_status_gui_in_worker_mode(monkeypatch, tmp_path):
    """Development mode relaunches this script and selects its bundled worker."""
    from types import SimpleNamespace
    monkeypatch.setattr(gui.sys, "frozen", False, raising=False)
    config_path = tmp_path / "camfrog-status-config.json"
    args = SimpleNamespace(config=str(config_path), lang="en")
    cmd = gui.worker_cmd(args)
    assert str(gui.Path(cmd[1]).resolve()) == str(gui.Path(gui.__file__).resolve())
    assert "--worker" in cmd
    assert cmd[cmd.index("--config") + 1] == str(config_path.resolve())
    assert cmd[cmd.index("--lang") + 1] == "en"
    assert not any(str(part).lower().endswith(("camfrog-auto.exe", "camfrog-auto-gui.exe"))
                   for part in cmd)


def test_worker_cmd_frozen_relaunches_the_same_executable(monkeypatch, tmp_path):
    from types import SimpleNamespace
    executable = tmp_path / "status-random.exe"
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(executable))
    config_path = tmp_path / "camfrog-status-config.json"
    args = SimpleNamespace(config=str(config_path), lang=None)
    cmd = gui.worker_cmd(args)
    assert cmd[0] == str(executable)
    assert "--worker" in cmd
    assert cmd[cmd.index("--config") + 1] == str(config_path.resolve())
    assert not any(str(part).lower().endswith(("camfrog-auto.exe", "camfrog-auto-gui.exe"))
                   for part in cmd)


def test_infinite_loop_validation_mirrors_core():
    import copy
    cfg = copy.deepcopy(gui.DEFAULTS)
    cfg["status"]["enabled"] = True
    cfg["status"]["messages"] = ["hello world, this is long enough"]
    cfg["status"]["marquee"]["enabled"] = True
    cfg["status"]["marquee"]["scroll"] = True
    cfg["status"]["marquee"]["width"] = 10
    cfg["status"]["marquee"]["infinite_loop"] = True
    cfg["status"]["marquee"]["cycles"] = 0  # ignored when infinite
    errs, _ = gui.validate(cfg)
    assert not [e for e in errs if "marquee" in e]
    cfg["status"]["marquee"]["infinite_loop"] = False
    errs, _ = gui.validate(cfg)
    assert any("marquee" in e for e in errs)


@pytest.mark.parametrize("module_name", ["camfrog_auto", "camfrog_status_gui"])
def test_experimental_combo_enter_targets_only_verified_parent(module_name, monkeypatch):
    module = __import__(module_name)
    sent = []
    user32 = SimpleNamespace(PostMessageW=lambda *args: sent.append(args) or 1)
    monkeypatch.setattr(module.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    combo = SimpleNamespace(class_name="CComboBoxTS", handle=77)
    edit = SimpleNamespace(handle=88, element_info=SimpleNamespace(parent=combo))

    module._background_enter(edit, "combo")
    assert sent == [(77, 0x0100, 0x0D, 0), (77, 0x0101, 0x0D, 0)]
    sent.clear()
    module._background_enter(edit)
    assert sent == [(88, 0x0100, 0x0D, 0), (88, 0x0101, 0x0D, 0)]

    sent.clear()
    edit.element_info.parent = SimpleNamespace(class_name="Edit", handle=99)
    with pytest.raises(LookupError, match="CComboBoxTS"):
        module._background_enter(edit, "combo")
    assert not sent


@pytest.mark.parametrize("module_name", ["camfrog_auto", "camfrog_status_gui"])
def test_combo_enter_fails_closed_when_parent_has_no_handle(module_name, monkeypatch):
    module = __import__(module_name)
    sent = []
    user32 = SimpleNamespace(PostMessageW=lambda *args: sent.append(args) or 1)
    monkeypatch.setattr(module.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    ctrl = SimpleNamespace(element_info=SimpleNamespace(parent=SimpleNamespace(
        class_name="CComboBoxTS", handle=0)))
    with pytest.raises(LookupError, match="no HWND"):
        module._background_enter(ctrl, "combo")
    assert not sent


@pytest.mark.parametrize("module_name", ["camfrog_auto", "camfrog_status_gui"])
def test_invalid_background_enter_target_is_rejected(module_name):
    import copy
    module = __import__(module_name)
    cfg = copy.deepcopy(module.DEFAULTS)
    cfg["status"]["messages"] = ["test"]
    cfg["status"]["background_enter_target"] = "other"
    errors, _ = module.validate(cfg)
    assert any("background_enter_target" in e for e in errors)


def test_runner_uses_combo_target_for_status_only(tmp_path, monkeypatch):
    import camfrog_auto as core
    import copy
    cfg = copy.deepcopy(core.DEFAULTS)
    cfg["status"]["messages"] = ["test"]
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["background_enter_target"] = "combo"
    monkeypatch.setattr(core, "BASE", tmp_path)
    runner = core.Runner(cfg)
    runner.win = object()
    runner.status_edit = object()
    calls = []
    monkeypatch.setattr(core, "commit", lambda *args: calls.append(args) or True)
    runner.send(runner.status_edit, "status")
    runner.send(object(), "chat", win=object())
    assert calls[0][-1] == "combo"
    assert calls[1][-1] == "edit"


def test_runner_never_uses_legacy_commit_for_strict_status(tmp_path, monkeypatch):
    import camfrog_auto as core
    import copy

    cfg = copy.deepcopy(core.DEFAULTS)
    cfg["status"]["messages"] = ["test"]
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["execution_mode"] = "strict_background"
    monkeypatch.setattr(core, "BASE", tmp_path)
    runner = core.Runner(cfg)
    runner.status_edit = object()
    strict_calls = []
    monkeypatch.setattr(core, "commit", lambda *args: pytest.fail("legacy commit used"))
    monkeypatch.setattr(core, "commit_strict_background_status",
                        lambda text, dry=False: strict_calls.append((text, dry)) or True)

    assert runner.send(runner.status_edit, "safe status") is True
    assert strict_calls == [("safe status", True)]


def test_cli_status_uses_strict_background_path(monkeypatch):
    import camfrog_auto as core
    import copy

    cfg = copy.deepcopy(core.DEFAULTS)
    cfg["status"]["execution_mode"] = "strict_background"
    calls = []
    monkeypatch.setattr(core, "commit_strict_background_status",
                        lambda text, dry=False: calls.append((text, dry)) or True)
    monkeypatch.setattr(core, "get_window",
                        lambda _cfg: pytest.fail("legacy window lookup used"))

    assert core.cmd_status(cfg, "safe status") == 0
    assert calls == [("safe status", True)]


def test_parse_interval_seconds_passes_through_valid_values():
    assert gui.parse_interval_seconds("30") == 30
    assert gui.parse_interval_seconds("90") == 90
    assert gui.parse_interval_seconds(" 600 ") == 600


def test_parse_interval_seconds_clamps_up_to_minimum():
    assert gui.parse_interval_seconds("0.1") == gui.RANDOM_INTERVAL_MIN
    assert gui.parse_interval_seconds("-5") == gui.RANDOM_INTERVAL_MIN
    assert gui.parse_interval_seconds("10") == 10


def test_parse_interval_seconds_clamps_down_to_maximum():
    assert gui.parse_interval_seconds("999999") == gui.RANDOM_INTERVAL_MAX
    assert gui.parse_interval_seconds("90") == 90


@pytest.mark.parametrize("bad", ["", "abc", "12.5.3", "nan", "inf", "-inf", None])
def test_parse_interval_seconds_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        gui.parse_interval_seconds(bad)


def test_parsed_interval_seconds_passes_validation():
    import copy
    cfg = copy.deepcopy(gui.DEFAULTS)
    cfg["status"]["enabled"] = True
    cfg["status"]["messages"] = ["hello world, this is long enough"]
    cfg["status"]["interval_seconds"] = gui.parse_interval_seconds("45")
    errs, _ = gui.validate(cfg)
    assert not [e for e in errs if "interval" in e]


def test_status_gui_marquee_defaults_to_per_line():
    assert gui.DEFAULTS["status"]["marquee"]["scroll"] is False


def test_status_gui_marquee_shape_skipped_when_scroll_off():
    import copy
    cfg = copy.deepcopy(gui.DEFAULTS)
    cfg["status"]["enabled"] = True
    cfg["status"]["messages"] = ["hello world, this is long enough"]
    cfg["status"]["marquee"]["enabled"] = True
    cfg["status"]["marquee"]["scroll"] = False
    cfg["status"]["marquee"]["width"] = 3
    errs, _ = gui.validate(cfg)
    assert not [e for e in errs if "marquee" in e]


@pytest.mark.parametrize("module_name", ["status_random_gui", "status_marquee_gui"])
def test_pid_and_stats_paths_match_between_gui_and_worker(module_name, tmp_path, monkeypatch):
    """GUI and worker must resolve PID/stats files identically.

    Regression: the worker is spawned with ZCFATO_DATA_DIR, which re-points
    BASE at DATA_DIR. Resolving these files under BASE split-brained them:
    the worker wrote one place, the GUI read another, so Start-verification,
    Stop, and refresh_state could never see the worker.
    """
    module = pytest.importorskip(module_name)
    exe_dir = tmp_path / "app"
    data_dir = exe_dir / "mode-data"
    data_dir.mkdir(parents=True)
    cfg = {"safety": {"pid_file": "w.pid"}, "stats": {"file": "s.json"}}
    monkeypatch.setattr(module, "DATA_DIR", data_dir)
    # GUI side: BASE is the exe dir.
    monkeypatch.setattr(module, "BASE", exe_dir)
    gui_pid, gui_stats = module.pid_path(cfg), module.stats_path(cfg)
    # Worker side: ZCFATO_DATA_DIR re-points BASE at DATA_DIR.
    monkeypatch.setattr(module, "BASE", data_dir)
    assert module.pid_path(cfg) == gui_pid == data_dir / "w.pid"
    assert module.stats_path(cfg) == gui_stats == data_dir / "s.json"


def test_cmd_status_strict_mode_skips_legacy_window_resolution(monkeypatch):
    import copy

    cfg = copy.deepcopy(gui.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"]["execution_mode"] = "strict_background"

    def _fail_window(_cfg):
        raise AssertionError("legacy get_window must not run in strict mode")

    def _fail_find(_win, _spec):
        raise AssertionError("legacy find must not run in strict mode")

    monkeypatch.setattr(gui, "get_window", _fail_window)
    monkeypatch.setattr(gui, "find", _fail_find)

    assert gui.cmd_status(cfg, "hello strict") == 0
