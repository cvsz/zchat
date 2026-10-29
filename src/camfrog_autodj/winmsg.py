"""Win32 message posting helpers for background sends."""
from __future__ import annotations
import ctypes
import ctypes.wintypes
import time

# ---- constants ----
WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_RETURN = 0x0D
GW_OWNER = 4

# ---- DLLs ----
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---- type definitions ----
HWND = ctypes.wintypes.HWND
UINT = ctypes.wintypes.UINT
WPARAM = ctypes.wintypes.WPARAM
LPARAM = ctypes.wintypes.LPARAM
LRESULT = ctypes.wintypes.LRESULT

# ---- function prototypes ----
PostMessageW = user32.PostMessageW
PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
PostMessageW.restype = ctypes.wintypes.BOOL

SendMessageW = user32.SendMessageW
SendMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
SendMessageW.restype = LRESULT

GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.restype = HWND

SetForegroundWindow = user32.SetForegroundWindow
SetForegroundWindow.argtypes = [HWND]
SetForegroundWindow.restype = ctypes.wintypes.BOOL

GetWindow = user32.GetWindow
GetWindow.argtypes = [HWND, ctypes.c_int]
GetWindow.restype = HWND

IsWindowVisible = user32.IsWindowVisible
IsWindowVisible.argtypes = [HWND]
IsWindowVisible.restype = ctypes.wintypes.BOOL

ShowWindow = user32.ShowWindow
ShowWindow.argtypes = [HWND, ctypes.c_int]
ShowWindow.restype = ctypes.wintypes.BOOL

SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4


def foreground() -> int:
    return int(GetForegroundWindow())


def restore_foreground(prev: int) -> None:
    if prev:
        SetForegroundWindow(HWND(prev))


def show_noactivate(hwnd: int) -> None:
    """Show window without stealing focus (SW_SHOWNOACTIVATE)."""
    ShowWindow(HWND(hwnd), SW_SHOWNOACTIVATE)


def post_text(hwnd: int, pt: tuple[int, int], text: str, enter: bool = True) -> bool:
    """Post WM_CHAR for each character, optionally followed by Enter."""
    try:
        for ch in text:
            PostMessageW(HWND(hwnd), WM_CHAR, WPARAM(ord(ch)), LPARAM(0))
            time.sleep(0.001)
        if enter:
            PostMessageW(HWND(hwnd), WM_KEYDOWN, WPARAM(VK_RETURN), LPARAM(0))
            time.sleep(0.01)
            PostMessageW(HWND(hwnd), WM_KEYUP, WPARAM(VK_RETURN), LPARAM(0))
        return True
    except Exception:
        return False