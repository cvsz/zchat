"""Behavioural: best_effort_background refuses when Camfrog is not foreground."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "features"))

import status_changer_gui as scg  # noqa: E402
import status_random_gui as srg  # noqa: E402
import status_marquee_gui as smg  # noqa: E402


def test_status_changer_best_effort_refuses_not_foreground():
    result = scg._commit_impl(None, None, "TEST", dry=False, execution_mode="best_effort_background")
    assert result is False


def test_status_random_best_effort_refuses_not_foreground():
    result = srg._commit_impl(None, None, "TEST", dry=False, execution_mode="best_effort_background")
    assert result is False


def test_status_marquee_best_effort_refuses_not_foreground():
    result = smg._commit_impl(None, None, "TEST", dry=False, execution_mode="best_effort_background")
    assert result is False