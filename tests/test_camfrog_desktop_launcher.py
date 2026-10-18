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
