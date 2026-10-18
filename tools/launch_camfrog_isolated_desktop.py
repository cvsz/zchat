"""Launch Camfrog on a named Win32 desktop for manual isolation testing.

This does not switch desktops, automate login, send status, or terminate
Camfrog. It only proves that Windows created the desktop and started the
requested executable with that desktop assigned. Verify the client manually
on a Windows machine before treating the setup as usable.
"""

from __future__ import annotations

import argparse
import ctypes
import re
import subprocess
import sys
import time
from pathlib import Path

DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_READOBJECTS = 0x0001
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_ENUMERATE = 0x0040
DESKTOP_SWITCHDESKTOP = 0x0100
DESKTOP_ACCESS = (
    DESKTOP_CREATEWINDOW
    | DESKTOP_READOBJECTS
    | DESKTOP_WRITEOBJECTS
    | DESKTOP_ENUMERATE
    | DESKTOP_SWITCHDESKTOP
)
DEFAULT_DESKTOP = "Default"
DEFAULT_ISOLATED_DESKTOP = "ZChatCamfrogTest"
CAMFROG_BASENAMES = {"camfrog video chat.exe", "camfrog_video_chat.exe"}


class LauncherError(RuntimeError):
    """Raised when a desktop or process operation cannot be verified."""


def _windows_api():
    if sys.platform != "win32":
        raise LauncherError("This launcher is Windows-only.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    user32.CreateDesktopW.restype = ctypes.c_void_p
    user32.OpenDesktopW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    user32.OpenDesktopW.restype = ctypes.c_void_p
    user32.EnumDesktopWindows.argtypes = [
        ctypes.c_void_p,
        ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t),
        ctypes.c_ssize_t,
    ]
    user32.EnumDesktopWindows.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
    ]
    user32.GetWindowThreadProcessId.restype = ctypes.c_uint
    user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    user32.CloseDesktop.restype = ctypes.c_int
    user32.SwitchDesktop.argtypes = [ctypes.c_void_p]
    user32.SwitchDesktop.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return user32, kernel32


def _camfrog_processes():
    """Return matching process metadata without reading command lines."""
    try:
        import psutil
    except ImportError as exc:
        raise LauncherError("psutil is required to check for an existing Camfrog process.") from exc
    matches = []
    for process in psutil.process_iter(("pid", "name", "exe")):
        try:
            info = process.info
            name = (info.get("name") or "").casefold()
            exe = info.get("exe") or ""
            if name in CAMFROG_BASENAMES or Path(exe).name.casefold() in CAMFROG_BASENAMES:
                matches.append({"pid": int(info["pid"]), "exe": exe})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return matches


def _validate_desktop_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise LauncherError("Desktop name must be 1-64 ASCII letters, digits, '_' or '-'.")
    if name.casefold() == DEFAULT_DESKTOP.casefold():
        raise LauncherError("Refusing to create or launch on the Default desktop.")
    return name


def _open_or_create_desktop(user32, name: str):
    handle = user32.CreateDesktopW(name, None, None, 0, DESKTOP_ACCESS, None)
    if handle:
        return handle, True
    create_error = ctypes.get_last_error()
    # ERROR_ALREADY_EXISTS is not guaranteed for CreateDesktop; try opening the
    # existing named desktop and preserve it rather than deleting/recreating it.
    handle = user32.OpenDesktopW(name, 0, False, DESKTOP_ACCESS)
    if handle:
        return handle, False
    raise LauncherError(
        f"Could not create/open desktop (CreateDesktop error {create_error}; "
        f"OpenDesktop error {ctypes.get_last_error()})."
    )


def launch(exe: Path, desktop: str, args: list[str]) -> dict:
    user32, kernel32 = _windows_api()
    desktop = _validate_desktop_name(desktop)
    exe = exe.expanduser().resolve(strict=True)
    if not exe.is_file() or exe.name.casefold() not in CAMFROG_BASENAMES:
        raise LauncherError("--exe must point to the installed Camfrog Video Chat executable.")
    running = _camfrog_processes()
    if running:
        raise LauncherError(
            "Camfrog is already running; close it manually first so single-instance "
            "forwarding cannot send this launch back to the visible desktop. "
            f"Matching PID(s): {[p['pid'] for p in running]}"
        )

    desktop_handle, created = _open_or_create_desktop(user32, desktop)
    process_handle = thread_handle = None
    try:
        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint),
                ("lpReserved", ctypes.c_wchar_p),
                ("lpDesktop", ctypes.c_wchar_p),
                ("lpTitle", ctypes.c_wchar_p),
                ("dwX", ctypes.c_uint),
                ("dwY", ctypes.c_uint),
                ("dwXSize", ctypes.c_uint),
                ("dwYSize", ctypes.c_uint),
                ("dwXCountChars", ctypes.c_uint),
                ("dwYCountChars", ctypes.c_uint),
                ("dwFillAttribute", ctypes.c_uint),
                ("dwFlags", ctypes.c_uint),
                ("wShowWindow", ctypes.c_ushort),
                ("cbReserved2", ctypes.c_ushort),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", ctypes.c_void_p),
                ("hStdOutput", ctypes.c_void_p),
                ("hStdError", ctypes.c_void_p),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", ctypes.c_void_p),
                ("hThread", ctypes.c_void_p),
                ("dwProcessId", ctypes.c_uint),
                ("dwThreadId", ctypes.c_uint),
            ]

        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.lpDesktop = f"WinSta0\\{desktop}"
        info = PROCESS_INFORMATION()
        command_line = subprocess.list2cmdline([str(exe), *args])
        mutable_command = ctypes.create_unicode_buffer(command_line)
        create_process = kernel32.CreateProcessW
        create_process.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        create_process.restype = ctypes.c_int
        ok = create_process(
            str(exe), mutable_command, None, None, False, 0, None,
            str(exe.parent), ctypes.byref(startup), ctypes.byref(info)
        )
        if not ok:
            raise LauncherError(f"CreateProcessW failed (error {ctypes.get_last_error()}).")
        process_handle, thread_handle = info.hProcess, info.hThread
        return {
            "desktop": desktop,
            "desktop_created": created,
            "pid": int(info.dwProcessId),
            "executable": str(exe),
            "process_started": True,
            "camfrog_window_verified": False,
            "camfrog_login_verified": False,
            "provider_status_verified": False,
        }
    finally:
        if thread_handle:
            kernel32.CloseHandle(thread_handle)
        if process_handle:
            kernel32.CloseHandle(process_handle)
        user32.CloseDesktop(desktop_handle)


