from __future__ import annotations

import json
from types import SimpleNamespace

from tools import probe_submit_routes as probe


class FakeForegroundEventMonitor:
    def __init__(self, _target_pid, _baseline_hwnd):
        self.started = False

    def start(self):
        self.started = True

    def stop(self, *, minimum_duration=2.0):
        assert self.started
        return {
            "foreground_monitor": "win_event_hook",
            "foreground_monitor_stopped": True,
            "foreground_monitor_error": None,
            "foreground_event_count": 0,
            "target_foreground_event_count": 0,
            "baseline_foreground_event_count": 0,
            "other_foreground_event_count": 0,
            "unknown_foreground_event_count": 0,
        }


def _runtime(monkeypatch, *, lock_error=None, stop_error=None):
    events = []
    process = SimpleNamespace(pid=1234)
    window = SimpleNamespace(hwnd=4567)
    adapter = SimpleNamespace(controls_are_current=lambda pid, hwnd: True)

    class FakeLock:
        def __init__(self, resource_key, timeout_seconds):
            events.append(("lock_init", resource_key, timeout_seconds))

        def __enter__(self):
            events.append(("lock_enter",))
            if lock_error:
                raise lock_error
            return self

        def __exit__(self, *_):
            events.append(("lock_exit",))

    class FakeStopGuard:
        def __init__(self, scope, resource_key, description):
            events.append(("stop_init", scope, resource_key, description))

        def __enter__(self):
            events.append(("stop_enter",))
            if stop_error:
                raise stop_error
            return self

        def __exit__(self, *_):
            events.append(("stop_exit",))

    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(
        probe,
        "CamfrogWindowResolver",
        lambda: SimpleNamespace(
            resolve_status_controls=lambda pid=None: (process, window, adapter)
        ),
    )
    monkeypatch.setattr(probe, "revalidate_process_identity", lambda _: True)
    monkeypatch.setattr(probe, "revalidate_window_identity", lambda *_: True)
    monkeypatch.setattr(probe, "StatusResourceLock", FakeLock)
    monkeypatch.setattr(probe, "EmergencyStopGuard", FakeStopGuard)
    monkeypatch.setattr(
        probe, "_submit_and_record", lambda *_: events.append(("submit",)) or 0
    )
    return events


def test_submit_requires_shared_resource_lock_and_feature_emergency_guard(monkeypatch):
    events = _runtime(monkeypatch)

    result = probe.main(["--route", "edit-char", "--status", "probe text", "--yes"])

    assert result == 0
    assert events == [
        ("lock_init", "camfrog_status_writer", 0.5),
        ("lock_enter",),
        ("lock_init", "pid_1234", 3.0),
        ("lock_enter",),
        (
            "stop_init",
            probe.EmergencyStopScope.FEATURE,
            "camfrog.status-changer",
            "Camfrog status submit probe",
        ),
        ("stop_enter",),
        ("submit",),
        ("stop_exit",),
        ("lock_exit",),
        ("lock_exit",),
    ]


def test_probe_uses_each_status_features_emergency_stop_scope(monkeypatch):
    events = _runtime(monkeypatch)

    for feature, resource_key in probe.FEATURE_RESOURCE_KEYS.items():
        result = probe.main(
            [
                "--route",
                "edit-char",
                "--feature",
                feature,
                "--status",
                "probe text",
                "--yes",
            ]
        )
        assert result == 0
        assert (
            "stop_init",
            probe.EmergencyStopScope.FEATURE,
            resource_key,
            "Camfrog status submit probe",
        ) in events

    assert events.count(("submit",)) == len(probe.FEATURE_RESOURCE_KEYS)


def test_probe_does_not_write_when_resource_lock_is_owned(monkeypatch, capsys):
    events = _runtime(monkeypatch, lock_error=TimeoutError())

    result = probe.main(["--route", "edit-char", "--status", "probe", "--yes"])

    assert result == 6
    assert ("submit",) not in events
    assert "already owned" in capsys.readouterr().err


def test_probe_does_not_write_when_emergency_stop_is_active(monkeypatch, capsys):
    events = _runtime(monkeypatch, stop_error=StopIteration())

    result = probe.main(["--route", "edit-char", "--status", "probe", "--yes"])

    assert result == 7
    assert ("submit",) not in events
    assert "Emergency stop is active" in capsys.readouterr().err


