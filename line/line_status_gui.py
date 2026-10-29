"""Standalone Windows GUI for changing the signed-in LINE profile status."""
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import random
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from config_store import load_config, save_config, validate_status_text
from clipboard_support import ClipboardController, get_clipboard_text, set_clipboard_text
from line_automation import (
    process_for_window, read_profile_status, select_image_in_open_dialog,
    set_profile_display_name, set_profile_status,
)
from line_tray import LineTrayIcon
from schedule_engine import due_schedules, mark_schedule_fired
from windows_features import set_startup, validate_profile_image


if getattr(sys, "frozen", False):
    # In a one-file PyInstaller app, __file__ points inside its temporary
    # extraction directory. Keep editable configuration beside the EXE while
    # loading bundled image resources from that temporary directory.
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = APP_DIR
CONFIG_PATH = APP_DIR / "line_config.json"
ICON_PATH = RESOURCE_DIR / "app.ico"
LOGGER = logging.getLogger(__name__)


def friendly_task_error(task, error):
    """Map technical AutomationError text to actionable Thai guidance."""
    err = str(error or "")
    if "No LINE window is selected" in err or "Reopen LINE" in err:
        return ("ไม่พบหน้าต่าง LINE: เปิด LINE > Settings > Profile "
                "แล้วรอแถบสีเขียว 'พบหน้าต่าง LINE' ก่อนกดอีกครั้ง")
    if "was closed" in err:
        return ("หน้าต่าง LINE ถูกปิดไปแล้ว: เปิด LINE > Settings > Profile "
                "ใหม่ แล้วลองอีกครั้ง")
    if "no longer a LINE" in err or "changed (LINE restarted" in err:
        return ("หน้าต่าง LINE เปลี่ยนไป (อาจรีสตาร์ทหรือสลับหน้าต่าง): "
                "คลิกที่หน้าต่าง LINE Settings > Profile อีกครั้ง แล้วลองใหม่")
    if "exactly one" in err and "editor" in err:
        return ("หาช่อง Status message ไม่เจอ (หรือเจอหลายช่อง): เปิด LINE > "
                "Settings > Profile ให้เห็นช่องข้อความสถานะ แล้วลองใหม่ "
                f"({err})")
    if "unambiguous Status message" in err:
        return ("อ่านค่าข้อความสถานะจากหน้า Profile ไม่ได้: ตรวจว่า LINE "
                "อยู่หน้า Settings > Profile และเห็นช่องข้อความ แล้วลองใหม่")
    if "not foreground" in err:
        return ("หน้าต่าง LINE ไม่ได้อยู่ด้านหน้า: อย่าสลับหน้าต่างระหว่างทำงาน "
                "แล้วลองใหม่")
    if "Save button" in err:
        return ("หาปุ่ม Save ใน LINE ไม่เจอ (หรือมีหลายปุ่ม): เปิดหน้า Profile "
                "ให้เห็นปุ่มบันทึก แล้วลองใหม่ หรือใช้ Copy แล้วบันทึกเอง")
    if "file picker" in err:
        return ("หา file picker ของ LINE ไม่เจอ: เปิด file picker เลือกรูปใน LINE "
                "ก่อน แล้วค่อยกดปุ่มในโปรแกรมนี้")
    return f"งาน {task} ไม่สำเร็จ: {err}"

