"""Probe Camfrog status submit messages without global keyboard or mouse input.

This tool can change the current Camfrog status. It requires --yes and logs only
sanitized metadata. It does not restore the prior provider status. Endpoint
foreground snapshots cannot rule out transient focus changes, and combo readback
is not independent provider verification.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import sys
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

ROUTES = ("combo-post", "edit-char", "edit-post", "combo-send", "parent-msg")


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
    args, process, window, code: str, restored: bool
) -> None:
    _append_log(
        args.log,
        {
            "captured_at": datetime.now(UTC).isoformat(),
            "provider": "camfrog",
            "provider_version": process.version,
            "pid": process.pid,
            "window_role": window.role.value,
            "route": args.route,
            "status_length": len(args.status),
            "client_state": "SUBMIT_NOT_ACCEPTED",
            "failure_code": code,
            "prior_edit_text_restored": restored,
            "provider_state_verified": False,
        },
    )


def _submit_and_record(args, process, window, adapter) -> int:
    before = _snapshot()
    prior_edit_text = adapter.read_edit_text()
    try:
        staged = adapter.write_text_no_focus(args.status)
        staged_readback = adapter.read_edit_text()
    except Exception:  # noqa: BLE001 - WM calls may raise platform-specific errors.
        staged = False
        staged_readback = ""
    if not staged or staged_readback != args.status:
        restored = _restore_prior_edit_text(prior_edit_text, process, window, adapter)
        print(
            "Could not stage and read back the status text; no submit route was sent.",
            file=sys.stderr,
        )
        _record_pre_submit_failure(args, process, window, "TEXT_STAGE_FAILED", restored)
        return 4 if restored else 8

    stop_registry = EmergencyStopRegistry()
    if stop_registry.is_stopped(EmergencyStopScope.FEATURE, "camfrog.status-changer"):
        restored = _restore_prior_edit_text(prior_edit_text, process, window, adapter)
        failure_code = (
            "EMERGENCY_STOP_ACTIVE" if restored else "EMERGENCY_STOP_RESTORE_FAILED"
        )
        _record_pre_submit_failure(args, process, window, failure_code, restored)
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
        failure_code = (
            "TARGET_IDENTITY_CHANGED" if restored else "TARGET_RESTORE_FAILED"
        )
        _record_pre_submit_failure(args, process, window, failure_code, restored)
        if not restored:
            print(
                "Target changed before submit; restoring the prior edit text failed.",
                file=sys.stderr,
            )
            return 8
        print("Target identity changed before submit.", file=sys.stderr)
        return 3

    try:
        if args.route == "combo-post":
            sent = adapter.post_background_enter()
        elif args.route == "edit-char":
            sent = (
                _send_message_timeout(
                    adapter.edit_hwnd, WM_CHAR, 0x0D, 0, MESSAGE_TIMEOUT_MS
                )
                is not None
            )
        elif args.route == "edit-post":
            user32 = ctypes.windll.user32
            sent = bool(
                user32.PostMessageW(
                    adapter.edit_hwnd, WM_KEYDOWN, VK_RETURN, _enter_lparam()
                )
                and user32.PostMessageW(
                    adapter.edit_hwnd, WM_KEYUP, VK_RETURN, _enter_lparam(key_up=True)
                )
            )
        elif args.route == "combo-send":
            sent = _send_enter(adapter.combo_hwnd)
        else:
            sent = _send_parent_command(
                window.hwnd, adapter.combo_hwnd, adapter.edit_hwnd, args.wparam
            )

        deadline = time.monotonic() + 3.0
        readback = adapter.read_applied_status()
        while readback != args.status and time.monotonic() < deadline:
            time.sleep(0.05)
            readback = adapter.read_applied_status()
        after = _snapshot()
    except Exception as exc:  # noqa: BLE001 - an interrupted submit has an unknown outcome.
        edit_text_restored = _restore_prior_edit_text(
            prior_edit_text, process, window, adapter
        )
        record = {
            "captured_at": datetime.now(UTC).isoformat(),
            "provider": "camfrog",
            "provider_version": process.version,
            "pid": process.pid,
            "window_role": window.role.value,
            "route": args.route,
            "status_length": len(args.status),
            "client_state": "EFFECT_UNKNOWN",
            "failure_code": "SUBMIT_ROUTE_EXCEPTION",
            "exception_type": type(exc).__name__,
            "prior_edit_text_restored": edit_text_restored,
            "foreground_same_at_endpoints": None,
            "cursor_same_at_endpoints": None,
            "keyboard_state_same_at_endpoints": None,
            "endpoint_snapshots_only": True,
            "provider_state_verified": False,
        }
        _append_log(args.log, record)
        print(json.dumps(record, sort_keys=True))
        print(f"Sanitized log: {args.log}")
        return 5 if edit_text_restored else 8

    edit_text_restored = _restore_prior_edit_text(
        prior_edit_text, process, window, adapter
    )
    if not edit_text_restored:
        print("Probe ended; restoring the prior edit text failed.", file=sys.stderr)
    record = {
        "captured_at": datetime.now(UTC).isoformat(),
        "provider": "camfrog",
        "provider_version": process.version,
        "pid": process.pid,
        "window_role": window.role.value,
        "route": args.route,
        "status_length": len(args.status),
        "submit_message_accepted": bool(sent),
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
        "endpoint_snapshots_only": True,
        "provider_state_verified": False,
    }
    _append_log(args.log, record)
    print(json.dumps(record, sort_keys=True))
    print(f"Sanitized log: {args.log}")
    print(
        "A matching endpoint snapshot does not prove absence of transient focus changes."
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
            StatusResourceLock(resource_key=f"pid_{process.pid}", timeout_seconds=3.0),
            EmergencyStopGuard(
                EmergencyStopScope.FEATURE,
                resource_key="camfrog.status-changer",
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
        print("Status resource is already owned by another writer.", file=sys.stderr)
        return 6
    except StopIteration:
        print("Emergency stop is active; probe was refused.", file=sys.stderr)
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
