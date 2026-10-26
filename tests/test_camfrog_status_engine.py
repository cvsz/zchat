"""Unit tests for camfrog_status_engine.py.

Verifies:
- Resource-scoped synchronization (StatusResourceLock)
- Strict background execution without focus stealing, mouse/keyboard injection, or clipboard mutations
- Authoritative status commit verification via CComboBoxTS item state
- Fail-closed handling for missing process, missing window controls, write failures, and lock timeouts
- Non-claim rule: text write or message dispatch alone does NOT prove Camfrog applied the status
- Configuration contract validation and safe defaults
"""

from __future__ import annotations

import threading
import time
import multiprocessing
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import camfrog_status_engine as cse
from camfrog_emergency_stop import EmergencyStopRegistry, EmergencyStopScope
from camfrog_status_engine import (
    BackgroundStatusExecutor,
    ExecutionMode,
    StatusControlAdapter,
    StatusExecutionEvidence,
    StatusResourceLock,
    StatusResultState,
    StatusScheduler,
    validate_status_config,
)
from camfrog_windows_runtime import (
    ClassifiedWindow,
    ProcessIdentity,
    WindowRole,
)


@pytest.fixture(autouse=True)
def isolate_emergency_stop_store(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ZCHAT_EMERGENCY_STOP_DB", str(tmp_path / "emergency-stops.sqlite3")
    )
    registry = EmergencyStopRegistry()
    registry._reset_for_testing()
    yield
    registry._reset_for_testing()


# ---------- StatusResourceLock Tests ----------


def test_status_resource_lock_basic_acquire_and_release():
    lock = StatusResourceLock(resource_key="test_res_1", timeout_seconds=1.0)
    assert lock.acquire() is True
    lock.release()


def test_status_resource_lock_context_manager():
    with StatusResourceLock(resource_key="test_res_2", timeout_seconds=1.0) as lock:
        assert lock._acquired_thread is True


def test_windows_status_resource_lock_preserves_pointer_width_handle(monkeypatch):
    from ctypes import wintypes

    handle = 0x1234567887654321
    create_mutex = MagicMock(return_value=handle)
    wait_for_single_object = MagicMock(return_value=0x00000000)
    release_mutex = MagicMock(return_value=True)
    close_handle = MagicMock(return_value=True)
    kernel32 = SimpleNamespace(
        CreateMutexW=create_mutex,
        WaitForSingleObject=wait_for_single_object,
        ReleaseMutex=release_mutex,
        CloseHandle=close_handle,
    )
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(
        cse.ctypes,
        "WinDLL",
        MagicMock(return_value=kernel32),
        raising=False,
    )
    lock = StatusResourceLock(resource_key="pointer_width", timeout_seconds=1.0)

    assert lock.acquire() is True
    assert create_mutex.restype is wintypes.HANDLE
    assert wait_for_single_object.argtypes == (wintypes.HANDLE, wintypes.DWORD)
    assert kernel32.CloseHandle.argtypes == (wintypes.HANDLE,)
    assert create_mutex.call_args.args[2] == "Local\\ZChat_StatusLock_pointer_width"
    assert wait_for_single_object.call_args.args[0] == handle
    lock.release()
    release_mutex.assert_called_once_with(handle)
    close_handle.assert_called_once_with(handle)


def test_windows_status_resource_lock_closes_handle_on_timeout(monkeypatch):
    handle = 0x1234567887654321
    close_handle = MagicMock(return_value=True)
    kernel32 = SimpleNamespace(
        CreateMutexW=MagicMock(return_value=handle),
        WaitForSingleObject=MagicMock(return_value=0x00000102),
        ReleaseMutex=MagicMock(return_value=True),
        CloseHandle=close_handle,
    )
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(
        cse.ctypes,
        "WinDLL",
        MagicMock(return_value=kernel32),
        raising=False,
    )
    lock = StatusResourceLock(resource_key="timeout", timeout_seconds=1.0)

    assert lock.acquire() is False
    assert lock._acquired_thread is False
    assert lock._mutex_handle is None
    close_handle.assert_called_once_with(handle)


