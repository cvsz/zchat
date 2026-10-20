"""Correlate screenshots with verification events."""

from __future__ import annotations

from pathlib import Path


def correlate_screenshot(event: dict, screenshot_path: Path) -> dict:
    return {
        **event,
        "screenshot_id": screenshot_path.stem,
        "screenshot_path": str(screenshot_path),
    }
