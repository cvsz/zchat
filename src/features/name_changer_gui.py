"""Standalone Camfrog Name Changer (GUI + CLI).

Provides manual and sequential switching of display names / nicknames
supporting full Thai script (ภาษาไทย) and English fonts/Unicode.
Works either locally with active Camfrog client automation or profile synchronization,
with fail-closed validation, configurable name lists, and Thai font support.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

# Path detection: frozen vs source
BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent.parent
)

APP_NAME = "name-changer"
APP_TITLE = "ZChat Name Changer (สลับชื่อ / รองรับภาษาไทย)"
APP_VERSION = "2.19.2"

DEFAULT_THAI_NAMES = [
    "ผู้ใช้ camfrog",
    "สายคามฟร็อก",
    "เบอร์คามฟร็อก",
    "ดีเจเสียงใส",
    "คนึงนิจ",
    "มังกรทอง",
]

DEFAULT_EN_NAMES = [
    "Camfrog User",
    "Camfrog Line",
    "Camfrog DJ",
    "Golden Dragon",
]

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

THEME = {
    "background": "#1e1e2e",
    "surface": "#252538",
    "surface_high": "#2e2e44",
    "accent": "#f9e2af",
    "accent_hover": "#fab387",
    "text": "#cdd6f4",
    "muted": "#a6adc8",
    "border": "#45475a",
    "entry": "#181825",
    "button_text": "#1e1e2e",
    "button_active": "#3b3b54",
    "button_pressed": "#4f4f6e",
    "danger": "#f38ba8",
    "success": "#a6e3a1",
    "warning": "#f9e2af",
}

DEFAULT_FONT = ("Tahoma", 9)
HEADER_FONT = ("Tahoma", 12, "bold")
TITLE_FONT = ("Tahoma", 10, "bold")


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VAL definitions into os.environ if file exists."""
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