def test_status_resource_lock_blocks_concurrent_threads():
    barrier = threading.Barrier(2)
    acquired_second = []

    def holder():
        with StatusResourceLock(resource_key="test_res_3", timeout_seconds=2.0):
            barrier.wait()
            time.sleep(0.3)

    def contender():
        barrier.wait()
        start = time.monotonic()
        try:
            with StatusResourceLock(resource_key="test_res_3", timeout_seconds=0.1):
                acquired_second.append(True)
        except TimeoutError:
            acquired_second.append(False)

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=contender)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert acquired_second == [False]


def _hold_status_resource_lock_in_child(resource_key, acquired, release):
    lock = StatusResourceLock(resource_key=resource_key, timeout_seconds=5.0)
    if not lock.acquire():
        return
    try:
        acquired.set()
        release.wait(8.0)
    finally:
        lock.release()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires a Windows named mutex (Windows-only feature)",
)
def test_windows_status_resource_lock_serializes_separate_processes():
    context = multiprocessing.get_context("spawn")
    resource_key = f"process_{uuid.uuid4().hex}"
    acquired = context.Event()
    release = context.Event()
    child = context.Process(
        target=_hold_status_resource_lock_in_child,
        args=(resource_key, acquired, release),
    )
    child.start()
    try:
        assert acquired.wait(10.0), "child did not acquire the named mutex"
        contender = StatusResourceLock(resource_key=resource_key, timeout_seconds=0.1)
        assert contender.acquire() is False
    finally:
        release.set()
        child.join(10.0)
        if child.is_alive():
            child.terminate()
            child.join(5.0)

    assert child.exitcode == 0


# ---------- StatusControlAdapter Non-Windows Fallback Tests ----------


def test_status_control_adapter_linux_safety():
    adapter = StatusControlAdapter(combo_hwnd=123, edit_hwnd=456)
    # On non-Windows platforms, low-level Win32 calls safely return empty/False
    assert adapter.write_text_no_focus("Hello") is False
    assert adapter.read_edit_text() == ""
    assert adapter.read_applied_status() == ""
    assert adapter.post_background_enter() is False


def test_post_background_enter_targets_edit_post_route_parameters(monkeypatch):
    calls = []
    user32 = SimpleNamespace(
        PostMessageW=lambda *args: calls.append(args) or 1,
    )
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(
        cse.ctypes,
        "windll",
        SimpleNamespace(user32=user32),
        raising=False,
    )
    adapter = StatusControlAdapter(combo_hwnd=123, edit_hwnd=456)
    monkeypatch.setattr(adapter, "controls_are_current", lambda: True)

    assert adapter.post_background_enter() is True
    assert calls == [
        (456, 0x0100, 0x0D, 0x001C0001),
        (456, 0x0101, 0x0D, 0xC01C0001),
    ]


def test_status_control_adapter_rejects_stale_or_recycled_control_handles(monkeypatch):
    import sys

    owner_pids = {10: 55, 11: 55}
    control_ids = {10: 1436, 11: 1001}
    win32gui = SimpleNamespace(
        IsWindow=lambda hwnd: hwnd in owner_pids,
        IsChild=lambda parent, hwnd: parent == 99 and hwnd in owner_pids,
        GetDlgCtrlID=lambda hwnd: control_ids[hwnd],
        GetClassName=lambda hwnd: "CComboBoxTS" if hwnd == 10 else "Edit",
    )
    win32process = SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, owner_pids[hwnd])
    )
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setitem(sys.modules, "win32process", win32process)
    adapter = StatusControlAdapter(
        combo_hwnd=10,
        edit_hwnd=11,
        expected_pid=55,
        parent_hwnd=99,
    )

    assert adapter.controls_are_current() is True
    owner_pids[11] = 56
    assert adapter.controls_are_current() is False


