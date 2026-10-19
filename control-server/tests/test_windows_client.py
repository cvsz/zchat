from __future__ import annotations

import pytest

from camfrog_control_server.windows_client import WindowsCamfrogClientProvider


class FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float) -> int:
        assert timeout == 10
        return 0


def test_windows_provider_launches_only_configured_executable_and_dispatches_room_uri(
    tmp_path,
) -> None:
    executable = tmp_path / "Camfrog.exe"
    executable.write_bytes(b"Not a valid Windows executable.")
    launched: list[tuple[list[str], dict]] = []
    uris: list[str] = []
    process = FakeProcess()

    def popen(args: list[str], **kwargs) -> FakeProcess:
        launched.append((args, kwargs))
        return process

    provider = WindowsCamfrogClientProvider(
        str(executable),
        platform_name="win32",
        popen=popen,
        uri_launcher=uris.append,
    )
    assert provider.process_state() == "unknown"
    assert provider.execute({"operation": "launch"}) == ("succeeded", "ok")
    assert provider.process_state() == "running"
    assert launched[0][0] == [str(executable)]
    assert launched[0][1]["shell"] is False
    assert provider.execute({"operation": "join_room", "payload": {"room": "ZeaZ Dev"}}) == (
        "effect_unknown",
        "handoff_accepted",
    )
    assert provider.execute({"operation": "add_friend", "payload": {"nickname": "Friend One"}}) == (
        "effect_unknown",
        "handoff_accepted",
    )
    assert provider.execute({"operation": "open_im", "payload": {"nickname": "Friend One"}}) == (
        "effect_unknown",
        "handoff_accepted",
    )
    assert uris == [
        "camfrog://join_room/?name=ZeaZ%20Dev",
        "camfrog:add:Friend%20One",
        "camfrog:im:Friend%20One",
    ]
    assert provider.execute({"operation": "stop"}) == ("succeeded", "ok")
    assert provider.process_state() == "stopped"
    assert process.terminated
    assert provider.execute({"operation": "stop"}) == ("failed", "client_not_found")


def test_windows_provider_requires_existing_absolute_exe_and_windows(tmp_path) -> None:
    executable = tmp_path / "Camfrog.exe"
    executable.write_bytes(b"Not a valid Windows executable.")
    with pytest.raises(ValueError, match="only on Windows"):
        WindowsCamfrogClientProvider(str(executable), platform_name="linux")
    with pytest.raises(ValueError, match="absolute .exe"):
        WindowsCamfrogClientProvider(str(tmp_path / "missing.exe"), platform_name="win32")


def test_windows_provider_does_not_report_launch_success_after_immediate_exit(tmp_path) -> None:
    executable = tmp_path / "Camfrog.exe"
    executable.write_bytes(b"Not a valid Windows executable.")
    process = FakeProcess()
    process.returncode = 1
    provider = WindowsCamfrogClientProvider(
        str(executable),
        platform_name="win32",
        popen=lambda *_args, **_kwargs: process,
    )

    assert provider.execute({"operation": "launch"}) == ("failed", "execution_failed")
    assert provider.process_state() == "unknown"


def test_windows_provider_does_not_implement_unverified_leave_room(tmp_path) -> None:
    executable = tmp_path / "Camfrog.exe"
    executable.write_bytes(b"Not a valid Windows executable.")
    provider = WindowsCamfrogClientProvider(
        str(executable), platform_name="win32", popen=lambda *_args, **_kwargs: None
    )
    assert provider.execute({"operation": "leave_room"}) == (
        "failed",
        "provider_not_configured",
    )


def test_windows_provider_restart_requires_a_process_it_started(tmp_path) -> None:
    executable = tmp_path / "Camfrog.exe"
    executable.write_bytes(b"Not a valid Windows executable.")
    processes = [FakeProcess(), FakeProcess()]
    provider = WindowsCamfrogClientProvider(
        str(executable),
        platform_name="win32",
        popen=lambda *_args, **_kwargs: processes.pop(0),
    )
    assert provider.execute({"operation": "restart"}) == ("failed", "client_not_found")
    assert provider.execute({"operation": "launch"}) == ("succeeded", "ok")
    assert provider.execute({"operation": "restart"}) == ("succeeded", "ok")
