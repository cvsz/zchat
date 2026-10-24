"""Windows UI runtime integration and evidence-driven discovery for Camfrog Video Chat 8.5.

This module provides verified, fail-closed process discovery, Authenticode signature
verification, structural window classification, state separation, read-only client
attachment, and ownership-safe lifecycle handling for Camfrog Video Chat.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

# System taskkill path helper (safe Windows system directory execution)
def system_taskkill_path() -> str:
    """Return taskkill.exe from Windows system directory, never from PATH/cwd."""
    if sys.platform != "win32":
        return "taskkill.exe"
    try:
        from ctypes import create_unicode_buffer, windll
        buffer = create_unicode_buffer(260)
        chars = windll.kernel32.GetSystemDirectoryW(buffer, 260)
        if chars > 0:
            return os.path.join(buffer.value, "taskkill.exe")
    except Exception:
        pass
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return os.path.join(windir, "System32", "taskkill.exe")


# ---------- Enums and Data Models ----------

class WindowRole(str, Enum):
    """Semantic UI roles for Camfrog client top-level windows."""
    MAIN_DIRECTORY = "main_directory"
    ROOM_WINDOW = "room_window"
    IM_WINDOW = "im_window"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProcessIdentity:
    """Immutable identity record for a verified Camfrog process."""
    pid: int
    create_time: float
    exe_path: str
    publisher: str
    signature_valid: bool
    version: str
    is_agent_launched: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "create_time": self.create_time,
            "exe_path": self.exe_path,
            "publisher": self.publisher,
            "signature_valid": self.signature_valid,
            "version": self.version,
            "is_agent_launched": self.is_agent_launched,
        }


@dataclass
class ClassifiedWindow:
    """Record of a top-level window classified by structural UI signatures."""
    hwnd: int
    role: WindowRole
    title: str
    pid: int
    is_visible: bool
    signatures: List[str] = field(default_factory=list)
    element: Any = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "role": self.role.value,
            "title": self.title,
            "pid": self.pid,
            "is_visible": self.is_visible,
            "signatures": list(self.signatures),
        }


@dataclass
class ClientRuntimeState:
    """Evidence-driven runtime states separated per evidence boundary rules.

    Rules:
    - A process being alive does not establish authentication.
    - A room window existing does not establish current room membership.
    - Historical messages do not prove current activity.
    - A UI control labelled Online does not establish current presence.
    - An accepted URI handoff does not establish completed behavior.
    - UI Automation success is not equivalent to successful server-side execution.
    """
    process_running: bool
    ui_visible: bool
    account_authenticated: bool
    room_member: bool
    evidence_notes: List[str] = field(default_factory=list)
    evaluated_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "process_running": self.process_running,
            "ui_visible": self.ui_visible,
            "account_authenticated": self.account_authenticated,
            "room_member": self.room_member,
            "evidence_notes": list(self.evidence_notes),
            "evaluated_at": self.evaluated_at,
        }


# ---------- Authenticode Verification Helper ----------

def verify_windows_authenticode(exe_path: str) -> Tuple[bool, str]:
    """Verify Authenticode digital signature and extract Subject/Publisher name.

    Returns:
        (signature_valid: bool, publisher_name: str)
    """
    if sys.platform != "win32" or not os.path.isfile(exe_path):
        return False, ""

    try:
        from ctypes import wintypes

        class WINTRUST_FILE_INFO(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pcwszFilePath", wintypes.LPCWSTR),
                ("hFile", wintypes.HANDLE),
                ("pgKnownSubject", ctypes.c_void_p),
            ]

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_byte * 8),
            ]

        class WINTRUST_DATA(ctypes.Structure):
            _fields_ = [
                ("cbStruct", wintypes.DWORD),
                ("pPolicyCallbackData", ctypes.c_void_p),
                ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", wintypes.DWORD),
                ("fdwRevocationChecks", wintypes.DWORD),
                ("dwUnionChoice", wintypes.DWORD),
                ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
                ("dwStateAction", wintypes.DWORD),
                ("hWVTStateData", wintypes.HANDLE),
                ("pwszURLReference", wintypes.LPCWSTR),
                ("dwProvFlags", wintypes.DWORD),
                ("dwUIContext", wintypes.DWORD),
                ("pSignatureSettings", ctypes.c_void_p),
            ]

        WINTRUST_ACTION_GENERIC_VERIFY_V2 = GUID(
            0x00AAC56B, 0xCD44, 0x11D0,
            (ctypes.c_byte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE)
        )

        WTD_UI_NONE = 2
        WTD_REVOKE_NONE = 0
        WTD_CHOICE_FILE = 1
        WTD_STATEACTION_IGNORE = 0
        WTD_REVOCATION_CHECK_NONE = 0x00000010
        WTD_CACHE_ONLY_URL_RETRIEVAL = 0x00001000

        file_info = WINTRUST_FILE_INFO()
        file_info.cbStruct = ctypes.sizeof(WINTRUST_FILE_INFO)
        file_info.pcwszFilePath = exe_path
        file_info.hFile = None
        file_info.pgKnownSubject = None

        trust_data = WINTRUST_DATA()
        trust_data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
        trust_data.dwUIChoice = WTD_UI_NONE
        trust_data.fdwRevocationChecks = WTD_REVOKE_NONE
        trust_data.dwUnionChoice = WTD_CHOICE_FILE
        trust_data.pFile = ctypes.pointer(file_info)
        trust_data.dwStateAction = WTD_STATEACTION_IGNORE
        trust_data.dwProvFlags = WTD_REVOCATION_CHECK_NONE | WTD_CACHE_ONLY_URL_RETRIEVAL

        wintrust = ctypes.windll.wintrust
        status = wintrust.WinVerifyTrust(
            None,
            ctypes.byref(WINTRUST_ACTION_GENERIC_VERIFY_V2),
            ctypes.byref(trust_data)
        )
        is_valid = (status == 0)

        # Extract publisher / signer certificate subject via crypt32
        crypt32 = ctypes.windll.crypt32
        crypt32.CryptQueryObject.restype = wintypes.BOOL
        crypt32.CryptQueryObject.argtypes = [
            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p
        ]
        crypt32.CertEnumCertificatesInStore.restype = ctypes.c_void_p
        crypt32.CertEnumCertificatesInStore.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        crypt32.CertGetNameStringW.restype = wintypes.DWORD
        crypt32.CertGetNameStringW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.LPWSTR, wintypes.DWORD
        ]
        crypt32.CertCloseStore.restype = wintypes.BOOL
        crypt32.CertCloseStore.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        crypt32.CryptMsgClose.restype = wintypes.BOOL
        crypt32.CryptMsgClose.argtypes = [ctypes.c_void_p]

        CERT_QUERY_OBJECT_FILE = 0x00000001
        CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
        CERT_QUERY_FORMAT_FLAG_ALL = 0x0000000E
        CERT_NAME_SIMPLE_DISPLAY_TYPE = 4

        dwEncoding = wintypes.DWORD()
        dwContentType = wintypes.DWORD()
        dwFormatType = wintypes.DWORD()
        hStore = wintypes.HANDLE()
        hMsg = ctypes.c_void_p()

        publisher = ""
        res = crypt32.CryptQueryObject(
            CERT_QUERY_OBJECT_FILE,
            ctypes.c_wchar_p(exe_path),
            CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
            CERT_QUERY_FORMAT_FLAG_ALL,
            0,
            ctypes.byref(dwEncoding),
            ctypes.byref(dwContentType),
            ctypes.byref(dwFormatType),
            ctypes.byref(hStore),
            ctypes.byref(hMsg),
            None
        )

        try:
            if res and hStore.value:
                pCert = crypt32.CertEnumCertificatesInStore(hStore, None)
                while pCert:
                    buf = ctypes.create_unicode_buffer(512)
                    chars = crypt32.CertGetNameStringW(
                        pCert,
                        CERT_NAME_SIMPLE_DISPLAY_TYPE,
                        0,
                        None,
                        buf,
                        len(buf)
                    )
                    if chars > 1:
                        name = buf.value
                        if "camfrog" in name.lower() or not publisher:
                            publisher = name
                    pCert = crypt32.CertEnumCertificatesInStore(hStore, pCert)
        finally:
            # Release both native handles; the message handle is unused
            # afterward and must not leak on repeated verification.
            if hMsg.value:
                try:
                    crypt32.CryptMsgClose(hMsg)
                except Exception:
                    pass
            if hStore.value:
                try:
                    crypt32.CertCloseStore(hStore, 0)
                except Exception:
                    pass

        return is_valid, publisher
    except Exception:
        return False, ""


def get_pe_file_version(exe_path: str) -> str:
    """Return the PE FileVersion string (e.g. '8.5.0.51219') on Windows."""
    if sys.platform != "win32" or not os.path.isfile(exe_path):
        return ""
    try:
        import win32api
        info = win32api.GetFileVersionInfo(exe_path, "\\")
        ms = info["FileVersionMS"]
        ls = info["FileVersionLS"]
        return f"{win32api.HIWORD(ms)}.{win32api.LOWORD(ms)}.{win32api.HIWORD(ls)}.{win32api.LOWORD(ls)}"
    except Exception:
        return ""


# ---------- WindowsCamfrogProcessDiscovery ----------

class WindowsCamfrogProcessDiscovery:
    """Discovers and validates genuine Camfrog Video Chat client processes.

    Matches:
    - Executable name: 'Camfrog Video Chat.exe' (case-insensitive)
    - Valid Authenticode digital signature
    - Trusted publisher: 'Camfrog LLC'
    - Process creation timestamp and PID
    - Excludes CEF subprocesses (camfrog_cef.exe or --type= flags)
    - Excludes ZChat's own automation processes
    """

    DEFAULT_EXE_NAME = "Camfrog Video Chat.exe"
    CEF_EXE_NAME = "camfrog_cef.exe"
    DEFAULT_TRUSTED_PUBLISHERS = ("Camfrog LLC",)

    def __init__(
        self,
        trusted_publishers: Sequence[str] = DEFAULT_TRUSTED_PUBLISHERS,
        authenticode_verifier: Optional[Callable[[str], Tuple[bool, str]]] = None,
        version_reader: Optional[Callable[[str], str]] = None,
    ):
        self.trusted_publishers = tuple(trusted_publishers)
        self._verifier = authenticode_verifier or verify_windows_authenticode
        self._version_reader = version_reader or get_pe_file_version

    def is_cef_subprocess(self, proc_name: str, cmdline: Sequence[str]) -> bool:
        """Check if a process is an auxiliary CEF rendering/utility subprocess."""
        if (proc_name or "").lower() == self.CEF_EXE_NAME.lower():
            return True
        for arg in cmdline or []:
            if arg.startswith("--type="):
                return True
        return False

    def is_own_zchat_process(self, pid: int, cmdline: Sequence[str]) -> bool:
        """Check if a process belongs to ZChat rather than Camfrog."""
        if pid == os.getpid():
            return True
        joined_cmd = " ".join(cmdline or []).lower()
        if any(marker in joined_cmd for marker in (
            "camfrog_auto.py",
            "camfrog_gui.py",
            "camfrog_music_gui.py",
            "camfrog_status_gui.py",
            "room_control_gui.py",
            "chat_im_private_gui.py",
            "im_autoreply_gui.py",
            "web_status_gui.py",
            "pytest",
        )):
            return True
        return False

    def verify_publisher(self, publisher: str) -> bool:
        """Verify whether publisher matches authorized Camfrog entities.

        Uses exact identity matching, not substring matching: a certificate
        name merely *containing* a trusted string (e.g. "Fake Camfrog LLC")
        must not pass. Full DN forms are accepted only via an exact CN
        component (e.g. "CN=Camfrog LLC, O=Camfrog LLC").
        """
        pub = (publisher or "").strip()
        if not pub:
            return False
        pub_lower = pub.lower()
        for trusted in self.trusted_publishers:
            tnorm = trusted.strip().lower()
            if not tnorm:
                continue
            if pub_lower == tnorm:
                return True
            for part in (p.strip() for p in pub.split(",")):
                if part.lower() == f"cn={tnorm}":
                    return True
        return False

    def discover_processes(self) -> List[ProcessIdentity]:
        """Scan running processes and return verified Camfrog client identities."""
        import psutil

        verified: List[ProcessIdentity] = []
        for p in psutil.process_iter(["pid", "name", "exe", "create_time", "cmdline"]):
            try:
                info = p.info
                pid = info.get("pid")
                name = info.get("name") or ""
                exe = info.get("exe") or ""
                create_time = float(info.get("create_time") or 0.0)
                cmdline = info.get("cmdline") or []

                if not pid or pid <= 0:
                    continue

                # Exclude self/ZChat processes
                if self.is_own_zchat_process(pid, cmdline):
                    continue

                # Exclude CEF subprocesses
                if self.is_cef_subprocess(name, cmdline):
                    continue

                # Match base executable identity
                from pathlib import PureWindowsPath
                base_name = PureWindowsPath(exe).name if exe else name
                if base_name.lower() != self.DEFAULT_EXE_NAME.lower():
                    continue

                if not os.path.isfile(exe):
                    continue

                # Verify Authenticode digital signature
                sig_valid, publisher = self._verifier(exe)
                if not sig_valid or not self.verify_publisher(publisher):
                    continue

                # Read FileVersion
                version = self._version_reader(exe)

                verified.append(
                    ProcessIdentity(
                        pid=pid,
                        create_time=create_time,
                        exe_path=exe,
                        publisher=publisher,
                        signature_valid=sig_valid,
                        version=version,
                        is_agent_launched=False,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue

        return verified

    def get_verified_process(self, pid: Optional[int] = None) -> ProcessIdentity:
        """Return exactly one verified Camfrog process, or fail closed.

        Raises:
            ProcessLookupError: if no verified Camfrog client is running.
            RuntimeError: if multiple verified processes match and no explicit PID was given.
        """
        candidates = self.discover_processes()
        if pid is not None:
            for cand in candidates:
                if cand.pid == pid:
                    return cand
            raise ProcessLookupError(f"No verified Camfrog process found with PID {pid}.")

        if not candidates:
            raise ProcessLookupError("No verified Camfrog Video Chat process found on this system.")

        if len(candidates) > 1:
            raise RuntimeError(
                f"Multiple verified Camfrog processes detected ({[c.pid for c in candidates]}). "
                "Specify an exact PID to prevent cross-process targeting."
            )

        return candidates[0]


# ---------- WindowsCamfrogWindowClassifier ----------

class WindowsCamfrogWindowClassifier:
    """Classifies top-level Camfrog windows into distinct semantic UI roles.

    Uses observed structural UI signatures:
    1. IM history window:
       - Internet Explorer_Server
       - camfrog://wb-log-im-data.inside/
       - Edit / CEditTS control
       - No VideoViewport
    2. Room window:
       - VideoViewport
       - Internet Explorer_Server
       - camfrog://wb-log-room-data.inside/
       - Edit / CEditTS control
    3. Main client / directory:
       - CTreeViewTS
       - CComboBoxTS
       - Native Edit controls
    4. Ambiguous / Unknown:
       - Fail-closed: marked as UNKNOWN and never targeted for role actions.
    """

    OWN_GUI_TITLE_PREFIXES = (
        "Camfrog Status Changer",
        "Camfrog Random Status",
        "Camfrog Marquee Status",
        "Camfrog Music DJ",
        "Camfrog Auto Reply",
        "Camfrog Presence",
        "Room Control",
        "ZChat",
    )

    def is_own_gui_window(self, win: Any) -> bool:
        """Return True if window belongs to ZChat's own GUI."""
        # A generic toolkit class (for example TkTopLevel) does not establish
        # ownership. Camfrog or a controlled fixture may use the same class;
        # use the ZChat-specific title prefixes instead.
        try:
            txt = getattr(win, "window_text", lambda: "")() or ""
            if any(txt.startswith(p) for p in self.OWN_GUI_TITLE_PREFIXES):
                return True
        except Exception:
            pass

        return False

    def inspect_window_structures(
        self,
        hwnd: int,
        element: Any = None
    ) -> Dict[str, Any]:
        """Collect structural UI metadata without inspecting private message bodies."""
        child_classes: Set[str] = set()
        child_ids: Set[int] = set()

        if sys.platform == "win32":
            try:
                import win32gui
                def _enum_cb(ch, _):
                    cls = win32gui.GetClassName(ch)
                    child_classes.add(cls)
                    cid = win32gui.GetDlgCtrlID(ch)
                    if cid:
                        child_ids.add(cid)
                    return True
                win32gui.EnumChildWindows(hwnd, _enum_cb, None)
            except Exception:
                pass

        ie_urls: List[str] = []
        has_videoviewport = "VideoViewport" in child_classes

        # Inspect UIA element descendants if available for IE panes or VideoViewport
        if element is not None:
            try:
                for d in element.descendants():
                    info = getattr(d, "element_info", None)
                    if not info:
                        continue
                    cls = getattr(info, "class_name", "") or ""
                    name = getattr(info, "name", "") or ""
                    aid = getattr(info, "automation_id", "") or ""

                    if cls:
                        child_classes.add(cls)
                    if aid and aid.isdigit():
                        child_ids.add(int(aid))

                    if cls == "VideoViewport" or "VideoViewport" in name or "VideoViewport" in aid:
                        has_videoviewport = True

                    if cls == "Internet Explorer_Server":
                        if "wb-log-im-data" in name:
                            ie_urls.append("camfrog://wb-log-im-data.inside/")
                        elif "wb-log-room-data" in name:
                            ie_urls.append("camfrog://wb-log-room-data.inside/")
                        elif name.startswith("camfrog://"):
                            ie_urls.append(name.split("?")[0])
            except Exception:
                pass

        return {
            "child_classes": child_classes,
            "child_ids": child_ids,
            "ie_urls": ie_urls,
            "has_videoviewport": has_videoviewport,
        }

    def classify_window(self, win: Any) -> ClassifiedWindow:
        """Classify a pywinauto or mock window object into a semantic role."""
        hwnd = getattr(win, "handle", 0)
        try:
            pid = int(getattr(win, "process_id", lambda: 0)())
        except Exception:
            pid = 0

        try:
            title = str(getattr(win, "window_text", lambda: "")() or "")
        except Exception:
            title = ""

        try:
            is_vis = bool(getattr(win, "is_visible", lambda: True)())
        except Exception:
            is_vis = True

        if self.is_own_gui_window(win):
            return ClassifiedWindow(
                hwnd=hwnd,
                role=WindowRole.UNKNOWN,
                title=title,
                pid=pid,
                is_visible=is_vis,
                signatures=["zchat_own_gui"],
                element=win,
            )

        structures = self.inspect_window_structures(hwnd, win)
        child_classes = structures["child_classes"]
        ie_urls = structures["ie_urls"]
        has_video = structures["has_videoviewport"]

        signatures: List[str] = []
        is_main = False
        is_room = False
        is_im = False

        # 1. Main Directory signature: CTreeViewTS + CComboBoxTS + Edit
        has_tree = "CTreeViewTS" in child_classes
        has_combo = "CComboBoxTS" in child_classes
        has_edit = bool(child_classes.intersection({"Edit", "CEditTS"}))

        if has_tree and has_combo:
            is_main = True
            signatures.append("CTreeViewTS+CComboBoxTS")

        # 2. Room Window signature: VideoViewport + Internet Explorer_Server (wb-log-room-data)
        has_room_log = any("wb-log-room-data" in u for u in ie_urls)
        if has_video and (has_room_log or "Internet Explorer_Server" in child_classes):
            is_room = True
            signatures.append("VideoViewport+wb-log-room-data")

        # 3. IM Window signature: Internet Explorer_Server (wb-log-im-data) + Edit (no VideoViewport)
        has_im_log = any("wb-log-im-data" in u for u in ie_urls)
        if has_im_log and not has_video:
            is_im = True
            signatures.append("Internet Explorer_Server(wb-log-im-data)+Edit")

        # Fail-closed arbitration: exactly one role must match
        matched_roles = sum([1 for m in (is_main, is_room, is_im) if m])
        if matched_roles != 1:
            role = WindowRole.UNKNOWN
            if matched_roles > 1:
                signatures.append("ambiguous_multiple_structural_signatures")
            else:
                signatures.append("unrecognized_window_structure")
        elif is_main:
            role = WindowRole.MAIN_DIRECTORY
        elif is_room:
            role = WindowRole.ROOM_WINDOW
        elif is_im:
            role = WindowRole.IM_WINDOW
        else:
            role = WindowRole.UNKNOWN

        return ClassifiedWindow(
            hwnd=hwnd,
            role=role,
            title=title,
            pid=pid,
            is_visible=is_vis,
            signatures=signatures,
            element=win,
        )

    def classify_process_windows(
        self,
        identity: ProcessIdentity,
        visible_only: bool = False
    ) -> List[ClassifiedWindow]:
        """Classify top-level windows of a verified process, supporting minimized/tray windows."""
        classified: List[ClassifiedWindow] = []

        if sys.platform == "win32":
            try:
                import win32gui
                import win32process
                from pywinauto import Desktop

                desktop = Desktop(backend="uia")
                hwnds: List[int] = []

                def _cb(h, _):
                    try:
                        _, p = win32process.GetWindowThreadProcessId(h)
                        if p == identity.pid:
                            hwnds.append(h)
                    except Exception:
                        pass
                    return True

                win32gui.EnumWindows(_cb, None)
                for h in hwnds:
                    is_vis = bool(win32gui.IsWindowVisible(h))
                    if visible_only and not is_vis:
                        continue
                    try:
                        elem = desktop.window(handle=h).wrapper_object()
                    except Exception:
                        elem = None
                    cw = self.classify_window(elem) if elem is not None else ClassifiedWindow(
                        hwnd=h,
                        role=WindowRole.UNKNOWN,
                        title=win32gui.GetWindowText(h),
                        pid=identity.pid,
                        is_visible=is_vis,
                    )
                    # If elem was None or unknown, inspect structures directly by HWND
                    if cw.role == WindowRole.UNKNOWN:
                        structs = self.inspect_window_structures(h, elem)
                        child_classes = structs["child_classes"]
                        if "CTreeViewTS" in child_classes and "CComboBoxTS" in child_classes:
                            cw.role = WindowRole.MAIN_DIRECTORY
                            cw.signatures = ["CTreeViewTS+CComboBoxTS"]
                        cw.is_visible = is_vis

                    if not self.is_own_gui_window(cw):
                        classified.append(cw)
                return classified
            except Exception:
                pass

        try:
            from pywinauto import Desktop
            d = Desktop(backend="uia")
            top_windows = d.windows(process=identity.pid, visible_only=visible_only)
            for w in top_windows:
                if self.is_own_gui_window(w):
                    continue
                classified.append(self.classify_window(w))
        except Exception:
            pass

        return classified