def configure_logging():
    """Keep diagnostics outside the one-file extraction directory."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    log_dir = base / "LINE Status Changer" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=512 * 1024, backupCount=3, encoding="utf-8"
        )
        logging.basicConfig(level=logging.INFO, handlers=[handler],
                            format="%(asctime)s %(levelname)s %(message)s")
        return log_dir / "app.log"
    except OSError:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.exception("Could not initialize the per-user log file")
        return None


class SingleInstanceGuard:
    """Prevent two tray apps from racing on the same sidecar config file."""
    ERROR_ALREADY_EXISTS = 183

    def __init__(self):
        self.handle = None
        self.kernel32 = None

    def acquire(self):
        if os.name != "nt":
            return True
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        self.kernel32.CreateMutexW.restype = ctypes.c_void_p
        self.kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        self.kernel32.CloseHandle.restype = ctypes.c_int
        ctypes.set_last_error(0)
        self.handle = self.kernel32.CreateMutexW(None, False, "Local\\LINEStatusChanger")
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "Could not create LINE Status Changer mutex")
        if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS:
            self.release()
            return False
        return True

    def release(self):
        if self.handle and self.kernel32:
            self.kernel32.CloseHandle(self.handle)
        self.handle = None


def build_app(config_path=CONFIG_PATH):
    import tkinter as tk
    from tkinter import messagebox, ttk

    class LineStatusChanger:
        def __init__(self, path):
            self.config_path = Path(path).resolve()
            self.config = load_config(self.config_path)
            self.root = tk.Tk()
            self.root.configure(background="#f4f1e8")
            self.root.title("LINE Status Changer")
            self.root.geometry("360x480")
            self.root.minsize(320, 240)
            self.root.resizable(True, True)
            self.root.report_callback_exception = self.report_callback_exception
            try:
                self.root.iconbitmap(str(ICON_PATH))
            except (tk.TclError, OSError):
                pass

            self.line_hwnd = None
            self.line_pid = None
            self.busy = False
            self.worker_results = queue.Queue()
            self.save_job = None
            self.schedule_job = None
            self.status_text = tk.StringVar(value=self.config.get("last_text", ""))
            self.name_text = tk.StringVar()
            self.preset_name = tk.StringVar()
            self.schedule_mode = tk.StringVar(value="weekly")
            self.schedule_time = tk.StringVar(value="09:00")
            self.schedule_interval = tk.StringVar(value="60")
            self.weekday_vars = [tk.BooleanVar(value=day < 5) for day in range(7)]
            self.startup_var = tk.BooleanVar(value=self.config.get("start_with_windows", False))
            self.hotkey_var = tk.BooleanVar(value=self.config.get("show_hotkey", False))
            self.status_text.trace_add("write", self._text_changed)
            self._build()
            self.clipboard_controller = ClipboardController(
                self.root, on_error=self._clipboard_error)
            self.clipboard_controller.install(self.root)
            self.tray = LineTrayIcon(self.root, self.restore, self.exit, ICON_PATH)
            if self.hotkey_var.get():
                try:
                    self.tray.set_show_hotkey(True)
                except Exception as exc:
                    self.hotkey_var.set(False)
                    self.config["show_hotkey"] = False
                    self.persist_config()
                    logging.warning("Could not register global hotkey: %s", exc)
                    self.note.configure(text=f"คีย์ลัด Ctrl+Alt+L ใช้ไม่ได้: {exc}")
            # A disabled startup setting is passive at launch. In particular,
            # opening the app with an alternate --config must not delete a Run
            # entry configured by the user's primary config; the checkbox's
            # explicit off action still removes this app's entry.
            if os.name == "nt" and self.startup_var.get():
                try:
                    set_startup(True, self.config_path)
                except Exception as exc:
                    logging.warning("Could not register Windows startup: %s", exc)
                    self.note.configure(text=f"ซิงก์การตั้งค่าเปิดพร้อม Windows ไม่สำเร็จ: {exc}")
            if os.name == "nt" and not self.tray.active:
                self.note.configure(
                    text="สร้างไอคอน system tray ไม่สำเร็จ: หน้าต่างจะย่อลง taskbar แทน"
                )
            self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
            self.root.after(250, self.poll_line)
            self.root.after(100, self.poll_worker_results)
            self.root.after(1000, self.poll_schedule)

        def report_callback_exception(self, exc_type, exc_value, traceback):
            LOGGER.error("Unhandled GUI callback exception",
                         exc_info=(exc_type, exc_value, traceback))
            try:
                if self.root.state() in {"iconic", "withdrawn"}:
                    self.note.configure(
                        text="เกิดข้อผิดพลาดเบื้องหลัง รายละเอียดอยู่ใน app.log"
                    )
                    return
                messagebox.showerror(
                    "LINE Status Changer",
                    "เกิดข้อผิดพลาดในหน้าต่างโปรแกรม รายละเอียดอยู่ใน app.log",
                    parent=self.root,
                )
            except (AttributeError, tk.TclError) as exc:
                LOGGER.debug("Could not show callback error dialog: %s", exc)

        def _clipboard_error(self, exc):
            logging.error("Clipboard operation failed: %s", exc)
            try:
                self.note.configure(text=f"คลิปบอร์ดผิดพลาด: {exc}")
            except Exception:
                pass

        def _build(self):
            style = ttk.Style(self.root)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("TFrame", background="#f4f1e8")
            style.configure("TLabel", background="#f4f1e8", foreground="#283648", font=("Segoe UI", 9))
            style.configure("TCheckbutton", background="#f4f1e8", foreground="#283648")
            style.configure("TLabelframe", background="#f4f1e8", bordercolor="#d5c8ac")
            style.configure("TLabelframe.Label", background="#f4f1e8", foreground="#8b672b", font=("Segoe UI", 9, "bold"))
            style.configure("TButton", padding=(7, 4), font=("Segoe UI", 9), background="#ebe5d9", foreground="#283648")
            style.map("TButton", background=[("active", "#d6e9e8"), ("pressed", "#d5c8ac")])
            style.configure("TEntry", padding=(4, 4), fieldbackground="#ffffff", foreground="#283648")
            style.configure("TNotebook", background="#f4f1e8")
            style.configure("TNotebook.Tab", padding=(11, 6), font=("Segoe UI", 9))
            style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), foreground="#8b672b")
            style.configure("Hint.TLabel", font=("Segoe UI", 8), foreground="#626d7a")
            style.configure("State.TLabel", font=("Segoe UI", 9, "bold"))

            outer = ttk.Frame(self.root, padding=10)
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="LINE PROFILE STATUS", style="Title.TLabel").pack(anchor="w")
            self.line_state = ttk.Label(outer, text="กำลังค้นหา LINE…", style="State.TLabel")
            self.line_state.pack(anchor="w", pady=(4, 8))
            self.tabs = ttk.Notebook(outer)
            self.tabs.pack(fill="both", expand=True)
            status_tab = ttk.Frame(self.tabs, padding=10)
            schedule_tab = ttk.Frame(self.tabs, padding=10)
            preset_tab = ttk.Frame(self.tabs, padding=10)
            tools_tab = ttk.Frame(self.tabs, padding=10)
            self.tabs.add(status_tab, text="Status")
            self.tabs.add(schedule_tab, text="Schedule")
            self.tabs.add(preset_tab, text="Presets")
            self.tabs.add(tools_tab, text="Tools & Settings")

            ttk.Label(status_tab, text=(
                "เปิด LINE > Settings > Profile > Status message แล้วคลิกช่องข้อความ "
                "ก่อนกด Set in LINE โปรแกรมจะเปลี่ยนเฉพาะเมื่อยืนยันช่องและปุ่ม Save ได้"),
                style="Hint.TLabel", wraplength=620, justify="left").pack(fill="x", pady=(0, 8))
            ttk.Label(status_tab, text="ข้อความสถานะที่จะตั้ง").pack(anchor="w")
            self.entry = ttk.Entry(status_tab, textvariable=self.status_text)
            self.entry.pack(fill="x", pady=(3, 6))
            self.entry.bind("<Return>", lambda _event: self.set_status())
            actions = ttk.Frame(status_tab)
            actions.pack(fill="x", pady=(0, 7))
            self.set_button = ttk.Button(actions, text="Set in LINE", command=self.set_status)
            self.set_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
            ttk.Button(actions, text="Copy", command=self.copy_text).pack(
                side="left", fill="x", expand=True, padx=4)
            ttk.Button(actions, text="Paste", command=self.paste_text).pack(
                side="left", fill="x", expand=True, padx=(4, 0))

            ttk.Label(status_tab, text="Saved messages", font=("Segoe UI", 9, "bold")).pack(anchor="w")
            list_frame = ttk.Frame(status_tab)
            list_frame.pack(fill="both", expand=True, pady=(4, 5))
            self.messages = tk.Listbox(list_frame, height=5, activestyle="dotbox",
                                       exportselection=False)
            self.messages.pack(side="left", fill="both", expand=True)
            scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.messages.yview)
            scrollbar.pack(side="right", fill="y")
            self.messages.configure(yscrollcommand=scrollbar.set)
            self.messages.bind("<<ListboxSelect>>", self.use_selected)
            list_buttons = ttk.Frame(status_tab)
            list_buttons.pack(fill="x")
            for label, callback in (("Use", self.use_selected), ("Random", self.use_random),
                                    ("Add current", self.add_message), ("Remove", self.remove_message)):
                ttk.Button(list_buttons, text=label, command=callback).pack(
                    side="left", fill="x", expand=True, padx=2)
            self._build_history(status_tab)

            self._build_schedules(schedule_tab)
            self._build_presets(preset_tab)
            self._build_tools(tools_tab)

            self.note = ttk.Label(outer, text="X: ซ่อนที่ system tray • Ctrl+Alt+L: แสดงหน้าต่าง",
                                  style="Hint.TLabel", wraplength=640, justify="center")
            self.note.pack(fill="x", pady=(7, 0))
            self.refresh_messages()
            self.refresh_history()
            self.refresh_presets()
            self.refresh_schedules()

        def _build_history(self, parent):
            import tkinter as tk
            from tkinter import ttk
            box = ttk.LabelFrame(parent, text="Recent status history", padding=6)
            box.pack(fill="both", expand=True, pady=(8, 0))
            row = ttk.Frame(box)
            row.pack(fill="both", expand=True)
            self.history = tk.Listbox(row, height=4, activestyle="dotbox", exportselection=False)
            self.history.pack(side="left", fill="both", expand=True)
            scroll = ttk.Scrollbar(row, orient="vertical", command=self.history.yview)
            scroll.pack(side="right", fill="y")
            self.history.configure(yscrollcommand=scroll.set)
            ttk.Button(box, text="Restore selected to editor", command=self.restore_history).pack(
                anchor="e", pady=(4, 0))

        def _build_schedules(self, parent):
            from tkinter import ttk
            ttk.Label(parent, text=(
                "Automatic schedule rules: weekly time or interval. For safety, LINE must be "
                "foreground with the verified Status message editor focused at run time; otherwise "
                "that occurrence is skipped."), style="Hint.TLabel", wraplength=620,
                justify="left").pack(fill="x", pady=(0, 10))
            form = ttk.LabelFrame(parent, text="New schedule uses the current status text", padding=8)
            form.pack(fill="x")
            ttk.Label(form, text="Type").grid(row=0, column=0, sticky="w")
            mode = ttk.Combobox(form, textvariable=self.schedule_mode,
                                 values=("weekly", "interval"), state="readonly", width=12)
            mode.grid(row=0, column=1, sticky="w", padx=(5, 14))
            mode.bind("<<ComboboxSelected>>", lambda _event: self._schedule_mode_changed())
            self.weekly_frame = ttk.Frame(form)
            self.weekly_frame.grid(row=0, column=2, sticky="w")
            ttk.Label(self.weekly_frame, text="Time").pack(side="left")
            ttk.Entry(self.weekly_frame, textvariable=self.schedule_time, width=7).pack(
                side="left", padx=4)
            self.interval_frame = ttk.Frame(form)
            ttk.Label(self.interval_frame, text="Every minutes").pack(side="left")
            ttk.Spinbox(self.interval_frame, from_=1, to=10080, increment=1,
                        textvariable=self.schedule_interval, width=8).pack(side="left", padx=4)
            self.days_frame = ttk.Frame(form)
            self.days_frame.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 2))
            for index, day in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
                ttk.Checkbutton(self.days_frame, text=day,
                                variable=self.weekday_vars[index]).pack(side="left", padx=(0, 7))
            controls = ttk.Frame(parent)
            controls.pack(fill="x", pady=8)
            ttk.Button(controls, text="Add schedule", command=self.add_schedule).pack(side="left")
            self.pause_button = ttk.Button(controls, command=self.toggle_schedule_pause)
            self.pause_button.pack(side="left", padx=6)
            ttk.Button(controls, text="Remove selected", command=self.remove_schedule).pack(side="left")
            self.schedule_tree = ttk.Treeview(parent, columns=("when", "message"),
                                              show="headings", selectmode="browse")
            self.schedule_tree.heading("when", text="When")
            self.schedule_tree.heading("message", text="Status text")
            self.schedule_tree.column("when", width=220, stretch=False)
            self.schedule_tree.column("message", width=360, stretch=True)
            self.schedule_tree.pack(fill="both", expand=True)
            self._schedule_mode_changed()

        def _build_presets(self, parent):
            from tkinter import ttk
            ttk.Label(parent, text="Store reusable profile messages as named presets.",
                      style="Hint.TLabel").pack(anchor="w", pady=(0, 8))
            ttk.Label(parent, text="Saved presets").pack(anchor="w")
            self.preset_box = ttk.Combobox(parent, textvariable=self.preset_name, state="normal")
            self.preset_box.pack(fill="x", pady=(3, 10))
            row = ttk.Frame(parent)
            row.pack(fill="x")
            ttk.Button(row, text="Use selected", command=self.use_preset).pack(side="left", padx=(0, 4))
            ttk.Button(row, text="Save current as preset", command=self.save_preset).pack(side="left", padx=4)
            ttk.Button(row, text="Remove selected", command=self.remove_preset).pack(side="left", padx=4)

        def _build_tools(self, parent):
            from tkinter import ttk
            windows = ttk.LabelFrame(parent, text="Windows", padding=8)
            windows.pack(fill="x", pady=(0, 8))
            ttk.Checkbutton(windows, text="Start LINE Status Changer with Windows",
                            variable=self.startup_var, command=self.toggle_startup).pack(anchor="w")
            ttk.Checkbutton(windows, text="Global hotkey Ctrl+Alt+L shows this window",
                            variable=self.hotkey_var, command=self.toggle_hotkey).pack(anchor="w", pady=(4, 0))

            read_box = ttk.LabelFrame(parent, text="Read current LINE status", padding=8)
            read_box.pack(fill="x", pady=(0, 8))
            ttk.Label(read_box, text="Open LINE Settings > Profile first.",
                      style="Hint.TLabel").pack(anchor="w")
            ttk.Button(read_box, text="Read status from LINE", command=self.read_status).pack(
                anchor="w", pady=4)
            self.read_status_value = ttk.Label(read_box, text="Not read", wraplength=580)
            self.read_status_value.pack(fill="x", anchor="w")

            name_box = ttk.LabelFrame(parent, text="Profile display name", padding=8)
            name_box.pack(fill="x", pady=(0, 8))
            ttk.Label(name_box, text="Open the LINE name editor and click its text field first.",
                      style="Hint.TLabel").pack(anchor="w")
            row = ttk.Frame(name_box)
            row.pack(fill="x", pady=(4, 0))
            ttk.Entry(row, textvariable=self.name_text).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Set name…", command=self.set_name).pack(side="left", padx=(6, 0))

            image_box = ttk.LabelFrame(parent, text="Profile and cover image helpers", padding=8)
            image_box.pack(fill="x")
            ttk.Label(image_box, text=(
                "First open LINE's photo/cover file picker. Choose an image here; the helper "
                "fills only one verified LINE-owned native Open dialog. Confirm crop/save in LINE."),
                style="Hint.TLabel", wraplength=600, justify="left").pack(anchor="w")
            row = ttk.Frame(image_box)
            row.pack(fill="x", pady=(6, 0))
            ttk.Button(row, text="Choose profile photo…",
                       command=lambda: self.choose_profile_image("profile photo")).pack(side="left")
            ttk.Button(row, text="Choose cover photo…",
                       command=lambda: self.choose_profile_image("cover photo")).pack(side="left", padx=6)

        def _schedule_mode_changed(self):
            if not hasattr(self, "weekly_frame"):
                return
            if self.schedule_mode.get() == "interval":
                self.weekly_frame.grid_remove()
                self.days_frame.grid_remove()
                self.interval_frame.grid(row=0, column=2, sticky="w")
            else:
                self.interval_frame.grid_remove()
                self.weekly_frame.grid()
                self.days_frame.grid()

        def _text_changed(self, *_args):
            if self.save_job is not None:
                try:
                    self.root.after_cancel(self.save_job)
                except tk.TclError:
                    pass
            self.save_job = self.root.after(500, self.save_last_text)

        def save_last_text(self):
            self.save_job = None
            try:
                self.config = load_config(self.config_path)
                self.config["last_text"] = self.status_text.get()
                save_config(self.config_path, self.config)
            except (OSError, ValueError) as exc:
                logging.exception("Could not save status draft")
                self.note.configure(text=f"บันทึกข้อความไม่ได้: {exc}")

        def refresh_messages(self):
            try:
                self.config = load_config(self.config_path)
                self.messages.delete(0, "end")
                for message in self.config["messages"]:
                    self.messages.insert("end", message)
            except (OSError, ValueError) as exc:
                logging.exception("Could not read saved status list")
                self.note.configure(text=f"อ่าน line_config.json ไม่ได้: {exc}")

        def persist_config(self):
            save_config(self.config_path, self.config)

        def refresh_history(self):
            self.config = load_config(self.config_path)
            self.history.delete(0, "end")
            for entry in reversed(self.config["history"]):
                stamp = entry["timestamp"].replace("T", " ")[:16]
                marker = {"verified": "✓", "scheduled": "⏱", "saved": "•"}[entry["outcome"]]
                text = entry["text"].replace("\n", " ")
                self.history.insert("end", f"{marker} {stamp}  {text[:110]}")

        def restore_history(self):
            selection = self.history.curselection()
            if not selection:
                return
            history = list(reversed(self.config["history"]))
            index = selection[0]
            if index < len(history):
                self.status_text.set(history[index]["text"])
                self.tabs.select(0)
                self.note.configure(text="กู้ข้อความลงช่องแก้ไขแล้ว ตรวจข้อความก่อนกด Set in LINE")

        def refresh_presets(self):
            self.config = load_config(self.config_path)
            names = list(self.config["presets"])
            self.preset_box.configure(values=names)
            if self.preset_name.get() not in names:
                self.preset_name.set(names[0] if names else "")

        def use_preset(self):
            name = self.preset_name.get()
            text = self.config["presets"].get(name)
            if text is None:
                self.note.configure(text="เลือก preset ที่ต้องการก่อน")
                return
            self.status_text.set(text)
            self.tabs.select(0)

        def save_preset(self):
            name = self.preset_name.get().strip()
            text = self.status_text.get()
            try:
                validate_status_text(text)
            except ValueError as exc:
                self.note.configure(text=str(exc))
                return
            if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
                self.note.configure(text="ใส่ชื่อ preset 1–40 ตัวอักษร")
                return
            existing = name in self.config["presets"]
            if existing and not messagebox.askyesno(
                "Update preset", f"แทนที่ preset {name!r} ด้วยข้อความปัจจุบันหรือไม่?",
                parent=self.root,
            ):
                return
            if not existing and len(self.config["presets"]) >= 25:
                self.note.configure(text="มี preset ครบ 25 รายการแล้ว ลบรายการเก่าก่อน")
                return
            self.config["presets"][name] = text
            try:
                self.persist_config()
                self.refresh_presets()
                self.note.configure(text=f"บันทึก preset {name!r} แล้ว")
            except (OSError, ValueError) as exc:
                logging.exception("Could not save preset")
                self.note.configure(text=f"บันทึก preset ไม่ได้: {exc}")

        def remove_preset(self):
            name = self.preset_name.get()
            if name not in self.config["presets"]:
                return
            if not messagebox.askyesno("Remove preset", f"ลบ preset {name!r} หรือไม่?",
                                       parent=self.root):
                return
            del self.config["presets"][name]
            try:
                self.persist_config()
                self.preset_name.set("")
                self.refresh_presets()
                self.note.configure(text="ลบ preset แล้ว")
            except (OSError, ValueError) as exc:
                logging.exception("Could not remove preset")
                self.note.configure(text=f"ลบ preset ไม่ได้: {exc}")

        def _schedule_label(self, item):
            if item["mode"] == "interval":
                return f"Every {item['interval_minutes']} min"
            days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            return f"{','.join(days[d] for d in item['days'])} @ {item['time']}"

        def refresh_schedules(self):
            if not hasattr(self, "schedule_tree"):
                return
            self.config = load_config(self.config_path)
            for row in self.schedule_tree.get_children():
                self.schedule_tree.delete(row)
            for item in self.config["schedule"]["items"]:
                self.schedule_tree.insert("", "end", iid=item["id"],
                    values=(self._schedule_label(item), item["text"][:160]))
            paused = self.config["schedule"]["paused"]
            self.pause_button.configure(text="Resume all" if paused else "Pause all")

        def add_schedule(self):
            text = self.status_text.get()
            try:
                validate_status_text(text)
            except ValueError as exc:
                self.note.configure(text=f"ใส่ข้อความในแท็บ Status ก่อน: {exc}")
                return
            mode = self.schedule_mode.get()
            item = {"id": uuid.uuid4().hex, "text": text, "mode": mode, "enabled": True}
            if mode == "weekly":
                try:
                    datetime.strptime(self.schedule_time.get().strip(), "%H:%M")
                except ValueError:
                    self.note.configure(text="เวลาใช้รูปแบบ 24 ชั่วโมง HH:MM เช่น 09:30")
                    return
                days = [i for i, var in enumerate(self.weekday_vars) if var.get()]
                if not days:
                    self.note.configure(text="เลือกวันอย่างน้อยหนึ่งวัน")
                    return
                item.update(time=self.schedule_time.get().strip(), days=days)
                when_text = f"{','.join(str(d + 1) for d in days)} เวลา {item['time']}"
            else:
                try:
                    minutes = int(self.schedule_interval.get())
                except (TypeError, ValueError):
                    minutes = 0
                if not 1 <= minutes <= 10080:
                    self.note.configure(text="ช่วงเวลาต้องอยู่ระหว่าง 1–10,080 นาที")
                    return
                item.update(time="", days=[], interval_minutes=minutes)
                when_text = f"ทุก {minutes} นาที"
            if len(self.config["schedule"]["items"]) >= 100:
                self.note.configure(text="มีตารางครบ 100 รายการแล้ว")
                return
            if not messagebox.askyesno(
                "Confirm automatic schedule",
                f"เพิ่มการเปลี่ยนข้อความแบบอัตโนมัติ {when_text} หรือไม่?\n\n{text}\n\n"
                "ณ เวลานั้น LINE ต้องอยู่ด้านหน้าและช่อง Status message ต้องถูกเลือก "
                "หากตรวจสอบช่องไม่ได้ โปรแกรมจะข้ามรายการเพื่อความปลอดภัย",
                parent=self.root,
            ):
                return
            schedule = self.config["schedule"]
            schedule["items"].append(item)
            if mode == "interval":
                schedule["last_fired"][item["id"]] = datetime.now().isoformat(timespec="seconds")
            try:
                self.persist_config()
                self.refresh_schedules()
                self.note.configure(text="เพิ่มตารางแล้ว — โปรแกรมทำงานต่อได้เมื่อซ่อนไว้ที่ system tray")
            except (OSError, ValueError) as exc:
                logging.exception("Could not add schedule")
                self.note.configure(text=f"บันทึกตารางไม่ได้: {exc}")

        def remove_schedule(self):
            selected = self.schedule_tree.selection()
            if not selected:
                return
            item_id = selected[0]
            if not messagebox.askyesno("Remove schedule", "ลบตารางที่เลือกหรือไม่?",
                                       parent=self.root):
                return
            self.config["schedule"]["items"] = [
                item for item in self.config["schedule"]["items"] if item["id"] != item_id
            ]
            self.config["schedule"]["last_fired"].pop(item_id, None)
            try:
                self.persist_config()
                self.refresh_schedules()
            except (OSError, ValueError) as exc:
                self.note.configure(text=f"ลบตารางไม่ได้: {exc}")

        def toggle_schedule_pause(self):
            self.config["schedule"]["paused"] = not self.config["schedule"]["paused"]
            try:
                self.persist_config()
                self.refresh_schedules()
                self.note.configure(text="ตารางพักชั่วคราว" if self.config["schedule"]["paused"]
                                    else "เปิดตารางทำงานแล้ว")
            except (OSError, ValueError) as exc:
                self.note.configure(text=f"ปรับตารางไม่ได้: {exc}")

        def poll_schedule(self):
            try:
                if not self.busy:
                    due = due_schedules(self.config["schedule"])
                    if due:
                        item, occurrence = due[0]
                        mark_schedule_fired(self.config["schedule"], item["id"], occurrence)
                        self.persist_config()
                        if not self.line_hwnd or not self.line_pid:
                            self.note.configure(text="ข้ามตาราง: ไม่พบหน้าต่าง LINE ที่ยืนยันแล้ว")
                        else:
                            self.note.configure(text="ถึงเวลาตาราง — ตรวจช่อง LINE ก่อนเปลี่ยนสถานะ…")
                            hwnd, pid = self.line_hwnd, self.line_pid
                            scheduled_text = item["text"]
                            self._start_worker(
                                "scheduled", lambda: set_profile_status(
                                    hwnd, pid, scheduled_text, activate=False
                                ), lambda result: self.finish_status(
                                    result, scheduled=True, text=scheduled_text
                                ),
                            )
            except Exception as exc:
                logging.exception("Schedule polling failed")
                self.note.configure(text=f"ตรวจตารางไม่สำเร็จ: {exc}")
            try:
                self.schedule_job = self.root.after(1000, self.poll_schedule)
            except tk.TclError:
                pass

        def toggle_startup(self):
            enabled = self.startup_var.get()
            previous = self.config["start_with_windows"]
            try:
                set_startup(enabled, self.config_path)
                self.config["start_with_windows"] = enabled
                self.persist_config()
                self.note.configure(text="บันทึกการเปิดพร้อม Windows แล้ว")
            except Exception as exc:
                logging.exception("Could not update Windows startup setting")
                self.startup_var.set(previous)
                self.config["start_with_windows"] = previous
                try:
                    set_startup(previous, self.config_path)
                except Exception:
                    logging.exception("Could not roll back Windows startup setting")
                self.note.configure(text=f"ปรับการเปิดพร้อม Windows ไม่สำเร็จ: {exc}")

        def toggle_hotkey(self):
            enabled = self.hotkey_var.get()
            previous = self.config["show_hotkey"]
            try:
                if not self.tray.set_show_hotkey(enabled):
                    raise RuntimeError("system tray host is unavailable")
                self.config["show_hotkey"] = enabled
                self.persist_config()
                self.note.configure(text="คีย์ลัด Ctrl+Alt+L แสดงหน้าต่าง" if enabled
                                    else "ปิดคีย์ลัดแล้ว")
            except Exception as exc:
                logging.exception("Could not update global hotkey")
                self.hotkey_var.set(previous)
                self.config["show_hotkey"] = previous
                try:
                    self.tray.set_show_hotkey(previous)
                except Exception:
                    logging.exception("Could not roll back global hotkey")
                self.note.configure(text=f"ตั้งคีย์ลัดไม่สำเร็จ: {exc}")

        def read_status(self):
            if not self._line_window_ready():
                self.note.configure(
                    text="เปิด LINE > Settings > Profile ก่อน แล้วรอแถบสีเขียว "
                         "'พบหน้าต่าง LINE' แล้วค่อยกดอ่าน")
                self.read_status_value.configure(text="ยังไม่พร้อมอ่าน: ไม่พบหน้าต่าง LINE")
                return
            hwnd, pid = self.line_hwnd, self.line_pid
            self.read_status_value.configure(text="กำลังอ่านจาก LINE… (อาจใช้เวลาหลายวินาที)")
            self._start_worker("read-status", lambda: read_profile_status(hwnd, pid),
                               lambda value: self.read_status_value.configure(text=value))

        def set_name(self):
            text = self.name_text.get()
            if not self._line_window_ready():
                self.note.configure(
                    text="เปิด LINE และหน้าแก้ชื่อโปรไฟล์ก่อน แล้วรอแถบสีเขียว "
                         "'พบหน้าต่าง LINE'")
                return
            if not messagebox.askyesno(
                "Confirm profile name", f"เปลี่ยนชื่อโปรไฟล์ LINE เป็นข้อความนี้หรือไม่?\n\n{text}",
                parent=self.root,
            ):
                return
            hwnd, pid = self.line_hwnd, self.line_pid
            self._start_worker("name", lambda: set_profile_display_name(hwnd, pid, text),
                lambda result: self.note.configure(text=(result.detail or "คลิก Save แล้ว ตรวจชื่อใน LINE")))

        def choose_profile_image(self, kind):
            from tkinter import filedialog
            if not self._line_window_ready():
                self.note.configure(text="เปิด LINE และ file picker ของรูปก่อน")
                return
            path = filedialog.askopenfilename(
                parent=self.root, title=f"Select LINE {kind}",
                filetypes=(("Image files", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")),
            )
            if not path:
                return
            if not self._line_window_ready():
                self.note.configure(
                    text="หน้าต่าง LINE เปลี่ยนไป: คลิกหน้าต่าง LINE อีกครั้งก่อนเลือกรูป")
                return
            try:
                image_path = validate_profile_image(path)
            except (OSError, ValueError) as exc:
                self.note.configure(text=str(exc))
                return
            if not messagebox.askyesno(
                f"Confirm {kind}",
                f"Select this file in LINE's open {kind} picker?\n\n{image_path}\n\n"
                "You will still need to confirm crop and save in LINE.",
                parent=self.root,
            ):
                return
            hwnd, pid = self.line_hwnd, self.line_pid
            self._start_worker("image", lambda: select_image_in_open_dialog(hwnd, pid, image_path),
                lambda _result: self.note.configure(
                    text=f"เลือกไฟล์แล้ว — ตรวจ crop และกด Save ใน LINE เพื่อใช้ {kind}"
                ))

        def use_selected(self, _event=None):
            selection = self.messages.curselection()
            if selection:
                self.status_text.set(self.messages.get(selection[0]))

        def use_random(self):
            if not self.config["messages"]:
                self.note.configure(text="ยังไม่มีข้อความในรายการที่บันทึกไว้")
                return
            self.status_text.set(random.choice(self.config["messages"]))
            self.note.configure(text="เลือกข้อความสุ่มแล้ว — ตรวจข้อความก่อนกด Set in LINE")

        def add_message(self):
            text = self.status_text.get()
            try:
                validate_status_text(text)
            except ValueError as exc:
                self.note.configure(text=str(exc))
                return
            try:
                config = load_config(self.config_path)
                if text not in config["messages"]:
                    config["messages"].append(text)
                config["last_text"] = text
                save_config(self.config_path, config)
                self.config = config
                self.refresh_messages()
                self.note.configure(text="เพิ่มข้อความลง line_config.json แล้ว")
            except (OSError, ValueError) as exc:
                logging.exception("Could not add saved status")
                self.note.configure(text=f"บันทึกไม่ได้: {exc}")

        def remove_message(self):
            selection = self.messages.curselection()
            if not selection:
                return
            index = selection[0]
            text = self.messages.get(index)
            if not messagebox.askyesno("Remove saved status", f"ลบข้อความนี้หรือไม่?\n\n{text}",
                                       parent=self.root):
                return
            try:
                config = load_config(self.config_path)
                if index < len(config["messages"]) and config["messages"][index] == text:
                    del config["messages"][index]
                    config["last_text"] = self.status_text.get()
                    save_config(self.config_path, config)
                    self.refresh_messages()
                    self.note.configure(text="ลบข้อความแล้ว")
                else:
                    self.refresh_messages()
                    self.note.configure(text="รายการเปลี่ยนไปแล้ว โหลดรายการใหม่ให้แล้ว")
            except (OSError, ValueError) as exc:
                logging.exception("Could not remove saved status")
                self.note.configure(text=f"ลบไม่ได้: {exc}")

        def copy_text(self):
            text = self.status_text.get()
            if not text:
                self.note.configure(text="ยังไม่มีข้อความให้คัดลอก")
                return
            try:
                set_clipboard_text(self.root, text)
                self.note.configure(text="คัดลอกข้อความแล้ว")
            except Exception as exc:
                logging.exception("Could not copy status to clipboard")
                self.note.configure(text=f"คัดลอกไม่ได้: {exc}")

        def paste_text(self):
            try:
                text = get_clipboard_text(self.root)
                if not text:
                    self.note.configure(text="คลิปบอร์ดไม่มีข้อความ")
                    return
                current = self.status_text.get()
                if self.entry.selection_present():
                    start = int(self.entry.index("sel.first"))
                    end = int(self.entry.index("sel.last"))
                else:
                    start = end = int(self.entry.index("insert"))
                pasted = current[:start] + text + current[end:]
                validate_status_text(pasted)
                self.status_text.set(pasted)
                self.entry.focus_set()
                self.entry.icursor(start + len(text))
                self.note.configure(text="วางข้อความจากคลิปบอร์ดแล้ว")
            except Exception as exc:
                logging.exception("Could not paste status from clipboard")
                self.note.configure(text=f"วางไม่ได้: {exc}")

        def poll_line(self):
            if os.name == "nt":
                try:
                    import win32gui

                    foreground = win32gui.GetForegroundWindow()
                    process = process_for_window(foreground)
                    if process:
                        self.line_hwnd = foreground
                        self.line_pid = process[0]
                    elif self.line_hwnd and win32gui.IsWindow(self.line_hwnd):
                        process = process_for_window(self.line_hwnd)
                        if not process or process[0] != self.line_pid:
                            self.line_hwnd = self.line_pid = None
                    if self.line_hwnd and self.line_pid:
                        self.line_state.configure(text="พบหน้าต่าง LINE — เปิดช่อง Status message ก่อนกด Set",
                                                  foreground="#28744c")
                    else:
                        self.line_state.configure(text="ยังไม่พบหน้าต่าง LINE", foreground="#9f3740")
                except Exception:
                    logging.exception("LINE window polling failed")
                    self.line_state.configure(text="ตรวจ LINE ไม่สำเร็จ", foreground="#9f3740")
            else:
                self.line_state.configure(text="ตัวตั้งสถานะนี้ต้องใช้บน Windows", foreground="#9f3740")
            try:
                self.root.after(900, self.poll_line)
            except tk.TclError:
                pass

        def set_status(self):
            if self.busy:
                return
            text = self.status_text.get()
            try:
                validate_status_text(text)
            except ValueError as exc:
                self.note.configure(text=str(exc))
                return
            if not self._line_window_ready():
                messagebox.showinfo("LINE not found", "เปิด LINE แล้วไปที่หน้าแก้ Status message ก่อน",
                                    parent=self.root)
                return
            confirm = messagebox.askyesno(
                "Set LINE profile status",
                "เปลี่ยนข้อความสถานะใน LINE เป็นข้อความนี้หรือไม่?\n\n"
                "ข้อความสถานะแสดงบนโปรไฟล์ LINE ของคุณ\n\n" + text,
                parent=self.root,
            )
            if not confirm:
                return
            hwnd, pid = self.line_hwnd, self.line_pid
            self.note.configure(text="กำลังตรวจช่อง LINE (อาจใช้เวลาหลายวินาที) อย่าสลับหน้าต่าง…")
            self._start_worker("status", lambda: set_profile_status(hwnd, pid, text),
                               lambda result: self.finish_status(result, scheduled=False, text=text))

        def _start_worker(self, task, operation, on_success):
            if self.busy:
                self.note.configure(text="กำลังทำงานอยู่ รอให้รายการปัจจุบันเสร็จก่อน")
                return False
            self.busy = True
            self.set_button.configure(state="disabled")
            self.note.configure(text=f"กำลังทำงาน: {task}…")

            def work():
                pythoncom = None
                value, error = None, None
                try:
                    import pythoncom as _pythoncom
                    pythoncom = _pythoncom
                    pythoncom.CoInitialize()
                    value = operation()
                except Exception as exc:
                    error = str(exc)
                    logging.exception("LINE background task failed: %s", task)
                finally:
                    if pythoncom is not None:
                        try:
                            pythoncom.CoUninitialize()
                        except Exception:
                            logging.exception("COM cleanup failed for task %s", task)
                self.worker_results.put((task, on_success, value, error))

            try:
                threading.Thread(target=work, daemon=True, name=f"line-{task}").start()
                return True
            except RuntimeError as exc:
                logging.exception("Could not start LINE task %s", task)
                self.busy = False
                self.set_button.configure(state="normal")
                self.note.configure(text=f"เริ่มงานไม่ได้: {exc}", foreground="#9f3740")
                return False

        def finish_status(self, result, scheduled=False, text=None):
            self.busy = False
            self.set_button.configure(state="normal")
            if result.save_clicked:
                outcome = "verified" if result.verified else ("scheduled" if scheduled else "saved")
                try:
                    self.config = load_config(self.config_path)
                    self.config["history"].append({
                        "text": text if text is not None else self.status_text.get(),
                        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "outcome": outcome,
                    })
                    self.config["history"] = self.config["history"][-500:]
                    self.persist_config()
                    self.refresh_history()
                except Exception:
                    logging.exception("Could not record LINE status history")
                if result.verified:
                    note = "เปลี่ยนและตรวจยืนยันข้อความใน LINE แล้ว"
                    color = "#28744c"
                else:
                    note = "กด Save แล้ว แต่ยืนยันผลจาก LINE ไม่ได้ โปรดตรวจโปรไฟล์อีกครั้ง"
                    if result.detail:
                        note += f" ({result.detail})"
                    color = "#8b672b"
            else:
                note = "ใส่ข้อความแล้ว แต่ยังไม่ได้บันทึก: " + (result.detail or "คลิก Save ใน LINE")
                color = "#8b672b"
            self.note.configure(text=note, foreground=color)

        def _friendly_task_error(self, task, error):
            """Map technical AutomationError text to actionable Thai guidance."""
            return friendly_task_error(task, error)

        def _line_window_ready(self):
            """Pre-flight check: is there a tracked LINE window right now?"""
            hwnd, pid = self.line_hwnd, self.line_pid
            if not hwnd or not pid:
                return False
            if os.name != "nt":
                return False
            try:
                import win32gui
                from line_automation import process_for_window
                if not win32gui.IsWindow(hwnd):
                    return False
                proc = process_for_window(hwnd)
                return bool(proc and proc[0] == pid)
            except Exception:
                return False

        def poll_worker_results(self):
            try:
                while True:
                    task, callback, value, error = self.worker_results.get_nowait()
                    self.busy = False
                    self.set_button.configure(state="normal")
                    if error:
                        if task == "read-status":
                            self.read_status_value.configure(
                                text=f"อ่านไม่ได้: {self._friendly_task_error(task, error)}")
                        self.note.configure(text=self._friendly_task_error(task, error),
                                            foreground="#9f3740")
                    else:
                        callback(value)
            except queue.Empty:
                pass
            except Exception:
                logging.exception("Could not process a background result")
            try:
                self.root.after(100, self.poll_worker_results)
            except tk.TclError:
                pass

        def restore(self):
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()

        def hide_to_tray(self):
            if self.tray.active:
                self.root.withdraw()
            else:
                if os.name == "nt" and getattr(self.tray, "error", None):
                    self.note.configure(text="Tray ไม่พร้อมใช้งาน หน้าต่างย่อลง taskbar แทน")
                self.root.iconify()

        def exit(self):
            if self.busy and not messagebox.askyesno(
                "LINE update in progress",
                "กำลังแก้ข้อความใน LINE การออกตอนนี้อาจทิ้งข้อความที่ยังไม่บันทึก ออกจากโปรแกรมหรือไม่?",
                parent=self.root,
            ):
                return
            if self.save_job is not None:
                try:
                    self.root.after_cancel(self.save_job)
                except tk.TclError:
                    pass
            self.save_last_text()
            self.tray.shutdown()
            self.root.destroy()

        def mainloop(self):
            self.root.mainloop()

    return LineStatusChanger


def main(argv=None):
    import argparse

    log_path = configure_logging()
    parser = argparse.ArgumentParser(description="LINE profile status editor")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help="use an alternate line_config.json")
    args = parser.parse_args(argv)
    instance = SingleInstanceGuard()
    try:
        if not instance.acquire():
            from tkinter import messagebox
            messagebox.showinfo(
                "LINE Status Changer is already running",
                "The app is already open. Check the Windows system tray.",
            )
            return 0
        app = build_app()(args.config)
        app.mainloop()
        return 0
    except Exception as exc:
        logging.exception("LINE Status Changer startup or main loop failed")
        try:
            import tkinter.messagebox as messagebox
            detail = f"{exc}\n\nLog: {log_path}" if log_path else str(exc)
            messagebox.showerror("LINE Status Changer", detail)
        except Exception:
            print(f"LINE Status Changer failed: {exc}")
        return 1
    finally:
        instance.release()


if __name__ == "__main__":
    sys.exit(main())