def inspect(desktop: str) -> dict:
    user32, _kernel32 = _windows_api()
    try:
        import psutil
    except ImportError as exc:
        raise LauncherError("psutil is required to inspect the desktop windows.") from exc
    desktop = _validate_desktop_name(desktop)
    handle = user32.OpenDesktopW(desktop, 0, False, DESKTOP_ACCESS)
    if not handle:
        raise LauncherError(
            f"Desktop {desktop!r} is unavailable (error {ctypes.get_last_error()})."
        )
    windows = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)

    def visit(hwnd, _param):
        pid = ctypes.c_uint(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        try:
            process = psutil.Process(pid.value)
            image = process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            image = "unavailable"
        if image.casefold() in CAMFROG_BASENAMES:
            windows.append({"hwnd": int(hwnd), "pid": int(pid.value), "class": class_name.value})
        return True

    callback = callback_type(visit)
    try:
        ok = user32.EnumDesktopWindows(handle, callback, 0)
        if not ok and ctypes.get_last_error():
            raise LauncherError(f"EnumDesktopWindows failed (error {ctypes.get_last_error()}).")
    finally:
        user32.CloseDesktop(handle)
    return {
        "desktop": desktop,
        "camfrog_windows_found": len(windows),
        "windows": windows,
        "normal_operation_verified": False,
        "note": "Window presence does not prove login, status submit, or friend-list delivery.",
    }


def switch_for_test(desktop: str, seconds: int) -> dict:
    """Make the named desktop interactive temporarily, then restore Default."""
    user32, _kernel32 = _windows_api()
    desktop = _validate_desktop_name(desktop)
    if not 5 <= seconds <= 1800:
        raise LauncherError("Test duration must be between 5 and 1800 seconds.")
    target = user32.OpenDesktopW(desktop, 0, False, DESKTOP_SWITCHDESKTOP)
    if not target:
        raise LauncherError(
            f"Desktop {desktop!r} is unavailable (error {ctypes.get_last_error()})."
        )
    default = user32.OpenDesktopW(DEFAULT_DESKTOP, 0, False, DESKTOP_SWITCHDESKTOP)
    if not default:
        user32.CloseDesktop(target)
        raise LauncherError(
            f"Default desktop is unavailable (error {ctypes.get_last_error()})."
        )
    try:
        if not user32.SwitchDesktop(target):
            raise LauncherError(
                f"SwitchDesktop to test desktop failed (error {ctypes.get_last_error()})."
            )
        time.sleep(seconds)
    finally:
        restored = bool(user32.SwitchDesktop(default))
        user32.CloseDesktop(default)
        user32.CloseDesktop(target)
    if not restored:
        raise LauncherError(
            f"Could not switch back to Default (error {ctypes.get_last_error()})."
        )
    return {
        "desktop": desktop,
        "interactive_seconds": seconds,
        "returned_to_default": True,
        "normal_operation_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch_parser = sub.add_parser("launch", help="Launch Camfrog on a named separate desktop")
    launch_parser.add_argument("--exe", required=True, type=Path)
    launch_parser.add_argument("--desktop", default=DEFAULT_ISOLATED_DESKTOP)
    launch_parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments forwarded to Camfrog")
    inspect_parser = sub.add_parser("inspect", help="Count Camfrog windows on the named desktop")
    inspect_parser.add_argument("--desktop", default=DEFAULT_ISOLATED_DESKTOP)
    switch_parser = sub.add_parser(
        "switch-test", help="Interact with the test desktop temporarily, then return to Default"
    )
    switch_parser.add_argument("--desktop", default=DEFAULT_ISOLATED_DESKTOP)
    switch_parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("BLOCKED: CreateDesktop can only be verified on Windows.", file=sys.stderr)
        return 2
    try:
        if args.command == "launch":
            result = launch(args.exe, args.desktop, args.args)
        elif args.command == "inspect":
            result = inspect(args.desktop)
        else:
            result = switch_for_test(args.desktop, args.seconds)
        print(__import__("json").dumps(result, ensure_ascii=False))
        return 0
    except (LauncherError, OSError, ValueError) as exc:
        print(__import__("json").dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