def test_probe_refuses_live_route_without_explicit_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(
        probe,
        "CamfrogWindowResolver",
        lambda: (_ for _ in ()).throw(AssertionError("must not discover target")),
    )

    result = probe.main(["--route", "edit-char", "--status", "probe"])

    assert result == 2
    assert "Refusing to submit" in capsys.readouterr().out


def test_non_global_route_matrix_covers_edit_send_without_keyboard_injection():
    assert probe.ROUTES == (
        "combo-post",
        "combo-send",
        "edit-char",
        "edit-char-post",
        "edit-post",
        "edit-send",
        "engine-post",
        "parent-msg",
    )


def test_edit_send_uses_bounded_messages_on_edit_hwnd(monkeypatch):
    messages = []

    def send(hwnd, message, wparam, lparam, timeout):
        messages.append((hwnd, message, wparam, lparam, timeout))
        return 1

    monkeypatch.setattr(probe, "_send_message_timeout", send)

    assert probe._send_enter(2345) is True
    assert [message[:3] for message in messages] == [
        (2345, probe.WM_KEYDOWN, probe.VK_RETURN),
        (2345, probe.WM_KEYUP, probe.VK_RETURN),
    ]
    assert all(message[4] == probe.MESSAGE_TIMEOUT_MS for message in messages)


def test_post_enter_targets_only_the_requested_window(monkeypatch):
    messages = []

    class User32:
        @staticmethod
        def PostMessageW(hwnd, message, wparam, lparam):
            messages.append((hwnd, message, wparam, lparam))
            return 1

    monkeypatch.setattr(
        probe.ctypes,
        "windll",
        SimpleNamespace(user32=User32()),
        raising=False,
    )

    assert probe._post_enter(3456) is True
    assert messages == [
        (3456, probe.WM_KEYDOWN, probe.VK_RETURN, probe._enter_lparam()),
        (
            3456,
            probe.WM_KEYUP,
            probe.VK_RETURN,
            probe._enter_lparam(key_up=True),
        ),
    ]


def test_edit_char_post_targets_only_the_status_edit(monkeypatch):
    calls = []

    class User32:
        @staticmethod
        def PostMessageW(hwnd, message, wparam, lparam):
            calls.append((hwnd, message, wparam, lparam))
            return 1

    monkeypatch.setattr(
        probe.ctypes,
        "windll",
        SimpleNamespace(user32=User32()),
        raising=False,
    )

    assert probe._post_edit_char(2345) is True
    assert calls == [(2345, probe.WM_CHAR, 0x0D, 0)]


def test_probe_logs_only_sanitized_client_evidence_and_restores_edit_text(
    monkeypatch, tmp_path
):
    class FakeRegistry:
        def is_stopped(self, *_):
            return False

    class FakeAdapter:
        edit_hwnd = 2345
        combo_hwnd = 3456

        def __init__(self):
            self.edit_text = "prior draft"
            self.applied_text = "previous status"

        def controls_are_current(self, *_):
            return True

        def read_edit_text(self):
            return self.edit_text

        def write_text_no_focus(self, value):
            self.edit_text = value
            return True

        def read_applied_status(self):
            return self.applied_text

        def post_background_enter(self):
            self.applied_text = self.edit_text
            return True

    adapter = FakeAdapter()
    process = SimpleNamespace(pid=1234, version="8.5.0.51219")
    window = SimpleNamespace(hwnd=4567, role=SimpleNamespace(value="main_directory"))
    args = SimpleNamespace(
        route="engine-post",
        status="sensitive test status",
        wparam=None,
        log=tmp_path / "probe.jsonl",
    )
    monkeypatch.setattr(probe, "EmergencyStopRegistry", FakeRegistry)
    monkeypatch.setattr(probe, "ForegroundEventMonitor", FakeForegroundEventMonitor)
    monkeypatch.setattr(probe, "revalidate_process_identity", lambda _: True)
    monkeypatch.setattr(probe, "revalidate_window_identity", lambda *_: True)
    monkeypatch.setattr(
        probe,
        "_snapshot",
        lambda: {"foreground_hwnd": 9, "cursor": [1, 2], "keyboard_state_sha256": "x"},
    )
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)

    result = probe._submit_and_record(args, process, window, adapter)

    record = json.loads(args.log.read_text(encoding="utf-8"))
    assert result == 0
    assert adapter.edit_text == "prior draft"
    assert record["client_state"] == "CLIENT_STATE_CONFIRMED"
    assert record["route_message_dispatched"] is True
    assert record["provider_state_verified"] is False
    assert record["foreground_monitor"] == "win_event_hook"
    assert record["target_foreground_event_count"] == 0
    assert "sensitive test status" not in args.log.read_text(encoding="utf-8")


