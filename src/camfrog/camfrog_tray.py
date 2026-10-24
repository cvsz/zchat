"""Small Windows notification-area icon used by the Camfrog GUIs.

The Tk window owns the message pump. Closing a GUI window can therefore hide it
without leaving a second Python event loop running in the background.
"""
import os
from pathlib import Path


class TrayIcon:
    """A native Windows tray icon with Show and Exit menu items.

    On non-Windows platforms (or when pywin32 cannot initialize), ``active`` is
    false and callers can fall back to normal window behavior.
    """

    WM_TRAY = 0x8000 + 41
    ID_SHOW = 1001
    ID_EXIT = 1002

    def __init__(self, root, title, on_show, on_exit, icon_path=None):
        self.root = root
        self.on_show = on_show
        self.on_exit = on_exit
        self.title = str(title)[:127]
        self.active = False
        self._class_atom = None
        self.hwnd = None
        self.icon = None
        if os.name != "nt":
            return
        try:
            import win32con
            import win32gui

            self.win32con = win32con
            self.win32gui = win32gui
            self.hinstance = win32gui.GetModuleHandle(None)
            self.class_name = f"CamfrogTrayWindow_{os.getpid()}_{id(self):x}"
            wc = win32gui.WNDCLASS()
            wc.hInstance = self.hinstance
            wc.lpszClassName = self.class_name
            wc.lpfnWndProc = self._window_proc
            self._class_atom = win32gui.RegisterClass(wc)
            self.hwnd = win32gui.CreateWindowEx(
                0, self.class_name, self.title, 0,
                0, 0, 0, 0, 0, 0, self.hinstance, None,
            )
            self.icon = self._load_icon(icon_path)
            flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
            data = (self.hwnd, 1, flags, self.WM_TRAY, self.icon, self.title)
            if not win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, data):
                raise RuntimeError("Shell_NotifyIcon rejected NIM_ADD")
            self.active = True
            self.root.after(100, self._pump)
        except Exception:
            self.shutdown()

    def _load_icon(self, icon_path):
        gui, con = self.win32gui, self.win32con
        paths = []
        if icon_path:
            paths.append(Path(icon_path))
        frozen_dir = getattr(__import__("sys"), "_MEIPASS", None)
        if frozen_dir:
            paths.append(Path(frozen_dir) / "app.ico")
        paths.append(Path(__import__("sys").executable).resolve().parent / "app.ico")
        paths.append(Path(__file__).resolve().with_name("app.ico"))
        for path in paths:
            try:
                if path.is_file():
                    icon = gui.LoadImage(
                        0, str(path), con.IMAGE_ICON, 0, 0,
                        con.LR_LOADFROMFILE | con.LR_DEFAULTSIZE,
                    )
                    if icon:
                        return icon
            except Exception:
                continue
        return gui.LoadIcon(0, con.IDI_APPLICATION)

    def _window_proc(self, hwnd, message, wparam, lparam):
        gui, con = self.win32gui, self.win32con
        if message == self.WM_TRAY:
            if lparam in (con.WM_LBUTTONUP, con.WM_LBUTTONDBLCLK):
                self.on_show()
            elif lparam == con.WM_RBUTTONUP:
                self._show_menu()
            return 0
        if message == con.WM_COMMAND:
            command = wparam & 0xFFFF
            if command == self.ID_SHOW:
                self.on_show()
            elif command == self.ID_EXIT:
                self.on_exit()
            return 0
        return gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _show_menu(self):
        gui, con = self.win32gui, self.win32con
        menu = gui.CreatePopupMenu()
        gui.AppendMenu(menu, con.MF_STRING, self.ID_SHOW, "Open window")
        gui.AppendMenu(menu, con.MF_SEPARATOR, 0, None)
        gui.AppendMenu(menu, con.MF_STRING, self.ID_EXIT, "Exit GUI")
        x, y = gui.GetCursorPos()
        gui.SetForegroundWindow(self.hwnd)
        gui.TrackPopupMenu(menu, con.TPM_RIGHTBUTTON, x, y, 0, self.hwnd, None)
        gui.PostMessage(self.hwnd, con.WM_NULL, 0, 0)
        gui.DestroyMenu(menu)

    def _pump(self):
        if not self.active:
            return
        try:
            self.win32gui.PumpWaitingMessages()
        except Exception:
            self.shutdown()
            return
        if self.active:
            try:
                self.root.after(100, self._pump)
            except Exception:
                self.shutdown()

    def shutdown(self):
        if not self.active and not self.hwnd:
            return
        gui = getattr(self, "win32gui", None)
        if gui:
            try:
                if self.hwnd:
                    gui.Shell_NotifyIcon(gui.NIM_DELETE, (self.hwnd, 1))
            except Exception:
                pass
            try:
                if self.hwnd:
                    gui.DestroyWindow(self.hwnd)
            except Exception:
                pass
            try:
                if self._class_atom:
                    gui.UnregisterClass(self.class_name, self.hinstance)
            except Exception:
                pass
        self.active = False
        self.hwnd = None
        self._class_atom = None
