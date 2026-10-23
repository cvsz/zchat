"""Process and Windows GUI regression tests for runtime revalidation.

Process identity and ownership checks run on every platform. HWND and real
window checks run only on Windows using the local Tk fixture. None of these
tests submits a Camfrog status or terminates a non-fixture process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Queue

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from camfrog_windows_runtime import (
    CamfrogClientAttachment,
    ClassifiedWindow,
    ProcessIdentity,
    WindowRole,
    WindowsCamfrogWindowClassifier,
    revalidate_process_identity,
    revalidate_window_identity,
)

FIXTURE_SCRIPT = Path(__file__).parent / "fixtures" / "tk_fixture_window.py"
TESTAPPS_CACHE = Path(__file__).parent / "fixtures" / "_cache" / "PywinautoTestapps"
TESTAPPS_PIN = Path(__file__).parent / "fixtures" / "pywinauto_testapps.pin"


def _identity(pid: int) -> ProcessIdentity:
    import psutil

    process = psutil.Process(pid)
    return ProcessIdentity(
        pid=pid,
        create_time=process.create_time(),
        exe_path=process.exe(),
        publisher="test-fixture",
        signature_valid=False,
        version="0",
        is_agent_launched=False,
    )


def _configured_testapp_exe() -> Path | None:
    configured = os.environ.get("ZCHAT_TESTAPP_EXE")
    if not configured:
        return None
    executable = Path(configured).resolve(strict=True)
    cache = TESTAPPS_CACHE.resolve(strict=True)
    try:
        executable.relative_to(cache)
    except ValueError as exc:
        raise ValueError(
            "ZCHAT_TESTAPP_EXE must point inside the pinned PywinautoTestapps cache."
        ) from exc
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("ZCHAT_TESTAPP_EXE must point to an existing .exe file.")
    pin = {
        key: value
        for key, value in (line.split("=", 1) for line in TESTAPPS_PIN.read_text().splitlines())
    }
    head = subprocess.run(
        ["git", "-C", str(cache), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != pin["commit"]:
        raise ValueError(f"Test-app checkout is not at the pinned commit {pin['commit']}.")
    return executable


@pytest.fixture
def process_fixture():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"]
    )
    try:
        yield process, _identity(process.pid)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.live
def test_live_process_identity_revalidates(process_fixture):
    _process, identity = process_fixture
    assert revalidate_process_identity(identity) is True


@pytest.mark.live
def test_pid_reuse_create_time_mismatch_fails_closed(process_fixture):
    _process, identity = process_fixture
    reused = ProcessIdentity(
        pid=identity.pid,
        create_time=identity.create_time - 5.0,
        exe_path=identity.exe_path,
        publisher=identity.publisher,
        signature_valid=False,
        version=identity.version,
    )
    assert revalidate_process_identity(reused) is False


@pytest.mark.live
def test_attachment_fails_after_target_process_exits(process_fixture):
    process, identity = process_fixture
    attachment = CamfrogClientAttachment(identity, discovery=None, classifier=None)
    assert attachment.revalidate() is True
    process.terminate()
    process.wait(timeout=5)
    assert attachment.revalidate() is False
    with pytest.raises(ProcessLookupError):
        attachment.get_windows()
    with pytest.raises(ProcessLookupError):
        attachment.get_runtime_state()


@pytest.mark.live
def test_terminate_refuses_a_process_not_launched_by_agent(process_fixture):
    process, identity = process_fixture
    attachment = CamfrogClientAttachment(identity, discovery=None, classifier=None)
    with pytest.raises(PermissionError, match="Ownership restriction violated"):
        attachment.terminate(force=True)
    assert process.poll() is None
    assert revalidate_process_identity(identity) is True


def _require_windows() -> None:
    if sys.platform != "win32":
        pytest.skip("HWND integration cases require Windows.")
    if os.environ.get("ZCHAT_RUN_WINDOWS_GUI_TESTS") != "1":
        pytest.skip(
            "GUI fixtures are opt-in; set ZCHAT_RUN_WINDOWS_GUI_TESTS=1 only "
            "in a dedicated Windows test session."
        )


def _hwnds_for_pid(pid: int) -> list[int]:
    import win32gui
    import win32process

    found: list[int] = []

    def collect(hwnd, _extra):
        if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
            found.append(hwnd)
        return True

    win32gui.EnumWindows(collect, None)
    return found


def _wait_for_hwnd(pid: int, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        handles = _hwnds_for_pid(pid)
        if handles:
            return handles[0]
        time.sleep(0.1)
    raise AssertionError(f"No top-level window appeared for fixture PID {pid}.")


class WindowFixture:
    def __init__(self):
        testapp_exe = _configured_testapp_exe()
        if testapp_exe is not None:
            # Optional prebuilt PywinautoTestapps executable. The tests own
            # this child process and only inspect/terminate that fixture.
            self.process = subprocess.Popen(
                [str(testapp_exe)], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.scripted = False
        else:
            self.ready: Queue[str] = Queue()
            self.process = subprocess.Popen(
                [sys.executable, str(FIXTURE_SCRIPT), "zchat-runtime-fixture"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            threading.Thread(
                target=lambda: self.ready.put(self.process.stdout.readline()),
                daemon=True,
            ).start()
            self.scripted = True
        try:
            if self.scripted and self.ready.get(timeout=10).strip() != "READY":
                raise RuntimeError("Tk fixture did not report readiness.")
            self.hwnd = _wait_for_hwnd(self.process.pid)
            self.identity = _identity(self.process.pid)
            self.window = ClassifiedWindow(
                hwnd=self.hwnd,
                role=WindowRole.UNKNOWN,
                title="zchat-runtime-fixture",
                pid=self.process.pid,
                is_visible=True,
            )
        except Exception:
            self.cleanup()
            raise

    def close_window_keep_process(self) -> None:
        if not self.scripted:
            pytest.skip("Window closure while the process remains alive needs the Tk fixture.")
        assert self.process.stdin is not None
        self.process.stdin.write("close\n")
        self.process.stdin.flush()

    def cleanup(self) -> None:
        if self.process.poll() is None:
            try:
                assert self.process.stdin is not None
                self.process.stdin.write("exit\n")
                self.process.stdin.flush()
                self.process.wait(timeout=3)
            except (AssertionError, OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()


def test_pywinauto_testapps_checkout_is_pinned_when_fetched():
    if not TESTAPPS_CACHE.exists():
        pytest.skip("Optional fixture source is absent; run fetch_pywinauto_testapps.ps1 to fetch it.")
    pin = {
        key: value
        for key, value in (line.split("=", 1) for line in TESTAPPS_PIN.read_text().splitlines())
    }
    head = subprocess.run(
        ["git", "-C", str(TESTAPPS_CACHE), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == pin["commit"]
    assert (TESTAPPS_CACHE / "LICENSE").is_file()


@pytest.fixture
def window_fixture():
    _require_windows()
    fixture = WindowFixture()
    try:
        yield fixture
    finally:
        fixture.cleanup()


@pytest.mark.live
def test_live_window_and_process_revalidate(window_fixture):
    assert revalidate_process_identity(window_fixture.identity) is True
    assert revalidate_window_identity(window_fixture.window, window_fixture.identity) is True


@pytest.mark.live
def test_stale_hwnd_after_process_exit_fails_closed(window_fixture):
    window_fixture.process.terminate()
    window_fixture.process.wait(timeout=5)
    assert revalidate_process_identity(window_fixture.identity) is False
    assert revalidate_window_identity(window_fixture.window, window_fixture.identity) is False


@pytest.mark.live
def test_closed_window_with_live_process_fails_closed(window_fixture):
    import win32gui

    window_fixture.close_window_keep_process()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and win32gui.IsWindow(window_fixture.hwnd):
        time.sleep(0.05)
    assert not win32gui.IsWindow(window_fixture.hwnd)
    assert revalidate_process_identity(window_fixture.identity) is True
    assert revalidate_window_identity(window_fixture.window, window_fixture.identity) is False


@pytest.mark.live
def test_foreign_window_owner_fails_closed(window_fixture):
    other = WindowFixture()
    try:
        assert revalidate_window_identity(other.window, window_fixture.identity) is False
        assert revalidate_window_identity(window_fixture.window, other.identity) is False
    finally:
        other.cleanup()


@pytest.mark.live
def test_attachment_classifies_live_fixture_window(window_fixture):
    attachment = CamfrogClientAttachment(
        window_fixture.identity,
        discovery=None,
        classifier=WindowsCamfrogWindowClassifier(),
    )
    windows = attachment.get_windows()
    assert any(window.hwnd == window_fixture.hwnd for window in windows)


@pytest.mark.live
def test_attachment_fails_when_fixture_window_is_closed(window_fixture):
    import win32gui

    attachment = CamfrogClientAttachment(
        window_fixture.identity,
        discovery=None,
        classifier=WindowsCamfrogWindowClassifier(),
    )
    window_fixture.close_window_keep_process()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and win32gui.IsWindow(window_fixture.hwnd):
        time.sleep(0.05)
    assert not win32gui.IsWindow(window_fixture.hwnd)
    assert attachment.revalidate() is True
    assert attachment.get_windows() == []
