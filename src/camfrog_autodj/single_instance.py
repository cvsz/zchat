"""Single-instance enforcement and crash reporting via named mutexes/events."""
from __future__ import annotations
import atexit
import logging
import os
import sys
import uuid
from typing import Optional

log = logging.getLogger("autodj")

# Windows-specific imports
if os.name == "nt":
    import ctypes
    import ctypes.wintypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    # Constants
    SYNCHRONIZE = 0x00100000
    MUTEX_ALL_ACCESS = 0x1F0001
    EVENT_ALL_ACCESS = 0x1F0003
    ERROR_ALREADY_EXISTS = 183
    ERROR_SUCCESS = 0

    # Function prototypes
    CreateMutexW = kernel32.CreateMutexW
    CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    CreateMutexW.restype = wintypes.HANDLE

    OpenMutexW = kernel32.OpenMutexW
    OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    OpenMutexW.restype = wintypes.HANDLE

    CreateEventW = kernel32.CreateEventW
    CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    CreateEventW.restype = wintypes.HANDLE

    SetEvent = kernel32.SetEvent
    SetEvent.argtypes = [wintypes.HANDLE]
    SetEvent.restype = wintypes.BOOL

    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL

    GetLastError = kernel32.GetLastError
    GetLastError.restype = wintypes.DWORD

    GetCurrentProcessId = kernel32.GetCurrentProcessId
    GetCurrentProcessId.restype = wintypes.DWORD


class SingleInstance:
    """Single-instance enforcement using a named mutex."""

    def __init__(self, app_name: str = "AutoDJ"):
        self.app_name = app_name
        self.mutex_name = f"Global\\{app_name}_Instance"
        self.mutex_handle: Optional[int] = None
        self.is_first_instance = False

    def acquire(self) -> bool:
        """Try to acquire the instance mutex. Returns True if this is the first instance."""
        if os.name != "nt":
            # On non-Windows, always allow (use file-based lock if needed)
            self.is_first_instance = True
            return True

        # Try to create the mutex
        self.mutex_handle = CreateMutexW(None, True, self.mutex_name)
        last_error = GetLastError()

        if not self.mutex_handle:
            log.error("Failed to create instance mutex: %s", ctypes.WinError())
            return False

        if last_error == ERROR_ALREADY_EXISTS:
            log.warning("Another instance of %s is already running (mutex: %s)",
                        self.app_name, self.mutex_name)
            self.is_first_instance = False
            return False

        self.is_first_instance = True
        log.info("Acquired single-instance mutex: %s", self.mutex_name)
        return True

    def release(self):
        """Release the instance mutex."""
        if self.mutex_handle and os.name == "nt":
            CloseHandle(self.mutex_handle)
            self.mutex_handle = None
            log.debug("Released instance mutex: %s", self.mutex_name)


class CrashReporter:
    """Crash reporting via named event for cross-process notification."""

    def __init__(self, app_name: str = "AutoDJ"):
        self.app_name = app_name
        self.event_name = f"Global\\{app_name}_CrashRpt_{uuid.uuid4().hex[:16]}"
        self.event_handle: Optional[int] = None

    def initialize(self):
        """Create the crash report event."""
        if os.name != "nt":
            return

        self.event_handle = CreateEventW(None, True, False, self.event_name)
        if not self.event_handle:
            log.warning("Failed to create crash report event: %s", ctypes.WinError())
            return

        log.debug("Created crash report event: %s", self.event_name)

        # Register to signal on normal exit
        atexit.register(self._signal_normal_exit)

        # Set up exception hook for unhandled exceptions
        sys.excepthook = self._handle_exception

    def _signal_normal_exit(self):
        """Signal normal exit (not a crash)."""
        if self.event_handle and os.name == "nt":
            SetEvent(self.event_handle)
            log.debug("Signaled normal exit via crash event")

    def _handle_exception(self, exc_type, exc_value, exc_traceback):
        """Handle unhandled exceptions by signaling crash event."""
        if exc_type is KeyboardInterrupt:
            # Let KeyboardInterrupt through normally
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        log.error("Unhandled exception: %s", exc_value, exc_info=(exc_type, exc_value, exc_traceback))

        if self.event_handle and os.name == "nt":
            # Signal crash event so any monitoring process knows
            SetEvent(self.event_handle)
            log.info("Signaled crash report event: %s", self.event_name)

        # Call original excepthook
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    def cleanup(self):
        """Clean up the crash event."""
        if self.event_handle and os.name == "nt":
            CloseHandle(self.event_handle)
            self.event_handle = None
            log.debug("Closed crash report event: %s", self.event_name)


def get_instance_id() -> str:
    """Get a unique instance identifier for this run."""
    return f"{os.getpid()}_{uuid.uuid4().hex[:8]}"


def setup_single_instance_and_crash_reporting(app_name: str = "AutoDJ") -> tuple[SingleInstance, CrashReporter]:
    """Set up single-instance enforcement and crash reporting."""
    instance = SingleInstance(app_name)
    crash_reporter = CrashReporter(app_name)

    if not instance.acquire():
        sys.exit(1)

    crash_reporter.initialize()
    log.info("Single-instance and crash reporting initialized for %s", app_name)

    return instance, crash_reporter