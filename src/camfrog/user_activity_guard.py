"""Shared user-activity guard used by every self-contained module.

Yield the desktop to the human before any command that injects global input.
Commands that move the mouse, steal focus or send real keystrokes must not run
while the user is working. Wait (bounded) until the user has been idle for
`idle_ms`; input produced by our own injection is not counted as user activity.
Windows only: elsewhere `user_idle_ms()` is None and the guard never blocks.

Kept identical (same AST) across every module that embeds a copy.
"""

import ctypes
import logging
import time

log = logging.getLogger(__name__)


class UserActivityGuard:
    """Yield the desktop to the human before any command that injects global input."""

    def __init__(
        self,
        idle_ms=None,
        poll=0.25,
        max_wait=120.0,
        tick=None,
        last_input=None,
        sleep=None,
        clock=None,
        os=None,
    ):
        self._os = os or __import__("os")
        if idle_ms is None:
            try:
                idle_ms = int(self._os.environ.get("ZCHAT_USER_IDLE_MS", "2000"))
            except ValueError:
                idle_ms = 2000
        self.idle_ms = max(0, idle_ms)
        self.poll = poll
        self.max_wait = max_wait
        self._tick = tick or self._win_tick
        self._last_input = last_input or self._win_last_input
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._own = None  # (start_tick, end_tick) of our latest injection
        self._user_tick = None

    @staticmethod
    def _win_tick():
        try:
            kernel32 = getattr(getattr(ctypes, "windll", None), "kernel32", None)
            if kernel32 and hasattr(kernel32, "GetTickCount"):
                return kernel32.GetTickCount() & 0xFFFFFFFF
        except Exception:  # noqa: S110, BLE001 - defensive: Win32 API may not exist on this platform
            pass
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    def _win_last_input(self):
        if self._os.name != "nt":
            return None
        try:
            user32 = getattr(getattr(ctypes, "windll", None), "user32", None)
            if not user32 or not hasattr(user32, "GetLastInputInfo"):
                return None

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            if not user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            return info.dwTime
        except Exception:  # noqa: BLE001 - defensive: Win32 API may not exist on this platform
            return None

    def mark_own(self, start, end, slack=400):
        self._own = (start, (end + slack) & 0xFFFFFFFF)

    def _is_own(self, last):
        if not self._own:
            return False
        start, end = self._own
        return ((last - start) & 0xFFFFFFFF) <= ((end - start) & 0xFFFFFFFF)

    def user_idle_ms(self):
        """Milliseconds since the last *user* input, or None when unknown."""
        try:
            last = self._last_input()
            if last is None:
                return None
            if not self._is_own(last):
                self._user_tick = last
            if self._user_tick is None:
                return 10**9  # only our own input seen so far
            return (self._tick() - self._user_tick) & 0xFFFFFFFF
        except Exception:  # noqa: BLE001 - defensive: Win32 API may not exist on this platform
            return None

    def wait_until_idle(self):
        """Wait for idle input; refuse unsafe injection when Windows input is unknown."""
        if self.idle_ms <= 0:
            return True
        deadline = self._clock() + self.max_wait
        noted = False
        while True:
            idle = self.user_idle_ms()
            if idle is None:
                if self._os.name == "nt":
                    log.error(
                        "Cannot verify Windows user activity; refusing desktop input injection"
                    )
                    return False
                return True
            if idle >= self.idle_ms:
                return True
            if self._clock() >= deadline:
                return False
            if not noted:
                log.info(
                    "User is active; deferring input injection until the desktop is idle"
                )
                noted = True
            self._sleep(self.poll)

    def run(self, func, *args, **kwargs):
        start = self._tick() if self._os.name == "nt" else 0
        try:
            return func(*args, **kwargs)
        finally:
            if self._os.name == "nt":
                self.mark_own(start, self._tick())


USER_GUARD = UserActivityGuard()
