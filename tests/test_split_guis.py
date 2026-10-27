"""Self-containment + behavior tests for the per-feature GUI modules.

Each module must stand alone: no imports of sibling project modules, its own
engine copy, and its own profile/mode pinning.
"""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config.example.json"

MODULES = {
    "room_control_gui": ("room", None),
    "im_autoreply_gui": ("im_reply", None),
    "camfrog_music_gui": ("music", None),
    "status_random_gui": ("status", "random"),
    "status_marquee_gui": ("status", "marquee"),
    "status_changer_gui": ("status", "both"),
    "chat_im_private_gui": (None, None),
    "web_status_gui": (None, None),
}


def load(name):
    return __import__(name)


@pytest.mark.parametrize("name", ["camfrog_auto", "im_autoreply_gui"])
def test_im_reply_requires_new_outgoing_echo(name, monkeypatch):
    mod = load(name)
    previous = ["zdevz: hello", "Seaza: [auto] old reply"]
    snapshots = iter([
        "zdevz: hello\nSeaza: [auto] old reply\nSeaza: [auto] hello zdevz",
    ])
    monkeypatch.setattr(mod, "read_chat", lambda *_args: next(snapshots))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    assert mod.confirm_im_reply_echo(
        object(), previous, "Seaza", "[auto] hello zdevz", timeout=0.1)


@pytest.mark.parametrize("name", ["camfrog_auto", "im_autoreply_gui"])
def test_im_reply_does_not_confirm_old_echo(name, monkeypatch):
    mod = load(name)
    previous = ["zdevz: hello", "Seaza: [auto] hello zdevz"]
    monkeypatch.setattr(
        mod, "read_chat", lambda *_args: "\n".join(previous))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    assert not mod.confirm_im_reply_echo(
        object(), previous, "Seaza", "[auto] hello zdevz", timeout=0.01)


@pytest.mark.parametrize("name", ["camfrog_auto", "im_autoreply_gui"])
def test_im_worker_mutex_blocks_duplicate_instance(name, tmp_path, monkeypatch):
    mod = load(name)
    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod, "acquire_run_mutex", lambda: False)
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: None)
    monkeypatch.setattr(mod, "Runner", lambda *_args: pytest.fail(
        "duplicate worker must not start"))

    assert mod.run_guarded({}, None) == 3


def test_im_state_counts_im_replies(tmp_path, monkeypatch, capsys):
    mod = load("im_autoreply_gui")
    monkeypatch.setattr(mod, "BASE", tmp_path)
    stats = copy.deepcopy(mod.DEFAULTS.get("stats", {}))
    stats["file"] = "stats.json"
    config = copy.deepcopy(mod.DEFAULTS)
    config["stats"] = stats
    (tmp_path / "stats.json").write_text(
        json.dumps({"replies": 0, "im_replies": 1}), encoding="utf-8")

    assert mod.cmd_state(config) == 1
    assert "replies 1" in capsys.readouterr().out


def test_im_activity_log_uses_app_log_path_and_redacts_credentials(tmp_path, monkeypatch):
    mod = load("im_autoreply_gui")
    monkeypatch.setattr(mod, "BASE", tmp_path)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["log"]["file"] = "im-autoreply.log"

    assert mod.activity_log_path(cfg) == tmp_path / "im-autoreply.log"
    detail = mod.activity_detail(
        'PHPSESSID=abc123 Cookie: session=private Authorization: Bearer abc.def token="xyz"')
    assert "abc123" not in detail
    assert "private" not in detail
    assert "abc.def" not in detail
    assert "xyz" not in detail
    assert "[REDACTED]" in detail

    cfg["log"]["file"] = ""
    assert mod.activity_log_path(cfg) is None


@pytest.mark.parametrize("name", ["camfrog_auto", "im_autoreply_gui"])
def test_start_reports_success_only_after_worker_pid_is_registered(
        name, tmp_path, monkeypatch, capsys):
    mod = load(name)
    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(mod, "child_cmd", lambda _args: ["pythonw.exe", "entry.py"])
    monkeypatch.setattr(mod, "child_env", lambda: {})
    states = iter([None, None, 123])
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: next(states))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    class Child:
        pid = 123
        returncode = None

        @staticmethod
        def poll():
            return None

    child = Child()
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *_args, **_kwargs: child)
    config = copy.deepcopy(mod.DEFAULTS)
    config["log"]["file"] = "worker.log"

    assert mod.cmd_start(config, SimpleNamespace(config=None, lang=None)) == 0
    assert "started in background (PID 123)" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["camfrog_auto", "im_autoreply_gui"])
def test_duplicate_start_terminates_unregistered_child(name, tmp_path, monkeypatch, capsys):
    mod = load(name)
    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod, "child_cmd", lambda _args: ["pythonw.exe", "entry.py"])
    monkeypatch.setattr(mod, "child_env", lambda: {})
    states = iter([None, 999])
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: next(states))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    class Child:
        pid = 123
        returncode = None
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        @staticmethod
        def wait(timeout):
            return 0

    child = Child()
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *_args, **_kwargs: child)
    config = copy.deepcopy(mod.DEFAULTS)
    config["log"]["file"] = "worker.log"

    assert mod.cmd_start(config, SimpleNamespace(config=None, lang=None)) == 1
    assert child.terminated
    assert "already running (PID 999)" in capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(MODULES))
def test_module_has_no_project_imports(name):
    for location in ("src/features", "src/camfrog"):
        candidate = ROOT / location / f"{name}.py"
        if candidate.is_file():
            src = candidate.read_text(encoding="utf-8")
            break
    else:
        raise AssertionError(f"{name}: module file not found under src/")
    banned = ("import camfrog_auto", "import camfrog_gui", "import camfrog_status_gui",
              "import camfrog_private_chat_gui", "import room_control_gui",
              "import im_autoreply_gui", "import camfrog_music_gui",
              "import status_random_gui", "import status_marquee_gui",
              "import status_changer_gui",
              "import chat_im_private_gui", "import web_status_gui",
              "from tools", "import tools", "import clipboard_support",
              "from clipboard_support")
    for bad in banned:
        assert bad not in src, f"{name}: {bad}"




@pytest.mark.parametrize(
    "name",
    [
        "camfrog_status_gui",
        "room_control_gui",
        "im_autoreply_gui",
        "camfrog_music_gui",
        "status_random_gui",
        "status_marquee_gui",
        "status_changer_gui",
        "chat_im_private_gui",
    ],
)
def test_standalone_engines_fail_closed_across_multiple_camfrog_processes(name):
    mod = load(name)
    windows = [
        SimpleNamespace(process_id=lambda: 101),
        SimpleNamespace(process_id=lambda: 202),
    ]

    with pytest.raises(RuntimeError, match="processes match"):
        mod._require_single_window_process(windows)

