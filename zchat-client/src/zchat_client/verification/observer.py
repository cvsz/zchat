"""Read-only Windows Camfrog Friend List observation.

UI Automation accessibility varies by Camfrog version. A missing or ambiguous
control is INCONCLUSIVE, never proof of a successful provider-side mutation.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Callable

from .comparison import normalize_text


@dataclass(frozen=True)
class Observation:
    visible: bool
    status: str | None
    display_name: str | None
    confidence: str
    method: str


class FriendListObserver:
    def __init__(
        self,
        target_account: str,
        reader: Callable[[str], Observation] | None = None,
    ) -> None:
        self.target_account = normalize_text(target_account)
        self._reader = reader or self._read_windows_uia

    def observe(self) -> Observation:
        if not self.target_account:
            return Observation(False, None, None, "low", "missing_target_account")
        if platform.system() != "Windows" and self._reader == self._read_windows_uia:
            return Observation(False, None, None, "low", "unsupported_platform")
        try:
            result = self._reader(self.target_account)
        except (OSError, RuntimeError, ImportError, AttributeError):
            return Observation(False, None, None, "low", "observer_unavailable")
        if not isinstance(result, Observation):
            return Observation(False, None, None, "low", "invalid_observation")
        if not result.visible or result.confidence != "high":
            return Observation(False, None, None, "low", result.method)
        # Seeing a matching friend alone cannot establish an external status.
        if not result.status:
            return Observation(True, None, result.display_name, "low", result.method)
        return result

    @staticmethod
    def _read_windows_uia(target: str) -> Observation:
        import psutil
        from pywinauto import Desktop

        matches: list[Observation] = []
        for window in Desktop(backend="uia").windows():
            try:
                pid = window.process_id()
                process = psutil.Process(pid)
                if process.name().casefold() not in {
                    "camfrog.exe",
                    "camfrog video chat.exe",
                }:
                    continue
                # An exact accessible friend identity is required. Window titles,
                # substrings and free-form text must not qualify as identity.
                for item in window.descendants(control_type="ListItem"):
                    if normalize_text(item.window_text()).casefold() != target.casefold():
                        continue
                    # Status/name controls differ between Camfrog builds and cannot
                    # be inferred from row position or visible labels alone.
                    matches.append(Observation(True, None, None, "low", "uia_identity_only"))
            except (OSError, RuntimeError, AttributeError, ValueError, psutil.Error):
                continue
        if len(matches) == 1:
            return matches[0]
        return Observation(
            False,
            None,
            None,
            "low",
            "ambiguous_identity" if matches else "friend_not_observed",
        )