def test_status_control_adapter_rejects_control_class_or_id_mismatch(monkeypatch):
    import sys

    control_ids = {10: 1436, 11: 1001}
    classes = {10: "CComboBoxTS", 11: "Edit"}
    win32gui = SimpleNamespace(
        IsWindow=lambda hwnd: hwnd in control_ids,
        IsChild=lambda parent, hwnd: parent == 99 and hwnd in control_ids,
        GetDlgCtrlID=lambda hwnd: control_ids[hwnd],
        GetClassName=lambda hwnd: classes[hwnd],
    )
    win32process = SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, 55)
    )
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setitem(sys.modules, "win32process", win32process)
    adapter = StatusControlAdapter(
        combo_hwnd=10,
        edit_hwnd=11,
        expected_pid=55,
        parent_hwnd=99,
    )

    classes[10] = "UnrelatedControl"
    assert adapter.controls_are_current() is False
    classes[10] = "CComboBoxTS"
    control_ids[11] = 9999
    assert adapter.controls_are_current() is False


def test_send_message_timeout_bounds_and_returns_message_result(monkeypatch):
    import ctypes

    calls = []

    def send_message(hwnd, message, wparam, lparam, flags, timeout_ms, result_ptr):
        calls.append((hwnd.value, message, wparam, lparam, flags, timeout_ms))
        ctypes.cast(result_ptr, ctypes.POINTER(ctypes.c_size_t))[0] = 23
        return 1

    send = MagicMock(side_effect=send_message)
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(
        cse.ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(SendMessageTimeoutW=send)),
        raising=False,
    )

    assert cse._send_message_timeout(123, 0x000C, 4, 5, 250) == 23
    assert calls == [(123, 0x000C, 4, 5, 0x0002, 250)]


def test_send_message_timeout_fails_closed_when_windows_times_out(monkeypatch):
    send = MagicMock(return_value=0)
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(
        cse.ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(SendMessageTimeoutW=send)),
        raising=False,
    )

    assert cse._send_message_timeout(123, 0x000C, 0, 0, 250) is None


# ---------- BackgroundStatusExecutor Tests ----------


def test_camfrog_adapter_does_not_claim_verified_strict_submit():
    adapter = StatusControlAdapter(combo_hwnd=1, edit_hwnd=2)
    assert adapter.supports_strict_background_submit() is False


def _make_mock_resolver(current_applied="Initial Status", target_readback="New Status"):
    proc = ProcessIdentity(
        pid=5555,
        create_time=100.0,
        exe_path="C:\\Camfrog\\Camfrog Video Chat.exe",
        publisher="Camfrog LLC",
        signature_valid=True,
        version="8.5.0.51219",
        is_agent_launched=False,
    )
    main_win = ClassifiedWindow(
        hwnd=9999,
        role=WindowRole.MAIN_DIRECTORY,
        title="zdevz (Online)",
        pid=5555,
        is_visible=True,
        signatures=["directory_tree", "status_combo"],
    )

    mock_adapter = MagicMock(spec=StatusControlAdapter)
    mock_adapter.combo_hwnd = 8881
    mock_adapter.edit_hwnd = 8882
    mock_adapter.write_text_no_focus.return_value = True
    mock_adapter.read_edit_text.return_value = target_readback
    mock_adapter.post_background_enter.return_value = True
    mock_adapter.supports_strict_background_submit.return_value = True
    mock_adapter.read_applied_status.return_value = current_applied

    resolver = MagicMock()
    resolver.resolve_status_controls.return_value = (proc, main_win, mock_adapter)
    return resolver, proc, main_win, mock_adapter


def _mock_valid_target(executor):
    """Keep executor tests focused on the operation after discovery.

    The resolver fixture uses synthetic process/window identities. Windows
    identity revalidation is covered separately with explicit Win32 mocks.
    """
    executor._target_identity_is_current = MagicMock(return_value=True)
    return executor