def test_profiles_and_modes_are_pinned():
    assert load("room_control_gui").APP_PROFILE == "room"
    assert load("im_autoreply_gui").APP_PROFILE == "im_reply"
    assert load("camfrog_music_gui").APP_PROFILE == "music"
    assert load("status_random_gui").STATUS_MODE == "random"
    assert load("status_marquee_gui").STATUS_MODE == "marquee"
    assert load("status_changer_gui").STATUS_MODE == "both"
    assert load("status_random_gui").runtime_config(
        load("status_random_gui").DEFAULTS)["status"]["random"] is True
    assert load("status_marquee_gui").runtime_config(
        load("status_marquee_gui").DEFAULTS)["status"]["marquee"]["enabled"] is True
    assert load("status_random_gui").runtime_config(
        load("status_random_gui").DEFAULTS)["status"]["execution_mode"] == "strict_background"
    assert load("status_marquee_gui").runtime_config(
        load("status_marquee_gui").DEFAULTS)["status"]["execution_mode"] == "strict_background"
    assert load("status_random_gui").SingleInstanceGuard().name != load(
        "status_marquee_gui").SingleInstanceGuard().name
    assert load("status_random_gui").SingleInstanceGuard().window_title == "ZChat Random Status"
    assert load("status_marquee_gui").SingleInstanceGuard().window_title == "ZChat Marquee Status"
    combined = load("status_changer_gui")
    assert combined.runtime_config(combined.DEFAULTS, "random")["status"]["random"] is True
    assert combined.runtime_config(
        combined.DEFAULTS, "marquee")["status"]["marquee"]["enabled"] is True


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
@pytest.mark.parametrize("entry", ["cmd_start", "cmd_start_worker"])
def test_split_status_start_waits_for_worker_pid_registration(
    name, entry, monkeypatch, tmp_path
):
    mod = load(name)
    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(mod, "_strict_status_start_blocked", lambda *_a, **_k: False)
    monkeypatch.setattr(mod, "child_env", lambda: {})
    monkeypatch.setattr(mod, "child_cmd", lambda _args: ["status-worker.exe"])
    monkeypatch.setattr(mod, "worker_cmd", lambda _args: ["status-worker.exe", "--worker"])
    states = iter([None, None, 321, 321])
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: next(states))
    monkeypatch.setattr(mod, "read_pid", lambda _cfg: 321)
    monkeypatch.setattr(mod, "pid_alive", lambda _pid: False)
    clock = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    class Child:
        pid = 321
        returncode = None

        @staticmethod
        def poll():
            return None

    child = Child()
    launch = {}

    def start_child(*args, **kwargs):
        launch.update(kwargs)
        return child

    monkeypatch.setattr(mod.subprocess, "Popen", start_child)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    args = SimpleNamespace(config=None, lang=None)
    messages = []

    if entry == "cmd_start":
        rc = mod.cmd_start(cfg, args)
    else:
        rc = mod.cmd_start_worker(cfg, args, report=messages.append)

    assert rc == 0
    assert clock[0] >= 0.1
    assert "PID 321" in ("\n".join(messages) if messages else "") or entry == "cmd_start"
    gate = mod.worker_start_gate_path(cfg)
    assert gate.exists()
    assert launch["env"]["ZCFATO_WORKER_START_GATE"] == str(gate)


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
@pytest.mark.parametrize("entry", ["cmd_start", "cmd_start_worker"])
def test_split_status_accepts_registered_inner_pid_from_onefile_launcher(
    name, entry, monkeypatch, tmp_path, capsys
):
    mod = load(name)
    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(mod, "_strict_status_start_blocked", lambda *_a, **_k: False)
    monkeypatch.setattr(mod, "child_env", lambda: {})
    monkeypatch.setattr(mod, "child_cmd", lambda _args: ["status-worker.exe"])
    monkeypatch.setattr(mod, "worker_cmd", lambda _args: ["status-worker.exe", "--worker"])
    states = iter([None, 654, 654])
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: next(states))
    monkeypatch.setattr(mod, "read_pid", lambda _cfg: 654)
    monkeypatch.setattr(mod, "pid_alive", lambda _pid: False)

    class Launcher:
        pid = 321
        returncode = None

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *_a, **_k: Launcher())
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    args = SimpleNamespace(config=None, lang=None)
    reports = []
    if entry == "cmd_start_worker":
        rc = mod.cmd_start_worker(cfg, args, report=reports.append)
        output = "\n".join(reports)
    else:
        rc = mod.cmd_start(cfg, args)
        output = capsys.readouterr().out

    assert rc == 0
    assert "PID 654" in output


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
@pytest.mark.parametrize("entry", ["cmd_start", "cmd_start_worker"])
def test_split_status_accepts_registration_after_launcher_exits(
    name, entry, monkeypatch, tmp_path, capsys
):
    mod = load(name)
    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(mod, "_strict_status_start_blocked", lambda *_a, **_k: False)
    monkeypatch.setattr(mod, "child_env", lambda: {})
    monkeypatch.setattr(mod, "child_cmd", lambda _args: ["status-worker.exe"])
    monkeypatch.setattr(mod, "worker_cmd", lambda _args: ["status-worker.exe", "--worker"])
    clock = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(
        mod, "running_pid", lambda _cfg: 654 if clock[0] >= 0.5 else None
    )
    monkeypatch.setattr(mod, "read_pid", lambda _cfg: 654)
    monkeypatch.setattr(mod, "pid_alive", lambda _pid: False)

    class ExitedLauncher:
        pid = 321
        returncode = 0

        @staticmethod
        def poll():
            return 0

    monkeypatch.setattr(
        mod.subprocess, "Popen", lambda *_args, **_kwargs: ExitedLauncher()
    )
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    args = SimpleNamespace(config=None, lang=None)
    reports = []

    if entry == "cmd_start":
        rc = mod.cmd_start(cfg, args)
        output = capsys.readouterr().out
    else:
        rc = mod.cmd_start_worker(cfg, args, report=reports.append)
        output = "\n".join(reports)

    assert rc == 0
    assert clock[0] >= 0.5
    assert "PID 654" in output


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
@pytest.mark.parametrize("entry", ["cmd_start", "cmd_start_worker"])
def test_split_status_start_stops_child_that_never_registers_pid(
    name, entry, monkeypatch, tmp_path, capsys
):
    mod = load(name)
    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(mod, "_strict_status_start_blocked", lambda *_a, **_k: False)
    monkeypatch.setattr(mod, "child_env", lambda: {})
    monkeypatch.setattr(mod, "child_cmd", lambda _args: ["status-worker.exe"])
    monkeypatch.setattr(mod, "worker_cmd", lambda _args: ["status-worker.exe", "--worker"])
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: None)
    monkeypatch.setattr(mod, "system_taskkill_path", lambda: "taskkill.exe")
    monkeypatch.setattr(mod.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0))
    clock = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    class Child:
        pid = 321
        returncode = None
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    child = Child()
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *_a, **_k: child)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    args = SimpleNamespace(config=None, lang=None)
    messages = []

    if entry == "cmd_start":
        rc = mod.cmd_start(cfg, args)
    else:
        rc = mod.cmd_start_worker(cfg, args, report=messages.append)

    assert rc == 1
    assert child.terminated is True
    assert clock[0] >= mod.WORKER_START_TIMEOUT_SECONDS
    output = "\n".join(messages)
    assert "did not register its PID" in (output or capsys.readouterr().out)


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
def test_split_status_worker_entry_forces_strict_background(name, monkeypatch):
    mod = load(name)
    calls = []
    monkeypatch.setattr(
        mod, "cli_main", lambda argv, **kwargs: calls.append((argv, kwargs)) or 0
    )
    assert mod.main(["--worker", "--config", "legacy.json", "run"]) == 0
    assert calls == [
        (["--config", "legacy.json", "run"], {"force_strict_background": True})
    ]


@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_worker_start_gate_releases_only_after_parent_authorization(
    name, monkeypatch, tmp_path
):
    mod = load(name)
    gate = tmp_path / "worker.start"
    monkeypatch.setenv("ZCFATO_WORKER_START_GATE", str(gate))
    monkeypatch.setattr(mod, "WORKER_START_GATE_TIMEOUT_SECONDS", 1)
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 0.1:
            gate.write_text("start\n", encoding="ascii")

    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mod.time, "sleep", sleep)

    assert mod.wait_for_worker_start_gate() is True
    assert not gate.exists()


@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_worker_start_gate_times_out_without_authorization(name, monkeypatch, tmp_path):
    mod = load(name)
    gate = tmp_path / "worker.start"
    monkeypatch.setenv("ZCFATO_WORKER_START_GATE", str(gate))
    monkeypatch.setattr(mod, "WORKER_START_GATE_TIMEOUT_SECONDS", 0.2)
    clock = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    assert mod.wait_for_worker_start_gate() is False
    assert not gate.exists()


@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_run_guarded_does_not_enter_runner_without_start_authorization(
    name, monkeypatch, tmp_path
):
    mod = load(name)
    import camfrog_status_engine as engine

    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "_strict_status_start_blocked", lambda *_a, **_k: False)
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: None)
    monkeypatch.setattr(mod, "WORKER_START_GATE_TIMEOUT_SECONDS", 0.2)
    gate = tmp_path / "worker.start"
    monkeypatch.setenv("ZCFATO_WORKER_START_GATE", str(gate))
    clock = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    class WriterLock:
        def __init__(self, **_kwargs):
            self.released = False

        def acquire(self):
            return True

        def release(self):
            self.released = True

    lock = WriterLock()
    monkeypatch.setattr(engine, "StatusResourceLock", lambda **_kwargs: lock)
    monkeypatch.setattr(
        mod, "Runner", lambda *_a, **_k: pytest.fail("Runner must remain gated")
    )

    result = mod.run_guarded(copy.deepcopy(mod.DEFAULTS))

    assert result == 4
    assert not gate.exists()
    assert mod.read_pid(copy.deepcopy(mod.DEFAULTS)) is None
    assert lock.released is True


@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_status_workers_hold_one_shared_writer_lock_for_their_lifecycle(
    name, monkeypatch, tmp_path
):
    mod = load(name)
    events = []

    class WriterLock:
        def __init__(self, resource_key, timeout_seconds):
            events.append(("lock", resource_key, timeout_seconds))

        def acquire(self):
            events.append("acquired")
            return True

        def release(self):
            events.append("released")

    monkeypatch.setitem(
        sys.modules,
        "camfrog_status_engine",
        SimpleNamespace(StatusResourceLock=WriterLock),
    )
    pid_file = tmp_path / f"{name}.pid"
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: None)
    monkeypatch.setattr(mod, "pid_path", lambda _cfg: pid_file)
    monkeypatch.setattr(mod, "read_pid", lambda _cfg: mod.os.getpid())

    class Runner:
        def __init__(self, cfg, *_args):
            assert cfg["status"]["execution_mode"] == "strict_background"
            events.append("runner-created")

        def run(self):
            events.append("runner-ran")
            return 0

    monkeypatch.setattr(mod, "Runner", Runner)
    legacy_cfg = {
        "autoreply": {"enabled": True},
        "autoreply_im": {"enabled": True},
        "status": {"execution_mode": "interactive"},
    }
    assert mod.run_guarded(legacy_cfg, None) == 0
    assert events == [
        ("lock", "camfrog_status_writer", 0.5),
        "acquired",
        "runner-created",
        "runner-ran",
        "released",
    ]
    assert not pid_file.exists()


