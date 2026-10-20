"""Camfrog Friend List observer (Windows-only, best-effort)."""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass
class Observation:
    visible: bool
    status: str | None
    display_name: str | None
    confidence: str
    method: str


class FriendListObserver:
    def __init__(self, target_account: str) -> None:
        self.target_account = target_account

    def observe(self) -> Observation:
        if platform.system() != "Windows":
            return Observation(False, None, None, "low", "unsupported_platform")
        try:
            import pywinauto  # noqa: F401
        except Exception:
            return Observation(False, None, None, "low", "pywinauto_unavailable")
        return Observation(False, None, None, "low", "not_implemented")