def test_foreground_monitor_covers_staging_submit_and_draft_restore(
    monkeypatch, tmp_path
):
    events = []

    class FakeRegistry:
        def is_stopped(self, *_):
            return False

    class RecordingMonitor(FakeForegroundEventMonitor):
        def start(self):
            events.append("monitor_start")
            super().start()

        def stop(self, *, minimum_duration=2.0):
            events.append("monitor_stop")
            return super().stop(minimum_duration=minimum_duration)

    class FakeAdapter:
        edit_hwnd = 2345
        combo_hwnd = 3456

        def __init__(self):
            self.edit_text = "prior draft"
            self.applied_text = "previous status"

        def controls_are_current(self, *_):
            return True

        def read_edit_text(self):
            return self.edit_text

        def write_text_no_focus(self, value):
            events.append("restore" if value == "prior draft" else "stage")
            self.edit_text = value
            return True

        def read_applied_status(self):
            return self.applied_text

        def post_background_enter(self):
            events.append("submit")
            self.applied_text = self.edit_text
            return True

    adapter = FakeAdapter()
    process = SimpleNamespace(pid=1234, version="8.5.0.51219")
    window = SimpleNamespace(hwnd=4567, role=SimpleNamespace(value="main_directory"))
    args = SimpleNamespace(
        route="engine-post",
        status="test status",
        wparam=None,
        log=tmp_path / "probe.jsonl",
    )
    monkeypatch.setattr(probe, "EmergencyStopRegistry", FakeRegistry)
    monkeypatch.setattr(probe, "ForegroundEventMonitor", RecordingMonitor)
    monkeypatch.setattr(probe, "revalidate_process_identity", lambda _: True)
    monkeypatch.setattr(probe, "revalidate_window_identity", lambda *_: True)
    monkeypatch.setattr(
        probe,
        "_snapshot",
        lambda: {"foreground_hwnd": 9, "cursor": [1, 2], "keyboard_state_sha256": "x"},
    )
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)

    result = probe._submit_and_record(args, process, window, adapter)

    assert result == 0
    assert events.index("monitor_start") < events.index("stage")
    assert events.index("submit") < events.index("restore")
    assert events.index("restore") < events.index("monitor_stop")


def test_partial_stage_failure_restores_the_original_edit_text(monkeypatch, tmp_path):
    class FakeRegistry:
        def is_stopped(self, *_):
            return False

    class FakeAdapter:
        edit_hwnd = 2345
        combo_hwnd = 3456

        def __init__(self):
            self.edit_text = "prior draft"
            self.writes = 0

        def read_edit_text(self):
            return self.edit_text

        def write_text_no_focus(self, value):
            self.writes += 1
            self.edit_text = value
            return self.writes != 1

        def controls_are_current(self, *_):
            return True

    adapter = FakeAdapter()
    process = SimpleNamespace(pid=1234, version="8.5.0.51219")
    window = SimpleNamespace(hwnd=4567, role=SimpleNamespace(value="main_directory"))
    args = SimpleNamespace(
        route="combo-post",
        status="test status",
        wparam=None,
        log=tmp_path / "probe.jsonl",
    )
    monkeypatch.setattr(probe, "EmergencyStopRegistry", FakeRegistry)
    monkeypatch.setattr(probe, "ForegroundEventMonitor", FakeForegroundEventMonitor)
    monkeypatch.setattr(probe, "revalidate_process_identity", lambda _: True)
    monkeypatch.setattr(probe, "revalidate_window_identity", lambda *_: True)
    monkeypatch.setattr(probe, "_snapshot", lambda: {"foreground_hwnd": 9})

    result = probe._submit_and_record(args, process, window, adapter)

    assert result == 4
    assert adapter.edit_text == "prior draft"
    record = json.loads(args.log.read_text(encoding="utf-8"))
    assert record["failure_code"] == "TEXT_STAGE_FAILED"
    assert record["prior_edit_text_restored"] is True


