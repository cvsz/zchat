from __future__ import annotations

import json
from types import SimpleNamespace

from tools import probe_submit_routes as probe


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
    ]


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
        route="combo-post",
        status="sensitive test status",
        wparam=None,
        log=tmp_path / "probe.jsonl",
    )
    monkeypatch.setattr(probe, "EmergencyStopRegistry", FakeRegistry)
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
    assert record["provider_state_verified"] is False
    assert "sensitive test status" not in args.log.read_text(encoding="utf-8")


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
        route="combo-post",
        status="private test text",
        wparam=None,
        log=tmp_path / "probe.jsonl",
    )
    monkeypatch.setattr(probe, "EmergencyStopRegistry", FakeRegistry)
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
    assert "private test text" not in content