def test_executor_successful_status_application(monkeypatch):
    # Keep this executor-path test independent of the real Windows desktop.
    monkeypatch.setattr(cse, "_foreground_window_handle", lambda: 7001)
    new_status = "Available for chat"
    resolver, proc, main_win, mock_adapter = _make_mock_resolver(
        current_applied="Old Status",
        target_readback=new_status,
    )
    # After enter, read_applied_status returns the new status
    mock_adapter.read_applied_status.side_effect = ["Old Status", new_status]

    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))
    evidence = executor.execute_status_change(new_status, pid=5555)

    assert evidence.result_state == StatusResultState.CLIENT_STATE_CONFIRMED.value
    assert evidence.verification_source == "client_combo_item_0"
    assert evidence.applied_text == new_status
    assert evidence.client_version == "8.5.0.51219"
    assert evidence.process_identity["pid"] == 5555
    assert evidence.error_code is None
    assert mock_adapter.write_text_no_focus.called
    assert mock_adapter.post_background_enter.called


def test_strict_executor_refuses_unverified_submit_before_staging_text(monkeypatch):
    resolver, _, _, adapter = _make_mock_resolver(
        current_applied="Old Status", target_readback="New Status"
    )
    adapter.supports_strict_background_submit.return_value = False
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(StatusResourceLock, "acquire", lambda _self: True)
    monkeypatch.setattr(StatusResourceLock, "release", lambda _self: None)
    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))

    evidence = executor.execute_status_change("New Status", pid=5555)

    assert evidence.result_state == StatusResultState.BACKGROUND_UNSUPPORTED.value
    assert evidence.error_code == "STRICT_SUBMIT_UNVERIFIED"
    assert evidence.verification_source == "submit_capability"
    assert not adapter.write_text_no_focus.called
    assert not adapter.post_background_enter.called


@pytest.mark.parametrize(
    ("foreground_values", "expected", "result_state"),
    [
        ([7001, 7001, 7001], True, StatusResultState.CLIENT_STATE_CONFIRMED.value),
        ([7001, 7001, 8002], False, StatusResultState.BACKGROUND_UNSUPPORTED.value),
        ([0, 0, 0], None, StatusResultState.BACKGROUND_UNSUPPORTED.value),
    ],
)
def test_executor_records_foreground_snapshot_comparison(
    monkeypatch, foreground_values, expected, result_state
):
    resolver, _, _, adapter = _make_mock_resolver(
        current_applied="Old Status", target_readback="New Status"
    )
    adapter.read_applied_status.side_effect = ["Old Status", "New Status"]
    foreground = iter(foreground_values)
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(GetForegroundWindow=lambda: next(foreground)),
    )
    monkeypatch.setattr(StatusResourceLock, "acquire", lambda _self: True)
    monkeypatch.setattr(StatusResourceLock, "release", lambda _self: None)
    executor = BackgroundStatusExecutor(resolver=resolver)
    executor._target_identity_is_current = MagicMock(return_value=True)

    evidence = executor.execute_status_change("New Status", pid=5555)

    assert evidence.result_state == result_state
    assert evidence.foreground_matches_before_and_after is expected
    if expected is not True:
        expected_error = (
            "FOREGROUND_PRECONDITION_NOT_CONFIRMED"
            if expected is None
            else "FOREGROUND_ISOLATION_NOT_CONFIRMED"
        )
        assert evidence.error_code == expected_error


@pytest.mark.parametrize(
    ("foreground_values", "expected_match"),
    [([7001, 8002], False), ([0, 0], None)],
)
def test_strict_executor_does_not_submit_if_foreground_precondition_fails(
    monkeypatch, foreground_values, expected_match
):
    resolver, _, _, adapter = _make_mock_resolver(
        current_applied="Old Status", target_readback="New Status"
    )
    adapter.read_applied_status.side_effect = ["Old Status"]
    foreground = iter(foreground_values)
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(GetForegroundWindow=lambda: next(foreground)),
    )
    monkeypatch.setattr(StatusResourceLock, "acquire", lambda _self: True)
    monkeypatch.setattr(StatusResourceLock, "release", lambda _self: None)
    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))

    evidence = executor.execute_status_change("New Status", pid=5555)

    assert evidence.result_state == StatusResultState.BACKGROUND_UNSUPPORTED.value
    assert evidence.error_code == "FOREGROUND_PRECONDITION_NOT_CONFIRMED"
    assert evidence.foreground_matches_before_submit is expected_match
    assert adapter.write_text_no_focus.called
    assert not adapter.post_background_enter.called


