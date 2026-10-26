"""Tests for camfrog.camfrog_screenshot — screenshot capture with SHA-256 evidence."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "camfrog"))

import pytest
from camfrog_screenshot import capture_camfrog_window, utc_now_iso


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only screenshot capture path")
def test_capture_non_windows_returns_reason():
    result = capture_camfrog_window()
    assert result["captured"] is False
    assert "Windows only" in result["reason"]


def test_utc_now_iso_returns_iso_format():
    value = utc_now_iso()
    assert "T" in value
    assert value.endswith("Z") or "+" in value
