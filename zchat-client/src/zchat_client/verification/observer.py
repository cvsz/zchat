"""Read-only Windows Camfrog Friend List observation.

UI Automation accessibility varies by Camfrog version. A missing or ambiguous
control is INCONCLUSIVE, never proof of a successful provider-side mutation.
A friend row that is visible but exposes no readable Custom Status control is
reported as low-confidence identity-only, never as a verified status.
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .comparison import normalize_text

_CAMFROG_PROCESS_NAMES = frozenset({"camfrog.exe", "camfrog video chat.exe"})


# UIA automation-id/name hints that indicate identity, presence, flair,
# avatar, or tooltip controls rather than a user-entered Custom Status.
# Controls matching these hints are rejected during status-field discovery.
_NON_STATUS_NAME_HINTS = frozenset(
    {
        "name",
        "nick",
        "nickname",
        "display",
        "presence",
        "flair",
        "avatar",
        "statusicon",
        "tooltip",
        "mood",
    }
)


@dataclass(frozen=True)
class Observation:
    visible: bool
    status: str | None
    display_name: str | None
    confidence: str
    method: str
    account_identity: str | None = None
    process_identity: dict[str, str] | None = None
    screenshot: str | None = None
    error_reason: str | None = None


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
            return Observation(
                False,
                None,
                None,
                "low",
                "missing_target_account",
                error_reason="missing_target_account",
            )
        if platform.system() != "Windows" and self._reader is self._read_windows_uia:
            return Observation(
                False,
                None,
                None,
                "low",
                "unsupported_platform",
                error_reason="unsupported_platform",
            )
        try:
            result = self._reader(self.target_account)
        except (
            OSError,
            RuntimeError,
            ImportError,
            AttributeError,
            TimeoutError,
            LookupError,
        ):
            return Observation(
                False,
                None,
                None,
                "low",
                "observer_unavailable",
                error_reason="observer_unavailable",
            )
        if not isinstance(result, Observation):
            return Observation(
                False,
                None,
                None,
                "low",
                "invalid_observation",
                error_reason="invalid_observation",
            )
        if not result.visible or result.confidence != "high":
            return Observation(
                False,
                None,
                None,
                "low",
                result.method,
                account_identity=result.account_identity,
                process_identity=result.process_identity,
                screenshot=result.screenshot,
                error_reason=result.error_reason or result.method,
            )
        # Seeing a matching friend alone cannot establish an external status.
        if not result.status:
            return Observation(
                True,
                None,
                result.display_name,
                "low",
                result.method,
                account_identity=result.account_identity,
                process_identity=result.process_identity,
                screenshot=result.screenshot,
                error_reason="status_field_not_accessible",
            )
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
                if not _is_verified_camfrog_process(process):
                    continue
                # Window ownership: window must belong to the verified process.
                if window.process_id() != process.pid:
                    continue
                for item in window.descendants(control_type="ListItem"):
                    row_text = normalize_text(item.window_text())
                    if row_text.casefold() != target.casefold():
                        continue
                    # Discover any Custom Status control within this row.
                    status_value = _discover_row_status(item)
                    matches.append(
                        Observation(
                            True,
                            status_value,
                            row_text or None,
                            "high" if status_value is not None else "low",
                            "uia_status_field"
                            if status_value is not None
                            else "uia_identity_only",
                            account_identity=target,
                            process_identity=_process_identity(process),
                            error_reason=None
                            if status_value is not None
                            else "status_field_not_accessible",
                        )
                    )
            except (OSError, RuntimeError, AttributeError, ValueError, psutil.Error):
                continue
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return Observation(
                False,
                None,
                None,
                "low",
                "ambiguous_identity",
                error_reason="multiple_matching_rows",
            )
        return Observation(
            False,
            None,
            None,
            "low",
            "friend_not_observed",
            error_reason="friend_not_observed",
        )


# ---------- Mockable helpers (testable without pywinauto / Windows) ----------


def _is_verified_camfrog_process(process: Any) -> bool:
    """Exact process identity: Camfrog process name plus plausible exe path.

    A missing exe path (access-restricted) is acceptable; a non-Camfrog path is
    rejected even when the process name happens to match.
    """
    try:
        name = (process.name() or "").casefold()
    except Exception:  # noqa: BLE001 - fail-closed: an untrusted process object must not abort discovery.
        return False
    if name not in _CAMFROG_PROCESS_NAMES:
        return False
    try:
        exe = (process.exe() or "").casefold()
    except Exception:  # noqa: BLE001 - fail-closed: an untrusted process object must not abort discovery.
        exe = ""
    return "camfrog" in exe or not exe


def _process_identity(process: Any) -> dict[str, str]:
    identity: dict[str, str] = {"pid": str(getattr(process, "pid", 0))}
    try:
        identity["name"] = process.name() or ""
    except Exception:  # noqa: BLE001, S110 - fail-closed: an untrusted process object must not abort identity capture.
        pass
    try:
        identity["exe"] = process.exe() or ""
    except Exception:  # noqa: BLE001, S110 - fail-closed: an untrusted process object must not abort identity capture.
        pass
    return identity


def _read_control_value(control: Any) -> str | None:
    """Read a UIA control's value via supported pywinauto accessors, fail-closed.

    Supported paths on pywinauto 0.6.9:
      1. wrapper.get_value() (e.g. EditWrapper.iface_value.CurrentValue)
      2. element.GetCurrentPattern(Value pattern id) -> .Current.Value

    Only values exposed through a UIA value pattern are treated as real status
    text. `window_text()`/`name` labels are intentionally not returned as
    status values, because they may be control names rather than user-entered
    text. Every path fails closed: COM/disconnected/stale errors yield None.
    """
    # Path 1: wrapper-level accessor (pywinauto EditWrapper.get_value, etc.)
    try:
        value = control.get_value()
    except Exception:  # noqa: BLE001 - fail-closed: wrapper accessor unavailable.
        value = None
    if value:
        return str(value).strip()

    # Path 2: raw AutomationElement pattern access (pywinauto 0.6.9 supported).
    try:
        element = control.element
    except Exception:  # noqa: BLE001 - control exposes no raw element.
        element = None
    if element is not None:
        try:
            from pywinauto import uia_defines  # Windows-only import; guarded.

            ptrn_id, _ = uia_defines.pattern_ids["Value"]
            pattern = element.GetCurrentPattern(ptrn_id)
            current = getattr(pattern, "Current", None)
            value = getattr(current, "Value", None) if current is not None else None
        except Exception:  # noqa: BLE001 - fail-closed: Value pattern unsupported or stale.
            value = None
        if value:
            return str(value).strip()
    return None


def _is_custom_status_control(control: Any) -> bool:
    """Positive identification of a Custom Status UIA control.

    Conservative by design: only Text/Edit controls that are visible, enabled,
    and carry a non-empty name whose automation-id/name hints do not indicate
    identity, presence, flair, avatar, or tooltip are accepted. Never infers
    status identity from being the only readable descendant.
    """
    try:
        ctype = str(control.control_type or "")
    except Exception:  # noqa: BLE001 - fail-closed: untrusted control object.
        return False
    if ctype not in ("Text", "Edit"):
        return False
    try:
        if not control.enabled or not control.visible:
            return False
    except Exception:  # noqa: BLE001 - fail-closed: untrusted control object.
        return False
    try:
        name = (control.name or "").strip()
    except Exception:  # noqa: BLE001 - fail-closed: untrusted control object.
        return False
    if not name:
        return False
    try:
        aid = control.automation_id or ""
    except Exception:  # noqa: BLE001 - fail-closed: untrusted control object.
        aid = ""
    combined = f"{aid} {name}".casefold()
    return not any(hint in combined for hint in _NON_STATUS_NAME_HINTS)


def _discover_row_status(row_item: Any) -> str | None:
    """Inspect UIA children of a friend-list row for the Custom Status field.

    Positively identifies candidate Custom Status controls via verified UIA
    properties (control type, enabled/visible state, non-empty name, and
    rejection of identity/presence/flair/avatar/tooltip hints). Rejects
    multiple candidates. Returns the status text if exactly one candidate is
    readable, otherwise None (fail closed / INCONCLUSIVE).
    """
    try:
        descendants = row_item.descendants()
    except Exception:  # noqa: BLE001 - fail-closed: treat an undescendable row as having no status field.
        return None
    candidates: list[str] = []
    for control in descendants:
        try:
            if not _is_custom_status_control(control):
                continue
            value = _read_control_value(control)
        except Exception:  # noqa: BLE001, S112 - fail-closed: skip a control whose value cannot be read.
            continue
        if value:
            candidates.append(value)
    if len(candidates) == 1:
        return candidates[0]
    return None
