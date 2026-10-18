"""Shared background status engine and evidence-driven executor for Camfrog Video Chat.

Provides:
- Fail-closed strict background status execution unless a submit adapter can
  prove it does not activate or interact with the desktop.
- Legacy targeted window-message submission, which is not accepted as strict
  background because Camfrog may activate its window while handling key messages.
- Foreground endpoint checks that reject the result when desktop isolation cannot
  be confirmed; these checks cannot prevent or detect every transient activation.
- Camfrog client UI state confirmation via CComboBoxTS item state; this does not
  verify the provider's externally visible status.
- Separation of execution modes (strict_background vs interactive).
- Shared status rotation, marquee frames, concurrency protection, and recovery.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import camfrog_windows_runtime as cwr
from camfrog_emergency_stop import EmergencyStopHook, EmergencyStopScope
from camfrog_windows_runtime import (
    ClassifiedWindow,
    ProcessIdentity,
    WindowRole,
    WindowsCamfrogProcessDiscovery,
    WindowsCamfrogWindowClassifier,
    revalidate_process_identity,
    revalidate_window_identity,
)


class StatusResultState(str, Enum):
    """Evidence states for a status request; none assert external provider state."""
    PENDING = "PENDING"
    PROCESS_VERIFIED = "PROCESS_VERIFIED"
    WINDOW_VERIFIED = "WINDOW_VERIFIED"
    TEXT_WRITTEN = "TEXT_WRITTEN"
    SUBMIT_DISPATCHED = "SUBMIT_DISPATCHED"
    CLIENT_STATE_CONFIRMED = "CLIENT_STATE_CONFIRMED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    BACKGROUND_UNSUPPORTED = "BACKGROUND_UNSUPPORTED"


class ExecutionMode(str, Enum):
    """Execution mode distinguishing strict background from interactive."""
    STRICT_BACKGROUND = "strict_background"
    INTERACTIVE = "interactive"


def strict_background_submit_supported() -> bool:
    """Return whether a submit route is verified to preserve desktop state.

    Camfrog 8.5 currently has no verified route, so callers must fail closed.
    Keep startup and execution checks on the same capability gate.
    """
    return False


@dataclass
class StatusExecutionEvidence:
    """Sanitized evidence log of a status execution operation."""
    request_id: str
    operation_id: str
    client_version: str
    process_identity: Dict[str, Any]
    window_role: str
    execution_mode: str
    result_state: str
    duration_ms: float
    verification_source: str
    applied_text: str = ""
    error_code: Optional[str] = None
    foreground_matches_before_and_after: Optional[bool] = None
    foreground_matches_before_submit: Optional[bool] = None
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------- Status Control Adapter ----------

class StatusControlAdapter:
    """Low-level interface to Camfrog's CComboBoxTS and Edit controls."""

    MESSAGE_TIMEOUT_MS = 1000
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    VK_RETURN = 0x0D
    RETURN_LPARAM_DOWN = 0x001C0001
    RETURN_LPARAM_UP = 0xC01C0001

    def __init__(
        self,
        combo_hwnd: int,
        edit_hwnd: int,
        expected_pid: Optional[int] = None,
        parent_hwnd: Optional[int] = None,
    ):
        self.combo_hwnd = combo_hwnd
        self.edit_hwnd = edit_hwnd
        self.expected_pid = expected_pid
        self.parent_hwnd = parent_hwnd

    def supports_strict_background_submit(self) -> bool:
        """Whether this adapter has a submit path proven to preserve desktop state.

        The current Camfrog 8.5 adapter submits with targeted Enter key messages
        to the status Edit control. A live WinEvent probe observed Camfrog
        foreground activation on this route, so it cannot satisfy strict isolation.
        """
        return strict_background_submit_supported()

    def controls_are_current(
        self,
        expected_pid: Optional[int] = None,
        parent_hwnd: Optional[int] = None,
    ) -> bool:
        """Verify both HWNDs still identify the expected Camfrog child controls."""
        if sys.platform != "win32":
            return False

        pid = expected_pid or self.expected_pid
        parent = parent_hwnd or self.parent_hwnd
        if pid is None or parent is None:
            return False
        try:
            import win32gui
            import win32process

            expected_controls = (
                (self.combo_hwnd, 1436, {"CComboBoxTS"}),
                (self.edit_hwnd, 1001, {"Edit", "CEditTS"}),
            )
            for hwnd, control_id, classes in expected_controls:
                if not win32gui.IsWindow(hwnd) or not win32gui.IsChild(parent, hwnd):
                    return False
                _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
                if owner_pid != pid:
                    return False
                if (
                    win32gui.GetDlgCtrlID(hwnd) != control_id
                    and win32gui.GetClassName(hwnd) not in classes
                ):
                    return False
            return True
        except Exception:
            return False

    def write_text_no_focus(self, text: str) -> bool:
        """Write text into the status Edit control without setting keyboard focus."""
        if sys.platform != "win32":
            return False

        try:
            import win32con
            # Fail closed on stale/dangling handles: never report success
            # for a control that does not exist.
            import win32gui

            if not self.controls_are_current():
                return False

            # Cross-process SendMessage can block forever if Camfrog is hung.
            # Keep each UI request bounded and fail closed on timeout.
            text_buffer = ctypes.create_unicode_buffer(text)
            settext_result = _send_message_timeout(
                self.edit_hwnd,
                win32con.WM_SETTEXT,
                0,
                ctypes.addressof(text_buffer),
                self.MESSAGE_TIMEOUT_MS,
            )
            if not settext_result:
                return False

            # 2. Notify parent CComboBoxTS of EN_CHANGE
            wparam = (win32con.EN_CHANGE << 16) | 1001
            if _send_message_timeout(
                self.combo_hwnd,
                win32con.WM_COMMAND,
                wparam,
                self.edit_hwnd,
                self.MESSAGE_TIMEOUT_MS,
            ) is None:
                return False

            return True
        except Exception:
            return False

    def read_edit_text(self) -> str:
        """Read text from Edit control via WM_GETTEXT."""
        if sys.platform != "win32":
            return ""

        try:
            if not self.controls_are_current():
                return ""
            import win32con
            buf = ctypes.create_unicode_buffer(512)
            result = _send_message_timeout(
                self.edit_hwnd,
                win32con.WM_GETTEXT,
                512,
                ctypes.addressof(buf),
                self.MESSAGE_TIMEOUT_MS,
            )
            if result is None:
                return ""
            return buf.value
        except Exception:
            return ""

    def read_applied_status(self) -> str:
        """Read local status text at CComboBoxTS index 0, not provider state."""
        if sys.platform != "win32":
            return ""

        try:
            if not self.controls_are_current():
                return ""
            import win32con
            count = _send_message_timeout(
                self.combo_hwnd, win32con.CB_GETCOUNT, 0, 0, self.MESSAGE_TIMEOUT_MS
            )
            if count is None or count <= 0:
                return ""
            length = _send_message_timeout(
                self.combo_hwnd, win32con.CB_GETLBTEXTLEN, 0, 0, self.MESSAGE_TIMEOUT_MS
            )
            if length is None or length < 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            result = _send_message_timeout(
                self.combo_hwnd,
                win32con.CB_GETLBTEXT,
                0,
                ctypes.addressof(buf),
                self.MESSAGE_TIMEOUT_MS,
            )
            if result is None:
                return ""
            return buf.value
        except Exception:
            return ""

    def post_background_enter(self) -> bool:
        """Post Enter key messages to the status Edit control without global input.

        This method is not a strict-background capability: a live WinEvent probe
        observed Camfrog foreground activation while handling these messages.
        The strict executor refuses this path.
        """
        if sys.platform != "win32":
            return False

        try:
            if not self.controls_are_current():
                return False
            user32 = ctypes.windll.user32
            res_down = user32.PostMessageW(
                self.edit_hwnd,
                self.WM_KEYDOWN,
                self.VK_RETURN,
                self.RETURN_LPARAM_DOWN,
            )
            res_up = user32.PostMessageW(
                self.edit_hwnd,
                self.WM_KEYUP,
                self.VK_RETURN,
                self.RETURN_LPARAM_UP,
            )
            return bool(res_down and res_up)
        except Exception:
            return False


