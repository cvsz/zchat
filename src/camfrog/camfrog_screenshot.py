"""Screenshot capture with SHA-256 evidence for Camfrog windows.

Captures a screenshot of an attached Camfrog client window and records a
SHA-256 digest of the saved image. Evidence is read-only: screenshots are
captured from an already-running client and never modify client state.

Uses zchat's existing :mod:`camfrog_windows_runtime` for process discovery
and window classification. Capture only proceeds when exactly one visible
Camfrog window is found; ambiguous matches are refused.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from camfrog_windows_runtime import (
    WindowRole,
    attach_to_camfrog_client,
)


def utc_now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


def capture_camfrog_window(
    *,
    evidence_dir: str | Path = "evidence",
    pid: int | None = None,
) -> dict[str, Any]:
    """Capture a screenshot of a visible Camfrog window with SHA-256 evidence.

    Args:
        evidence_dir: Directory to store captured PNG files.
        pid: Optional PID of the Camfrog process to attach to. When omitted,
            exactly one verified Camfrog process must be running.

    Returns:
        A dictionary with capture metadata including ``captured``, ``sha256``,
        ``path``, ``size_bytes``, ``captured_at``, and an ``evidence`` list
        containing the SHA-256 digest. On failure, returns ``captured=False``
        with a ``reason`` or ``error``.

    Raises:
        RuntimeError: If running on a non-Windows platform.
    """
    import sys

    if sys.platform != "win32":
        return {
            "captured": False,
            "reason": "Screenshot capture is supported on Windows only.",
        }

    evidence_path = Path(evidence_dir)
    evidence_path.mkdir(parents=True, exist_ok=True)

    try:
        attachment = attach_to_camfrog_client(pid=pid)
    except Exception as exc:  # noqa: BLE001 - discovery failures are expected
        return {
            "captured": False,
            "reason": f"Could not attach to Camfrog client: {exc}",
        }

    try:
        windows = attachment.get_windows()
    except Exception as exc:  # noqa: BLE001 - revalidation failures are expected
        return {
            "captured": False,
            "reason": f"Could not enumerate Camfrog windows: {exc}",
        }

    # Filter to visible windows with a classified role (not unknown/own GUI).
    candidates = [
        w
        for w in windows
        if w.is_visible and w.role != WindowRole.UNKNOWN
    ]

    if not candidates:
        return {
            "captured": False,
            "reason": "No visible classified Camfrog window found to capture.",
        }

    if len(candidates) > 1:
        return {
            "captured": False,
            "reason": "Ambiguous Camfrog window match; specify an exact PID.",
            "candidates": [
                {"title": w.title, "hwnd": w.hwnd, "role": w.role.value}
                for w in candidates
            ],
        }

    win = candidates[0]

    try:
        # Use UIA capture if the element is available; fall back to a
        # placeholder note if capture is not supported in this environment.
        element = getattr(win, "element", None)
        if element is not None:
            try:
                image = element.capture_as_image()
            except AttributeError:
                image = None
        else:
            image = None

        if image is None:
            return {
                "captured": False,
                "reason": "Window element does not support screenshot capture.",
                "hwnd": win.hwnd,
                "title": win.title,
            }

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"camfrog-capture-{timestamp}.png"
        save_path = evidence_path / filename
        image.save(save_path, format="PNG")

        digest = hashlib.sha256(save_path.read_bytes()).hexdigest()
        size_bytes = save_path.stat().st_size
        captured_at = utc_now_iso()

        evidence_item = {
            "filename": filename,
            "sha256": digest,
            "size_bytes": size_bytes,
            "captured_at": captured_at,
        }

        return {
            "captured": True,
            "title": win.title,
            "hwnd": win.hwnd,
            "role": win.role.value,
            "path": str(save_path.resolve()),
            "sha256": digest,
            "size_bytes": size_bytes,
            "captured_at": captured_at,
            "evidence": [evidence_item],
        }
    except Exception as exc:  # noqa: BLE001 - capture failures are expected
        return {
            "captured": False,
            "error": str(exc)[:1000],
            "hwnd": win.hwnd,
            "title": win.title,
        }
