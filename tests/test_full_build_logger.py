"""Tests for the opt-in local full-build log wrapper."""

from __future__ import annotations

import datetime as dt
import io
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Self

from tools import full_build_logger


class _Child:
    def __init__(self, output: bytes, return_code: int) -> None:
        self.stdout = io.BytesIO(output)
        self.return_code = return_code

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.stdout.close()

    def wait(self) -> int:
        return self.return_code


def test_logger_streams_and_saves_full_child_output(
    monkeypatch, tmp_path: Path
) -> None:
    batch = tmp_path / "full build.bat"
    batch.write_bytes(b"@echo off\r\n")
    output = b"build output\r\n"
    child = _Child(output, 7)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(command: list[str], **kwargs: object) -> _Child:
        calls.append((command, kwargs))
        return child

    console = SimpleNamespace(buffer=io.BytesIO())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ComSpec", "cmd.exe")
    monkeypatch.setattr(full_build_logger.subprocess, "Popen", popen)
    monkeypatch.setattr(full_build_logger.sys, "stdout", console)

    assert full_build_logger.main([str(batch), "--release"]) == 7

    command, kwargs = calls[0]
    assert command == ["cmd.exe", "/d", "/c", "call", str(batch), "--release"]
    assert kwargs["cwd"] == batch.parent
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["env"]["ZCHAT_FULL_BUILD_LOG_CHILD"] == "1"
    logs = list((tmp_path / "local" / "ZChat" / "build-logs").glob("*.log"))
    assert len(logs) == 1
    assert logs[0].read_bytes() == output + b"\r\n[full-build] exit code: 7\r\n"
    assert output in console.buffer.getvalue()


def test_logger_requires_a_local_log_directory(monkeypatch, tmp_path: Path) -> None:
    batch = tmp_path / "full-build.bat"
    batch.touch()
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("TEMP", raising=False)

    assert full_build_logger.main([str(batch)]) == 2


def test_build_estimate_uses_median_of_successful_logs(tmp_path: Path) -> None:
    now = dt.datetime.now().astimezone()
    for seconds in (500, 700, 900):
        started = now - dt.timedelta(seconds=seconds)
        name = started.strftime("full-build-%Y%m%d-%H%M%S-%f-1234.log")
        log = tmp_path / name
        log.write_text("FULL BUILD COMPLETE\n", encoding="ascii")
        os.utime(log, (now.timestamp(), now.timestamp()))

    assert full_build_logger._estimate_duration_seconds(tmp_path) == 700


def test_build_estimate_falls_back_and_formats_countdown(tmp_path: Path) -> None:
    assert full_build_logger._estimate_duration_seconds(tmp_path) == 600
    assert full_build_logger._format_estimate(599) == "09:59"
    assert full_build_logger._format_estimate(0) == "00:00"