def _send_message_timeout(
    hwnd: int,
    message: int,
    wparam: int,
    lparam: int,
    timeout_ms: int,
) -> int | None:
    """Send one Win32 message with a finite wait; ``None`` means timeout/error."""
    if sys.platform != "win32":
        return None
    try:
        send_message_timeout = ctypes.windll.user32.SendMessageTimeoutW
        send_message_timeout.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        send_message_timeout.restype = ctypes.c_ssize_t
        result = ctypes.c_size_t()
        succeeded = send_message_timeout(
            ctypes.c_void_p(hwnd),
            message,
            wparam,
            lparam,
            0x0002,  # SMTO_ABORTIFHUNG
            max(1, int(timeout_ms)),
            ctypes.byref(result),
        )
        return int(result.value) if succeeded else None
    except Exception:
        return None


def _foreground_window_handle() -> Optional[int]:
    """Return the current foreground HWND, or None when it cannot be sampled."""
    if sys.platform != "win32":
        return None
    try:
        import win32gui

        return win32gui.GetForegroundWindow() or None
    except Exception:
        return None


# ---------- Camfrog Window Resolver ----------

class CamfrogWindowResolver:
    """Discovers and validates the status controls under verified Camfrog process."""

    def __init__(
        self,
        process_discovery: Optional[WindowsCamfrogProcessDiscovery] = None,
        window_classifier: Optional[WindowsCamfrogWindowClassifier] = None,
    ):
        self.discovery = process_discovery or WindowsCamfrogProcessDiscovery()
        self.classifier = window_classifier or WindowsCamfrogWindowClassifier()

    def resolve_status_controls(
        self,
        pid: Optional[int] = None
    ) -> Tuple[ProcessIdentity, ClassifiedWindow, StatusControlAdapter]:
        """Resolve verified process, main directory window, and status control adapter.

        Raises:
            ProcessLookupError: if process identity cannot be found/verified.
            LookupError: if main directory window or status controls are missing.
        """
        proc = self.discovery.get_verified_process(pid=pid)
        windows = self.classifier.classify_process_windows(proc)

        main_windows = [w for w in windows if w.role == WindowRole.MAIN_DIRECTORY]
        if not main_windows:
            raise LookupError(f"No verified MAIN_DIRECTORY window found for Camfrog PID {proc.pid}.")

        main_win = main_windows[0]
        if not revalidate_window_identity(main_win, proc):
            raise LookupError(f"Main directory window hwnd={main_win.hwnd} failed identity revalidation.")

        # Discover CComboBoxTS (automation ID 1436) and its child Edit (ID 1001).
        # Control IDs take precedence over class names: on windows with
        # several native edits, class-only matching could bind the wrong
        # pair and write status text into an unrelated field.
        combo_hwnd = 0
        edit_hwnd = 0
        combo_by_id = 0
        edit_by_id = 0

        if sys.platform == "win32":
            try:
                import win32gui
                def _cb(ch, _):
                    nonlocal combo_hwnd, edit_hwnd, combo_by_id, edit_by_id
                    cid = win32gui.GetDlgCtrlID(ch)
                    cls = win32gui.GetClassName(ch)
                    if cls == "CComboBoxTS" or cid == 1436:
                        combo_hwnd = ch
                    elif cls in ("Edit", "CEditTS") or cid == 1001:
                        edit_hwnd = ch
                    if cid == 1436:
                        combo_by_id = ch
                    elif cid == 1001:
                        edit_by_id = ch
                    return True
                win32gui.EnumChildWindows(main_win.hwnd, _cb, None)
                if combo_by_id and edit_by_id:
                    try:
                        if win32gui.GetParent(edit_by_id) == combo_by_id:
                            combo_hwnd, edit_hwnd = combo_by_id, edit_by_id
                    except Exception:
                        pass
            except Exception:
                pass

        if not combo_hwnd or not edit_hwnd:
            raise LookupError(
                f"Status controls not found in main window hwnd={main_win.hwnd} "
                f"(combo={combo_hwnd}, edit={edit_hwnd})."
            )

        adapter = StatusControlAdapter(
            combo_hwnd=combo_hwnd,
            edit_hwnd=edit_hwnd,
            expected_pid=proc.pid,
            parent_hwnd=main_win.hwnd,
        )
        return proc, main_win, adapter


