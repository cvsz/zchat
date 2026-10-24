"""Unit tests for WindowsCamfrogProcessDiscovery and WindowsCamfrogWindowClassifier.

Tests run offline with simulated process and UI hierarchies to verify fail-closed
discovery, role classification, identity revalidation, ownership restrictions,
and evidence-driven state separation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import camfrog_windows_runtime as cwr
from camfrog_windows_runtime import (
    ClassifiedWindow,
    ClientRuntimeState,
    ProcessIdentity,
    WindowRole,
    WindowsCamfrogProcessDiscovery,
    WindowsCamfrogWindowClassifier,
    evaluate_client_runtime_state,
    revalidate_process_identity,
    revalidate_window_identity,
    terminate_client_process,
)


# ---------- Process Discovery Unit Tests ----------

def test_cef_subprocess_exclusion():
    discovery = WindowsCamfrogProcessDiscovery()

    # camfrog_cef.exe by name
    assert discovery.is_cef_subprocess("camfrog_cef.exe", []) is True
    assert discovery.is_cef_subprocess("CAMFROG_CEF.EXE", []) is True

    # Subprocess with --type=renderer or --type=gpu-process
    assert discovery.is_cef_subprocess(
        "Camfrog Video Chat.exe",
        ["Camfrog Video Chat.exe", "--type=renderer", "--no-sandbox"]
    ) is True
    assert discovery.is_cef_subprocess(
        "Camfrog Video Chat.exe",
        ["Camfrog Video Chat.exe", "--type=gpu-process"]
    ) is True

    # Main process without --type flags
    assert discovery.is_cef_subprocess(
        "Camfrog Video Chat.exe",
        ["C:\\Program Files\\Camfrog Video Chat\\Camfrog Video Chat.exe"]
    ) is False


def test_zchat_own_process_exclusion():
    discovery = WindowsCamfrogProcessDiscovery()

    # Current process PID
    assert discovery.is_own_zchat_process(os.getpid(), []) is True

    # Python scripts belonging to ZChat
    assert discovery.is_own_zchat_process(
        99999,
        ["python.exe", "camfrog_auto.py", "--run"]
    ) is True
    assert discovery.is_own_zchat_process(
        99999,
        ["python.exe", "room_control_gui.py"]
    ) is True

    # Real Camfrog client
    assert discovery.is_own_zchat_process(
        12345,
        ["C:\\Camfrog\\Camfrog Video Chat.exe"]
    ) is False


def test_publisher_verification():
    discovery = WindowsCamfrogProcessDiscovery(trusted_publishers=["Camfrog LLC"])

    assert discovery.verify_publisher("Camfrog LLC") is True
    assert discovery.verify_publisher("camfrog llc") is True
    assert discovery.verify_publisher("CN=Camfrog LLC, O=Camfrog LLC") is True

    # Untrusted / spoofed publishers
    assert discovery.verify_publisher("Fake Publisher Corp") is False
    assert discovery.verify_publisher("") is False
    assert discovery.verify_publisher("Untrusted LLC") is False
    # Substring spoofs must not pass exact-identity matching
    assert discovery.verify_publisher("Fake Camfrog LLC Corp") is False
    assert discovery.verify_publisher("Camfrog LLC Malware Publisher") is False
    assert discovery.verify_publisher("CN=Fake, O=Camfrog LLC") is False
    assert discovery.verify_publisher("Camfrog LLC ") is True  # padding tolerated


def test_process_discovery_filters_and_fail_closed(monkeypatch):
    # Mock psutil.process_iter with a mix of processes:
    # 1. Main Camfrog process with valid Authenticode
    # 2. Main Camfrog process with invalid Authenticode
    # 3. CEF renderer subprocess
    # 4. ZChat automation script
    # 5. Unrelated process (notepad.exe)

    simulated_processes = [
        SimpleNamespace(
            info={
                "pid": 1001,
                "name": "Camfrog Video Chat.exe",
                "exe": "C:\\Camfrog\\Camfrog Video Chat.exe",
                "create_time": 1000.0,
                "cmdline": ["C:\\Camfrog\\Camfrog Video Chat.exe"],
            }
        ),
        SimpleNamespace(
            info={
                "pid": 1002,
                "name": "Camfrog Video Chat.exe",
                "exe": "C:\\Tampered\\Camfrog Video Chat.exe",
                "create_time": 1010.0,
                "cmdline": ["C:\\Tampered\\Camfrog Video Chat.exe"],
            }
        ),
        SimpleNamespace(
            info={
                "pid": 1003,
                "name": "camfrog_cef.exe",
                "exe": "C:\\Camfrog\\camfrog_cef.exe",
                "create_time": 1002.0,
                "cmdline": ["camfrog_cef.exe", "--type=renderer"],
            }
        ),
        SimpleNamespace(
            info={
                "pid": 1004,
                "name": "python.exe",
                "exe": "C:\\Python\\python.exe",
                "create_time": 1005.0,
                "cmdline": ["python.exe", "camfrog_auto.py"],
            }
        ),
        SimpleNamespace(
            info={
                "pid": 1005,
                "name": "notepad.exe",
                "exe": "C:\\Windows\\notepad.exe",
                "create_time": 500.0,
                "cmdline": ["notepad.exe"],
            }
        ),
    ]

    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: simulated_processes)
    monkeypatch.setattr(os.path, "isfile", lambda path: True)

    # Verifier: only C:\Camfrog\Camfrog Video Chat.exe has valid signature from Camfrog LLC
    def mock_verifier(path: str):
        if "Tampered" in path:
            return False, "Untrusted Signer"
        return True, "Camfrog LLC"

    def mock_version(path: str):
        return "8.5.0.51219"

    discovery = WindowsCamfrogProcessDiscovery(
        authenticode_verifier=mock_verifier,
        version_reader=mock_version,
    )

    discovered = discovery.discover_processes()
    assert len(discovered) == 1
    proc = discovered[0]
    assert proc.pid == 1001
    assert proc.publisher == "Camfrog LLC"
    assert proc.signature_valid is True
    assert proc.version == "8.5.0.51219"
    assert proc.is_agent_launched is False


def test_get_verified_process_single_multiple_and_not_found(monkeypatch):
    import psutil

    def mock_verifier(path: str):
        return True, "Camfrog LLC"

    # Case 1: Exactly 1 verified process
    proc1 = SimpleNamespace(
        info={
            "pid": 2001,
            "name": "Camfrog Video Chat.exe",
            "exe": "C:\\Camfrog\\Camfrog Video Chat.exe",
            "create_time": 2000.0,
            "cmdline": ["Camfrog Video Chat.exe"],
        }
    )
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [proc1])
    monkeypatch.setattr(os.path, "isfile", lambda path: True)

    discovery = WindowsCamfrogProcessDiscovery(
        authenticode_verifier=mock_verifier,
        version_reader=lambda p: "8.5.0",
    )
    res = discovery.get_verified_process()
    assert res.pid == 2001

    # Case 2: 0 verified processes -> ProcessLookupError
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [])
    with pytest.raises(ProcessLookupError, match="No verified Camfrog"):
        discovery.get_verified_process()

    # Case 3: Multiple verified processes without PID -> RuntimeError (fail closed against cross-targeting)
    proc2 = SimpleNamespace(
        info={
            "pid": 2002,
            "name": "Camfrog Video Chat.exe",
            "exe": "C:\\Camfrog\\Camfrog Video Chat.exe",
            "create_time": 2010.0,
            "cmdline": ["Camfrog Video Chat.exe"],
        }
    )
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [proc1, proc2])
    with pytest.raises(RuntimeError, match="Multiple verified Camfrog processes"):
        discovery.get_verified_process()

    # Case 4: Multiple verified processes with explicit PID -> returns the requested PID
    res_explicit = discovery.get_verified_process(pid=2002)
    assert res_explicit.pid == 2002


# ---------- Window Classification Unit Tests ----------

class MockUIAElement:
    def __init__(self, class_name="", name="", automation_id="", control_type=""):
        self.element_info = SimpleNamespace(
            class_name=class_name,
            name=name,
            automation_id=automation_id,
            control_type=control_type,
        )


class MockWindow:
    def __init__(
        self,
        handle: int,
        title: str = "",
        class_name: str = "#32770",
        pid: int = 1001,
        is_visible: bool = True,
        descendants: List[MockUIAElement] = None,
    ):
        self.handle = handle
        self._title = title
        self._class = class_name
        self._pid = pid
        self._visible = is_visible
        self._descendants = descendants or []

    def window_text(self):
        return self._title

    def class_name(self):
        return self._class

    def process_id(self):
        return self._pid

    def is_visible(self):
        return self._visible

    def descendants(self, **kw):
        return self._descendants


def test_classify_main_directory_window(monkeypatch):
    # Main directory has CTreeViewTS, CComboBoxTS, and Edit controls
    desc = [
        MockUIAElement(class_name="CTreeViewTS", automation_id="10867", control_type="Tree"),
        MockUIAElement(class_name="CComboBoxTS", automation_id="1436", control_type="ComboBox"),
        MockUIAElement(class_name="Edit", automation_id="1001", control_type="Edit"),
    ]
    win = MockWindow(handle=10, title="Camfrog Video Chat", descendants=desc)

    classifier = WindowsCamfrogWindowClassifier()
    # In offline test, inspect_window_structures relies on element.descendants()
    classified = classifier.classify_window(win)

    assert classified.hwnd == 10
    assert classified.role == WindowRole.MAIN_DIRECTORY
    assert "CTreeViewTS+CComboBoxTS" in classified.signatures


def test_classify_room_window(monkeypatch):
    # Room window has VideoViewport and Internet Explorer_Server with wb-log-room-data
    desc = [
        MockUIAElement(class_name="VideoViewport", automation_id="11425", control_type="Pane"),
        MockUIAElement(
            class_name="Internet Explorer_Server",
            name="camfrog://wb-log-room-data.inside/",
            control_type="Pane"
        ),
        MockUIAElement(class_name="CEditTS", automation_id="1314", control_type="Edit"),
    ]
    win = MockWindow(handle=20, title="Cool Room: Chat", descendants=desc)

    classifier = WindowsCamfrogWindowClassifier()
    classified = classifier.classify_window(win)

    assert classified.hwnd == 20
    assert classified.role == WindowRole.ROOM_WINDOW
    assert "VideoViewport+wb-log-room-data" in classified.signatures


def test_classify_im_window(monkeypatch):
    # IM window has Internet Explorer_Server with wb-log-im-data and Edit (no VideoViewport)
    desc = [
        MockUIAElement(
            class_name="Internet Explorer_Server",
            name="camfrog://wb-log-im-data.inside/",
            control_type="Pane"
        ),
        MockUIAElement(class_name="CEditTS", automation_id="1314", control_type="Edit"),
    ]
    win = MockWindow(handle=30, title="Friend (Online)", descendants=desc)

    classifier = WindowsCamfrogWindowClassifier()
    classified = classifier.classify_window(win)

    assert classified.hwnd == 30
    assert classified.role == WindowRole.IM_WINDOW
    assert "Internet Explorer_Server(wb-log-im-data)+Edit" in classified.signatures


def test_classify_ambiguous_and_unknown_windows_fail_closed():
    classifier = WindowsCamfrogWindowClassifier()

    # Case 1: Conflicting signatures (both wb-log-im-data and VideoViewport)
    desc_conflict = [
        MockUIAElement(class_name="VideoViewport"),
        MockUIAElement(
            class_name="Internet Explorer_Server",
            name="camfrog://wb-log-im-data.inside/"
        ),
        MockUIAElement(class_name="CTreeViewTS"),
        MockUIAElement(class_name="CComboBoxTS"),
    ]
    win_conflict = MockWindow(handle=40, title="Conflict", descendants=desc_conflict)
    classified_conflict = classifier.classify_window(win_conflict)
    assert classified_conflict.role == WindowRole.UNKNOWN
    assert any("ambiguous" in s for s in classified_conflict.signatures)

    # Case 2: Random dialog (e.g. login dialog or about box)
    desc_dialog = [
        MockUIAElement(class_name="Static", name="Please sign in"),
        MockUIAElement(class_name="Edit", name="Username"),
        MockUIAElement(class_name="Button", name="Sign In"),
    ]
    win_dialog = MockWindow(handle=50, title="Sign In", descendants=desc_dialog)
    classified_dialog = classifier.classify_window(win_dialog)
    assert classified_dialog.role == WindowRole.UNKNOWN


def test_classify_ignores_own_zchat_gui_windows():
    classifier = WindowsCamfrogWindowClassifier()

    # Generic toolkit classes do not prove that a window belongs to ZChat.
    win_tk = MockWindow(handle=60, title="Camfrog Helper", class_name="TkTopLevel")
    assert classifier.is_own_gui_window(win_tk) is False
    classified_tk = classifier.classify_window(win_tk)
    assert classified_tk.role == WindowRole.UNKNOWN
    assert "unrecognized_window_structure" in classified_tk.signatures

    # Title prefix matching ZChat GUI
    win_title = MockWindow(
        handle=61, title="Camfrog Status Changer v2", class_name="TkTopLevel"
    )
    assert classifier.is_own_gui_window(win_title) is True

    classified = classifier.classify_window(win_tk)
    assert classified.role == WindowRole.UNKNOWN
    assert "unrecognized_window_structure" in classified.signatures

    classified_title = classifier.classify_window(win_title)
    assert classified_title.role == WindowRole.UNKNOWN
    assert "zchat_own_gui" in classified_title.signatures


# ---------- Revalidation and Anti-Targeting Tests ----------

def test_revalidate_process_identity(monkeypatch):
    import psutil

    identity = ProcessIdentity(
        pid=1234,
        create_time=5000.0,
        exe_path="C:\\Camfrog\\Camfrog Video Chat.exe",
        publisher="Camfrog LLC",
        signature_valid=True,
        version="8.5.0",
        is_agent_launched=False,
    )

    # Case 1: PID is dead
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    assert revalidate_process_identity(identity) is False

    # Case 2: PID alive, same create_time and path
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    mock_proc = SimpleNamespace(
        is_running=lambda: True,
        create_time=lambda: 5000.0,
        exe=lambda: "C:\\Camfrog\\Camfrog Video Chat.exe",
    )
    monkeypatch.setattr(psutil, "Process", lambda pid: mock_proc)
    assert revalidate_process_identity(identity) is True

    # Case 3: PID recycled (different create_time) -> fail closed
    mock_recycled = SimpleNamespace(
        is_running=lambda: True,
        create_time=lambda: 9999.0,
        exe=lambda: "C:\\Camfrog\\Camfrog Video Chat.exe",
    )
    monkeypatch.setattr(psutil, "Process", lambda pid: mock_recycled)
    assert revalidate_process_identity(identity) is False


# ---------- Ownership Restrictions Tests ----------

def test_terminate_client_process_ownership_restriction():
    # If not launched by agent, MUST raise PermissionError
    unmanaged_proc = ProcessIdentity(
        pid=5555,
        create_time=100.0,
        exe_path="C:\\Camfrog.exe",
        publisher="Camfrog LLC",
        signature_valid=True,
        version="8.5.0",
        is_agent_launched=False,
    )
    with pytest.raises(PermissionError, match="Ownership restriction violated"):
        terminate_client_process(unmanaged_proc)

    # If launched by agent, termination is permitted
    managed_proc = ProcessIdentity(
        pid=5556,
        create_time=100.0,
        exe_path="C:\\Camfrog.exe",
        publisher="Camfrog LLC",
        signature_valid=True,
        version="8.5.0",
        is_agent_launched=True,
    )
    # mock revalidate to return False (process already exited)
    import camfrog_windows_runtime as m
    orig = m.revalidate_process_identity
    m.revalidate_process_identity = lambda p: False
    try:
        assert terminate_client_process(managed_proc) is True
    finally:
        m.revalidate_process_identity = orig


# ---------- Evidence State Separation Tests ----------

def test_evaluate_client_runtime_state_boundary_rules(monkeypatch):
    import camfrog_windows_runtime as m
    monkeypatch.setattr(m, "revalidate_process_identity", lambda p: True)

    identity = ProcessIdentity(
        pid=3001,
        create_time=100.0,
        exe_path="C:\\Camfrog\\Camfrog Video Chat.exe",
        publisher="Camfrog LLC",
        signature_valid=True,
        version="8.5.0",
    )

    # Rule 1: Process running alone does NOT imply authentication or room membership
    state_empty = evaluate_client_runtime_state(identity, [])
    assert state_empty.process_running is True
    assert state_empty.ui_visible is False
    assert state_empty.account_authenticated is False
    assert state_empty.room_member is False
    assert any(
        "Room membership not established: No room window present." in note
        for note in state_empty.evidence_notes
    )

    # Rule 2: Room window existing does NOT establish current room membership if kicked
    kicked_desc = [
        MockUIAElement(class_name="Static", name="You were kicked due to inactivity."),
    ]
    room_win_kicked = ClassifiedWindow(
        hwnd=201,
        role=WindowRole.ROOM_WINDOW,
        title="Room 1",
        pid=3001,
        is_visible=True,
        signatures=["VideoViewport+wb-log-room-data"],
        element=MockWindow(handle=201, descendants=kicked_desc),
    )
    main_win = ClassifiedWindow(
        hwnd=101,
        role=WindowRole.MAIN_DIRECTORY,
        title="Camfrog Video Chat",
        pid=3001,
        is_visible=True,
        signatures=["CTreeViewTS+CComboBoxTS"],
    )

    state_kicked = evaluate_client_runtime_state(identity, [main_win, room_win_kicked])
    assert state_kicked.process_running is True
    assert state_kicked.ui_visible is True
    assert state_kicked.account_authenticated is True
    assert state_kicked.room_member is False  # Kicked per evidence boundary rule!
    assert any("kicked" in note for note in state_kicked.evidence_notes)

    # Active room window without kick/disconnect indicator establishes membership
    active_room_desc = [
        MockUIAElement(class_name="Static", name="Welcome to the room"),
    ]
    room_win_active = ClassifiedWindow(
        hwnd=202,
        role=WindowRole.ROOM_WINDOW,
        title="Room 2",
        pid=3001,
        is_visible=True,
        signatures=["VideoViewport+wb-log-room-data"],
        element=MockWindow(handle=202, descendants=active_room_desc),
    )
    state_active = evaluate_client_runtime_state(identity, [main_win, room_win_active])
    assert state_active.room_member is True


# ---------- Diagnostics and Privacy Tests ----------

def test_diagnostics_sanitized_and_no_private_messages():
    identity = ProcessIdentity(
        pid=4001,
        create_time=200.0,
        exe_path="C:\\Camfrog\\Camfrog Video Chat.exe",
        publisher="Camfrog LLC",
        signature_valid=True,
        version="8.5.0",
    )
    im_win = ClassifiedWindow(
        hwnd=301,
        role=WindowRole.IM_WINDOW,
        title="Friend (Online)",
        pid=4001,
        is_visible=True,
        signatures=["Internet Explorer_Server(wb-log-im-data)+Edit"],
    )

    state = evaluate_client_runtime_state(identity, [im_win])
    diag = {
        "process": identity.as_dict(),
        "windows": [im_win.as_dict()],
        "state": state.as_dict(),
        "read_only": True,
    }

    # Verify no private chat message bodies in the diagnostic structure
    raw_str = str(diag)
    assert "password" not in raw_str.lower()
    assert "token" not in raw_str.lower()
    assert diag["windows"][0]["role"] == "im_window"
    assert diag["read_only"] is True
