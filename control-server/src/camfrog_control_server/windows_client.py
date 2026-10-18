"""Opt-in Windows adapter for launching the installed Camfrog client via official URIs."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote


class WindowsCamfrogClientProvider:
    """Launch one configured client and dispatch room joins through the registered URI handler.

    This adapter requires an interactive Windows user session. It does not log in, inspect the
    account, confirm room membership, or terminate processes that it did not launch itself.
    """

    STARTUP_VERIFY_SECONDS = 0.1

    def __init__(
        self,
        executable: str,
        *,
        platform_name: str | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        uri_launcher: Callable[[str], Any] | None = None,
    ) -> None:
        self._platform = platform_name or sys.platform
        if self._platform != "win32":
            raise ValueError("The Camfrog client provider is available only on Windows.")
        path = Path(executable).expanduser()
        if not path.is_absolute() or path.suffix.casefold() != ".exe" or not path.is_file():
            raise ValueError("Camfrog client path must be an existing absolute .exe path.")
        self._executable = path.resolve()
        self._popen = popen
        self._uri_launcher = uri_launcher or self._launch_uri
        self._process: Any | None = None

    def process_state(self) -> str:
        """Report only the child process handle created by this provider."""
        if self._process is None:
            return "unknown"
        return "running" if self._process.poll() is None else "stopped"

    @staticmethod
    def _launch_uri(uri: str) -> None:
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("Windows protocol handler is unavailable.")
        startfile(uri)

    def execute(self, command: dict[str, Any]) -> tuple[str, str]:
        operation = command.get("operation")
        try:
            if operation == "launch":
                return self._launch()
            if operation == "stop":
                return self._stop()
            if operation == "restart":
                return self._restart()
            if operation == "join_room":
                return self._join_room(command)
            if operation == "add_friend":
                return self._open_nickname_uri(command, "add")
            if operation == "open_im":
                return self._open_nickname_uri(command, "im")
            # No verified Camfrog URI or client action for leaving a room is available.
            return "failed", "provider_not_configured"
        except subprocess.TimeoutExpired:
            return "failed", "timed_out"
        except (OSError, ValueError, TypeError):
            return "failed", "execution_failed"

    def _launch(self) -> tuple[str, str]:
        if self._process is not None and self._process.poll() is None:
            return "succeeded", "ok"
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self._process = self._popen(
            [str(self._executable)],
            cwd=str(self._executable.parent),
            shell=False,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        # A successful CreateProcess/Popen call is not enough: the executable can
        # exit immediately because it is missing a runtime dependency or is rejected
        # by Windows. Only report launch success after the owned process remains live.
        time.sleep(self.STARTUP_VERIFY_SECONDS)
        if self._process.poll() is not None:
            self._process = None
            return "failed", "execution_failed"
        return "succeeded", "ok"

    def _restart(self) -> tuple[str, str]:
        if self._process is None or self._process.poll() is not None:
            self._process = None
            return "failed", "client_not_found"
        stopped_state, stopped_code = self._stop()
        if stopped_state != "succeeded":
            return stopped_state, stopped_code
        return self._launch()

    def _stop(self) -> tuple[str, str]:
        if self._process is None or self._process.poll() is not None:
            self._process = None
            return "failed", "client_not_found"
        process = self._process
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Kill only the exact process handle created by this adapter.
            process.kill()
            process.wait(timeout=5)
        return "succeeded", "ok"

    def _join_room(self, command: dict[str, Any]) -> tuple[str, str]:
        payload = command.get("payload")
        room = payload.get("room") if isinstance(payload, dict) else None
        if not isinstance(room, str) or not room.strip() or len(room) > 128:
            return "failed", "execution_failed"
        normalized = room.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
            return "failed", "execution_failed"
        uri = "camfrog://join_room/?name=" + quote(normalized, safe="")
        self._uri_launcher(uri)
        # The OS accepted the URI; Camfrog's resulting state is unverified.
        return "effect_unknown", "handoff_accepted"

    def _open_nickname_uri(self, command: dict[str, Any], action: str) -> tuple[str, str]:
        payload = command.get("payload")
        nickname = payload.get("nickname") if isinstance(payload, dict) else None
        if not isinstance(nickname, str) or not nickname.strip() or len(nickname) > 256:
            return "failed", "execution_failed"
        nickname = nickname.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in nickname):
            return "failed", "execution_failed"
        self._uri_launcher(f"camfrog:{action}:{quote(nickname, safe='')}")
        # The OS accepted the URI; Camfrog's resulting state is unverified.
        return "effect_unknown", "handoff_accepted"
