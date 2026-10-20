"""Screenshot capture interface (platform-specific implementation)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ScreenshotCapture(Protocol):
    def capture(self, target: str, destination: Path) -> bool: ...


class NullScreenshotCapture:
    def capture(self, target: str, destination: Path) -> bool:
        return False