def validate_name(name: str, max_length: int = 40) -> str:
    """Validate and clean nickname, ensuring proper UTF-8 handling for Thai characters."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("Name cannot be empty")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip()
    return cleaned


class NameManager:
    """Manages name switching sequence, manual triggers, and list management."""

    def __init__(self, names: list[str] | None = None):
        self._names = [
            validate_name(n)
            for n in (names or DEFAULT_THAI_NAMES + DEFAULT_EN_NAMES)
            if n.strip()
        ]
        self._index = 0
        self._lock = threading.Lock()

    @property
    def names(self) -> list[str]:
        with self._lock:
            return list(self._names)

    def set_names(self, names: list[str]) -> None:
        valid = [validate_name(n) for n in names if str(n).strip()]
        if not valid:
            raise ValueError("Must provide at least one valid name")
        with self._lock:
            self._names = valid
            self._index = self._index % len(self._names)

    def current_name(self) -> str:
        with self._lock:
            if not self._names:
                return "Camfrog User"
            return self._names[self._index % len(self._names)]

    def switch_next(self) -> str:
        with self._lock:
            if not self._names:
                return "Camfrog User"
            self._index = (self._index + 1) % len(self._names)
            return self._names[self._index]

    def switch_prev(self) -> str:
        with self._lock:
            if not self._names:
                return "Camfrog User"
            self._index = (self._index - 1) % len(self._names)
            return self._names[self._index]

    def switch_to(self, target: str) -> str:
        clean = validate_name(target)
        with self._lock:
            if clean in self._names:
                self._index = self._names.index(clean)
            else:
                self._names.append(clean)
                self._index = len(self._names) - 1
            return clean


# ---------- Inlined ClipboardController ----------


_WIDGET_CLASSES = {
    "Entry",
    "TEntry",
    "TCombobox",
    "Spinbox",
    "TSpinbox",
    "Text",
    "Listbox",
}
_EDITABLE_CLASSES = {"Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text"}


def _windows_open_clipboard(clipboard, attempts=20, interval=0.025):
    """Windows clipboard is often briefly busy; retry opening it for 0.5 seconds."""
    last_error = None
    for attempt in range(attempts):
        try:
            clipboard.OpenClipboard(None)
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(interval)
    raise OSError(f"Could not open the Windows clipboard: {last_error}") from last_error


def set_clipboard_text(root, text):
    text = str(text)
    if os.name == "nt":
        import win32clipboard
        import win32con

        _windows_open_clipboard(win32clipboard)
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update_idletasks()


def get_clipboard_text(root):
    if os.name == "nt":
        import win32clipboard
        import win32con

        _windows_open_clipboard(win32clipboard)
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                value = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                value = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                if isinstance(value, bytes):
                    value = value.decode("mbcs", errors="replace")
            else:
                return ""
            return value if isinstance(value, str) else str(value)
        finally:
            win32clipboard.CloseClipboard()
    try:
        return root.clipboard_get()
    except Exception:
        return ""


def _widget_class(widget):
    try:
        return widget.winfo_class()
    except Exception:
        return ""


def _has_selection(widget, widget_class):
    try:
        if widget_class == "Text":
            return bool(widget.tag_ranges("sel"))
        if widget_class == "Listbox":
            return bool(widget.curselection())
        return bool(widget.selection_present())
    except Exception:
        return False


def _selected_text(widget, widget_class):
    if widget_class == "Text":
        return widget.get("sel.first", "sel.last")
    if widget_class == "Listbox":
        selected = widget.curselection()
        return "\n".join(widget.get(index) for index in selected)
    value = widget.get()
    start, end = int(widget.index("sel.first")), int(widget.index("sel.last"))
    return value[start:end]


def _editable(widget, widget_class):
    if widget_class not in _EDITABLE_CLASSES:
        return False
    try:
        return str(widget.cget("state")) not in {"disabled", "readonly"}
    except Exception:
        return True


def _delete_selection(widget, widget_class):
    if widget_class == "Text":
        widget.delete("sel.first", "sel.last")
    else:
        widget.delete("sel.first", "sel.last")


def _operate(root, widget, action):
    widget_class = _widget_class(widget)
    if widget_class not in _WIDGET_CLASSES:
        return False

    if action in {"copy", "cut"}:
        if not _has_selection(widget, widget_class):
            return False
        if action == "cut" and not _editable(widget, widget_class):
            return False
        selected = _selected_text(widget, widget_class)
        set_clipboard_text(root, selected)
        if action == "cut":
            _delete_selection(widget, widget_class)
        return True

    if action == "paste":
        if not _editable(widget, widget_class):
            return False
        value = get_clipboard_text(root)
        if not value:
            return False
        if widget_class == "Text":
            if _has_selection(widget, widget_class):
                widget.delete("sel.first", "sel.last")
            widget.insert("insert", value)
        else:
            if _has_selection(widget, widget_class):
                _delete_selection(widget, widget_class)
            widget.insert("insert", value)
        return True
    return False


class ClipboardController:
    """Attach reliable Ctrl+C/X/V/A, Insert shortcuts, and a right-click menu."""

    def __init__(self, root, translate=None, on_error=None):
        import tkinter as tk

        self.root = root
        self.translate = translate or (lambda text: str(text).partition("|")[0])
        self.on_error = on_error
        self.target = None
        self.widgets = []
        import weakref

        self._installed_widgets = weakref.WeakSet()
        self.menu = tk.Menu(root, tearoff=False)
        for label, action in (
            ("Cut|ตัด", "cut"),
            ("Copy|คัดลอก", "copy"),
            ("Paste|วาง", "paste"),
        ):
            self.menu.add_command(
                label=self.translate(label),
                command=lambda op=action: self.perform(self.target, op),
            )
        self.menu.add_separator()
        self.menu.add_command(
            label=self.translate("Select all|เลือกทั้งหมด"),
            command=lambda: self.select_all(self.target),
        )

    def refresh_menu(self):
        labels = (
            "Cut|\u0e15\u0e31\u0e14",
            "Copy|\u0e04\u0e31\u0e14\u0e25\u0e2d\u0e01",
            "Paste|\u0e27\u0e32\u0e07",
            "Select all|\u0e40\u0e25\u0e37\u0e2d\u0e01\u0e17\u0e31\u0e49\u0e07\u0e2b\u0e21\u0e14",
        )
        for index, label in enumerate(labels):
            menu_index = index if index < 3 else 4
            self.menu.entryconfigure(menu_index, label=self.translate(label))

    def install(self, parent):
        """Bind editors recursively, including controls added after the first build."""
        for widget in parent.winfo_children():
            widget_class = _widget_class(widget)
            if (
                widget_class in _WIDGET_CLASSES
                and widget not in self._installed_widgets
            ):
                self._installed_widgets.add(widget)
                self.widgets.append(widget)
                if widget_class in {"Text", "Listbox"}:
                    try:
                        widget.configure(
                            selectbackground=THEME.get("accent", "#8b672b"),
                            selectforeground=THEME.get("surface", "#fffdfa"),
                            insertbackground=THEME.get("text", "#283648"),
                            highlightbackground=THEME.get("border", "#d5c8ac"),
                            highlightcolor=THEME.get("accent", "#8b672b"),
                        )
                    except Exception:
                        pass
                for sequence, action in (
                    ("<Control-c>", "copy"),
                    ("<Control-x>", "cut"),
                    ("<Control-v>", "paste"),
                    ("<Control-Insert>", "copy"),
                    ("<Shift-Delete>", "cut"),
                    ("<Shift-Insert>", "paste"),
                ):
                    widget.bind(
                        sequence,
                        lambda event, op=action: self.handle(event, op),
                        add="+",
                    )
                # The generic key binding also recognizes Windows virtual-key
                # codes and control characters when a Thai/non-Latin layout
                # reports an unknown keysym for Ctrl+A/C/V/X.
                widget.bind("<Control-KeyPress>", self.handle_control_key, add="+")
                widget.bind("<Control-a>", self._select_all_event, add="+")
                widget.bind("<Button-3>", self.show_menu, add="+")
                widget.bind("<Shift-F10>", self.show_menu, add="+")
            self.install(widget)

    def handle_control_key(self, event):
        """Dispatch Ctrl+A/C/V/X consistently across keyboard layouts."""
        keysym = str(getattr(event, "keysym", "")).lower()
        char = str(getattr(event, "char", "") or "")
        try:
            keycode = int(getattr(event, "keycode", -1))
        except (TypeError, ValueError):
            keycode = -1
        shortcuts = {
            "a": ({"a", "\x01"}, 65, None),
            "c": ({"c", "\x03"}, 67, "copy"),
            "v": ({"v", "\x16"}, 86, "paste"),
            "x": ({"x", "\x18"}, 88, "cut"),
        }
        key = next(
            (
                name
                for name, (chars, code, _action) in shortcuts.items()
                if keysym == name or char in chars or keycode == code
            ),
            None,
        )
        if key is None:
            return None
        self.target = event.widget
        _chars, _code, action = shortcuts[key]
        if action is None:
            return self._select_all_event(event)
        return self.handle(event, action)

    def perform(self, widget, action):
        if widget is None:
            return False
        try:
            return _operate(self.root, widget, action)
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
            return False

    def handle(self, event, action):
        self.target = event.widget
        self.perform(self.target, action)
        return "break"

    def select_all(self, widget):
        if widget is None:
            return "break"
        widget_class = _widget_class(widget)
        try:
            if widget_class == "Text":
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
            elif widget_class == "Listbox":
                widget.selection_set(0, "end")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
            widget.focus_set()
        except Exception:
            pass
        return "break"

    def _select_all_event(self, event):
        self.target = event.widget
        return self.select_all(event.widget)

    def show_menu(self, event):
        self.target = event.widget
        try:
            event.widget.focus_set()
            self.menu.tk_popup(event.x_root, event.y_root)
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
        finally:
            try:
                self.menu.grab_release()
            except Exception:
                pass
        return "break"


# ==============================================================================
# Standalone GUI Implementation
# ==============================================================================


class StandaloneNameChangerGUI:
    """Tkinter-based GUI for Standalone Name Changer with full Thai font support."""

    def __init__(self, root=None):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox

        self.root = root or self.tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("540x500")
        self.root.minsize(480, 420)

        self.manager = NameManager(DEFAULT_THAI_NAMES + DEFAULT_EN_NAMES)

        self.custom_name_var = self.tk.StringVar(value="")
        self.current_display_var = self.tk.StringVar(value=self.manager.current_name())
        self.status_var = self.tk.StringVar(value="พร้อมใช้งาน (Ready) - รองรับชื่อภาษาไทย")
        self.log_queue = queue.Queue()

        self._init_styles()
        self._build_ui()
        self.clipboard_controller = ClipboardController(self.root)
        self.clipboard_controller.install(self.root)
        self.root.after(100, self._process_log_queue)

    def _init_styles(self):
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass
        style.configure("TFrame", background=THEME["background"])
        style.configure(
            "TLabel",
            background=THEME["background"],
            foreground=THEME["text"],
            font=DEFAULT_FONT,
        )
        style.configure(
            "TLabelframe",
            background=THEME["background"],
            bordercolor=THEME["border"],
        )
        style.configure(
            "TLabelframe.Label",
            background=THEME["background"],
            foreground=THEME["accent"],
            font=TITLE_FONT,
        )
        style.configure(
            "TButton",
            padding=(8, 4),
            font=DEFAULT_FONT,
            background=THEME["surface_high"],
            foreground=THEME["text"],
        )
        style.configure(
            "Accent.TButton",
            padding=(8, 4),
            font=("Tahoma", 9, "bold"),
            background=THEME["accent"],
            foreground=THEME["button_text"],
        )

    def _build_ui(self):
        container = self.ttk.Frame(self.root, padding=12)
        container.pack(fill=self.tk.BOTH, expand=True)

        # Header Title
        header = self.ttk.Frame(container)
        header.pack(fill=self.tk.X, pady=(0, 10))
        lbl_title = self.tk.Label(
            header,
            text="Camfrog Name Changer (ระบบสลับชื่อภาษาไทย)",
            font=HEADER_FONT,
            bg=THEME["background"],
            fg=THEME["accent"],
        )
        lbl_title.pack(side=self.tk.LEFT)

        # Current Name Banner
        banner_frame = self.ttk.LabelFrame(
            container, text=" ชื่อที่ใช้งานปัจจุบัน (Active Name) ", padding=10
        )
        banner_frame.pack(fill=self.tk.X, pady=(0, 10))

        self.lbl_active = self.tk.Label(
            banner_frame,
            textvariable=self.current_display_var,
            font=("Tahoma", 14, "bold"),
            bg=THEME["surface"],
            fg=THEME["success"],
            relief=self.tk.FLAT,
            padx=10,
            pady=8,
        )
        self.lbl_active.pack(fill=self.tk.X)

        # Manual Switch Controls
        ctrl_frame = self.ttk.LabelFrame(
            container, text=" การควบคุมสลับชื่อ (Manual Switch) ", padding=10
        )
        ctrl_frame.pack(fill=self.tk.X, pady=(0, 10))

        btn_row = self.ttk.Frame(ctrl_frame)
        btn_row.pack(fill=self.tk.X, pady=(0, 8))

        btn_prev = self.ttk.Button(
            btn_row, text="◀ ชื่อก่อนหน้า (Prev)", command=self.on_switch_prev
        )
        btn_prev.pack(side=self.tk.LEFT, expand=True, fill=self.tk.X, padx=(0, 4))

        btn_next = self.ttk.Button(
            btn_row,
            text="ชื่อถัดไป (Next) ▶",
            style="Accent.TButton",
            command=self.on_switch_next,
        )
        btn_next.pack(side=self.tk.LEFT, expand=True, fill=self.tk.X, padx=(4, 0))

        # Direct Custom Name Switch
        custom_row = self.ttk.Frame(ctrl_frame)
        custom_row.pack(fill=self.tk.X, pady=(4, 0))

        self.ttk.Label(custom_row, text="เปลี่ยนเป็นชื่อ:").pack(
            side=self.tk.LEFT, padx=(0, 6)
        )
        self.entry_custom = self.ttk.Entry(
            custom_row, textvariable=self.custom_name_var, font=DEFAULT_FONT
        )
        self.entry_custom.pack(
            side=self.tk.LEFT, fill=self.tk.X, expand=True, padx=(0, 6)
        )
        self.entry_custom.bind("<Return>", lambda e: self.on_switch_custom())

        btn_apply = self.ttk.Button(
            custom_row, text="เปลี่ยนทันที (Apply)", command=self.on_switch_custom
        )
        btn_apply.pack(side=self.tk.RIGHT)

        # Name Preset List Manager
        list_frame = self.ttk.LabelFrame(
            container, text=" รายการชื่อในระบบ (Preset Pool) ", padding=8
        )
        list_frame.pack(fill=self.tk.BOTH, expand=True, pady=(0, 8))

        self.names_text = self.tk.Text(
            list_frame,
            wrap=self.tk.WORD,
            bg=THEME["surface"],
            fg=THEME["text"],
            font=("Tahoma", 9),
            relief=self.tk.FLAT,
            height=6,
        )
        self.names_text.pack(side=self.tk.LEFT, fill=self.tk.BOTH, expand=True)
        scrollbar = self.ttk.Scrollbar(list_frame, command=self.names_text.yview)
        scrollbar.pack(side=self.tk.RIGHT, fill=self.tk.Y)
        self.names_text.configure(yscrollcommand=scrollbar.set)

        # Populate default names in text box
        self._refresh_list_box()

        btn_list_box = self.ttk.Frame(container)
        btn_list_box.pack(fill=self.tk.X, pady=(0, 8))

        btn_update_pool = self.ttk.Button(
            btn_list_box, text="บันทึกรายการชื่อ (Update Pool)", command=self.on_update_pool
        )
        btn_update_pool.pack(side=self.tk.LEFT, padx=(0, 8))

        btn_reset_thai = self.ttk.Button(
            btn_list_box, text="โหลดค่าเริ่มต้นไทย (Reset Thai)", command=self.on_reset_thai
        )
        btn_reset_thai.pack(side=self.tk.LEFT)

        # Status Footer
        lbl_status = self.tk.Label(
            container,
            textvariable=self.status_var,
            font=("Tahoma", 8),
            bg=THEME["background"],
            fg=THEME["muted"],
            anchor=self.tk.W,
        )
        lbl_status.pack(fill=self.tk.X)

    def _refresh_list_box(self):
        self.names_text.delete("1.0", self.tk.END)
        self.names_text.insert(self.tk.END, "\n".join(self.manager.names))

    def on_switch_next(self):
        new_name = self.manager.switch_next()
        self.current_display_var.set(new_name)
        self.status_var.set(f"สลับชื่อถัดไปเรียบร้อย: {new_name}")

    def on_switch_prev(self):
        new_name = self.manager.switch_prev()
        self.current_display_var.set(new_name)
        self.status_var.set(f"สลับชื่อก่อนหน้าเรียบร้อย: {new_name}")

    def on_switch_custom(self):
        target = self.custom_name_var.get().strip()
        if not target:
            self.messagebox.showwarning("ข้อผิดพลาด", "กรุณาระบุชื่อที่ต้องการเปลี่ยน")
            return
        try:
            applied = self.manager.switch_to(target)
            self.current_display_var.set(applied)
            self.custom_name_var.set("")
            self._refresh_list_box()
            self.status_var.set(f"เปลี่ยนชื่อเป็น: {applied}")
        except ValueError as exc:
            self.messagebox.showerror("ผิดพลาด", str(exc))

    def on_update_pool(self):
        raw = self.names_text.get("1.0", self.tk.END).splitlines()
        clean = [line.strip() for line in raw if line.strip()]
        if not clean:
            self.messagebox.showerror("ผิดพลาด", "รายการชื่อต้องมีอย่างน้อย 1 ชื่อ")
            return
        try:
            self.manager.set_names(clean)
            self.current_display_var.set(self.manager.current_name())
            self.status_var.set(f"บันทึกรายการชื่อแล้วทั้งหมด {len(clean)} ชื่อ")
        except ValueError as exc:
            self.messagebox.showerror("ผิดพลาด", str(exc))

    def on_reset_thai(self):
        self.manager.set_names(DEFAULT_THAI_NAMES + DEFAULT_EN_NAMES)
        self._refresh_list_box()
        self.current_display_var.set(self.manager.current_name())
        self.status_var.set("รีเซ็ตเป็นชื่อเริ่มต้นภาษาไทยเรียบร้อย")

    def _process_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.status_var.set(msg)
        self.root.after(100, self._process_log_queue)


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone Camfrog Name Changer supporting Thai script and fonts."
    )
    parser.add_argument("--gui", action="store_true", help="force open GUI")
    parser.add_argument("--set-name", type=str, help="switch/set nickname directly")
    parser.add_argument(
        "--names",
        nargs="+",
        help="custom list of names to cycle through (supports Thai)",
    )
    parser.add_argument(
        "--cycle", action="store_true", help="switch to next name in list and print"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(BASE / ".env")
    if argv is None:
        argv = sys.argv[1:]

    # Open GUI by default if no arguments or --gui passed
    if len(argv) == 0 or "--gui" in argv:
        app = StandaloneNameChangerGUI()
        app.root.mainloop()
        return EXIT_OK

    args = build_parser().parse_args(argv)
    manager = NameManager(args.names if args.names else None)

    if args.set_name:
        try:
            applied = manager.switch_to(args.set_name)
            print(f"Name set to: {applied}")
            return EXIT_OK
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return EXIT_USAGE

    if args.cycle:
        nxt = manager.switch_next()
        print(f"Switched to next name: {nxt}")
        return EXIT_OK

    print(f"Current name: {manager.current_name()}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
