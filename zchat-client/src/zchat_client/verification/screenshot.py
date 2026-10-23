"""Explicit read-only window screenshot capture for Windows acceptance tests."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Protocol


class ScreenshotCapture(Protocol):
    def capture(self, target: str, destination: Path) -> bool: ...


class NullScreenshotCapture:
    def capture(self, target: str, destination: Path) -> bool:
        return False


class WindowsWindowScreenshotCapture:
    """Capture an explicitly selected Camfrog HWND; never capture the desktop.

    `target` is a decimal window handle supplied by a supervising operator.
    Capture is best-effort and returns False for hidden/minimized/inaccessible
    windows or if the process identity cannot be confirmed.
    """

    def capture(self, target: str, destination: Path) -> bool:
        if platform.system() != "Windows" or not target.isdecimal():
            return False
        try:
            import psutil
            from pywinauto import Desktop

            window = Desktop(backend="win32").window(handle=int(target))
            if not window.exists() or not window.is_visible() or window.is_minimized():
                return False
            process = psutil.Process(window.process_id())
            if process.name().casefold() not in {
                "camfrog.exe",
                "camfrog video chat.exe",
            }:
                return False
            image = window.capture_as_image()
            destination = Path(destination)
            if destination.suffix.lower() != ".png":
                return False
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".tmp")
            try:
                image.save(temporary, format="PNG")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            return destination.is_file() and destination.stat().st_size > 0
        except (OSError, ValueError, RuntimeError, AttributeError, ImportError):
            return False
