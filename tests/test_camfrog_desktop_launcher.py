"""Portable guard tests for the Windows-only desktop launcher."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "launch_camfrog_isolated_desktop",
    ROOT / "tools" / "launch_camfrog_isolated_desktop.py",
)
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_desktop_name_rejects_default_and_path_like_values():
    with pytest.raises(launcher.LauncherError):
        launcher._validate_desktop_name("Default")
    with pytest.raises(launcher.LauncherError):
        launcher._validate_desktop_name("WinSta0\\Default")
    assert launcher._validate_desktop_name("ZChatCamfrogTest") == "ZChatCamfrogTest"


def test_launcher_reports_windows_runtime_blocker_without_touching_processes(
    monkeypatch, capsys
):
    monkeypatch.setattr(launcher.sys, "platform", "linux")

    assert launcher.main(["launch", "--exe", "Camfrog Video Chat.exe"]) == 2
    assert "BLOCKED" in capsys.readouterr().err


def test_switch_test_returns_to_default_and_closes_desktop_handles(monkeypatch):
    class FakeUser32:
        def __init__(self):
            self.switched = []
            self.closed = []

        def OpenDesktopW(self, name, _flags, _inherit, _access):
            return {"ZChatCamfrogTest": 11, "Default": 22}[name]

        def SwitchDesktop(self, handle):
            self.switched.append(handle)
            return 1

        def CloseDesktop(self, handle):
            self.closed.append(handle)

    user32 = FakeUser32()
    monkeypatch.setattr(launcher, "_windows_api", lambda: (user32, object()))
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)

    result = launcher.switch_for_test("ZChatCamfrogTest", 5)

    assert user32.switched == [11, 22]
    assert user32.closed == [22, 11]
    assert result["returned_to_default"] is True
    assert result["normal_operation_verified"] is False
