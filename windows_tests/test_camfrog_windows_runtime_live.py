"""Live Windows UI integration tests against genuine Camfrog Video Chat 8.5 client.

Requires:
- Windows 11
- Genuine installed Camfrog Video Chat 8.5 client running
- Python 3.14 runtime environment (with psutil 7.2.2 and pywinauto 0.6.9)
- Camfrog LLC Authenticode signature
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import camfrog_windows_runtime as cwr
from camfrog_windows_runtime import (
    WindowRole,
    WindowsCamfrogProcessDiscovery,
    WindowsCamfrogWindowClassifier,
    attach_to_camfrog_client,
    revalidate_process_identity,
    revalidate_window_identity,
)


def _require_live_client():
    """Attach to the genuine client, or SKIP when it is absent.

    CI runners and machines without Camfrog must skip (BLOCKED), never fail:
    a missing provider is an environment precondition, not a code regression.
    """
    try:
        return attach_to_camfrog_client()
    except Exception as exc:
        pytest.skip(f"No verified live Camfrog client: {exc}")


def test_live_camfrog_process_discovery_and_authenticode():
    if sys.platform != "win32":
        pytest.fail("This integration test must run on Windows.")

    discovery = WindowsCamfrogProcessDiscovery()
    processes = discovery.discover_processes()

    if not processes:
        pytest.skip("No verified Camfrog Video Chat process running.")

    proc = processes[0]
    assert proc.pid > 0
    assert proc.signature_valid is True, "Authenticode signature must be valid."
    assert "camfrog llc" in proc.publisher.lower(), f"Publisher must be Camfrog LLC, got {proc.publisher!r}"
    assert proc.version.startswith("8.5"), f"Expected Camfrog 8.5.x, got version {proc.version!r}"
    assert "Camfrog Video Chat.exe" in proc.exe_path
    assert proc.is_agent_launched is False, "Discovered live process was not launched by agent."


def test_live_camfrog_window_classification():
    if sys.platform != "win32":
        pytest.fail("This integration test must run on Windows.")

    att = _require_live_client()
    windows = att.get_windows()

    assert len(windows) >= 1, "Expected at least one top-level window for running Camfrog."

    roles = {w.role for w in windows}
    # In the verified environment, all 3 windows (main directory, room, IM) are active
    assert WindowRole.MAIN_DIRECTORY in roles, f"Main directory window must be detected. Found roles: {roles}"

    for w in windows:
        assert w.pid == att.identity.pid, "Window must belong to verified Camfrog process."
        assert revalidate_window_identity(w, att.identity) is True, "Window identity must revalidate successfully."


def test_live_camfrog_ownership_restrictions():
    if sys.platform != "win32":
        pytest.fail("This integration test must run on Windows.")

    att = _require_live_client()
    assert att.read_only is True

    # Attempting to terminate an unmanaged process MUST raise PermissionError
    with pytest.raises(PermissionError, match="Ownership restriction violated"):
        att.terminate(force=True)

    # Revalidate that the process is still alive and unharmed
    assert revalidate_process_identity(att.identity) is True, "Camfrog process must remain alive."


def test_live_camfrog_runtime_state_and_evidence_boundaries():
    if sys.platform != "win32":
        pytest.fail("This integration test must run on Windows.")

    att = _require_live_client()
    state = att.get_runtime_state()

    assert state.process_running is True, "Process running must be True."
    assert state.ui_visible is True, "UI visible must be True."
    assert state.account_authenticated is True, "Account authenticated must be True for logged-in client."

    # Membership must follow current UI evidence, not a historical expectation
    # that this client is always kicked or disconnected.
    if state.room_member:
        assert any("Room window active" in note for note in state.evidence_notes), (
            "Room membership requires a visible room window without a disconnect indicator."
        )
    else:
        assert any(
            "Room membership not established: No room window present." in note
            or "user was kicked or disconnected" in note
            for note in state.evidence_notes
        ), "A negative room-membership result must identify the UI evidence that supports it."


def test_live_camfrog_diagnostics_sanitized_and_privacy():
    if sys.platform != "win32":
        pytest.fail("This integration test must run on Windows.")

    att = _require_live_client()
    diag = att.get_diagnostics()

    assert "process" in diag
    assert "windows" in diag
    assert "state" in diag
    assert diag["read_only"] is True

    # Verify no private chat message text or credentials leaked in diagnostic data
    diag_repr = repr(diag)
    for forbidden in ("password", "private_key", "secret", "token"):
        assert forbidden not in diag_repr.lower()
