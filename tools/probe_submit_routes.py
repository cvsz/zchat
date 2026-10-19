"""Probe Camfrog status submit messages without global keyboard or mouse input.

This tool can change the current Camfrog status. It requires --yes and logs only
sanitized metadata. It does not restore the prior provider status. Endpoint
snapshots are supplemented with EVENT_SYSTEM_FOREGROUND notifications during the
submit window. A WinEvent monitor cannot prove that every system event was
delivered, and combo readback is not independent provider verification.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "camfrog"))

from camfrog_emergency_stop import (
    EmergencyStopGuard,
    EmergencyStopRegistry,
    EmergencyStopScope,
)
from camfrog_status_engine import (
    CamfrogWindowResolver,
    StatusResourceLock,
    _foreground_window_handle,
    _send_message_timeout,
)
from camfrog_windows_runtime import (
    revalidate_process_identity,
    revalidate_window_identity,
)

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_COMMAND = 0x0111
VK_RETURN = 0x0D
EN_CHANGE = 0x0300
CBN_SELCHANGE = 0xFFFF
CBN_SELENDOK = 0xFFF7
MESSAGE_TIMEOUT_MS = 1000
MAX_FOREGROUND_EVENT_TRACE = 64
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000

# Matrix of non-global-input paths found in the Status Changer source: posted or
# synchronous Enter to the status Edit/combo, WM_CHAR, and observed-shape parent
# WM_COMMAND notifications. Do not add global key/mouse injection here.
ROUTES = (
    "combo-post",
    "combo-send",
    "edit-char",
    "edit-char-post",
    "edit-post",
    "edit-send",
    "engine-post",
    "parent-msg",
)

FEATURE_RESOURCE_KEYS = {
    "status": "camfrog.status",
    "status-random": "camfrog.status-random",
    "status-marquee": "camfrog.status-marquee",
    "status-changer": "camfrog.status-changer",
}


def _selected_feature(args) -> str:
    """Return a validated feature name, preserving test/caller compatibility."""
    feature = getattr(args, "feature", "status-changer")
    if feature not in FEATURE_RESOURCE_KEYS:
        raise ValueError("Unsupported status feature scope.")
    return feature


def _snapshot() -> dict[str, object]:
    """Capture desktop endpoint state without changing it or reading clipboard data."""
    user32 = ctypes.windll.user32
    cursor = ctypes.wintypes.POINT()
    cursor_ok = bool(user32.GetCursorPos(ctypes.byref(cursor)))
    keyboard = (ctypes.c_ubyte * 256)()
    keyboard_ok = bool(user32.GetKeyboardState(keyboard))
    return {
        "foreground_hwnd": _foreground_window_handle(),
        "cursor": [cursor.x, cursor.y] if cursor_ok else None,
        "keyboard_state_sha256": _sha256(bytes(keyboard)) if keyboard_ok else None,
    }


class ForegroundEventMonitor:
    """Observe foreground changes on a dedicated WinEvent message-pump thread."""

    def __init__(self, target_pid: int, baseline_hwnd: int | None):
        self.target_pid = target_pid
        self.baseline_hwnd = baseline_hwnd
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._error_type: str | None = None
        self._hook_installed = False
        self._counts = {
            "foreground_event_count": 0,
            "target_foreground_event_count": 0,
            "baseline_foreground_event_count": 0,
            "other_foreground_event_count": 0,
            "unknown_foreground_event_count": 0,
        }
        self._events: list[dict[str, object]] = []
        self._started_at: float | None = None

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, name="zchat-foreground-event-monitor", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=3.0):
            self._request_stop()
            raise RuntimeError("foreground event monitor did not initialize")
        if self._error_type or not self._hook_installed:
            raise RuntimeError("foreground event monitor initialization failed")

    def _run(self) -> None:
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            wintypes = ctypes.wintypes
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            callback_type = ctypes.WINFUNCTYPE(
                None,
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HWND,
                wintypes.LONG,
                wintypes.LONG,
                wintypes.DWORD,
                wintypes.DWORD,
            )
        except Exception as exc:  # noqa: BLE001 - observer setup must report failure.
            self._error_type = type(exc).__name__
            self._ready.set()
            return
        message = wintypes.MSG()
        hook = None

        def on_foreground(_hook, event, hwnd, _object_id, _child_id, _thread, _time):
            if event != EVENT_SYSTEM_FOREGROUND:
                return
            try:
                process_id = wintypes.DWORD()
                if hwnd:
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                self._record_event(hwnd, process_id.value)
            except Exception:  # noqa: BLE001 - observer errors must not escape a WinEvent callback.
                with self._lock:
                    self._error_type = "ForegroundCallbackError"

        callback = callback_type(on_foreground)
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            # Create this thread's message queue before posting WM_QUIT to stop it.
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_NOREMOVE)
            user32.SetWinEventHook.argtypes = [
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
                callback_type,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            user32.SetWinEventHook.restype = wintypes.HANDLE
            hook = user32.SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND,
                EVENT_SYSTEM_FOREGROUND,
                None,
                callback,
                0,
                0,
                WINEVENT_OUTOFCONTEXT,
            )
            if not hook:
                self._error_type = "SetWinEventHookError"
                return
            self._hook_installed = True
            self._ready.set()
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.GetMessageW.restype = ctypes.c_int
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            user32.UnhookWinEvent.restype = wintypes.BOOL
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    if result == -1:
                        self._error_type = "GetMessageError"
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:  # noqa: BLE001 - fail closed if monitoring cannot start.
            self._error_type = type(exc).__name__
        finally:
            if hook:
                try:
                    user32.UnhookWinEvent(hook)
                except Exception:  # noqa: BLE001 - surface cleanup failure in monitor evidence.
                    self._error_type = self._error_type or "UnhookWinEventError"
            self._ready.set()

    def _record_event(self, hwnd: int | None, process_id: int | None) -> None:
        """Record sanitized foreground metadata without window titles or contents."""
        hwnd_value = int(hwnd or 0)
        pid_value = int(process_id or 0)
        if not hwnd_value or not pid_value:
            category = "unknown"
        elif pid_value == self.target_pid:
            category = "target"
        elif hwnd_value == self.baseline_hwnd:
            category = "baseline"
        else:
            category = "other"

        observed_at = time.monotonic()
        started_at = self._started_at
        with self._lock:
            self._counts["foreground_event_count"] += 1
            self._counts[f"{category}_foreground_event_count"] += 1
            if len(self._events) < MAX_FOREGROUND_EVENT_TRACE:
                self._events.append(
                    {
                        "offset_ms": round(
                            max(
                                0.0,
                                observed_at
                                - (started_at if started_at is not None else observed_at),
                            ) * 1000,
                            1,
                        ),
                        "hwnd": hwnd_value or None,
                        "pid": pid_value or None,
                        "category": category,
                    }
                )

    def stop(self, *, minimum_duration: float = 2.0) -> dict[str, object]:
        if self._started_at is not None:
            remaining = minimum_duration - (time.monotonic() - self._started_at)
            if remaining > 0:
                time.sleep(remaining)
        self._request_stop()
        if self._thread:
            self._thread.join(timeout=3.0)
        with self._lock:
            counts = dict(self._counts)
            events = list(self._events)
        counts.update(
            {
                "foreground_events": events,
                "foreground_event_trace_truncated": (
                    counts["foreground_event_count"] > len(events)
                ),
                "foreground_monitor": "win_event_hook",
                "foreground_monitor_stopped": bool(
                    self._thread and not self._thread.is_alive()
                ),
                "foreground_monitor_error": self._error_type,
            }
        )
        return counts

    def _request_stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            try:
                user32 = ctypes.windll.user32
                user32.PostThreadMessageW.argtypes = [
                    ctypes.wintypes.DWORD,
                    ctypes.wintypes.UINT,
                    ctypes.wintypes.WPARAM,
                    ctypes.wintypes.LPARAM,
                ]
                user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL
                if not user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0):
                    self._error_type = "PostThreadMessageError"
            except Exception:  # noqa: BLE001 - preserve the action outcome for the caller.
                self._error_type = "PostThreadMessageError"


def _sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _enter_lparam(key_up: bool = False) -> int:
    # Return key scan code 0x1c; bit 30/31 indicate previous/down transition.
    return 0xC01C0001 if key_up else 0x001C0001


def _send_enter(hwnd: int) -> bool:
    down = _send_message_timeout(
        hwnd, WM_KEYDOWN, VK_RETURN, _enter_lparam(), MESSAGE_TIMEOUT_MS
    )
    if down is None:
        return False
    up = _send_message_timeout(
        hwnd, WM_KEYUP, VK_RETURN, _enter_lparam(key_up=True), MESSAGE_TIMEOUT_MS
    )
    return up is not None


def _post_enter(hwnd: int) -> bool:
    """Post the same bounded VK_RETURN down/up pair used by the GUI path."""
    try:
        user32 = ctypes.windll.user32
        return bool(
            user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, _enter_lparam())
            and user32.PostMessageW(
                hwnd, WM_KEYUP, VK_RETURN, _enter_lparam(key_up=True)
            )
        )
    except Exception:  # noqa: BLE001 - platform and message errors fail closed.
        return False


def _post_edit_char(hwnd: int) -> bool:
    """Post Enter as WM_CHAR to the verified Edit without global input."""
    try:
        return bool(ctypes.windll.user32.PostMessageW(hwnd, WM_CHAR, 0x0D, 0))
    except Exception:  # noqa: BLE001 - platform and message errors are fail-closed.
        return False


def _send_parent_command(
    main_hwnd: int, combo_hwnd: int, edit_hwnd: int, wparam: int
) -> bool:
    """Allow only observed notification shapes, never arbitrary parent commands."""
    code = (wparam >> 16) & 0xFFFF
    control_id = wparam & 0xFFFF
    if (code, control_id) == (EN_CHANGE, 1001):
        child_hwnd = edit_hwnd
    elif (code, control_id) in ((CBN_SELCHANGE, 1436), (CBN_SELENDOK, 1436)):
        child_hwnd = combo_hwnd
    else:
        return False
    result = _send_message_timeout(
        main_hwnd, WM_COMMAND, wparam, child_hwnd, MESSAGE_TIMEOUT_MS
    )
    return result is not None


def _append_log(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _default_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ZChat" / "status-changer" / "submit-probe.jsonl"
    return Path.home() / ".zchat" / "status-changer" / "submit-probe.jsonl"


def _parse_int(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "use a decimal or 0x-prefixed integer"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", choices=ROUTES, required=True)
    parser.add_argument(
        "--feature",
        choices=tuple(FEATURE_RESOURCE_KEYS),
        default="status-changer",
        help="Status feature scope whose emergency stop must permit the probe.",
    )
    parser.add_argument(
        "--status",
        required=True,
        help="Test status sent to Camfrog; it may remain externally applied and is never logged",
    )
    parser.add_argument(
        "--pid", type=int, help="Verified Camfrog PID when multiple clients exist"
    )
    parser.add_argument(
        "--yes", action="store_true", help="Confirm a real status submit"
    )
    parser.add_argument(
        "--parent-msg", type=_parse_int, help="Replay only WM_COMMAND (0x111)"
    )
    parser.add_argument(
        "--wparam", type=_parse_int, help="Captured WM_COMMAND notification value"
    )
    parser.add_argument("--log", type=Path, default=_default_log_path())
    return parser


def _restore_prior_edit_text(prior_text: str, process, window, adapter) -> bool:
    """Restore the local edit draft only while the original target is still valid."""
    try:
        if (
            not revalidate_process_identity(process)
            or not revalidate_window_identity(window, process)
            or not adapter.controls_are_current(process.pid, window.hwnd)
        ):
            return False
        return bool(adapter.write_text_no_focus(prior_text)) and (
            adapter.read_edit_text() == prior_text
        )
    except Exception:  # noqa: BLE001 - recovery must fail closed for any adapter error.
        return False


def _record_pre_submit_failure(
    args,
    process,
    window,
    code: str,
    restored: bool,
    extra: dict[str, object] | None = None,
) -> None:
    record = {
        "captured_at": datetime.now(UTC).isoformat(),
        "provider": "camfrog",
        "provider_version": process.version,
        "pid": process.pid,
        "window_role": window.role.value,
        "route": args.route,
        "feature": _selected_feature(args),
        "status_length": len(args.status),
        "client_state": "SUBMIT_NOT_ACCEPTED",
        "failure_code": code,
        "prior_edit_text_restored": restored,
        "provider_state_verified": False,
    }
    if extra:
        record.update(extra)
    _append_log(args.log, record)


def _submit_and_record(args, process, window, adapter) -> int:
    before = _snapshot()
    prior_edit_text = adapter.read_edit_text()
    monitor = ForegroundEventMonitor(process.pid, before["foreground_hwnd"])
    try:
        # Start observing before any mutating control message. Monitoring only
        # from the Enter dispatch misses activation caused by WM_SETTEXT or the
        # subsequent EN_CHANGE notification.
        monitor.start()
    except Exception as exc:  # noqa: BLE001 - never mutate without an observer.
        _record_pre_submit_failure(
            args,
            process,
            window,
            "FOREGROUND_MONITOR_UNAVAILABLE",
            True,
            {
                "foreground_monitor": "win_event_hook",
                "foreground_monitor_error": type(exc).__name__,
            },
        )
        print(
            "Foreground event monitoring is unavailable; no status control was changed.",
            file=sys.stderr,
        )
        return 9

    foreground_events: dict[str, object] | None = None

    def stop_monitor(*, minimum_duration: float = 2.0) -> dict[str, object]:
        nonlocal foreground_events
        if foreground_events is None:
            foreground_events = monitor.stop(minimum_duration=minimum_duration)
        return foreground_events

    try:
        staged = adapter.write_text_no_focus(args.status)
        staged_readback = adapter.read_edit_text()
    except Exception:  # noqa: BLE001 - WM calls may raise platform-specific errors.
        staged = False
        staged_readback = ""
    if not staged or staged_readback != args.status:
        restored = _restore_prior_edit_text(prior_edit_text, process, window, adapter)
        events = stop_monitor(minimum_duration=0.0)
        print(
            "Could not stage and read back the status text; no submit route was sent.",
            file=sys.stderr,
        )
        _record_pre_submit_failure(
            args, process, window, "TEXT_STAGE_FAILED", restored, events
        )
        return 4 if restored else 8

    stop_registry = EmergencyStopRegistry()
    feature_resource_key = FEATURE_RESOURCE_KEYS[_selected_feature(args)]
    if stop_registry.is_stopped(EmergencyStopScope.FEATURE, feature_resource_key):
        restored = _restore_prior_edit_text(prior_edit_text, process, window, adapter)
        events = stop_monitor(minimum_duration=0.0)
        failure_code = (
            "EMERGENCY_STOP_ACTIVE" if restored else "EMERGENCY_STOP_RESTORE_FAILED"
        )
        _record_pre_submit_failure(
            args, process, window, failure_code, restored, events
        )
        if not restored:
            print(
                "Emergency stop is active; restoring the prior edit text failed.",
                file=sys.stderr,
            )
            return 8
        print(
            "Emergency stop became active before submit; probe was refused.",
            file=sys.stderr,
        )
        return 7
    if (
        not revalidate_process_identity(process)
        or not revalidate_window_identity(window, process)
        or not adapter.controls_are_current(process.pid, window.hwnd)
    ):
        restored = _restore_prior_edit_text(prior_edit_text, process, window, adapter)
        events = stop_monitor(minimum_duration=0.0)
        failure_code = (
            "TARGET_IDENTITY_CHANGED" if restored else "TARGET_RESTORE_FAILED"
        )
        _record_pre_submit_failure(
            args, process, window, failure_code, restored, events
        )
        if not restored:
            print(
                "Target changed before submit; restoring the prior edit text failed.",
                file=sys.stderr,
            )
            return 8
        print("Target identity changed before submit.", file=sys.stderr)
        return 3

    if (
        not revalidate_process_identity(process)
        or not revalidate_window_identity(window, process)
        or not adapter.controls_are_current(process.pid, window.hwnd)
    ):
        restored = _restore_prior_edit_text(prior_edit_text, process, window, adapter)
        events = stop_monitor(minimum_duration=0.0)
        _record_pre_submit_failure(
            args,
            process,
            window,
            "TARGET_IDENTITY_CHANGED",
            restored,
            events,
        )
        return 3 if restored else 8

    try:
        if args.route == "combo-post":
            sent = _post_enter(adapter.combo_hwnd)
        elif args.route == "edit-char":
            sent = (
                _send_message_timeout(
                    adapter.edit_hwnd, WM_CHAR, 0x0D, 0, MESSAGE_TIMEOUT_MS
                )
                is not None
            )
        elif args.route == "edit-char-post":
            sent = _post_edit_char(adapter.edit_hwnd)
        elif args.route == "edit-post":
            sent = _post_enter(adapter.edit_hwnd)
        elif args.route == "edit-send":
            sent = _send_enter(adapter.edit_hwnd)
        elif args.route == "combo-send":
            sent = _send_enter(adapter.combo_hwnd)
        elif args.route == "engine-post":
            sent = adapter.post_background_enter()
        else:
            sent = _send_parent_command(
                window.hwnd, adapter.combo_hwnd, adapter.edit_hwnd, args.wparam
            )

        deadline = time.monotonic() + 3.0
        readback = adapter.read_applied_status()
        while readback != args.status and time.monotonic() < deadline:
            time.sleep(0.05)
            readback = adapter.read_applied_status()
        # Restore the local draft before ending observation so recovery itself
        # is also covered by the foreground-event record.
        edit_text_restored = _restore_prior_edit_text(
            prior_edit_text, process, window, adapter
        )
        foreground_events = stop_monitor()
        after = _snapshot()
    except Exception as exc:  # noqa: BLE001 - an interrupted submit has an unknown outcome.
        edit_text_restored = _restore_prior_edit_text(
            prior_edit_text, process, window, adapter
        )
        foreground_events = stop_monitor(minimum_duration=0.0)
        record = {
            "captured_at": datetime.now(UTC).isoformat(),
            "provider": "camfrog",
            "provider_version": process.version,
            "pid": process.pid,
            "window_role": window.role.value,
            "route": args.route,
            "feature": _selected_feature(args),
            "status_length": len(args.status),
            "client_state": "EFFECT_UNKNOWN",
            "failure_code": "SUBMIT_ROUTE_EXCEPTION",
            "exception_type": type(exc).__name__,
            "prior_edit_text_restored": edit_text_restored,
            "foreground_same_at_endpoints": None,
            "cursor_same_at_endpoints": None,
            "keyboard_state_same_at_endpoints": None,
            "endpoint_snapshots_only": False,
            **foreground_events,
            "provider_state_verified": False,
        }
        _append_log(args.log, record)
        print(json.dumps(record, sort_keys=True))
        print(f"Sanitized log: {args.log}")
        return 5 if edit_text_restored else 8

    if not edit_text_restored:
        print("Probe ended; restoring the prior edit text failed.", file=sys.stderr)
    record = {
        "captured_at": datetime.now(UTC).isoformat(),
        "provider": "camfrog",
        "provider_version": process.version,
        "pid": process.pid,
        "window_role": window.role.value,
        "route": args.route,
        "feature": _selected_feature(args),
        "status_length": len(args.status),
        "route_message_dispatched": bool(sent),
        "client_state": (
            "CLIENT_STATE_CONFIRMED"
            if readback == args.status
            else "EFFECT_UNKNOWN"
            if sent
            else "SUBMIT_NOT_ACCEPTED"
        ),
        "client_combo_readback_matches": readback == args.status,
        "client_combo_readback_length": len(readback),
        "prior_edit_text_restored": edit_text_restored,
        "foreground_same_at_endpoints": (
            before["foreground_hwnd"] == after["foreground_hwnd"]
            if before["foreground_hwnd"] is not None
            and after["foreground_hwnd"] is not None
            else None
        ),
        "cursor_same_at_endpoints": before["cursor"] == after["cursor"],
        "keyboard_state_same_at_endpoints": (
            before["keyboard_state_sha256"] == after["keyboard_state_sha256"]
            if before["keyboard_state_sha256"] is not None
            and after["keyboard_state_sha256"] is not None
            else None
        ),
        "endpoint_snapshots_only": False,
        **foreground_events,
        "provider_state_verified": False,
    }
    _append_log(args.log, record)
    print(json.dumps(record, sort_keys=True))
    print(f"Sanitized log: {args.log}")
    if foreground_events["target_foreground_event_count"]:
        print("Camfrog foreground activation was observed during the probe.")
    elif (
        foreground_events["foreground_monitor_error"]
        or not foreground_events["foreground_monitor_stopped"]
    ):
        print("Foreground event monitoring was incomplete; isolation is unverified.")
    else:
        print(
            "No Camfrog foreground event was observed during the WinEvent monitor window; this is not provider verification."
        )
    if not edit_text_restored:
        return 8
    return 0 if sent and readback == args.status else 5


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.platform != "win32":
        print("This probe requires native Windows.", file=sys.stderr)
        return 2
    if not args.yes:
        print(
            "Refusing to submit: rerun with --yes after reviewing the target and status."
        )
        return 2
    if (
        not args.status.strip()
        or len(args.status) > 120
        or any(ord(char) < 32 or ord(char) == 127 for char in args.status)
    ):
        print("Status must contain 1 to 120 printable characters.", file=sys.stderr)
        return 2
    if args.route == "parent-msg" and (
        args.parent_msg != WM_COMMAND or args.wparam is None
    ):
        print(
            "parent-msg requires --parent-msg 0x111 and an observed --wparam.",
            file=sys.stderr,
        )
        return 2

    try:
        process, window, adapter = CamfrogWindowResolver().resolve_status_controls(
            pid=args.pid
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Target discovery failed ({type(exc).__name__}).", file=sys.stderr)
        return 3

    if not revalidate_process_identity(process) or not revalidate_window_identity(
        window, process
    ):
        print("Target identity changed during discovery.", file=sys.stderr)
        return 3
    if not adapter.controls_are_current(process.pid, window.hwnd):
        print("Status controls failed identity validation.", file=sys.stderr)
        return 3

    try:
        with (
            # Match status-worker lock order: lifecycle writer before target PID.
            StatusResourceLock(
                resource_key="camfrog_status_writer", timeout_seconds=0.5
            ),
            StatusResourceLock(resource_key=f"pid_{process.pid}", timeout_seconds=3.0),
            EmergencyStopGuard(
                EmergencyStopScope.FEATURE,
                resource_key=FEATURE_RESOURCE_KEYS[_selected_feature(args)],
                description="Camfrog status submit probe",
            ),
        ):
            if not revalidate_process_identity(
                process
            ) or not revalidate_window_identity(window, process):
                print("Target identity changed before submit.", file=sys.stderr)
                return 3
            if not adapter.controls_are_current(process.pid, window.hwnd):
                print("Status controls changed before submit.", file=sys.stderr)
                return 3
            return _submit_and_record(args, process, window, adapter)
    except TimeoutError:
        print("Status writer or target resource is already owned.", file=sys.stderr)
        return 6
    except StopIteration:
        print("Emergency stop is active; probe was refused.", file=sys.stderr)
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