# ---------- Resource-Scoped Status Lock ----------

class StatusResourceLock:
    """Inter-process and intra-process resource-scoped synchronization for status writes.

    Ensures that Random Status, Marquee Status, and Unified Client cannot simultaneously
    write to the same Camfrog client status control.
    """

    _process_locks: Dict[str, threading.RLock] = {}
    _meta_lock = threading.Lock()

    def __init__(self, resource_key: str = "global", timeout_seconds: float = 5.0):
        self.resource_key = str(resource_key or "global").replace("\\", "_")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._mutex_handle = None
        self._kernel32 = None
        self._thread_lock = self._get_thread_lock(self.resource_key)
        self._acquired_thread = False
        self._acquired_mutex = False

    @classmethod
    def _get_thread_lock(cls, key: str) -> threading.RLock:
        with cls._meta_lock:
            if key not in cls._process_locks:
                cls._process_locks[key] = threading.RLock()
            return cls._process_locks[key]

    def acquire(self) -> bool:
        """Acquire the scoped lock across threads and processes."""
        deadline = time.monotonic() + self.timeout_seconds

        rem_time = max(0.01, deadline - time.monotonic())
        acquired_thread = self._thread_lock.acquire(timeout=rem_time)
        if not acquired_thread:
            return False
        self._acquired_thread = True

        if sys.platform == "win32":
            try:
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                create_mutex = kernel32.CreateMutexW
                create_mutex.argtypes = (
                    ctypes.c_void_p,
                    wintypes.BOOL,
                    wintypes.LPCWSTR,
                )
                create_mutex.restype = wintypes.HANDLE
                wait_for_single_object = kernel32.WaitForSingleObject
                wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
                wait_for_single_object.restype = wintypes.DWORD
                release_mutex = kernel32.ReleaseMutex
                release_mutex.argtypes = (wintypes.HANDLE,)
                release_mutex.restype = wintypes.BOOL
                close_handle = kernel32.CloseHandle
                close_handle.argtypes = (wintypes.HANDLE,)
                close_handle.restype = wintypes.BOOL
                mutex_name = f"Local\\ZChat_StatusLock_{self.resource_key}"

                handle = create_mutex(None, False, mutex_name)
                if not handle:
                    self._thread_lock.release()
                    self._acquired_thread = False
                    return False
                self._mutex_handle = handle
                self._kernel32 = kernel32

                rem_ms = max(10, int(max(0.01, deadline - time.monotonic()) * 1000))
                wait_res = wait_for_single_object(handle, rem_ms)
                if wait_res not in (0x00000000, 0x00000080):  # WAIT_OBJECT_0 or WAIT_ABANDONED
                    close_handle(handle)
                    self._mutex_handle = None
                    self._kernel32 = None
                    self._thread_lock.release()
                    self._acquired_thread = False
                    return False
                self._acquired_mutex = True
                return True
            except Exception:
                if self._mutex_handle:
                    try:
                        if self._kernel32 is not None:
                            self._kernel32.CloseHandle(self._mutex_handle)
                    except Exception:
                        pass
                    self._mutex_handle = None
                    self._kernel32 = None
                self._thread_lock.release()
                self._acquired_thread = False
                return False

        return True

    def release(self) -> None:
        """Release the scoped lock."""
        if sys.platform == "win32" and self._mutex_handle and self._kernel32 is not None:
            try:
                if self._acquired_mutex:
                    self._kernel32.ReleaseMutex(self._mutex_handle)
                self._kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            finally:
                self._mutex_handle = None
                self._kernel32 = None
                self._acquired_mutex = False

        if self._acquired_thread:
            try:
                self._thread_lock.release()
            except RuntimeError:
                pass
            finally:
                self._acquired_thread = False

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(
                f"Failed to acquire status lock for resource '{self.resource_key}' within {self.timeout_seconds}s"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


# ---------- Background Status Executor ----------

class BackgroundStatusExecutor(EmergencyStopHook):
    """Executes a status change only when strict desktop isolation is supported.

    When an adapter declares a verified submit capability, the execution sequence is:
    1. DISCOVER_PROCESS
    2. VERIFY_PROCESS
    3. DISCOVER_WINDOW
    4. VERIFY_STATUS_CONTROL
    5. WRITE_TEXT_WITHOUT_FOCUS
    6. VERIFY_TEXT_READBACK
    7. SUBMIT_WITHOUT_GLOBAL_INPUT
    8. CONFIRM_CLIENT_STATE
    9. REPORT_RESULT

    The current Camfrog adapter does not declare that capability, so strict mode
    returns BACKGROUND_UNSUPPORTED before staging text or submitting key messages.
    """

    def __init__(
        self,
        resolver: Optional[CamfrogWindowResolver] = None,
        default_mode: ExecutionMode = ExecutionMode.STRICT_BACKGROUND,
        emergency_scope: EmergencyStopScope = EmergencyStopScope.GLOBAL,
        emergency_resource_key: Optional[str] = None,
    ):
        EmergencyStopHook.__init__(self)
        self.resolver = resolver or CamfrogWindowResolver()
        self.default_mode = default_mode
        self.emergency_scope = emergency_scope
        self.emergency_resource_key = emergency_resource_key
        self._lock = threading.Lock()

    @staticmethod
    def _target_identity_is_current(
        proc: ProcessIdentity,
        main_win: ClassifiedWindow,
        adapter: StatusControlAdapter,
    ) -> bool:
        """Revalidate the Camfrog process, main HWND, and status child controls."""
        if sys.platform != "win32":
            return True
        try:
            return (
                revalidate_process_identity(proc)
                and revalidate_window_identity(main_win, proc)
                and adapter.controls_are_current(proc.pid, main_win.hwnd)
            )
        except Exception:
            return False

    def execute_status_change(
        self,
        status_text: str,
        pid: Optional[int] = None,
        execution_mode: Optional[ExecutionMode] = None,
        timeout_seconds: float = 3.0,
    ) -> StatusExecutionEvidence:
        """Execute a status change with evidence recording and strict background isolation."""
        start_time = time.monotonic()
        req_id = f"req-{uuid.uuid4().hex[:8]}"
        op_id = f"op-{uuid.uuid4().hex[:8]}"
        mode = execution_mode or self.default_mode

        def emergency_stop_evidence(
            proc: Optional[ProcessIdentity] = None,
            main_win: Optional[ClassifiedWindow] = None,
        ) -> StatusExecutionEvidence:
            return StatusExecutionEvidence(
                request_id=req_id,
                operation_id=op_id,
                client_version=proc.version if proc else "",
                process_identity=proc.as_dict() if proc else {},
                window_role=main_win.role.value if main_win else "",
                execution_mode=mode.value,
                result_state=StatusResultState.FAILED.value,
                duration_ms=(time.monotonic() - start_time) * 1000,
                verification_source="emergency_stop",
                error_code="EMERGENCY_STOP",
            )

        def target_identity_evidence(
            proc: ProcessIdentity,
            main_win: ClassifiedWindow,
            result_state: StatusResultState = StatusResultState.FAILED,
            error_code: str = "TARGET_IDENTITY_CHANGED",
        ) -> StatusExecutionEvidence:
            return StatusExecutionEvidence(
                request_id=req_id,
                operation_id=op_id,
                client_version=proc.version,
                process_identity=proc.as_dict(),
                window_role=main_win.role.value,
                execution_mode=mode.value,
                result_state=result_state.value,
                duration_ms=(time.monotonic() - start_time) * 1000,
                verification_source="target_identity_revalidation",
                error_code=error_code,
            )

        clean_text = (status_text or "").strip()
        if not clean_text:
            return StatusExecutionEvidence(
                request_id=req_id,
                operation_id=op_id,
                client_version="",
                process_identity={},
                window_role="",
                execution_mode=mode.value,
                result_state=StatusResultState.FAILED.value,
                duration_ms=0.0,
                verification_source="validation",
                error_code="EMPTY_STATUS_TEXT",
            )

        with self._lock:
            # 0. EMERGENCY STOP GATE — refuse new work while stopped.
            try:
                self.check_emergency_stop()
            except StopIteration:
                return emergency_stop_evidence()
            # 1 & 2. DISCOVER_PROCESS & VERIFY_PROCESS
            try:
                proc, main_win, adapter = self.resolver.resolve_status_controls(pid=pid)
            except ProcessLookupError as e:
                return StatusExecutionEvidence(
                    request_id=req_id,
                    operation_id=op_id,
                    client_version="",
                    process_identity={},
                    window_role="",
                    execution_mode=mode.value,
                    result_state=StatusResultState.FAILED.value,
                    duration_ms=(time.monotonic() - start_time) * 1000,
                    verification_source="process_discovery",
                    error_code="PROCESS_NOT_FOUND",
                )
            except LookupError as e:
                return StatusExecutionEvidence(
                    request_id=req_id,
                    operation_id=op_id,
                    client_version=getattr(locals().get("proc"), "version", ""),
                    process_identity=locals()["proc"].as_dict() if locals().get("proc") else {},
                    window_role=WindowRole.MAIN_DIRECTORY.value,
                    execution_mode=mode.value,
                    result_state=StatusResultState.BACKGROUND_UNSUPPORTED.value,
                    duration_ms=(time.monotonic() - start_time) * 1000,
                    verification_source="window_resolver",
                    error_code="CONTROLS_NOT_FOUND",
                )
            except RuntimeError as e:
                # Ambiguous process/window identity (e.g. multiple verified
                # clients): report evidence, never choose a target.
                return StatusExecutionEvidence(
                    request_id=req_id,
                    operation_id=op_id,
                    client_version="",
                    process_identity={},
                    window_role="",
                    execution_mode=mode.value,
                    result_state=StatusResultState.FAILED.value,
                    duration_ms=(time.monotonic() - start_time) * 1000,
                    verification_source="process_discovery",
                    error_code="AMBIGUOUS_PROCESS_IDENTITY",
                )

            # Resource-scoped synchronization across processes targeting this Camfrog client
            lock_resource = f"pid_{proc.pid}"
            try:
                with StatusResourceLock(resource_key=lock_resource, timeout_seconds=timeout_seconds):
                    # Snapshot foreground before and after; this detects endpoint changes,
                    # but cannot rule out a transient focus change between samples.
                    fg_before = _foreground_window_handle()

                    if not self._target_identity_is_current(proc, main_win, adapter):
                        return target_identity_evidence(proc, main_win)

                    # Check current client UI text to avoid duplicate submissions.
                    current_status = adapter.read_applied_status()
                    if current_status == clean_text:
                        return StatusExecutionEvidence(
                            request_id=req_id,
                            operation_id=op_id,
                            client_version=proc.version,
                            process_identity=proc.as_dict(),
                            window_role=main_win.role.value,
                            execution_mode=mode.value,
                            result_state=StatusResultState.CLIENT_STATE_CONFIRMED.value,
                            duration_ms=(time.monotonic() - start_time) * 1000,
                            verification_source="client_combo_state_already_active",
                            applied_text=clean_text,
                        )

                    if (
                        mode == ExecutionMode.STRICT_BACKGROUND
                        and not adapter.supports_strict_background_submit()
                    ):
                        return StatusExecutionEvidence(
                            request_id=req_id,
                            operation_id=op_id,
                            client_version=proc.version,
                            process_identity=proc.as_dict(),
                            window_role=main_win.role.value,
                            execution_mode=mode.value,
                            result_state=StatusResultState.BACKGROUND_UNSUPPORTED.value,
                            duration_ms=(time.monotonic() - start_time) * 1000,
                            verification_source="submit_capability",
                            error_code="STRICT_SUBMIT_UNVERIFIED",
                        )

                    # 5. WRITE_TEXT_WITHOUT_FOCUS
                    try:
                        self.check_emergency_stop()
                    except StopIteration:
                        return emergency_stop_evidence(proc, main_win)
                    if not self._target_identity_is_current(proc, main_win, adapter):
                        return target_identity_evidence(proc, main_win)
                    write_ok = adapter.write_text_no_focus(clean_text)
                    if not write_ok:
                        return StatusExecutionEvidence(
                            request_id=req_id,
                            operation_id=op_id,
                            client_version=proc.version,
                            process_identity=proc.as_dict(),
                            window_role=main_win.role.value,
                            execution_mode=mode.value,
                            result_state=StatusResultState.FAILED.value,
                            duration_ms=(time.monotonic() - start_time) * 1000,
                            verification_source="status_control_adapter",
                            error_code="WRITE_TEXT_FAILED",
                        )

                    # 6. VERIFY_TEXT_READBACK
                    read_back = adapter.read_edit_text()
                    if read_back != clean_text:
                        return StatusExecutionEvidence(
                            request_id=req_id,
                            operation_id=op_id,
                            client_version=proc.version,
                            process_identity=proc.as_dict(),
                            window_role=main_win.role.value,
                            execution_mode=mode.value,
                            result_state=StatusResultState.FAILED.value,
                            duration_ms=(time.monotonic() - start_time) * 1000,
                            verification_source="edit_readback",
                            error_code="READBACK_MISMATCH",
                        )

                    # 7. SUBMIT_WITHOUT_GLOBAL_INPUT
                    try:
                        self.check_emergency_stop()
                    except StopIteration:
                        return emergency_stop_evidence(proc, main_win)
                    if not self._target_identity_is_current(proc, main_win, adapter):
                        return target_identity_evidence(proc, main_win)
                    fg_before_submit = _foreground_window_handle()
                    foreground_matches_before_submit = (
                        fg_before == fg_before_submit
                        if fg_before is not None and fg_before_submit is not None
                        else None
                    )
                    if (
                        sys.platform == "win32"
                        and mode == ExecutionMode.STRICT_BACKGROUND
                        and foreground_matches_before_submit is not True
                    ):
                        return StatusExecutionEvidence(
                            request_id=req_id,
                            operation_id=op_id,
                            client_version=proc.version,
                            process_identity=proc.as_dict(),
                            window_role=main_win.role.value,
                            execution_mode=mode.value,
                            result_state=StatusResultState.BACKGROUND_UNSUPPORTED.value,
                            duration_ms=(time.monotonic() - start_time) * 1000,
                            verification_source="foreground_pre_submit",
                            error_code="FOREGROUND_PRECONDITION_NOT_CONFIRMED",
                            foreground_matches_before_submit=foreground_matches_before_submit,
                        )
                    post_ok = adapter.post_background_enter()
                    if not post_ok:
                        return StatusExecutionEvidence(
                            request_id=req_id,
                            operation_id=op_id,
                            client_version=proc.version,
                            process_identity=proc.as_dict(),
                            window_role=main_win.role.value,
                            execution_mode=mode.value,
                            result_state=StatusResultState.FAILED.value,
                            duration_ms=(time.monotonic() - start_time) * 1000,
                            verification_source="post_enter",
                            error_code="POST_ENTER_FAILED",
                        )

                    # 8. CONFIRM_CLIENT_STATE
                    # Poll CComboBoxTS item 0. This only confirms local client UI state.
                    applied_confirmed = False
                    deadline = time.monotonic() + timeout_seconds
                    while time.monotonic() < deadline:
                        time.sleep(0.05)
                        if not self._target_identity_is_current(proc, main_win, adapter):
                            return target_identity_evidence(
                                proc,
                                main_win,
                                result_state=StatusResultState.UNKNOWN,
                                error_code="TARGET_IDENTITY_LOST_AFTER_SUBMIT",
                            )
                        applied = adapter.read_applied_status()
                        if applied == clean_text:
                            applied_confirmed = True
                            break

                    # Confirm desktop foreground was not stolen
                    fg_after = _foreground_window_handle()
                    focus_matches = (
                        fg_before == fg_after
                        if fg_before is not None and fg_after is not None
                        else None
                    )

                    duration_ms = (time.monotonic() - start_time) * 1000

                    if not applied_confirmed:
                        return StatusExecutionEvidence(
                            request_id=req_id,
                            operation_id=op_id,
                            client_version=proc.version,
                            process_identity=proc.as_dict(),
                            window_role=main_win.role.value,
                            execution_mode=mode.value,
                            result_state=StatusResultState.FAILED.value,
                            duration_ms=duration_ms,
                            verification_source="combo_history_verification",
                            error_code="COMMIT_NOT_VERIFIED",
                            foreground_matches_before_and_after=focus_matches,
                        )

                    # Local text read-back is not enough to call strict mode
                    # safe when the desktop foreground changed or could not be
                    # sampled. The status may have reached the client, but the
                    # no-focus acceptance condition was not established.
                    if (
                        sys.platform == "win32"
                        and mode == ExecutionMode.STRICT_BACKGROUND
                        and focus_matches is not True
                    ):
                        return StatusExecutionEvidence(
                            request_id=req_id,
                            operation_id=op_id,
                            client_version=proc.version,
                            process_identity=proc.as_dict(),
                            window_role=main_win.role.value,
                            execution_mode=mode.value,
                            result_state=StatusResultState.BACKGROUND_UNSUPPORTED.value,
                            duration_ms=duration_ms,
                            verification_source="foreground_isolation",
                            applied_text=clean_text,
                            error_code="FOREGROUND_ISOLATION_NOT_CONFIRMED",
                            foreground_matches_before_and_after=focus_matches,
                            foreground_matches_before_submit=foreground_matches_before_submit,
                        )

                    return StatusExecutionEvidence(
                        request_id=req_id,
                        operation_id=op_id,
                        client_version=proc.version,
                        process_identity=proc.as_dict(),
                        window_role=main_win.role.value,
                        execution_mode=mode.value,
                        result_state=StatusResultState.CLIENT_STATE_CONFIRMED.value,
                        duration_ms=duration_ms,
                        verification_source="client_combo_item_0",
                        applied_text=clean_text,
                        foreground_matches_before_and_after=focus_matches,
                        foreground_matches_before_submit=foreground_matches_before_submit,
                    )
            except TimeoutError:
                return StatusExecutionEvidence(
                    request_id=req_id,
                    operation_id=op_id,
                    client_version=proc.version,
                    process_identity=proc.as_dict(),
                    window_role=main_win.role.value,
                    execution_mode=mode.value,
                    result_state=StatusResultState.FAILED.value,
                    duration_ms=(time.monotonic() - start_time) * 1000,
                    verification_source="status_resource_lock",
                    error_code="LOCK_TIMEOUT",
                )


# ---------- Shared Status Scheduler ----------

class StatusScheduler:
    """Manages scheduled, random, or marquee status rotations safely."""

    def __init__(self, executor: Optional[BackgroundStatusExecutor] = None):
        self.executor = executor or BackgroundStatusExecutor()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._history: List[StatusExecutionEvidence] = []
        self._lock = threading.Lock()

    def stop(self) -> None:
        """Signal running worker to stop."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    def is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def get_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [ev.as_dict() for ev in self._history]

    def record_evidence(self, ev: StatusExecutionEvidence) -> None:
        with self._lock:
            self._history.append(ev)
            if len(self._history) > 200:
                self._history.pop(0)

    def set_single_status(self, text: str, pid: Optional[int] = None) -> StatusExecutionEvidence:
        """Set a single status update immediately in background."""
        ev = self.executor.execute_status_change(text, pid=pid)
        self.record_evidence(ev)
        return ev


# ---------- Configuration Contract Validation ----------

DEFAULT_CONFIG_CONTRACT = {
    "status": {
        "enabled": True,
        "execution_mode": "strict_background",
        "background_enter_target": "combo",
        "verify_result": True,
        "interval_seconds": 600,
    },
    "safety": {
        "require_foreground": False,
        "restore_previous_window": False,
        "allow_focus_stealing": False,
        "allow_mouse_injection": False,
        "allow_keyboard_injection": False,
        "allow_clipboard_mutation": False,
        "allow_system_sound_changes": False,
    },
}


def validate_status_config(cfg: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Validate status configuration against safety contract and supply safe defaults."""
    errors = []
    normalized = dict(cfg)

    status_sec = normalized.setdefault("status", {})
    safety_sec = normalized.setdefault("safety", {})

    # Defaults
    status_sec.setdefault("enabled", True)
    status_sec.setdefault("execution_mode", "strict_background")
    status_sec.setdefault("background_enter_target", "combo")
    status_sec.setdefault("verify_result", True)
    status_sec.setdefault("interval_seconds", 600)

    safety_sec.setdefault("require_foreground", False)
    safety_sec.setdefault("restore_previous_window", False)
    safety_sec.setdefault("allow_focus_stealing", False)
    safety_sec.setdefault("allow_mouse_injection", False)
    safety_sec.setdefault("allow_keyboard_injection", False)
    safety_sec.setdefault("allow_clipboard_mutation", False)
    safety_sec.setdefault("allow_system_sound_changes", False)

    mode = status_sec.get("execution_mode")
    if mode not in ("strict_background", "best_effort_background", "interactive"):
        errors.append(
            "Invalid status.execution_mode: "
            f"{mode!r} (must be 'strict_background', "
            "'best_effort_background', or 'interactive')"
        )

    target = status_sec.get("background_enter_target")
    if target not in ("combo", "edit"):
        errors.append(f"Invalid status.background_enter_target: {target!r} (must be 'combo' or 'edit')")

    return len(errors) == 0, errors, normalized