@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_status_worker_fails_closed_without_shared_writer_lock(
    name, monkeypatch, tmp_path, capsys
):
    mod = load(name)

    class WriterLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self):
            raise TimeoutError("writer already owned")

        def release(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "camfrog_status_engine",
        SimpleNamespace(StatusResourceLock=WriterLock),
    )
    pid_file = tmp_path / f"{name}.pid"
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: None)
    monkeypatch.setattr(mod, "pid_path", lambda _cfg: pid_file)
    monkeypatch.setattr(mod, "Runner", lambda *_args: pytest.fail("writer lock must fail closed"))
    assert mod.run_guarded(
        {
            "autoreply": {"enabled": True},
            "autoreply_im": {"enabled": True},
            "status": {"execution_mode": "interactive"},
        },
        None,
    ) == 3
    assert not pid_file.exists()
    assert "Another status worker owns" in capsys.readouterr().out


@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_status_standalone_profile_overrides_legacy_interactive_mode(name):
    mod = load(name)
    legacy = copy.deepcopy(mod.DEFAULTS)
    legacy["status"]["execution_mode"] = "interactive"

    configured = mod.apply_app_profile(legacy)

    assert configured["status"]["execution_mode"] == "strict_background"
    assert legacy["status"]["execution_mode"] == "interactive"


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
def test_split_status_interactive_test_requires_explicit_opt_in(name, monkeypatch):
    mod = load(name)
    configured = copy.deepcopy(mod.DEFAULTS)
    configured["status"]["execution_mode"] = "interactive"
    configured["dry_run"] = False

    monkeypatch.delenv("ZCHAT_STATUS_TEST_UNVERIFIED_SUBMIT", raising=False)
    normal = mod.apply_app_profile(configured)
    assert normal["status"]["execution_mode"] == "strict_background"
    assert mod.runtime_config(configured)["status"]["execution_mode"] == "strict_background"
    assert mod._strict_status_start_blocked(configured, emit=lambda *_args: None)

    monkeypatch.setenv("ZCHAT_STATUS_TEST_UNVERIFIED_SUBMIT", "1")
    opted_in = mod.apply_app_profile(configured)
    runtime = mod.runtime_config(opted_in)
    assert opted_in["status"]["execution_mode"] == "interactive"
    assert runtime["status"]["execution_mode"] == "interactive"
    assert runtime["status"]["background_enter_target"] == "combo"
    warnings = []
    assert not mod._strict_status_start_blocked(runtime, emit=warnings.append)
    assert any("UNVERIFIED TEST MODE" in warning for warning in warnings)


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
def test_split_status_best_effort_is_now_enabled(name, monkeypatch):
    mod = load(name)
    config = copy.deepcopy(mod.DEFAULTS)
    config["dry_run"] = False
    config["status"]["execution_mode"] = "best_effort_background"
    assert not mod._strict_status_start_blocked(config, emit=lambda *_args: None)

@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_status_profile_preserves_explicit_best_effort_mode(name):
    mod = load(name)
    configured = copy.deepcopy(mod.DEFAULTS)
    configured["status"]["execution_mode"] = "best_effort_background"

    profiled = mod.apply_app_profile(configured)
    runtime = mod.runtime_config(profiled) if name != "status_changer_gui" else mod.runtime_config(profiled, "random")

    assert profiled["status"]["execution_mode"] == "best_effort_background"
    assert runtime["status"]["execution_mode"] == "best_effort_background"
    assert runtime["status"]["background_enter_target"] == "combo"


@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_status_commit_strict_mode_never_uses_foreground_input(name, monkeypatch):
    mod = load(name)
    win = SimpleNamespace(handle=100)
    ctrl = SimpleNamespace(
        set_focus=lambda: pytest.fail("strict status commit must not set focus"),
        click_input=lambda: pytest.fail("strict status commit must not click"),
        type_keys=lambda *_args, **_kwargs: pytest.fail(
            "strict status commit must not inject keys"
        ),
    )
    calls = []
    monkeypatch.setattr(
        mod, "commit_background", lambda text, dry=False: calls.append((text, dry)) or True
    )

    assert mod.commit(
        win,
        ctrl,
        "background status",
        False,
        execution_mode="strict_background",
    )
    assert calls == [("background status", False)]


@pytest.mark.parametrize(
    "name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"]
)
def test_live_status_worker_fails_before_spawn_when_strict_submit_is_unverified(
    name, monkeypatch, capsys
):
    mod = load(name)
    monkeypatch.setitem(
        sys.modules,
        "camfrog_status_engine",
        SimpleNamespace(strict_background_submit_supported=lambda: False),
    )
    monkeypatch.setattr(mod, "running_pid", lambda _cfg: None)
    monkeypatch.setattr(
        mod.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("unsupported worker must not spawn"),
    )
    config = copy.deepcopy(mod.DEFAULTS)
    config["dry_run"] = False
    config["status"]["enabled"] = True
    args = SimpleNamespace(config=None, lang=None)

    # Both CLI entry points must reject before creating a child or entering the
    # retrying worker loop. The GUI button has a separate start adapter too.
    assert mod.cmd_start(config, args) == 2
    assert "BACKGROUND_UNSUPPORTED" in capsys.readouterr().out
    assert mod.run_guarded(config) == 2
    assert "BACKGROUND_UNSUPPORTED" in capsys.readouterr().out

    messages = []
    assert mod.cmd_start_worker(
        config, args, report=messages.append,
    ) == 2
    assert len(messages) == 1
    assert "BACKGROUND_UNSUPPORTED" in messages[0]
    assert capsys.readouterr().out == ""

    assert mod.cmd_start_worker(
        config, SimpleNamespace(config=None, lang=None)
    ) == 2
    assert "BACKGROUND_UNSUPPORTED" in capsys.readouterr().out


def test_room_and_im_profiles_disable_other_features():
    room = load("room_control_gui")
    im = load("im_autoreply_gui")
    cfg = copy.deepcopy(room.DEFAULTS)
    cfg["status"]["enabled"] = True
    cfg["autoreply_im"]["enabled"] = True
    out = room.apply_app_profile(copy.deepcopy(cfg))
    assert out["status"]["enabled"] is False and out["autoreply_im"]["enabled"] is False
    out_im = im.apply_app_profile(copy.deepcopy(cfg))
    assert out_im["status"]["enabled"] is False and out_im["autoreply"]["enabled"] is False


def test_music_profile_disables_status_and_im():
    music = load("camfrog_music_gui")
    cfg = copy.deepcopy(music.DEFAULTS)
    cfg["status"]["enabled"] = True
    cfg["autoreply_im"]["enabled"] = True
    out = music.apply_app_profile(copy.deepcopy(cfg))
    assert out["status"]["enabled"] is False
    assert out["autoreply_im"]["enabled"] is False
    assert out["autoreply"]["enabled"] is True


@pytest.mark.parametrize("name", ["im_autoreply_gui", "room_control_gui", "camfrog_music_gui"])
def test_feature_engines_parse_current_camfrog_dom_rows(name):
    mod = load(name)
    rows = [{"class_name": "msg-user animated-message", "children": [
        {"class_name": "nickname-field", "text": "FriendA"},
        {"class_name": "msg-time", "text": "10/11/2026 08:24"},
        {"class_name": "msgid", "text": "hello\nthere"},
    ]}]

    assert mod.parse_web_dom_rows(rows) == ["FriendA: hello there"]


@pytest.mark.parametrize("name", ["camfrog_auto", "im_autoreply_gui", "room_control_gui", "camfrog_music_gui"])
def test_web_chat_reader_prefers_dom_when_uia_has_no_hyperlinks(name, monkeypatch):
    mod = load(name)
    rows = [{"class_name": "msg-user animated-message", "children": [
        {"class_name": "nickname-field", "text": "zdevz"},
        {"class_name": "msg-time", "text": "10/11/2026 08:24"},
        {"class_name": "msgid", "text": "hello"},
    ]}]
    monkeypatch.setattr(mod, "is_web", lambda _ctrl: True)
    monkeypatch.setattr(mod, "web_dom_rows", lambda _ctrl: rows)
    monkeypatch.setattr(mod, "web_items", lambda *_args, **_kwargs: pytest.fail(
        "the DOM parser should handle current Camfrog UIA trees without Hyperlink controls"))

    assert mod.read_chat(object()) == "zdevz: hello"


@pytest.mark.parametrize("name,cmd", [
    ("room_control_gui", "check"),
    ("im_autoreply_gui", "check"),
    ("camfrog_music_gui", "check"),
])
def test_cli_passthrough_validates_example_config(name, cmd, capsys):
    mod = load(name)
    assert mod.main([cmd, "--config", str(EXAMPLE)]) == 0
    assert "config OK" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui", "status_changer_gui"])
def test_status_modules_carry_a_working_engine_cli(name, capsys):
    mod = load(name)
    assert mod.cli_main(["check", "--config", str(EXAMPLE)]) == 0
    assert "config OK" in capsys.readouterr().out


def test_status_modules_reject_other_modes_commands(capsys):
    assert load("status_random_gui").cli_main(["marquee", "x"]) == 2


def test_status_changer_logs_the_completed_status_text(caplog, monkeypatch):
    import logging
    from types import SimpleNamespace

    mod = load("status_changer_gui")
    monkeypatch.setattr(mod, "LANG", "en")
    monkeypatch.setattr(mod, "LOG_TEXT", True)
    caplog.set_level(logging.INFO, logger="camfrog_status")
    runner_state = SimpleNamespace(
        marq={"slot_index": None}, status_i=0, stats={"statuses": 0}, current_text="")

    mod.Runner._finish_status(
        runner_state, {"slot_index": None, "raw": "status text sent"}, "status text sent")

    assert "status submission dispatched (provider outcome unverified)" in caplog.text