def test_executor_duplicate_status_fast_path():
    same_status = "Currently In Meeting"
    resolver, proc, main_win, mock_adapter = _make_mock_resolver(
        current_applied=same_status,
        target_readback=same_status,
    )

    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))
    evidence = executor.execute_status_change(same_status, pid=5555)

    assert evidence.result_state == StatusResultState.CLIENT_STATE_CONFIRMED.value
    assert evidence.verification_source == "client_combo_state_already_active"
    assert evidence.applied_text == same_status
    # UI writes and post-enter should be skipped when already active
    assert not mock_adapter.write_text_no_focus.called
    assert not mock_adapter.post_background_enter.called


def test_target_identity_validation_requires_process_window_and_controls(
    monkeypatch,
):
    resolver, proc, main_win, adapter = _make_mock_resolver()
    monkeypatch.setattr(cse.sys, "platform", "win32")
    process_check = MagicMock(return_value=True)
    window_check = MagicMock(return_value=True)
    monkeypatch.setattr(cse, "revalidate_process_identity", process_check)
    monkeypatch.setattr(cse, "revalidate_window_identity", window_check)
    adapter.controls_are_current.return_value = True

    assert BackgroundStatusExecutor._target_identity_is_current(proc, main_win, adapter)
    process_check.assert_called_once_with(proc)
    window_check.assert_called_once_with(main_win, proc)
    adapter.controls_are_current.assert_called_once_with(proc.pid, main_win.hwnd)

    adapter.controls_are_current.return_value = False
    assert not BackgroundStatusExecutor._target_identity_is_current(
        proc, main_win, adapter
    )


@pytest.mark.parametrize(
    ("identity_results", "expected_state", "expected_error", "write", "submit"),
    [
        (
            [True, False],
            StatusResultState.FAILED.value,
            "TARGET_IDENTITY_CHANGED",
            False,
            False,
        ),
        (
            [True, True, False],
            StatusResultState.FAILED.value,
            "TARGET_IDENTITY_CHANGED",
            True,
            False,
        ),
        (
            [True, True, True, False],
            StatusResultState.UNKNOWN.value,
            "TARGET_IDENTITY_LOST_AFTER_SUBMIT",
            True,
            True,
        ),
    ],
)
def test_executor_fails_closed_when_target_identity_changes(
    identity_results, expected_state, expected_error, write, submit, monkeypatch
):
    # Target identity behavior must not depend on whichever app is foreground.
    monkeypatch.setattr(cse, "_foreground_window_handle", lambda: 7001)
    resolver, _, _, adapter = _make_mock_resolver(
        current_applied="Old Status", target_readback="New Status"
    )
    adapter.read_applied_status.side_effect = ["Old Status", "New Status"]
    executor = BackgroundStatusExecutor(resolver=resolver)
    executor._target_identity_is_current = MagicMock(side_effect=identity_results)

    evidence = executor.execute_status_change("New Status", pid=5555)

    assert evidence.result_state == expected_state
    assert evidence.error_code == expected_error
    assert adapter.write_text_no_focus.called is write
    assert adapter.post_background_enter.called is submit


def test_executor_never_claims_text_write_as_applied(monkeypatch):
    """Text write or message dispatch alone does NOT prove Camfrog applied the status.

    If CComboBoxTS item 0 never updates to match, fail closed as COMMIT_NOT_VERIFIED.
    """
    monkeypatch.setattr(cse, "_foreground_window_handle", lambda: 7001)
    new_status = "Never applied status"
    resolver, proc, main_win, mock_adapter = _make_mock_resolver(
        current_applied="Old Status",
        target_readback=new_status,
    )
    # read_applied_status stubbornly remains Old Status
    mock_adapter.read_applied_status.return_value = "Old Status"

    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))
    evidence = executor.execute_status_change(new_status, pid=5555, timeout_seconds=0.1)

    assert evidence.result_state == StatusResultState.FAILED.value
    assert evidence.error_code == "COMMIT_NOT_VERIFIED"
    assert evidence.verification_source == "combo_history_verification"
    assert evidence.applied_text == ""