# ---------- Identity Revalidation and Anti-Targeting ----------

def revalidate_process_identity(identity: ProcessIdentity) -> bool:
    """Revalidate that the target PID is alive and unchanged in identity.

    Fails closed if the PID is dead, or was recycled by OS for another process.
    """
    import psutil

    if not psutil.pid_exists(identity.pid):
        return False

    try:
        p = psutil.Process(identity.pid)
        if not p.is_running():
            return False
        # Allow tiny float precision margin for creation timestamp
        if abs(p.create_time() - identity.create_time) > 0.05:
            return False
        if os.path.normcase(p.exe()) != os.path.normcase(identity.exe_path):
            return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    except Exception:
        return False


def revalidate_window_identity(
    window: ClassifiedWindow,
    identity: ProcessIdentity
) -> bool:
    """Revalidate that the window handle exists and still belongs to the target process."""
    if not revalidate_process_identity(identity):
        return False

    if sys.platform == "win32":
        try:
            import win32gui
            import win32process

            if not win32gui.IsWindow(window.hwnd):
                return False
            _, win_pid = win32process.GetWindowThreadProcessId(window.hwnd)
            if win_pid != identity.pid:
                return False
            return True
        except Exception:
            return False

    return True


# ---------- Ownership Restrictions ----------

def terminate_client_process(
    identity: ProcessIdentity,
    force: bool = False
) -> bool:
    """Terminate client process only if launched by the agent.

    Preserves strict ownership:
    NEVER terminates a client process that was already running or not launched by the agent.

    Raises:
        PermissionError: if called on a process not launched by the agent.
    """
    if not identity.is_agent_launched:
        raise PermissionError(
            f"Ownership restriction violated: Refusing to terminate PID {identity.pid}. "
            "Process was not launched by this agent."
        )

    if not revalidate_process_identity(identity):
        return True

    try:
        import subprocess
        cmd = [system_taskkill_path(), "/PID", str(identity.pid)]
        if force:
            cmd.append("/F")
        res = subprocess.run(cmd, capture_output=True, check=False, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


# ---------- Evidence State Evaluation ----------

def evaluate_client_runtime_state(
    identity: ProcessIdentity,
    classified_windows: Sequence[ClassifiedWindow]
) -> ClientRuntimeState:
    """Evaluate runtime states following explicit evidence boundary rules.

    Rules:
    - A process being alive does not establish authentication.
    - A room window existing does not establish current room membership.
    - Historical messages do not prove current activity.
    - A UI control labelled Online does not establish current presence.
    - An accepted URI handoff does not establish completed behavior.
    - UI Automation success is not equivalent to successful server-side execution.
    """
    evidence_notes: List[str] = []

    # 1. Process running state
    is_running = revalidate_process_identity(identity)
    if is_running:
        evidence_notes.append(
            f"Process PID {identity.pid} is alive, path verified ({identity.exe_path}), "
            f"Authenticode signed by '{identity.publisher}'."
        )
    else:
        evidence_notes.append(f"Process PID {identity.pid} is not running or failed identity revalidation.")
        return ClientRuntimeState(
            process_running=False,
            ui_visible=False,
            account_authenticated=False,
            room_member=False,
            evidence_notes=evidence_notes,
        )

    # 2. UI visible state
    visible_windows = [w for w in classified_windows if w.is_visible]
    ui_visible = len(visible_windows) > 0
    if ui_visible:
        evidence_notes.append(
            f"UI visible with {len(visible_windows)} top-level window(s): "
            f"{[f'hwnd={w.hwnd} role={w.role.value}' for w in visible_windows]}."
        )
    else:
        evidence_notes.append("No visible top-level windows found (client may be minimized to system tray).")

    # 3. Account authenticated state
    # Rule: Process running does NOT establish authentication!
    # Evidence requirement: Main Directory window must be present, visible, containing CTreeViewTS and CComboBoxTS.
    main_wins = [w for w in classified_windows if w.role == WindowRole.MAIN_DIRECTORY and w.is_visible]
    account_authenticated = False
    if main_wins:
        account_authenticated = True
        evidence_notes.append(
            f"Account authenticated: Main client window (hwnd={main_wins[0].hwnd}) active with contact directory."
        )
    else:
        evidence_notes.append(
            "Account authentication not established: Main client directory window not present or visible."
        )

    # 4. Room member state
    # Rule: A room window existing does NOT establish current room membership!
    # Room window may remain open after being kicked, disconnected, or left.
    # Limitation: without a verified provider-side membership signal, a
    # visible room window with no kick/disconnect indicator is treated as
    # current membership (best available UI evidence). Affirmative
    # server-side membership verification is NOT_IMPLEMENTED.
    room_wins = [w for w in classified_windows if w.role == WindowRole.ROOM_WINDOW and w.is_visible]
    room_member = False
    if not room_wins:
        evidence_notes.append("Room membership not established: No room window present.")
    else:
        # Check for kick or disconnected banner in the room window
        is_kicked_or_parted = False
        room_win = room_wins[0]
        if room_win.element is not None:
            try:
                for desc in room_win.element.descendants():
                    txt = (getattr(desc.element_info, "name", "") or "").lower()
                    if "kicked" in txt or "disconnected" in txt or "you were kicked" in txt:
                        is_kicked_or_parted = True
                        break
            except Exception:
                pass

        if is_kicked_or_parted:
            room_member = False
            evidence_notes.append(
                f"Room window exists (hwnd={room_win.hwnd}), but user was kicked or disconnected. "
                "Active membership is FALSE per evidence boundary rule."
            )
        else:
            room_member = True
            evidence_notes.append(
                f"Room window active (hwnd={room_win.hwnd}) without disconnect/kick indicator. "
                "Current room membership established."
            )

    return ClientRuntimeState(
        process_running=is_running,
        ui_visible=ui_visible,
        account_authenticated=account_authenticated,
        room_member=room_member,
        evidence_notes=evidence_notes,
    )


# ---------- Read-Only Attachment Model ----------

class CamfrogClientAttachment:
    """Read-only attachment to an already-running authorized Camfrog client."""

    def __init__(
        self,
        identity: ProcessIdentity,
        discovery: WindowsCamfrogProcessDiscovery,
        classifier: WindowsCamfrogWindowClassifier,
    ):
        self.identity = identity
        self.discovery = discovery
        self.classifier = classifier
        self.attached_at = time.time()
        self.read_only = True

    def revalidate(self) -> bool:
        """Confirm the attached process is still valid."""
        return revalidate_process_identity(self.identity)

    def get_windows(self) -> List[ClassifiedWindow]:
        """Classify and return current windows for the process."""
        if not self.revalidate():
            raise ProcessLookupError(f"Target process PID {self.identity.pid} is no longer running.")
        return self.classifier.classify_process_windows(self.identity)

    def get_runtime_state(self) -> ClientRuntimeState:
        """Evaluate evidence-separated runtime states."""
        windows = self.get_windows()
        return evaluate_client_runtime_state(self.identity, windows)

    def get_diagnostics(self) -> Dict[str, Any]:
        """Generate a sanitized runtime diagnostic report without leaking message bodies."""
        windows = self.get_windows()
        state = evaluate_client_runtime_state(self.identity, windows)
        return {
            "process": self.identity.as_dict(),
            "windows": [w.as_dict() for w in windows],
            "state": state.as_dict(),
            "attached_at": self.attached_at,
            "read_only": self.read_only,
        }

    def terminate(self, force: bool = False) -> bool:
        """Attempt to terminate client. Fails closed under ownership restrictions."""
        return terminate_client_process(self.identity, force=force)


def attach_to_camfrog_client(
    pid: Optional[int] = None,
    trusted_publishers: Sequence[str] = WindowsCamfrogProcessDiscovery.DEFAULT_TRUSTED_PUBLISHERS,
) -> CamfrogClientAttachment:
    """Discover and attach in read-only mode to an already-running authorized client."""
    discovery = WindowsCamfrogProcessDiscovery(trusted_publishers=trusted_publishers)
    identity = discovery.get_verified_process(pid=pid)
    classifier = WindowsCamfrogWindowClassifier()
    return CamfrogClientAttachment(identity, discovery, classifier)