def test_status_changer_refuses_combo_submit_if_it_may_take_focus(monkeypatch):
    from types import SimpleNamespace

    mod = load("status_changer_gui")
    posted = []
    post_message = lambda *args: posted.append(args) or 1
    user32 = SimpleNamespace(GetForegroundWindow=lambda: 999, PostMessageW=post_message)
    monkeypatch.setattr(mod.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    combo = SimpleNamespace(class_name="CComboBoxTS", handle=77)
    ctrl = SimpleNamespace(
        handle=88, element_info=SimpleNamespace(parent=combo),
        set_focus=lambda: pytest.fail("background update must not move focus"),
        type_keys=lambda *_a, **_k: pytest.fail("background update must not simulate typing"),
    )
    monkeypatch.setattr(mod, "is_web", lambda _ctrl: False)
    writes = []
    monkeypatch.setattr(mod, "write_text", lambda _ctrl, _text: writes.append(_text) or True)

    assert not mod.commit(SimpleNamespace(handle=123), ctrl, "status", False,
                          require_fg=True, background_enter_target="combo")
    assert writes == []
    assert posted == []


@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
def test_standalone_status_startup_discovers_controls(name):
    app_class = load(name).build_app()
    app = object.__new__(app_class)
    app.close_requested = False
    discovered = []
    app.discover = lambda: discovered.append(True)

    app._discover_controls_on_start()
    assert discovered == [True]
    app.close_requested = True
    app._discover_controls_on_start()
    assert discovered == [True]


@pytest.mark.parametrize("name", ["room_control_gui", "im_autoreply_gui", "camfrog_music_gui"])
def test_camfrog_apps_schedule_and_run_startup_discovery(name, monkeypatch):
    mod = load(name)
    app_class = mod.build_app()
    app = object.__new__(app_class)
    app._closing = False
    app.busy = False
    app.model = SimpleNamespace(path="settings.json", cfg={"seed": True})
    tasks = []
    app.run_bg = lambda fn, kind: tasks.append((fn, kind))
    called = []
    monkeypatch.setattr(mod, "cmd_detect", lambda *args: called.append(args) or 0)

    app._discover_controls_on_start()
    assert len(tasks) == 1
    assert tasks[0][1] == "startup-detect"
    assert tasks[0][0]() == 0
    assert called == [({"seed": True}, "settings.json", True, True)]

    app._closing = True
    app._discover_controls_on_start()
    assert len(tasks) == 1


@pytest.mark.parametrize("name", [
    "room_control_gui", "im_autoreply_gui", "camfrog_music_gui",
    "status_random_gui", "status_marquee_gui", "status_changer_gui",
    "chat_im_private_gui",
])
def test_each_camfrog_standalone_saves_only_valid_detected_selectors(
        name, tmp_path, monkeypatch, capsys):
    mod = load(name)
    cfg = {"status": {"edit": {}}, "autoreply": {"history": {}, "input": {}}}
    window = SimpleNamespace(handle=42)
    control_rows = [{
        "class_name": "Edit", "control_type": "Edit", "name": "",
        "auto_id": "statusEdit", "visible": True, "enabled": True,
        "left": 10, "top": 10, "w": 180, "h": 24,
        "parent_class": "CComboBoxTS",
    }]
    monkeypatch.setattr(mod, "BASE", tmp_path)
    monkeypatch.setattr(mod, "get_window", lambda _cfg: window)
    monkeypatch.setattr(mod, "process_windows", lambda _window: [])
    monkeypatch.setattr(mod, "describe_controls", lambda _window: control_rows)
    monkeypatch.setattr(mod, "validate", lambda _candidate: ([], []))
    path = tmp_path / "settings.json"

    assert mod.cmd_detect(cfg, path, apply=True) == 0
    capsys.readouterr()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"]["edit"]["auto_id"] == "statusEdit"
    assert saved["status"]["edit"]["index"] == 0


@pytest.mark.parametrize("name", [
    "room_control_gui", "im_autoreply_gui", "camfrog_music_gui", "chat_im_private_gui",
])
def test_camfrog_standalones_schedule_startup_discovery(name):
    import ast

    path = ROOT / "src" / "features" / f"{name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scheduled = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "after" or len(node.args) < 2:
            continue
        callback = node.args[1]
        if (isinstance(callback, ast.Attribute)
                and isinstance(callback.value, ast.Name)
                and callback.value.id == "self"
                and callback.attr == "_discover_controls_on_start"):
            scheduled.append(node.args[0])
    assert scheduled, f"{name} does not schedule startup control discovery"
    assert any(isinstance(delay, ast.Constant) and delay.value >= 500 for delay in scheduled)

def test_combined_status_startup_saves_and_discovers_both_modes(monkeypatch):
    mod = load("status_changer_gui")
    app = object.__new__(mod.build_app())
    app.embedded = False
    app.close_requested = False
    app.busy = False
    app.mode = "random"
    app.save_job = "pending-save"
    events = []
    app.root = SimpleNamespace(after_cancel=lambda job: events.append(("cancel", job)))
    app.config_paths = {"random": "random.json", "marquee": "marquee.json"}
    app.runtime_config_paths = {
        "random": "random-runtime.json", "marquee": "marquee-runtime.json"
    }
    app.config_path = "random.json"
    app.random_fields = [SimpleNamespace(get=lambda: "random text")]
    app.marquee_fields = [SimpleNamespace(get=lambda: "marquee text")]
    app._fields_for_mode = lambda mode: (
        app.random_fields if mode == "random" else app.marquee_fields
    )
    app.save_settings = lambda: events.append(("save-active", "random")) or {}
    app._log_activity = lambda message, mode: events.append(("saved", mode))
    tasks = []
    app.run_task = lambda function, *_args, **_kwargs: tasks.append(function)
    app._tr = lambda en, _th: en
    configs = {
        path: {"status": {"messages": ["old"]}}
        for path in app.config_paths.values()
    }
    monkeypatch.setattr(mod, "load_cfg", lambda path: configs[path])
    monkeypatch.setattr(mod, "validate", lambda _config: ([], []))
    monkeypatch.setattr(
        mod,
        "write_config",
        lambda path, config: events.append(("write", path, config["status"]["messages"])),
    )
    monkeypatch.setattr(mod, "runtime_config", lambda config, _mode: config)
    monkeypatch.setattr(
        mod, "run_control_discovery", lambda path: events.append(("discover", path))
    )

    app._discover_controls_on_start()
    assert app.save_job is None
    assert len(tasks) == 1
    assert tasks[0]() == 0
    assert events == [
        ("cancel", "pending-save"),
        ("save-active", "random"),
        ("write", "random.json", ["random text"]),
        ("write", "random-runtime.json", ["random text"]),
        ("write", "marquee.json", ["marquee text"]),
        ("write", "marquee-runtime.json", ["marquee text"]),
        ("saved", "marquee"),
        ("discover", "random.json"),
        ("discover", "marquee.json"),
    ]
    assert app.config == configs["random.json"]


def test_status_logs_clear_at_local_noon_once_per_day(tmp_path):
    import datetime as dt

    mod = load("status_changer_gui")
    tz = dt.timezone(dt.timedelta(hours=7))
    files = ("status_activity.log", "random-status-worker.log",
             "marquee-status-worker.log", "camfrog_status_changer.log")
    for filename in files:
        (tmp_path / filename).write_text("old log\n", encoding="utf-8")
        (tmp_path / f"{filename}.1").write_text("rotated log\n", encoding="utf-8")

    before_noon = dt.datetime(2026, 10, 14, 11, 59, tzinfo=tz)
    assert mod.clean_status_logs_at_noon(tmp_path, before_noon) == ()
    assert (tmp_path / "status_activity.log").read_text("utf-8") == "old log\n"

    noon = dt.datetime(2026, 10, 14, 12, 0, tzinfo=tz)
    cleaned = mod.clean_status_logs_at_noon(tmp_path, noon, active_modes={"random"})
    assert "status_activity.log" in cleaned
    assert "marquee-status-worker.log" in cleaned
    assert "random-status-worker.log" not in cleaned
    assert (tmp_path / "random-status-worker.log").read_text("utf-8") == "old log\n"
    assert not (tmp_path / "marquee-status-worker.log.1").exists()

    later = dt.datetime(2026, 10, 14, 12, 1, tzinfo=tz)
    assert "random-status-worker.log" in mod.clean_status_logs_at_noon(
        tmp_path, later, active_modes=set())
    (tmp_path / "status_activity.log").write_text("today log\n", encoding="utf-8")
    assert mod.clean_status_logs_at_noon(tmp_path, later) == ()
    assert (tmp_path / "status_activity.log").read_text("utf-8") == "today log\n"

    next_noon = dt.datetime(2026, 10, 15, 12, 0, tzinfo=tz)
    assert "status_activity.log" in mod.clean_status_logs_at_noon(tmp_path, next_noon)
    assert (tmp_path / "status_activity.log").read_text("utf-8") == ""


def test_active_worker_clears_its_open_log_at_noon(tmp_path):
    import datetime as dt
    import logging.handlers

    mod = load("status_changer_gui")
    path = tmp_path / "random-status-worker.log"
    path.write_text("old log\n", encoding="utf-8")
    rotated = Path(f"{path}.1")
    rotated.write_text("rotated log\n", encoding="utf-8")
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=10000, backupCount=1, encoding="utf-8")
    now = dt.datetime(2026, 10, 14, 12, 0,
                      tzinfo=dt.timezone(dt.timedelta(hours=7)))
    try:
        assert mod.clean_worker_log_at_noon(now, handlers=[handler]) == (path.name,)
        assert path.read_text("utf-8") == ""
        assert not rotated.exists()
        record = logging.LogRecord("test", logging.INFO, "", 0, "after clean", (), None)
        handler.emit(record)
        handler.flush()
        assert "after clean" in path.read_text("utf-8")
    finally:
        handler.close()