def test_executor_empty_text_fails_fast():
    executor = BackgroundStatusExecutor()
    evidence = executor.execute_status_change("   ")
    assert evidence.result_state == StatusResultState.FAILED.value
    assert evidence.error_code == "EMPTY_STATUS_TEXT"


def test_executor_process_not_found():
    resolver = MagicMock()
    resolver.resolve_status_controls.side_effect = ProcessLookupError("No process")

    executor = BackgroundStatusExecutor(resolver=resolver)
    evidence = executor.execute_status_change("Test Status", pid=99999)

    assert evidence.result_state == StatusResultState.FAILED.value
    assert evidence.error_code == "PROCESS_NOT_FOUND"
    assert evidence.verification_source == "process_discovery"


def test_executor_controls_not_found():
    resolver = MagicMock()
    resolver.resolve_status_controls.side_effect = LookupError("Controls missing")

    executor = BackgroundStatusExecutor(resolver=resolver)
    evidence = executor.execute_status_change("Test Status", pid=5555)

    assert evidence.result_state == StatusResultState.BACKGROUND_UNSUPPORTED.value
    assert evidence.error_code == "CONTROLS_NOT_FOUND"


def test_executor_write_text_failure():
    resolver, proc, main_win, mock_adapter = _make_mock_resolver()
    mock_adapter.write_text_no_focus.return_value = False

    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))
    evidence = executor.execute_status_change("New Status", pid=5555)

    assert evidence.result_state == StatusResultState.FAILED.value
    assert evidence.error_code == "WRITE_TEXT_FAILED"


def test_executor_readback_mismatch():
    resolver, proc, main_win, mock_adapter = _make_mock_resolver(
        target_readback="Mismatched Text"
    )

    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))
    evidence = executor.execute_status_change("Expected Text", pid=5555)

    assert evidence.result_state == StatusResultState.FAILED.value
    assert evidence.error_code == "READBACK_MISMATCH"


def test_executor_post_enter_failure(monkeypatch):
    monkeypatch.setattr(cse, "_foreground_window_handle", lambda: 7001)
    resolver, proc, main_win, mock_adapter = _make_mock_resolver(
        target_readback="Valid"
    )
    mock_adapter.post_background_enter.return_value = False

    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))
    evidence = executor.execute_status_change("Valid", pid=5555)

    assert evidence.result_state == StatusResultState.FAILED.value
    assert evidence.error_code == "POST_ENTER_FAILED"


# ---------- StatusScheduler Tests ----------


def test_status_scheduler_record_and_history():
    scheduler = StatusScheduler()
    assert len(scheduler.get_history()) == 0

    ev = StatusExecutionEvidence(
        request_id="req-1",
        operation_id="op-1",
        client_version="8.5.0",
        process_identity={"pid": 1234},
        window_role="main_directory",
        execution_mode="strict_background",
        result_state="CLIENT_STATE_CONFIRMED",
        duration_ms=45.2,
        verification_source="client_combo_item_0",
        applied_text="Hello",
    )
    scheduler.record_evidence(ev)
    history = scheduler.get_history()
    assert len(history) == 1
    assert history[0]["applied_text"] == "Hello"
    assert history[0]["result_state"] == "CLIENT_STATE_CONFIRMED"


def test_status_scheduler_stop_not_running():
    scheduler = StatusScheduler()
    assert scheduler.is_running() is False
    scheduler.stop()
    assert scheduler.is_running() is False


# ---------- Configuration Contract Validation Tests ----------


def test_validate_status_config_defaults():
    valid, errors, normalized = validate_status_config({})
    assert valid is True
    assert errors == []
    assert normalized["status"]["execution_mode"] == "strict_background"
    assert normalized["status"]["background_enter_target"] == "combo"
    assert normalized["safety"]["allow_focus_stealing"] is False
    assert normalized["safety"]["allow_mouse_injection"] is False
    assert normalized["safety"]["allow_keyboard_injection"] is False
    assert normalized["safety"]["allow_system_sound_changes"] is False