def test_route_exception_is_logged_as_unknown_and_restores_edit_text(
    monkeypatch, tmp_path
):
    class FakeRegistry:
        def is_stopped(self, *_):
            return False

    class FakeAdapter:
        edit_hwnd = 2345
        combo_hwnd = 3456

        def __init__(self):
            self.edit_text = "prior draft"

        def read_edit_text(self):
            return self.edit_text

        def write_text_no_focus(self, value):
            self.edit_text = value
            return True

        def controls_are_current(self, *_):
            return True

        def post_background_enter(self):
            raise OSError("simulated send failure")

    adapter = FakeAdapter()
    process = SimpleNamespace(pid=1234, version="8.5.0.51219")
    window = SimpleNamespace(hwnd=4567, role=SimpleNamespace(value="main_directory"))
    args = SimpleNamespace(
        route="engine-post",
        status="private test text",
        wparam=None,
        log=tmp_path / "probe.jsonl",
    )
    monkeypatch.setattr(probe, "EmergencyStopRegistry", FakeRegistry)
    monkeypatch.setattr(probe, "ForegroundEventMonitor", FakeForegroundEventMonitor)
    monkeypatch.setattr(probe, "revalidate_process_identity", lambda _: True)
    monkeypatch.setattr(probe, "revalidate_window_identity", lambda *_: True)
    monkeypatch.setattr(probe, "_snapshot", lambda: {"foreground_hwnd": 9})

    result = probe._submit_and_record(args, process, window, adapter)

    assert result == 5
    assert adapter.edit_text == "prior draft"
    content = args.log.read_text(encoding="utf-8")
    record = json.loads(content)
    assert record["client_state"] == "EFFECT_UNKNOWN"
    assert record["failure_code"] == "SUBMIT_ROUTE_EXCEPTION"
    assert record["exception_type"] == "OSError"
    assert record["foreground_monitor_stopped"] is True
    assert "private test text" not in content


def test_probe_refuses_to_submit_when_foreground_observer_cannot_start(
    monkeypatch, tmp_path, capsys
):
    class FakeRegistry:
        def is_stopped(self, *_):
            return False

    class FakeAdapter:
        edit_hwnd = 2345
        combo_hwnd = 3456

        def __init__(self):
            self.edit_text = "prior draft"
            self.applied_text = "previous status"

        def controls_are_current(self, *_):
            return True

        def read_edit_text(self):
            return self.edit_text

        def write_text_no_focus(self, value):
            self.edit_text = value
            return True

        def read_applied_status(self):
            return self.applied_text

    class UnavailableMonitor:
        def __init__(self, *_):
            pass

        def start(self):
            raise OSError("monitor unavailable")

    adapter = FakeAdapter()
    process = SimpleNamespace(pid=1234, version="8.5.0.51219")
    window = SimpleNamespace(hwnd=4567, role=SimpleNamespace(value="main_directory"))
    args = SimpleNamespace(
        route="combo-post",
        status="test status",
        wparam=None,
        log=tmp_path / "probe.jsonl",
    )
    monkeypatch.setattr(probe, "EmergencyStopRegistry", FakeRegistry)
    monkeypatch.setattr(probe, "ForegroundEventMonitor", UnavailableMonitor)
    monkeypatch.setattr(probe, "revalidate_process_identity", lambda _: True)
    monkeypatch.setattr(probe, "revalidate_window_identity", lambda *_: True)
    monkeypatch.setattr(probe, "_snapshot", lambda: {"foreground_hwnd": 9})

    result = probe._submit_and_record(args, process, window, adapter)

    assert result == 9
    assert adapter.edit_text == "prior draft"
    record = json.loads(args.log.read_text(encoding="utf-8"))
    assert record["failure_code"] == "FOREGROUND_MONITOR_UNAVAILABLE"
    assert record["foreground_monitor_error"] == "OSError"
    assert "Foreground event monitoring is unavailable" in capsys.readouterr().err