def test_chat_im_private_engine_copy():
    mod = load("chat_im_private_gui")
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["status"]["messages"] = ["a long enough status line"]
    errs, _ = mod.validate(cfg)
    assert errs == []
    assert mod.private_config_path(EXAMPLE).name == "config.example.json"


def test_web_status_merged_module():
    mod = load("web_status_gui")
    assert mod.wants_gui([]) is True
    assert mod.wants_gui(["check"]) is False
    pool = mod.parse_pool("line one\nline two\n")
    assert pool == ["line one", "line two"]


def test_entry_points_import_their_module():
    import importlib
    for entry, module in (
            ("room_control_entry", "room_control_gui"),
            ("im_autoreply_entry", "im_autoreply_gui"),
            ("status_random_entry", "status_random_gui"),
            ("status_marquee_entry", "status_marquee_gui"),
            ("status_changer_entry", "status_changer_gui"),
            ("chat_im_private_entry", "chat_im_private_gui"),
            ("autodj_entry", "camfrog_music_gui"),
            ("web_status_entry", "web_status_gui")):
        mod = importlib.import_module(entry)
        assert importlib.import_module(module) is not None
        assert hasattr(mod, "main") or module is not None


@pytest.mark.parametrize(("profile", "selected_override"), [
    ("im_reply", "CAMFROG_APP_DATA_DIR"),
    ("status", "ZCFATO_DATA_DIR"),
])
def test_engine_data_directory_override_is_profile_scoped(profile, selected_override, tmp_path):
    import os
    import subprocess

    app_data = tmp_path / "app-data"
    status_data = tmp_path / "status-data"
    env = os.environ.copy()
    pythonpath = os.pathsep.join([
        str(ROOT / "src" / "camfrog"),
        str(ROOT / "src" / "features"),
        env.get("PYTHONPATH", ""),
    ])
    env.update({
        "CAMFROG_APP_PROFILE": profile,
        "CAMFROG_APP_DATA_DIR": str(app_data),
        "ZCFATO_DATA_DIR": str(status_data),
        "PYTHONPATH": pythonpath,
    })
    result = subprocess.run(
        [sys.executable, "-c", "import camfrog_auto; print(camfrog_auto.BASE)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 0, result.stderr
    expected = status_data if selected_override == "ZCFATO_DATA_DIR" else app_data
    assert Path(result.stdout.strip()).resolve() == expected.resolve()


def test_im_entry_clears_status_data_override(tmp_path):
    import os
    import subprocess

    env = os.environ.copy()
    env["ZCFATO_DATA_DIR"] = str(tmp_path / "status-data")
    env["PYTHONPATH"] = os.pathsep.join([
        str(ROOT / "src" / "camfrog"),
        str(ROOT / "src" / "features"),
        env.get("PYTHONPATH", ""),
    ])
    result = subprocess.run(
        [sys.executable, "-c",
         "import os, im_autoreply_entry; print('ZCFATO_DATA_DIR' in os.environ)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_im_source_worker_dispatches_through_entrypoint():
    import os
    import subprocess

    command = load("im_autoreply_gui").child_cmd(
        SimpleNamespace(config=None, lang=None))
    assert Path(command[1]).name == "im_autoreply_entry.py"
    command[0] = sys.executable
    result = subprocess.run(
        command + ["not-a-real-command"],
        capture_output=True, text=True, timeout=5, env=os.environ.copy(),
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr.lower()
    assert "not-a-real-command" in result.stderr


@pytest.mark.parametrize(("module", "entrypoint"), [
    ("status_random_gui", "status_random_entry.py"),
    ("status_marquee_gui", "status_marquee_entry.py"),
    ("status_changer_gui", "status_changer_entry.py"),
])
def test_status_worker_command_dispatches_through_entrypoint(module, entrypoint):
    """Source workers must use the same launcher that calls the GUI module's main()."""
    import os
    import subprocess
    from types import SimpleNamespace

    command = load(module).worker_cmd(SimpleNamespace(config=None, lang=None))
    assert Path(command[1]).name == entrypoint
    command[0] = sys.executable
    result = subprocess.run(
        command + ["not-a-real-command"],
        capture_output=True, text=True, timeout=5, env=os.environ.copy(),
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr.lower()
    assert "not-a-real-command" in result.stderr


def test_combined_status_ctrl_x_cuts_selected_entry_text(monkeypatch):
    import tkinter
    from types import SimpleNamespace

    mod = load("status_changer_gui")

    class Menu:
        def __init__(self, *_args, **_kwargs):
            pass

        def add_command(self, **_kwargs):
            pass

        def add_separator(self):
            pass

    class Entry:
        def __init__(self):
            self.value = "abcdef"
            self.bindings = {}

        def winfo_class(self):
            return "TEntry"

        def winfo_children(self):
            return []

        def bind(self, sequence, callback, add=None):
            self.bindings[sequence] = callback

        def cget(self, name):
            assert name == "state"
            return "normal"

        def selection_present(self):
            return True

        def get(self):
            return self.value

        def index(self, name):
            return {"sel.first": 1, "sel.last": 4}[name]

        def delete(self, _start, _end):
            self.value = self.value[:1] + self.value[4:]

    class Root:
        def __init__(self, entry):
            self.entry = entry

        def winfo_children(self):
            return [self.entry]

    monkeypatch.setattr(tkinter, "Menu", Menu)
    copied = []
    monkeypatch.setattr(mod, "set_clipboard_text", lambda _root, text: copied.append(text))
    entry = Entry()
    controller = mod.ClipboardController(Root(entry))
    controller.install(controller.root)

    result = entry.bindings["<Control-x>"](SimpleNamespace(widget=entry))

    assert result == "break"
    assert copied == ["bcd"]
    assert entry.value == "aef"


from conftest import has_display, tk_root  # noqa: E402


@pytest.mark.skipif(not has_display(), reason="needs tkinter + display")
def test_room_gui_smoke(tmp_path, monkeypatch):
    import tkinter
    import room_control_gui as g
    config = json.loads(EXAMPLE.read_text("utf-8"))
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(g, "BASE", tmp_path)
    root = tk_root()
    root.deiconify()
    monkeypatch.setattr(tkinter, "Tk", lambda: root)
    app = g.build_app()(tmp_path / "config.json")
    app.root.update()
    assert len(app.nb.tabs()) == 4
    cfg, errs = app.collect()
    assert not errs
    assert hasattr(app, "tree") and not hasattr(app, "im_tree")


@pytest.mark.skipif(not has_display(), reason="needs tkinter + display")
def test_im_gui_smoke(tmp_path, monkeypatch):
    import tkinter
    import im_autoreply_gui as g
    config = json.loads(EXAMPLE.read_text("utf-8"))
    config["autoreply_im"]["only_nicknames"] = ["FriendA"]
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(g, "BASE", tmp_path)
    root = tk_root()
    root.deiconify()
    monkeypatch.setattr(tkinter, "Tk", lambda: root)
    app = g.build_app()(tmp_path / "config.json")
    app.root.update()
    tabs = app.nb.tabs()
    assert len(tabs) == 5
    assert any(app.nb.tab(tab, "text") in ("Activity Log", "บันทึกกิจกรรม")
               for tab in tabs)
    assert "Application opened" in app.log.get("1.0", "end")
    cfg, errs = app.collect()
    assert not errs
    assert hasattr(app, "im_tree") and not hasattr(app, "tree")
    app.tin.set("FriendA")  # a listed nick so the rule engine actually runs
    app.test_im_rule()
    assert "rule" in app.tiout.cget("text")


@pytest.mark.skipif(not has_display(), reason="needs tkinter + display")
@pytest.mark.parametrize("name", ["status_random_gui", "status_marquee_gui"])
def test_status_gui_smoke_single_mode(name, tmp_path, monkeypatch):
    import tkinter
    mod = __import__(name)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    config = copy.deepcopy(mod.DEFAULTS)
    config["status"]["messages"] = ["first status line here", "second status line"]
    cfg_path = tmp_path / "camfrog-status-config.json"
    mod.write_config(cfg_path, config)
    root = tk_root()
    root.deiconify()
    monkeypatch.setattr(tkinter, "Tk", lambda: root)
    app = mod.build_app()(cfg_path)
    app.root.update()
    assert len(app.tabs.tabs()) == 1  # one mode, no both-modes notebook
    collected = app.collect_config()
    assert collected["status"]["messages"]  # pools seeded, not lost
    assert collected["status"]["random"] is (name == "status_random_gui")
    assert collected["status"]["marquee"]["enabled"] is (name == "status_marquee_gui")


def test_marquee_runner_scrolls_frames_and_rotates_lines(monkeypatch):
    """Live-path regression: scrolled lines advance frame-by-frame then rotate.

    The engine was once suspected of not scrolling; this drives the real
    Runner.do_status with a fake clock and asserts frames move and lines
    rotate 1->2.
    """
    import status_marquee_gui as m

    cfg = copy.deepcopy(m.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"]["enabled"] = True
    cfg["status"]["messages"] = ["AAA line one here", "BBB line two here"]
    cfg["status"]["interval_seconds"] = 10
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["marquee"].update(enabled=True, scroll=True, width=10, stride=2,
                                    step_seconds=10, cycles=1, max_frames=80)
    assert m.validate(cfg)[0] == []
    clock = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    r = m.Runner(cfg)
    r.status_edit, r.apply_btn, r.win = object(), None, object()
    sent = []
    monkeypatch.setattr(r, "send", lambda ctrl, text, btn=None, win=None:
                        sent.append(text) or True)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    for _ in range(400):
        clock[0] += 10
        r.do_status(clock[0])
    assert len(sent) > 10, "frames never advanced"
    assert len({s[:3] for s in sent}) > 1, "lines never rotated"
    assert r.stats.get("statuses", 0) >= 2


def test_marquee_slot_loop_advances_after_finishing_scroll_frames(monkeypatch):
    import status_marquee_gui as m

    cfg = copy.deepcopy(m.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"]["enabled"] = True
    cfg["status"]["messages"] = ["AAA status that is long enough", "BBB next status long enough"]
    cfg["status"]["interval_seconds"] = 10.0
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["marquee"].update(
        enabled=True, scroll=True, width=10, stride=2, step_seconds=10,
        cycles=1, max_frames=5, loop_slots=True, infinite_loop=False)
    assert m.validate(cfg)[0] == []

    clock = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    r = m.Runner(cfg)
    r.status_edit, r.apply_btn, r.win = object(), None, object()
    sent = []
    monkeypatch.setattr(r, "send", lambda ctrl, text, btn=None, win=None:
                        sent.append(text) or True)
    for instant in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0):
        clock[0] = instant
        r.do_status(instant)

    assert len(sent) == 7
    assert sent[5] == "AAA status that is long enough"
    assert sent[6].startswith("BBB")
    assert r.status_i == 1
    assert r.marq is not None


@pytest.mark.parametrize("name", ["status_changer_gui", "status_marquee_gui"])
def test_marquee_without_scroll_uses_frame_delay_not_legacy_interval(name, monkeypatch):
    """Static Marquee slots must advance on Frame even if old config says 600s."""
    mod = load(name)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"].update(
        enabled=True, random=False,
        messages=["SLOT 01", "SLOT 02", "SLOT 03"],
        interval_seconds=600, set_on_start=True,
    )
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["marquee"].update(
        enabled=True, scroll=False, loop_slots=True, infinite_loop=False,
        step_seconds=10,
    )
    clock = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    runner = mod.Runner(cfg)
    runner.status_edit, runner.apply_btn, runner.win = object(), None, object()
    sent = []
    runner.send = lambda _ctrl, text, btn=None: sent.append(text) or True
    for instant in (0.0, 9.99, 10.0, 19.99, 20.0, 29.99, 30.0, 39.99, 40.0):
        clock[0] = instant
        runner.do_status(instant)

    assert sent == ["SLOT 01", "SLOT 02", "SLOT 03", "SLOT 01"]
    assert runner.status_i == 1


@pytest.mark.parametrize("name", ["status_changer_gui", "status_marquee_gui"])
def test_camfrog_status_edit_replaces_text_through_accessible_value(name):
    """Read back the custom Edit through UIA even when native window_text is empty."""
    mod = load(name)
    wanted = "slot 2 ไทย {ready}"

    class Edit:
        value = "slot 1"

        @property
        def iface_value(self):
            return self

        @property
        def CurrentValue(self):
            return self.value

        def set_edit_text(self, value):
            self.value = value

        def window_text(self):
            return ""  # Camfrog's native Edit HWND does not expose WM_GETTEXT.

    edit = Edit()
    assert mod.write_text(edit, wanted)
    assert edit.value == wanted


@pytest.mark.parametrize("name", ["status_changer_gui", "status_marquee_gui"])
def test_foreground_status_commit_writes_frame_before_enter(name, monkeypatch):
    """Focus the Camfrog window, replace the frame, then submit without Ctrl+A."""
    mod = load(name)
    # Windows CI has no interactive desktop. Isolate the tested foreground
    # sequence from the activity guard, which has dedicated regression tests.
    monkeypatch.setattr(mod.USER_GUARD, "wait_until_idle", lambda: True)
    events = []
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(
        handle=88,
        set_focus=lambda: events.append(("focus", 88)),
        type_keys=lambda keys: events.append(("keys", keys)),
    )
    user32 = SimpleNamespace(
        GetForegroundWindow=lambda: win.handle,
    )
    monkeypatch.setattr(mod.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(mod, "is_web", lambda _ctrl: False)
    monkeypatch.setattr(mod, "foreground_ok", lambda _win: True)
    monkeypatch.setattr(
        mod, "write_text",
        lambda _ctrl, text: events.append(("write", text)) or True,
    )

    assert mod.commit(win, ctrl, "slot 2", False, require_fg=True)
    assert events == [("focus", 88), ("write", "slot 2"),
                      ("keys", "{ENTER}")]


@pytest.mark.parametrize("name", ["status_changer_gui", "status_marquee_gui"])
def test_combo_enter_option_refuses_when_camfrog_is_not_foreground(name, monkeypatch):
    """Targeted combo Enter can activate Camfrog, so non-foreground sends fail closed."""
    mod = load(name)
    events = []
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(
        handle=88,
        set_focus=lambda: events.append(("focus", 88)),
        type_keys=lambda keys: events.append(("keys", keys)),
    )
    user32 = SimpleNamespace(
        GetForegroundWindow=lambda: 55,
        IsWindow=lambda hwnd: True,
        SetForegroundWindow=lambda hwnd: events.append(("restore", hwnd)),
    )
    monkeypatch.setattr(mod.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(mod, "is_web", lambda _ctrl: False)
    monkeypatch.setattr(
        mod, "write_text",
        lambda _ctrl, text: events.append(("write", text)) or True,
    )
    monkeypatch.setattr(
        mod, "_background_enter",
        lambda target_ctrl, target: events.append(("background_enter", target_ctrl, target)),
    )

    assert not mod.commit(
        win, ctrl, "slot 2", False, require_fg=True, restore=True,
        background_enter_target="combo")
    assert events == []


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"]
)
def test_best_effort_worker_start_is_allowed(name):
    mod = load(name)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = False
    cfg["status"]["enabled"] = True
    cfg["status"]["execution_mode"] = "best_effort_background"
    messages = []

    assert not mod._strict_status_start_blocked(cfg, emit=messages.append)


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"]
)
def test_best_effort_mode_refuses_unisolated_combo_submit(name, monkeypatch, caplog):
    mod = load(name)
    events = []
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(
        handle=88,
        set_focus=lambda: pytest.fail("best-effort must not focus Camfrog"),
        type_keys=lambda *_args: pytest.fail("best-effort must not inject keys"),
    )
    user32 = SimpleNamespace(
        GetForegroundWindow=lambda: 55,
        IsWindow=lambda _hwnd: True,
        SetForegroundWindow=lambda _hwnd: pytest.fail("best-effort must not restore focus"),
    )
    monkeypatch.setattr(mod.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(mod, "is_web", lambda _ctrl: False)
    writes = []
    monkeypatch.setattr(mod, "write_text", lambda _ctrl, _text: writes.append(_text) or True)
    monkeypatch.setattr(
        mod, "_background_enter",
        lambda _ctrl, target: events.append(target),
    )

    assert not mod.commit(
        win, ctrl, "test", False, require_fg=True, restore=True,
        execution_mode="best_effort_background",
    )
    assert events == []
    assert writes == []


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"])
def test_status_update_refuses_combo_when_camfrog_is_not_foreground(name, monkeypatch):
    """Targeted Enter can activate Camfrog, so non-foreground submits fail closed."""
    mod = load(name)
    events = []
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(
        handle=88,
        set_focus=lambda: events.append(("focus", 88)),
        type_keys=lambda keys: events.append(("keys", keys)),
    )
    user32 = SimpleNamespace(
        GetForegroundWindow=lambda: 55,
        IsWindow=lambda _hwnd: True,
        SetForegroundWindow=lambda hwnd: events.append(("restore", hwnd)),
    )
    monkeypatch.setattr(mod.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(mod, "is_web", lambda _ctrl: False)
    monkeypatch.setattr(mod, "foreground_ok", lambda _win: pytest.fail(
        "background status updates must not focus Camfrog"))
    monkeypatch.setattr(
        mod, "write_text", lambda _ctrl, text: events.append(("write", text)) or True)
    monkeypatch.setattr(
        mod, "_background_enter",
        lambda target_ctrl, target: events.append(("background_enter", target_ctrl, target)))

    assert not mod.commit(
        win, ctrl, "slot 2", False, require_fg=True, restore=True,
        background_enter_target="edit", background_if_not_foreground=True)
    assert events == []


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_random_gui", "status_marquee_gui"]
)
def test_status_submit_never_imports_or_mutates_windows_sound_registry(name, monkeypatch):
    import builtins

    mod = load(name)
    sent = []
    user32 = SimpleNamespace(PostMessageW=lambda *args: sent.append(args) or 1)
    monkeypatch.setattr(mod.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(mod.os, "name", "nt")
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    original_import = builtins.__import__

    def guarded_import(module_name, *args, **kwargs):
        if module_name == "winreg":
            raise AssertionError("Status submission must not access Windows sound registry")
        return original_import(module_name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    combo = SimpleNamespace(class_name="CComboBoxTS", handle=77)
    edit = SimpleNamespace(handle=88, element_info=SimpleNamespace(parent=combo))

    mod._background_enter(edit, "combo")

    assert sent == [
        (77, 0x0100, 0x0D, 0x001C0001),
        (77, 0x0101, 0x0D, 0xC01C0001),
    ]


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_random_gui", "status_marquee_gui"]
)
def test_status_submit_fails_closed_without_hwnd_instead_of_sending_keys(name):
    mod = load(name)
    events = []
    ctrl = SimpleNamespace(
        handle=0,
        element_info=SimpleNamespace(handle=0),
        send_keystrokes=lambda keys: events.append(("keys", keys)),
    )

    with pytest.raises(LookupError, match="refusing keyboard-input fallback"):
        mod._background_enter(ctrl, "edit")

    assert events == []


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"])
def test_non_status_commit_keeps_foreground_requirement(name, monkeypatch):
    """Chat and IM controls must not use the status-only background fallback."""
    mod = load(name)
    events = []
    win = SimpleNamespace(handle=77)
    ctrl = SimpleNamespace(set_focus=lambda: events.append("focus"))
    user32 = SimpleNamespace(GetForegroundWindow=lambda: 55)
    monkeypatch.setattr(mod.ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(mod, "is_web", lambda _ctrl: False)
    monkeypatch.setattr(mod, "foreground_ok", lambda _win: False)
    monkeypatch.setattr(mod, "write_text", lambda *_args: events.append("write") or True)

    assert not mod.commit(win, ctrl, "reply", False, require_fg=True)
    assert events == []


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"]
)
def test_status_window_discovery_includes_hidden_windows(name, monkeypatch):
    mod = load(name)
    native = SimpleNamespace(
        handle=77,
        class_name=lambda: "#32770",
        window_text=lambda: "Camfrog Video Chat",
        process_id=lambda: 77,
    )
    calls = []

    class NativeDesktop:
        def windows(self, **kwargs):
            calls.append(kwargs)
            return [native]

    class UiaWindow:
        def wrapper_object(self):
            return self

        def process_id(self):
            return 77

    class UiaDesktop:
        def window(self, **_kwargs):
            return UiaWindow()

    def desktop_factory(**kwargs):
        return NativeDesktop() if kwargs["backend"] == "win32" else UiaDesktop()

    monkeypatch.setattr(mod, "find", lambda _window, _selector: object())
    cfg = {"window_title_regex": ".*Camfrog.*", "status": {"edit": {"class_name": "Edit"}}}
    assert isinstance(mod.get_status_window(cfg, desktop_factory), UiaWindow)
    assert calls == [{"title_re": ".*Camfrog.*", "visible_only": False}]


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"]
)
def test_status_window_discovery_uses_matching_status_control_to_disambiguate_processes(
        name, monkeypatch):
    mod = load(name)
    windows = [
        SimpleNamespace(handle=77, process_id=lambda: None,
                        class_name=lambda: "#32770", window_text=lambda: "Camfrog one"),
        SimpleNamespace(handle=88, process_id=lambda: 202,
                        class_name=lambda: "#32770", window_text=lambda: "Camfrog two"),
    ]
    wrappers = {77: SimpleNamespace(handle=77), 88: SimpleNamespace(handle=88)}

    class NativeDesktop:
        def windows(self, **_kwargs):
            return windows

    class UiaDesktop:
        def window(self, handle):
            return SimpleNamespace(wrapper_object=lambda: wrappers[handle])

    monkeypatch.setattr(
        mod, "find", lambda win, _selector: (
            object() if win.handle == 88 else (_ for _ in ()).throw(LookupError("no control"))))

    def desktop_factory(**kwargs):
        return NativeDesktop() if kwargs["backend"] == "win32" else UiaDesktop()

    cfg = {"window_title_regex": ".*Camfrog.*", "status": {"edit": {"class_name": "Edit"}}}
    assert mod.get_status_window(cfg, desktop_factory) is wrappers[88]


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"]
)
def test_status_window_discovery_fails_when_status_control_matches_multiple_processes(
        name, monkeypatch):
    mod = load(name)
    windows = [
        SimpleNamespace(handle=77, process_id=lambda: 101,
                        class_name=lambda: "#32770", window_text=lambda: "Camfrog one"),
        SimpleNamespace(handle=88, process_id=lambda: 202,
                        class_name=lambda: "#32770", window_text=lambda: "Camfrog two"),
    ]

    class NativeDesktop:
        def windows(self, **_kwargs):
            return windows

    class UiaDesktop:
        def window(self, handle):
            return SimpleNamespace(wrapper_object=lambda: SimpleNamespace(handle=handle))

    monkeypatch.setattr(mod, "find", lambda _win, _selector: object())

    def desktop_factory(**kwargs):
        return NativeDesktop() if kwargs["backend"] == "win32" else UiaDesktop()

    cfg = {"window_title_regex": ".*Camfrog.*", "status": {"edit": {"class_name": "Edit"}}}
    with pytest.raises(RuntimeError, match="processes match"):
        mod.get_status_window(cfg, desktop_factory)


@pytest.mark.parametrize("name", ["status_changer_gui", "status_marquee_gui"])
def test_marquee_submits_frames_only_after_configured_step(name, monkeypatch):
    mod = load(name)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"].update(enabled=True, random=False,
                         messages=["A marquee message long enough for several frames"],
                         set_on_start=True)
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["marquee"].update(
        enabled=True, scroll=True, width=8, stride=2, step_seconds=10,
        cycles=1, max_frames=5, loop_slots=False, infinite_loop=False)
    clock = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    runner = mod.Runner(cfg)
    runner.status_edit, runner.apply_btn, runner.win = object(), None, object()
    sent_at = []
    runner.send = lambda _ctrl, _text, btn=None: sent_at.append(clock[0]) or True

    for instant in (0.0, 9.99, 10.0, 19.99, 20.0, 29.99, 30.0):
        clock[0] = instant
        runner.do_status(instant)

    assert sent_at == [10.0, 20.0, 30.0]


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_random_gui", "status_marquee_gui"]
)
def test_status_retry_never_bypasses_ten_second_minimum(name, monkeypatch):
    mod = load(name)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"].update(
        enabled=True,
        random=name != "status_marquee_gui",
        messages=["Retry status"],
        interval_seconds=10,
        retry_seconds=1,
    )
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["marquee"]["enabled"] = name == "status_marquee_gui"
    cfg["status"]["marquee"]["scroll"] = False
    clock = [90.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    runner = mod.Runner(cfg)
    runner.status_edit, runner.apply_btn, runner.win = object(), None, object()
    sends = []
    runner.send = lambda _ctrl, _text, btn=None: (
        sends.append(True), len(sends) > 1
    )[1]

    with pytest.raises(RuntimeError):
        runner.do_status(100)

    runner.do_status(109.99)
    assert len(sends) == 1
    runner.do_status(110)
    assert len(sends) == 2


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_random_gui", "status_marquee_gui"]
)
def test_status_startup_waits_ten_seconds_before_first_change(name, monkeypatch):
    mod = load(name)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"].update(
        enabled=True,
        random=name == "status_random_gui",
        messages=["Startup delay status"],
        interval_seconds=600,
        set_on_start=True,
    )
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["marquee"].update(
        enabled=name != "status_random_gui", step_seconds=0.5, scroll=False
    )
    clock = [100.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    runner = mod.Runner(cfg)
    runner.status_edit, runner.apply_btn, runner.win = object(), None, object()
    sends = []
    runner.send = lambda *_args, **_kwargs: sends.append(clock[0]) or True

    runner.do_status(clock[0])
    clock[0] = 109.99
    runner.do_status(clock[0])
    assert sends == []
    clock[0] = 110.0
    runner.do_status(clock[0])
    assert sends == [110.0]


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_random_gui", "status_marquee_gui"]
)
def test_marquee_step_minimum_is_point_three_seconds_in_every_standalone(name, tmp_path):
    mod = load(name)
    config_path = tmp_path / f"{name}.json"
    config_path.write_text(
        json.dumps({"status": {"marquee": {"step_seconds": 0.2}}}),
        encoding="utf-8",
    )

    cfg = mod.load_cfg(config_path)

    assert mod.MARQUEE_STEP_MIN == 0.3
    assert cfg["status"]["marquee"]["step_seconds"] == 0.3
    assert mod.DEFAULTS["status"]["marquee"]["step_seconds"] == 0.5


@pytest.mark.parametrize("name", ["status_changer_gui", "status_marquee_gui"])
@pytest.mark.parametrize("scroll", [False, True])
@pytest.mark.parametrize("loop_slots", [False, True])
def test_marquee_uses_each_slot_once_then_wraps_only_when_loop_is_enabled(
        name, scroll, loop_slots, monkeypatch, tmp_path, caplog):
    """Marquee sends slots 1..10 in order, then returns to 1 only with Loop."""
    mod = load(name)
    monkeypatch.setattr(mod, "BASE", tmp_path)
    cfg = copy.deepcopy(mod.DEFAULTS)
    cfg["dry_run"] = True
    messages = [f"SLOT {index:02d} -- " + ("abcdefghijklmno" * 2)
                for index in range(1, 11)]
    cfg["status"].update(enabled=True, random=False, messages=messages,
                         interval_seconds=600, set_on_start=True,
                         language_cycle=["en", "th"])
    cfg["status"]["history"].update(
        enabled=True, use_as_source=True, seed_from_messages=False)
    cfg["status"]["marquee"].update(
        enabled=True, scroll=scroll, width=8, stride=2, step_seconds=10,
        cycles=1, max_frames=3, loop_slots=loop_slots, infinite_loop=False)
    caplog.set_level("INFO")
    clock = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    runner = mod.Runner(cfg)
    runner.hist.pick = lambda *_args, **_kwargs: "HISTORY OVERRIDE"
    runner.status_edit, runner.apply_btn, runner.win = object(), None, object()
    runner.send = lambda _ctrl, _text, btn=None: True

    completed = []
    previous_count = 0
    expected = messages + messages[:1] if loop_slots else messages
    for tick in range(1201):
        now = tick * 1.0
        clock[0] = now
        runner.do_status(now)
        count = runner.stats["statuses"]
        if count > previous_count:
            completed.append(runner.current_text)
            previous_count = count
            if loop_slots and len(completed) == len(expected):
                break

    assert completed[:len(expected)] == expected
    assert completed == expected
    assert "1/10" in caplog.text
    if loop_slots:
        assert runner.status_i == 1  # 11 completed, so slot 2 is next
    else:
        assert runner.status_i == 10


@pytest.mark.skipif(not has_display(), reason="needs tkinter + display")
def test_combined_status_changer_marquee_has_no_every_and_matches_web_status_theme(
        tmp_path, monkeypatch):
    import tkinter
    import status_changer_gui as app_module
    import web_status_gui

    monkeypatch.setattr(app_module, "BASE", tmp_path)
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path / "status-data")
    cfg_path = tmp_path / "camfrog-status-config.json"
    app_module.write_config(cfg_path, copy.deepcopy(app_module.DEFAULTS))
    root = tk_root()
    root.deiconify()
    monkeypatch.setattr(tkinter, "Tk", lambda: root)
    app = app_module.build_app()(cfg_path)
    root.update()

    def labels_under(widget):
        labels = []
        for child in widget.winfo_children():
            try:
                labels.append(str(child.cget("text")))
            except (tkinter.TclError, TypeError):
                pass
            labels.extend(labels_under(child))
        return labels

    app.tabs.select(app.pages["marquee"])
    root.update()
    marquee_labels = labels_under(app.pages["marquee"])
    assert not any(label in ("Every", "เปลี่ยนทุก") for label in marquee_labels)
    assert any(label in ("Frame", "จังหวะ") for label in marquee_labels)
    assert set(app.intervals) == {"random"}

    app.tabs.select(app.pages["random"])
    root.update()
    assert any(label in ("Every", "เปลี่ยนทุก")
               for label in labels_under(app.pages["random"]))
    assert app_module.STATUS_CHANGER_THEME == web_status_gui.WEB_STATUS_THEME
    assert root.cget("background") == web_status_gui.WEB_STATUS_THEME["background"]
    root.withdraw()


def test_apply_sends_frames_immediately_when_scroll_on(monkeypatch):
    """Apply scrolls at once: ordered frames with step delays between sends."""
    import status_marquee_gui as m

    cfg = copy.deepcopy(m.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"]["enabled"] = True
    cfg["status"]["execution_mode"] = "interactive"
    cfg["status"]["max_length"] = 120
    cfg["status"]["marquee"].update(enabled=True, scroll=True, width=10, stride=2,
                                    step_seconds=10, cycles=1, max_frames=80)
    monkeypatch.setattr(m, "get_window", lambda config: object())
    monkeypatch.setattr(m, "find", lambda win, spec: object())
    seen, sleeps = [], []
    monkeypatch.setattr(m, "commit", lambda *a, **_kwargs: seen.append(a[2]) or True)
    monkeypatch.setattr(m.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(m, "load_cfg", lambda path: cfg)
    assert m.apply_status_scrolling("dummy.json", "status message that scrolls on") == 0
    assert len(seen) > 1, "expected several frames sent in order"
    assert seen == sorted(seen, key=lambda t: seen.index(t)) and len(set(seen)) > 1
    assert sleeps and all(s >= 10 for s in sleeps)


def test_apply_falls_back_to_single_shot_when_scroll_off(monkeypatch):
    """Apply with scroll off sends exactly once via the static path."""
    import status_marquee_gui as m

    cfg = copy.deepcopy(m.DEFAULTS)
    cfg["dry_run"] = True
    cfg["status"]["enabled"] = True
    cfg["status"]["execution_mode"] = "interactive"
    cfg["status"]["marquee"].update(enabled=True, scroll=False)
    monkeypatch.setattr(m, "get_window", lambda config: object())
    monkeypatch.setattr(m, "find", lambda win, spec: object())
    seen = []
    monkeypatch.setattr(m, "commit", lambda *a, **_kwargs: seen.append(a[2]) or True)
    monkeypatch.setattr(m, "load_cfg", lambda path: cfg)
    assert m.apply_status_scrolling("dummy.json", "hello") == 0
    assert seen == ["hello"]


@pytest.mark.parametrize("scrolling", [False, True])
def test_strict_background_failure_does_not_fall_back_to_uia_discovery(
        monkeypatch, scrolling):
    """A strict submit rejection must not trigger legacy window discovery."""
    import status_marquee_gui as m

    cfg = copy.deepcopy(m.DEFAULTS)
    cfg["dry_run"] = False
    cfg["status"]["enabled"] = True
    cfg["status"]["execution_mode"] = "strict_background"
    cfg["status"]["marquee"].update(enabled=scrolling, scroll=scrolling,
                                     width=8, stride=1, cycles=1)
    monkeypatch.setattr(m, "load_cfg", lambda _path: cfg)
    monkeypatch.setattr(
        m, "commit_background",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(
            "background status failed (state=BACKGROUND_UNSUPPORTED, "
            "code=STRICT_SUBMIT_UNVERIFIED)")),
    )
    monkeypatch.setattr(
        m, "cmd_detect",
        lambda *_args, **_kwargs: pytest.fail("strict failure must not run UIA discovery"),
    )

    apply = m.apply_status_scrolling if scrolling else m.apply_status_text
    with pytest.raises(RuntimeError, match="STRICT_SUBMIT_UNVERIFIED"):
        apply("dummy.json", "strict background test")


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"]
)
def test_status_worker_reload_attach_failure_clears_stale_status_handles(
        name, monkeypatch, tmp_path, caplog):
    import logging

    m = load(name)

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    cfg = copy.deepcopy(m.DEFAULTS)
    cfg["reload_config"] = True
    runner = object.__new__(m.Runner)
    runner.cfg_path = cfg_path
    runner._mtime = 0
    runner._next_reload = 0
    runner.cfg = cfg
    runner.win = runner.status_edit = runner.apply_btn = object()
    runner.marq = {"raw": "stale"}
    runner.apply_cfg = lambda _cfg: None
    runner.resolve = lambda: (_ for _ in ()).throw(
        RuntimeError("multiple or unverifiable Camfrog processes"))

    monkeypatch.setattr(m, "apply_app_profile", lambda cfg: cfg)
    monkeypatch.setattr(m, "load_cfg", lambda _path: cfg)
    monkeypatch.setattr(m, "validate", lambda _cfg: ([], []))
    monkeypatch.setattr(m, "LANG_LOCKED", True)
    caplog.set_level(logging.WARNING, logger="camfrog_status")

    runner.maybe_reload(1)

    assert runner.win is runner.status_edit is runner.apply_btn is runner.marq is None
    assert "Config reloaded but Camfrog re-attach failed" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.parametrize(
    "name", ["status_changer_gui", "status_marquee_gui", "status_random_gui"]
)
def test_status_worker_resolve_invalidates_stale_handles_before_discovery(name, monkeypatch):
    m = load(name)

    runner = object.__new__(m.Runner)
    runner.cfg = {"status": {"enabled": True}, "autoreply": {"enabled": False}}
    runner.win = runner.status_edit = runner.apply_btn = object()
    monkeypatch.setattr(m, "get_window", lambda _cfg: (_ for _ in ()).throw(
        RuntimeError("ambiguous Camfrog process")))

    with pytest.raises(RuntimeError, match="ambiguous Camfrog process"):
        runner._do_resolve()

    assert runner.win is runner.status_edit is runner.apply_btn is None