def test_validate_status_config_invalid_mode_and_target():
    invalid_cfg = {
        "status": {
            "execution_mode": "invalid_stealing",
            "background_enter_target": "unsupported",
        }
    }
    valid, errors, _ = validate_status_config(invalid_cfg)
    assert valid is False
    assert any("execution_mode" in e for e in errors)
    assert any("background_enter_target" in e for e in errors)


def test_executor_ambiguous_identity_returns_evidence_not_raise():
    resolver = MagicMock()
    resolver.resolve_status_controls.side_effect = RuntimeError(
        "Multiple verified Camfrog processes detected ([1111, 2222])."
    )

    executor = BackgroundStatusExecutor(resolver=resolver)
    evidence = executor.execute_status_change("Test Status")

    assert evidence.result_state == StatusResultState.FAILED.value
    assert evidence.error_code == "AMBIGUOUS_PROCESS_IDENTITY"
    assert evidence.verification_source == "process_discovery"


def test_executor_refuses_new_work_during_emergency_stop(tmp_path, monkeypatch):
    from camfrog_emergency_stop import EmergencyStopRegistry, EmergencyStopScope

    monkeypatch.setenv(
        "ZCHAT_EMERGENCY_STOP_DB", str(tmp_path / "emergency-stops.sqlite3")
    )
    registry = EmergencyStopRegistry()
    registry._reset_for_testing()
    try:
        resolver, _, _, _ = _make_mock_resolver(
            current_applied="Old Status", target_readback="New Status"
        )
        executor = BackgroundStatusExecutor(resolver=resolver)
        registry.activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-test")

        evidence = executor.execute_status_change("New Status")

        assert evidence.result_state == StatusResultState.FAILED.value
        assert evidence.error_code == "EMERGENCY_STOP"
        assert evidence.verification_source == "emergency_stop"
    finally:
        registry._reset_for_testing()


def test_feature_scoped_stop_matches_only_configured_status_feature():
    registry = EmergencyStopRegistry()
    resource = "camfrog.status-random"
    registry.activate(
        EmergencyStopScope.FEATURE,
        "OPERATOR_HALT",
        "op-test",
        affected_resources=[resource],
    )
    stopped_executor = BackgroundStatusExecutor(
        emergency_scope=EmergencyStopScope.FEATURE,
        emergency_resource_key=resource,
    )
    other_executor = BackgroundStatusExecutor(
        emergency_scope=EmergencyStopScope.FEATURE,
        emergency_resource_key="camfrog.status-changer",
    )

    with pytest.raises(StopIteration):
        stopped_executor.check_emergency_stop()
    other_executor.check_emergency_stop()


def test_executor_rechecks_emergency_stop_before_touching_client_controls():
    resolver, _, _, mock_adapter = _make_mock_resolver(
        current_applied="Old Status", target_readback="New Status"
    )
    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))
    executor.check_emergency_stop = MagicMock(side_effect=[None, StopIteration()])

    evidence = executor.execute_status_change("New Status", pid=5555)

    assert evidence.error_code == "EMERGENCY_STOP"
    assert evidence.verification_source == "emergency_stop"
    assert not mock_adapter.write_text_no_focus.called
    assert not mock_adapter.post_background_enter.called


def test_executor_rechecks_emergency_stop_before_submit():
    resolver, _, _, mock_adapter = _make_mock_resolver(
        current_applied="Old Status", target_readback="New Status"
    )
    executor = _mock_valid_target(BackgroundStatusExecutor(resolver=resolver))
    executor.check_emergency_stop = MagicMock(side_effect=[None, None, StopIteration()])

    evidence = executor.execute_status_change("New Status", pid=5555)

    assert evidence.error_code == "EMERGENCY_STOP"
    assert evidence.verification_source == "emergency_stop"
    assert mock_adapter.write_text_no_focus.called
    assert not mock_adapter.post_background_enter.called
