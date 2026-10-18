"""Bilingual Tk interface for an assigned ZChat agent's internal chat."""

from __future__ import annotations

import os
import queue
import sys
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, ClassVar

from camfrog_control_server.transport import normalize_agent_token, validate_server_url

from .process_monitor import ProcessMonitorPage
from .session import AgentChatWorker, WorkerEvent

DEFAULT_SERVER_URL = ""

# Keep this palette synchronized with WEB_STATUS_THEME in web_status_gui.py.
PALETTE = {
    "background": "#f4f1e8",
    "surface": "#fffdfa",
    "surface_high": "#ebe5d9",
    "entry": "#ffffff",
    "border": "#d5c8ac",
    "accent": "#8b672b",
    "accent_hover": "#76541f",
    "gold": "#8b672b",
    "gold_hover": "#76541f",
    "teal": "#d6e9e8",
    "button_text": "#283648",
    "button_active": "#d5c8ac",
    "button_pressed": "#c2b392",
    "text": "#283648",
    "muted": "#626d7a",
    "danger": "#9f3740",
    "success": "#28744c",
    "error": "#a3303a",
    "log_text": "#2c3b4d",
}

TEXT = {
    "en": {
        "title": "ZChat Agent Chat",
        "control_server": "Control Server",
        "server_url": "Server URL",
        "agent_token": "Agent token",
        "connect": "Connect",
        "disconnect": "Disconnect",
        "conversation": "Conversation",
        "vip_membership": "VIP Membership",
        "vip_unknown": "VIP: Not verified",
        "vip_free": "Free",
        "vip_pro": "Pro VIP",
        "vip_extreme": "Extreme VIP",
        "vip_gold": "Gold VIP",
        "membership_detail": "Evidence: {evidence} · {record}",
        "membership_expiry": " · Valid until {expires_at}",
        "vip_disclaimer": "Control-plane record only; Camfrog has not been checked live.",
        "evidence_unknown": "Unknown",
        "evidence_operator_report": "Operator reported",
        "evidence_official_record": "Official record",
        "evidence_manual_entry": "Manual record",
        "record_temporary_overlay": "Temporary record",
        "record_durable_baseline": "Baseline record",
        "record_overlapping_overlays": "Conflicting records",
        "record_baseline_not_recorded": "No entitlement record",
        "record_baseline_unverified": "Baseline not verified",
        "message": "Message",
        "send": "Send",
        "speaker_you": "You",
        "speaker_operator": "Operator",
        "speaker_status": "Status",
        "language": "ไทย",
        "help": (
            "Enter the HTTPS origin of your control server. The token and messages stay "
            "in memory only. Use Ctrl+C/V/A/X or right-click to edit; Ctrl+Enter sends."
        ),
        "disconnected": "Disconnected",
        "connecting": "Connecting…",
        "checking_assignment": "Connected; checking account assignment…",
        "connected": "Connected",
        "assign_account": "Connected; assign an account to this agent",
        "disconnecting": "Disconnecting…",
        "no_account": "No account assigned",
        "account": "Account: {nickname}",
        "token_rejected": "Token rejected or revoked; reconnect with a newly issued token",
        "auth_invalid": "Authentication is no longer valid",
        "retrying": "Connection issue; retrying in {seconds:.1f}s",
        "send_accepted": "Message {message_id} accepted by the control server",
        "send_rejected": "Message {message_id} was rejected by the server",
        "send_canceled": "Message {message_id} was canceled because the account assignment changed",
        "sending": "Sending message {message_id}…",
        "ack_failed": "Could not acknowledge message {message_id}",
        "invalid_server_title": "Invalid server URL",
        "invalid_token_title": "Invalid agent token",
        "token_required_title": "Agent token required",
        "url_absolute": "Server URL must be an absolute HTTP(S) URL.",
        "url_host": "Server URL must include a valid host and port.",
        "url_credentials": "Server URL cannot include credentials, query, or fragment.",
        "url_https": "HTTPS is required except for loopback development URLs.",
        "url_origin": "Enter the control server origin only; remove the URL path.",
        "message_empty": "Message body cannot be empty or whitespace only.",
        "message_control": "Message body cannot contain control characters.",
        "message_too_long": "Message body must contain at most 4000 characters.",
        "account_required": "An assigned account is required to send a message.",
        "token_invalid": "Paste the raw one-time-issued agent token without a Bearer prefix.",
        "request_rejected": "Control server rejected the request (HTTP {status}{diagnostic}).",
        "session_stopped": "Chat session stopped ({error_type}).",
        "error": "{message}",
        "invalid_message_title": "Invalid message",
        "cut": "Cut",
        "copy": "Copy",
        "paste": "Paste",
        "select_all": "Select all",
    },
    "th": {
        "title": "แชตเอเจนต์ ZChat",
        "control_server": "เซิร์ฟเวอร์ควบคุม",
        "server_url": "URL เซิร์ฟเวอร์",
        "agent_token": "โทเคนเอเจนต์",
        "connect": "เชื่อมต่อ",
        "disconnect": "ตัดการเชื่อมต่อ",
        "conversation": "บทสนทนา",
        "vip_membership": "สถานะ VIP",
        "vip_unknown": "VIP: ยังยืนยันไม่ได้",
        "vip_free": "Free",
        "vip_pro": "Pro VIP",
        "vip_extreme": "Extreme VIP",
        "vip_gold": "Gold VIP",
        "membership_detail": "หลักฐาน: {evidence} · ประเภทข้อมูล: {record}",
        "membership_expiry": " · ใช้ได้ถึง {expires_at}",
        "vip_disclaimer": "ข้อมูลจาก control plane เท่านั้น ยังไม่ได้ตรวจสอบ Camfrog แบบสด",
        "evidence_unknown": "ไม่ทราบ",
        "evidence_operator_report": "เจ้าหน้าที่รายงาน",
        "evidence_official_record": "ระเบียนทางการ",
        "evidence_manual_entry": "บันทึกด้วยตนเอง",
        "record_temporary_overlay": "ระเบียนชั่วคราว",
        "record_durable_baseline": "ระเบียนพื้นฐาน",
        "record_overlapping_overlays": "ระเบียนขัดแย้งกัน",
        "record_baseline_not_recorded": "ยังไม่มีระเบียนสิทธิ์",
        "record_baseline_unverified": "ยังไม่ยืนยันระเบียนพื้นฐาน",
        "message": "ข้อความ",
        "send": "ส่ง",
        "speaker_you": "คุณ",
        "speaker_operator": "เจ้าหน้าที่",
        "speaker_status": "สถานะ",
        "language": "English",
        "help": (
            "กรอก URL ต้นทางแบบ HTTPS ของ control server โทเคนและข้อความเก็บในหน่วยความจำ "
            "ใช้ Ctrl+C/V/A/X หรือคลิกขวาเพื่อแก้ไข และ Ctrl+Enter เพื่อส่งข้อความ"
        ),
        "disconnected": "ไม่ได้เชื่อมต่อ",
        "connecting": "กำลังเชื่อมต่อ…",
        "checking_assignment": "เชื่อมต่อแล้ว กำลังตรวจสอบบัญชีที่กำหนด…",
        "connected": "เชื่อมต่อแล้ว",
        "assign_account": "เชื่อมต่อแล้ว โปรดกำหนดบัญชีให้เอเจนต์นี้",
        "disconnecting": "กำลังตัดการเชื่อมต่อ…",
        "no_account": "ยังไม่ได้กำหนดบัญชี",
        "account": "บัญชี: {nickname}",
        "token_rejected": "โทเคนถูกปฏิเสธหรือเพิกถอน โปรดเชื่อมต่อด้วยโทเคนใหม่",
        "auth_invalid": "การยืนยันตัวตนใช้ไม่ได้แล้ว",
        "retrying": "การเชื่อมต่อมีปัญหา กำลังลองใหม่ใน {seconds:.1f} วินาที",
        "send_accepted": "เซิร์ฟเวอร์ควบคุมรับข้อความ {message_id} แล้ว",
        "send_rejected": "เซิร์ฟเวอร์ปฏิเสธข้อความ {message_id}",
        "send_canceled": "ยกเลิกข้อความ {message_id} เนื่องจากมีการเปลี่ยนบัญชีที่กำหนด",
        "sending": "กำลังส่งข้อความ {message_id}…",
        "ack_failed": "ยืนยันการรับข้อความ {message_id} ไม่สำเร็จ",
        "invalid_server_title": "URL เซิร์ฟเวอร์ไม่ถูกต้อง",
        "invalid_token_title": "โทเคนเอเจนต์ไม่ถูกต้อง",
        "token_required_title": "ต้องระบุโทเคนเอเจนต์",
        "url_absolute": "URL เซิร์ฟเวอร์ต้องเป็น HTTP(S) แบบเต็ม",
        "url_host": "URL เซิร์ฟเวอร์ต้องมีชื่อโฮสต์และพอร์ตที่ถูกต้อง",
        "url_credentials": "ห้ามใส่ข้อมูลเข้าสู่ระบบ query หรือ fragment ใน URL เซิร์ฟเวอร์",
        "url_https": "ต้องใช้ HTTPS ยกเว้น URL loopback สำหรับการพัฒนา",
        "url_origin": "กรอกเฉพาะ URL ต้นทางของ control server โดยลบ path ออก",
        "message_empty": "ข้อความต้องไม่ว่างเปล่าหรือมีแต่ช่องว่าง",
        "message_control": "ข้อความต้องไม่มีอักขระควบคุม",
        "message_too_long": "ข้อความต้องมีความยาวไม่เกิน 4000 ตัวอักษร",
        "account_required": "ต้องกำหนดบัญชีก่อนส่งข้อความ",
        "token_invalid": "วางโทเคนเอเจนต์ที่ออกให้โดยตรง โดยไม่ใส่ Bearer นำหน้า",
        "request_rejected": "เซิร์ฟเวอร์ควบคุมปฏิเสธคำขอ (HTTP {status}{diagnostic})",
        "session_stopped": "เซสชันแชตหยุดทำงาน ({error_type})",
        "error": "{message}",
        "invalid_message_title": "ข้อความไม่ถูกต้อง",
        "cut": "ตัด",
        "copy": "คัดลอก",
        "paste": "วาง",
        "select_all": "เลือกทั้งหมด",
    },
}


def _application_icon_path() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = Path(
            getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)
        )
    else:
        bundle_root = Path(__file__).resolve().parents[2]
    return bundle_root / "app.ico"


def _apply_application_icon(root: tk.Widget) -> None:
    if sys.platform != "win32":
        return
    # iconbitmap() is a toplevel-only call; embedding frames (e.g. inside
    # UnifiedClientWindow) have no such attribute and must not raise.
    if not isinstance(root, (tk.Tk, tk.Toplevel)):
        return

    icon_path = _application_icon_path()
    if not icon_path.is_file():
        return
    try:
        root.iconbitmap(str(icon_path))
    except (OSError, tk.TclError):
        return


class AgentChatWindow:
    MAX_VISIBLE_ENTRIES = 500
    PALETTE = PALETTE
    _EDITABLE_CLASSES: ClassVar[frozenset[str]] = frozenset({"Entry", "TEntry", "Text"})

    def __init__(self, root: tk.Widget, language: tk.StringVar | None = None) -> None:
        self.root = root
        _apply_application_icon(root)
        self.language = language if language is not None else tk.StringVar(value="en")
        self._set_window_title(TEXT["en"]["title"])
        try:
            self.root.geometry("760x600")  # type: ignore[attr-defined]
            self.root.minsize(560, 420)  # type: ignore[attr-defined]
        except (AttributeError, tk.TclError):
            pass  # embedded as a page: toplevel sizing does not apply
        self.worker: AgentChatWorker | None = None
        self._entries: deque[str] = deque(maxlen=self.MAX_VISIBLE_ENTRIES)
        self._closing = False
        self._current_account_id: str | None = None
        self.context_target: tk.Widget | None = None
        self._status_key = "disconnected"
        self._status_data: dict[str, Any] = {}
        self._assignment_key = "no_account"
        self._assignment_data: dict[str, Any] = {}
        self._membership_tier = "unknown"
        self._membership_source = "baseline_not_recorded"
        self._membership_evidence_source = "unknown"
        self._membership_expires_at: Any = None

        self.server_url = tk.StringVar(value=DEFAULT_SERVER_URL)
        self.agent_token = tk.StringVar()
        self.status = tk.StringVar(value=TEXT["en"]["disconnected"])
        self.assignment = tk.StringVar(value=TEXT["en"]["no_account"])

        self._configure_theme()
        self._build_widgets()
        self._install_editing_support()
        try:
            self.root.protocol("WM_DELETE_WINDOW", self._close)  # type: ignore[attr-defined]
        except (AttributeError, tk.TclError):
            pass  # embedded as a page: close is owned by the shell
        self.root.after(100, self._drain_events)

    def _set_window_title(self, title: str) -> None:
        setter = getattr(self.root, "title", None)
        if callable(setter):
            try:
                setter(title)
            except (AttributeError, tk.TclError):
                pass  # embedded as a page: no toplevel title

    def _configure_theme(self) -> None:
        try:
            self.root.configure(background=PALETTE["background"])
        except (AttributeError, tk.TclError):
            pass  # embedded as a page: ttk containers have no -background option
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=PALETTE["background"])
        style.configure(
            "Surface.TFrame",
            background=PALETTE["surface_high"],
        )
        style.configure("Membership.TFrame", background=PALETTE["surface_high"])
        style.configure(
            "TLabel",
            background=PALETTE["background"],
            foreground=PALETTE["text"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Brand.TLabel",
            background=PALETTE["surface_high"],
            foreground=PALETTE["text"],
            font=("Segoe UI", 12, "bold"),
        )
        style.configure(
            "MembershipTitle.TLabel",
            background=PALETTE["surface_high"],
            foreground=PALETTE["accent"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "MembershipDetail.TLabel",
            background=PALETTE["surface_high"],
            foreground=PALETTE["text"],
            font=("Segoe UI", 8),
        )
        style.configure(
            "MembershipHint.TLabel",
            background=PALETTE["surface_high"],
            foreground=PALETTE["muted"],
            font=("Segoe UI", 8),
        )
        style.configure(
            "MembershipVIP.TLabel",
            background=PALETTE["gold"],
            foreground=PALETTE["surface"],
            padding=(8, 3),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "MembershipNeutral.TLabel",
            background=PALETTE["surface"],
            foreground=PALETTE["muted"],
            padding=(8, 3),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Hint.TLabel",
            background=PALETTE["background"],
            foreground=PALETTE["muted"],
            font=("Segoe UI", 8),
        )
        style.configure(
            "Status.TLabel",
            background=PALETTE["background"],
            foreground=PALETTE["accent_hover"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "TLabelframe",
            background=PALETTE["background"],
            bordercolor=PALETTE["border"],
        )
        style.configure(
            "TLabelframe.Label",
            background=PALETTE["background"],
            foreground=PALETTE["accent"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "TButton",
            padding=(7, 4),
            font=("Segoe UI", 9),
            background=PALETTE["surface_high"],
            foreground=PALETTE["button_text"],
        )
        style.map(
            "TButton",
            background=[
                ("disabled", PALETTE["surface_high"]),
                ("active", PALETTE["button_active"]),
                ("pressed", PALETTE["button_pressed"]),
            ],
            foreground=[("disabled", PALETTE["muted"])],
        )
        style.configure(
            "Primary.TButton",
            padding=(12, 6),
            font=("Segoe UI", 9, "bold"),
            background=PALETTE["accent"],
            foreground=PALETTE["surface"],
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", PALETTE["surface_high"]),
                ("active", PALETTE["accent_hover"]),
                ("pressed", PALETTE["accent_hover"]),
            ],
            foreground=[
                ("disabled", PALETTE["muted"]),
                ("!disabled", PALETTE["surface"]),
            ],
        )
        style.configure(
            "TEntry",
            padding=(4, 4),
            fieldbackground=PALETTE["entry"],
            foreground=PALETTE["text"],
        )

    def _build_widgets(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container, style="Surface.TFrame", padding=(10, 7))
        header.pack(fill=tk.X, pady=(0, 8))
        self.title_label = ttk.Label(header, style="Brand.TLabel")
        self.title_label.pack(side=tk.LEFT)
        self.language_button = ttk.Button(header, command=self._toggle_language)
        self.language_button.pack(side=tk.RIGHT)

        self.connection = ttk.LabelFrame(container, padding=10)
        self.connection.pack(fill=tk.X)
        self.connection.columnconfigure(1, weight=1)

        self.server_label = ttk.Label(self.connection)
        self.server_label.grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.server_entry = ttk.Entry(self.connection, textvariable=self.server_url)
        self.server_entry.grid(row=0, column=1, sticky=tk.EW, pady=3)

        self.token_label = ttk.Label(self.connection)
        self.token_label.grid(row=1, column=0, sticky=tk.W, padx=(0, 8))
        self.token_entry = ttk.Entry(
            self.connection, textvariable=self.agent_token, show="*"
        )
        self.token_entry.grid(row=1, column=1, sticky=tk.EW, pady=3)

        self.connect_button = ttk.Button(
            self.connection,
            style="Primary.TButton",
            command=self._connect,
        )
        self.connect_button.grid(row=0, column=2, rowspan=2, padx=(10, 0), sticky=tk.NS)

        status_row = ttk.Frame(container)
        status_row.pack(fill=tk.X, pady=(8, 8))
        ttk.Label(status_row, textvariable=self.status, style="Status.TLabel").pack(
            side=tk.LEFT
        )
        ttk.Label(status_row, textvariable=self.assignment).pack(side=tk.RIGHT)

        self.membership_frame = ttk.Frame(
            container, style="Membership.TFrame", padding=(10, 7)
        )
        self.membership_frame.pack(fill=tk.X, pady=(0, 8))
        self.membership_frame.columnconfigure(0, weight=1)
        self.membership_title = ttk.Label(
            self.membership_frame, style="MembershipTitle.TLabel"
        )
        self.membership_title.grid(row=0, column=0, sticky=tk.W)
        self.membership_badge = ttk.Label(self.membership_frame)
        self.membership_badge.grid(row=0, column=1, sticky=tk.E)
        self.membership_detail = ttk.Label(
            self.membership_frame, style="MembershipDetail.TLabel"
        )
        self.membership_detail.grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(4, 0)
        )
        self.membership_disclaimer = ttk.Label(
            self.membership_frame, style="MembershipHint.TLabel"
        )
        self.membership_disclaimer.grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(2, 0)
        )

        self.transcript_frame = ttk.LabelFrame(container, padding=6)
        self.transcript_frame.pack(fill=tk.BOTH, expand=True)
        self.transcript_frame.rowconfigure(0, weight=1)
        self.transcript_frame.columnconfigure(0, weight=1)
        self.transcript = tk.Text(
            self.transcript_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            undo=False,
            height=18,
            padx=8,
            pady=8,
            background=PALETTE["surface"],
            foreground=PALETTE["log_text"],
            insertbackground=PALETTE["accent"],
            selectbackground=PALETTE["border"],
            selectforeground=PALETTE["text"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["accent"],
            relief=tk.FLAT,
        )
        scrollbar = ttk.Scrollbar(
            self.transcript_frame,
            orient=tk.VERTICAL,
            command=self.transcript.yview,
        )
        self.transcript.configure(yscrollcommand=scrollbar.set)
        self.transcript.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)

        composer = ttk.Frame(container)
        composer.pack(fill=tk.X, pady=(8, 0))
        composer.columnconfigure(0, weight=1)
        self.message_label = ttk.Label(composer)
        self.message_label.grid(row=0, column=0, sticky=tk.W, pady=(0, 3))
        self.message_input = tk.Text(
            composer,
            height=3,
            wrap=tk.WORD,
            undo=False,
            background=PALETTE["entry"],
            foreground=PALETTE["text"],
            insertbackground=PALETTE["accent"],
            selectbackground=PALETTE["border"],
            selectforeground=PALETTE["text"],
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["accent"],
            relief=tk.FLAT,
        )
        self.message_input.grid(row=1, column=0, sticky=tk.EW)
        self.message_input.bind("<Control-Return>", self._send_event)
        self.send_button = ttk.Button(
            composer,
            style="Primary.TButton",
            command=self._send,
        )
        self.send_button.grid(row=1, column=1, sticky=tk.NS, padx=(8, 0))
        self.help_label = ttk.Label(container, style="Hint.TLabel", wraplength=700)
        self.help_label.pack(anchor=tk.W, pady=(6, 0))

        self.context_menu = tk.Menu(
            self.root,
            tearoff=False,
            background=PALETTE["surface"],
            foreground=PALETTE["text"],
            activebackground=PALETTE["button_active"],
            activeforeground=PALETTE["text"],
            disabledforeground=PALETTE["muted"],
            borderwidth=1,
            relief=tk.SOLID,
        )
        self.context_menu.add_command(
            label="Cut", command=lambda: self._context_action("cut")
        )
        self.context_menu.add_command(
            label="Copy", command=lambda: self._context_action("copy")
        )
        self.context_menu.add_command(
            label="Paste",
            command=lambda: self._context_action("paste"),
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Select all",
            command=lambda: self._context_action("select_all"),
        )

        self._apply_language()
        self._set_chat_enabled(False)

    def _install_editing_support(self) -> None:
        for widget in (
            self.server_entry,
            self.token_entry,
            self.message_input,
            self.transcript,
        ):
            widget.bind("<Control-KeyPress>", self._handle_control_key, add="+")
            for key in "acvxACVX":
                widget.bind(f"<Control-{key}>", self._handle_control_key, add="+")
            widget.bind("<Button-3>", self._show_context_menu, add="+")

        for sequence in ("<<Copy>>", "<<Cut>>", "<Control-Insert>", "<Shift-Delete>"):
            self.token_entry.bind(sequence, self._block_token_clipboard_event, add="+")

    def _text(self, key: str, **values: Any) -> str:
        return TEXT[self.language.get()][key].format(**values)

    def _toggle_language(self) -> None:
        self.language.set("th" if self.language.get() == "en" else "en")
        self._apply_language()

    def _apply_language(self) -> None:
        self._set_window_title(self._text("title"))
        self.title_label.configure(text=self._text("title"))
        self.language_button.configure(text=self._text("language"))
        self.connection.configure(text=self._text("control_server"))
        self.transcript_frame.configure(text=self._text("conversation"))
        self.server_label.configure(text=self._text("server_url"))
        self.token_label.configure(text=self._text("agent_token"))
        self.connect_button.configure(
            text=self._text(
                "disconnect" if self.worker and self.worker.is_alive else "connect"
            )
        )
        self.message_label.configure(text=self._text("message"))
        self.send_button.configure(text=self._text("send"))
        self.help_label.configure(text=self._text("help"))
        self.membership_title.configure(text=self._text("vip_membership"))
        self.membership_disclaimer.configure(text=self._text("vip_disclaimer"))
        for index, key in enumerate(("cut", "copy", "paste")):
            self.context_menu.entryconfigure(index, label=self._text(key))
        self.context_menu.entryconfigure(4, label=self._text("select_all"))
        self.status.set(self._text(self._status_key, **self._status_data))
        self.assignment.set(self._text(self._assignment_key, **self._assignment_data))
        self._render_membership()

    def _set_status(self, key: str, **values: Any) -> None:
        self._status_key = key
        self._status_data = values
        self.status.set(self._text(key, **values))

    def _set_assignment(self, key: str, **values: Any) -> None:
        self._assignment_key = key
        self._assignment_data = values
        self.assignment.set(self._text(key, **values))

    def _set_membership(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self._membership_tier = data.get("subscription_tier", "unknown")
        self._membership_source = data.get(
            "subscription_source", "baseline_not_recorded"
        )
        self._membership_evidence_source = data.get(
            "subscription_evidence_source", "unknown"
        )
        self._membership_expires_at = data.get("subscription_expires_at")
        self._render_membership()

    def _render_membership(self) -> None:
        tier_keys = {
            "unknown": "vip_unknown",
            "free": "vip_free",
            "pro": "vip_pro",
            "extreme": "vip_extreme",
            "gold": "vip_gold",
        }
        evidence_keys = {
            "unknown": "evidence_unknown",
            "operator_report": "evidence_operator_report",
            "official_record": "evidence_official_record",
            "manual_entry": "evidence_manual_entry",
        }
        record_keys = {
            "temporary_overlay": "record_temporary_overlay",
            "durable_baseline": "record_durable_baseline",
            "overlapping_overlays": "record_overlapping_overlays",
            "baseline_not_recorded": "record_baseline_not_recorded",
            "baseline_unverified": "record_baseline_unverified",
        }
        tier = (
            self._membership_tier if self._membership_tier in tier_keys else "unknown"
        )
        evidence = (
            self._membership_evidence_source
            if self._membership_evidence_source in evidence_keys
            else "unknown"
        )
        source = (
            self._membership_source
            if self._membership_source in record_keys
            else "baseline_not_recorded"
        )
        self.membership_badge.configure(
            text=self._text(tier_keys[tier]),
            style=(
                "MembershipVIP.TLabel"
                if tier in {"pro", "extreme", "gold"}
                else "MembershipNeutral.TLabel"
            ),
        )
        details = self._text(
            "membership_detail",
            evidence=self._text(evidence_keys[evidence]),
            record=self._text(record_keys[source]),
        )
        expires_at = self._membership_expiry_label(self._membership_expires_at)
        if expires_at:
            details += self._text("membership_expiry", expires_at=expires_at)
        self.membership_detail.configure(text=details)

    def _membership_expiry_label(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            observed_at = datetime.fromisoformat(value)
        except ValueError:
            return None
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return None
        return observed_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")

    def _localized_validation_error(self, error: ValueError) -> str:
        message = str(error)
        keys = (
            ("valid host and port", "url_host"),
            ("credentials, query, or fragment", "url_credentials"),
            ("HTTPS is required", "url_https"),
            ("origin only", "url_origin"),
            ("raw one-time-issued agent token", "token_invalid"),
            ("absolute HTTP(S) URL", "url_absolute"),
            ("cannot be empty or whitespace only", "message_empty"),
            ("cannot contain control characters", "message_control"),
            ("at most 4000 characters", "message_too_long"),
        )
        for fragment, key in keys:
            if fragment in message:
                return self._text(key)
        if "assigned account is required" in message:
            return self._text("account_required")
        return message

    def _connect(self) -> None:
        if self.worker is not None and self.worker.is_alive:
            self.worker.stop()
            self.connect_button.configure(state=tk.DISABLED)
            self._set_status("disconnecting")
            return

        try:
            server_url = validate_server_url(self.server_url.get())
        except ValueError as exc:
            messagebox.showerror(
                self._text("invalid_server_title"),
                self._localized_validation_error(exc),
                parent=self.root,
            )
            return

        try:
            token = normalize_agent_token(self.agent_token.get())
        except ValueError as exc:
            title_key = (
                "token_required_title"
                if not self.agent_token.get().strip()
                else "invalid_token_title"
            )
            messagebox.showerror(
                self._text(title_key),
                self._localized_validation_error(exc),
                parent=self.root,
            )
            return

        self._clear_conversation()
        self.worker = AgentChatWorker(server_url, token)
        self.agent_token.set("")
        self._set_connection_fields_enabled(False)
        self.connect_button.configure(text=self._text("disconnect"), state=tk.NORMAL)
        self._set_status("connecting")
        self.worker.start()

    def _send_event(self, _event: tk.Event[Any]) -> str:
        self._send()
        return "break"

    def _send(self) -> None:
        if self.worker is None or not self.worker.is_alive:
            return
        body = self.message_input.get("1.0", "end-1c")
        try:
            message_id = self.worker.submit_message(body, self._current_account_id)
        except ValueError as exc:
            messagebox.showerror(
                self._text("invalid_message_title"),
                self._localized_validation_error(exc),
                parent=self.root,
            )
            return
        self.message_input.delete("1.0", tk.END)
        self._append_entry(self._text("speaker_you"), body)
        self._append_entry(
            self._text("speaker_status"),
            self._text("sending", message_id=message_id[:8]),
        )

    def _drain_events(self) -> None:
        if self._closing:
            return
        worker = self.worker
        if worker is not None:
            while True:
                try:
                    event = worker.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(worker, event)
        self.root.after(100, self._drain_events)

    def _handle_event(self, worker: AgentChatWorker, event: WorkerEvent) -> None:
        data = event.data
        if event.kind == "connected":
            self._set_status("checking_assignment")
        elif event.kind == "assignment":
            account_id = data["account_id"]
            if account_id is None:
                if self._current_account_id is not None:
                    self._clear_conversation()
                self._current_account_id = None
                self._set_assignment("no_account")
                self._set_membership()
                self._set_status("assign_account")
                self._set_chat_enabled(False)
            else:
                if account_id != self._current_account_id:
                    self._clear_conversation()
                    self._current_account_id = account_id
                nickname = data.get("account_nickname") or account_id
                self._set_assignment("account", nickname=nickname)
                self._set_membership(data)
                self._set_status("connected")
                self._set_chat_enabled(True)
        elif event.kind == "incoming":
            message = data["message"]
            self._append_entry(self._text("speaker_operator"), message["body"])
            worker.accept_incoming(message["id"], message["claim_token"])
        elif event.kind == "sent":
            self._append_entry(
                self._text("speaker_status"),
                self._text("send_accepted", message_id=data["message_id"][:8]),
            )
        elif event.kind == "send_failed":
            self._append_entry(
                self._text("speaker_status"),
                self._text("send_rejected", message_id=data["message_id"][:8]),
            )
        elif event.kind == "send_canceled":
            self._append_entry(
                self._text("speaker_status"),
                self._text("send_canceled", message_id=data["message_id"][:8]),
            )
        elif event.kind == "connection_error":
            self._set_status("retrying", seconds=data["retry_seconds"])
        elif event.kind == "auth_required":
            self._set_status("token_rejected")
            self._append_entry(self._text("speaker_status"), self._text("auth_invalid"))
        elif event.kind == "ack_failed":
            self._append_entry(
                self._text("speaker_status"),
                self._text("ack_failed", message_id=data["message_id"][:8]),
            )
        elif event.kind == "error":
            status_code = data.get("status_code")
            if status_code is not None:
                ray_id = data.get("cf_ray_id")
                diagnostic = f"; Cloudflare Ray ID: {ray_id}" if ray_id else ""
                message = self._text(
                    "request_rejected",
                    status=status_code,
                    diagnostic=diagnostic,
                )
            else:
                error_type = data.get("error_type") or self._error_type(
                    data.get("message")
                )
                message = self._text("session_stopped", error_type=error_type)
            self._set_status("error", message=message)
            self._append_entry(self._text("speaker_status"), message)
        elif event.kind == "stopped":
            self._set_chat_enabled(False)
            self._set_connection_fields_enabled(True)
            self.connect_button.configure(text=self._text("connect"), state=tk.NORMAL)
            if self._status_key not in {"token_rejected", "error"}:
                self._set_status("disconnected")
            if self.worker is worker:
                self.worker = None
            self._apply_language()

    @staticmethod
    def _error_type(message: Any) -> str:
        if isinstance(message, str) and message.startswith("Chat session stopped ("):
            return message.removeprefix("Chat session stopped (").removesuffix(").")
        return "Error"

    def _append_entry(self, speaker: str, body: str) -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self._entries.append(f"[{timestamp}] {speaker}:\n{body}")
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.delete("1.0", tk.END)
        self.transcript.insert(tk.END, "\n\n".join(self._entries))
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _clear_conversation(self) -> None:
        self._entries.clear()
        self.message_input.configure(state=tk.NORMAL)
        self.message_input.delete("1.0", tk.END)
        self.message_input.configure(state=tk.DISABLED)
        self._set_assignment("no_account")
        self._current_account_id = None
        self._set_membership()
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.delete("1.0", tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _set_chat_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        self.message_input.configure(state=state)
        self.send_button.configure(state=state)

    def _set_connection_fields_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        self.server_entry.configure(state=state)
        self.token_entry.configure(state=state)

    def _widget_class(self, widget: tk.Widget) -> str:
        try:
            return widget.winfo_class()
        except tk.TclError:
            return ""

    @staticmethod
    def _control_action(event: tk.Event[Any]) -> str | None:
        keysym = str(getattr(event, "keysym", "")).lower()
        char = str(getattr(event, "char", "") or "")
        try:
            keycode = int(getattr(event, "keycode", -1))
        except (TypeError, ValueError):
            keycode = -1
        shortcuts = {
            "a": ({"a", "\x01"}, 65, "select_all"),
            "c": ({"c", "\x03"}, 67, "copy"),
            "v": ({"v", "\x16"}, 86, "paste"),
            "x": ({"x", "\x18"}, 88, "cut"),
        }
        for name, (control_chars, virtual_key, action) in shortcuts.items():
            if keysym == name or char in control_chars or keycode == virtual_key:
                return action
        return None

    def _handle_control_key(self, event: tk.Event[Any]) -> str | None:
        action = self._control_action(event)
        if action is None:
            return None
        self._perform_edit_action(event.widget, action)
        return "break"

    @staticmethod
    def _block_token_clipboard_event(_event: tk.Event[Any]) -> str:
        return "break"

    def _is_editable(self, widget: tk.Widget) -> bool:
        if self._widget_class(widget) not in self._EDITABLE_CLASSES:
            return False
        try:
            return str(widget.cget("state")) not in {"disabled", "readonly"}
        except (tk.TclError, AttributeError):
            return True

    def _is_masked(self, widget: tk.Widget) -> bool:
        try:
            return bool(widget.cget("show"))
        except (tk.TclError, AttributeError):
            return False

    def _selection(self, widget: tk.Widget) -> tuple[str, str] | None:
        try:
            if self._widget_class(widget) == "Text":
                return ("sel.first", "sel.last") if widget.tag_ranges("sel") else None
            return ("sel.first", "sel.last") if widget.selection_present() else None
        except (tk.TclError, AttributeError):
            return None

    def _selected_text(self, widget: tk.Widget, selection: tuple[str, str]) -> str:
        if self._widget_class(widget) == "Text":
            return str(widget.get(*selection))
        content = str(widget.get())
        start, end = (int(widget.index(index)) for index in selection)
        return content[start:end]

    def _write_clipboard(self, value: str) -> bool:
        for attempt in range(20):
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(value)
                return True
            except tk.TclError:
                if attempt == 19:
                    return False
                time.sleep(0.025)
        return False

    def _read_clipboard(self) -> str:
        for attempt in range(20):
            try:
                return str(self.root.clipboard_get())
            except tk.TclError:
                if attempt == 19:
                    return ""
                time.sleep(0.025)
        return ""

    def _select_all(self, widget: tk.Widget) -> bool:
        widget_class = self._widget_class(widget)
        if widget_class not in self._EDITABLE_CLASSES:
            return False
        try:
            if widget_class == "Text":
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
            else:
                widget.selection_range(0, tk.END)
                widget.icursor(tk.END)
            widget.focus_set()
            return True
        except (tk.TclError, AttributeError):
            return False

    def _perform_edit_action(self, widget: tk.Widget, action: str) -> bool:
        widget_class = self._widget_class(widget)
        if widget_class not in self._EDITABLE_CLASSES:
            return False
        selection = self._selection(widget)
        if action == "select_all":
            return self._select_all(widget)
        if action in {"copy", "cut"}:
            if selection is None or self._is_masked(widget):
                return False
            if action == "cut" and not self._is_editable(widget):
                return False
            try:
                selected = self._selected_text(widget, selection)
            except (tk.TclError, AttributeError, TypeError, ValueError):
                return False
            if not self._write_clipboard(selected):
                return False
            if action == "cut":
                try:
                    widget.delete(*selection)
                except (tk.TclError, AttributeError):
                    return False
            return True
        if action == "paste":
            if not self._is_editable(widget):
                return False
            value = self._read_clipboard()
            if not value:
                return False
            try:
                if selection is not None:
                    widget.delete(*selection)
                widget.insert("insert", value)
                return True
            except (tk.TclError, AttributeError):
                return False
        return False

    def _context_action(self, action: str) -> None:
        if self.context_target is not None:
            self._perform_edit_action(self.context_target, action)

    def _place_cursor_for_context(
        self, widget: tk.Widget, event: tk.Event[Any]
    ) -> None:
        selection = self._selection(widget)
        try:
            if self._widget_class(widget) == "Text":
                index = widget.index(f"@{event.x},{event.y}")
                selected = (
                    selection is not None
                    and widget.compare(selection[0], "<=", index)
                    and widget.compare(index, "<", selection[1])
                )
                if selected:
                    return
                widget.tag_remove("sel", "1.0", tk.END)
                widget.mark_set("insert", index)
            else:
                index = int(widget.index(f"@{event.x}"))
                if selection is not None:
                    start, end = (int(widget.index(part)) for part in selection)
                    if start <= index < end:
                        return
                widget.selection_clear()
                widget.icursor(index)
        except (tk.TclError, AttributeError, TypeError, ValueError):
            return

    def _show_context_menu(self, event: tk.Event[Any]) -> str:
        widget = event.widget
        if self._widget_class(widget) not in self._EDITABLE_CLASSES:
            return "break"
        self.context_target = widget
        self._place_cursor_for_context(widget, event)
        try:
            widget.focus_set()
        except tk.TclError:
            pass
        selection = self._selection(widget)
        editable = self._is_editable(widget)
        masked = self._is_masked(widget)
        self.context_menu.entryconfigure(
            0,
            state=tk.NORMAL
            if selection is not None and editable and not masked
            else tk.DISABLED,
        )
        self.context_menu.entryconfigure(
            1,
            state=tk.NORMAL if selection is not None and not masked else tk.DISABLED,
        )
        self.context_menu.entryconfigure(
            2, state=tk.NORMAL if editable else tk.DISABLED
        )
        self.context_menu.entryconfigure(4, state=tk.NORMAL)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        except tk.TclError:
            pass
        finally:
            try:
                self.context_menu.grab_release()
            except tk.TclError:
                pass
        return "break"

    def _close(self) -> None:
        if self.worker is not None and self.worker.is_alive:
            self.worker.stop()
        self._closing = True
        self.root.destroy()


SHELL_TEXT = {
    "en": {
        "title": "ZChat Client",
        "nav_agent": "Agent Chat",
        "nav_monitor": "Process Monitor",
        "nav_private_chat": "Private Chat",
        "nav_status": "Status Changer",
    },
    "th": {
        "title": "ZChat Client",
        "nav_agent": "แชตเอเจนต์",
        "nav_monitor": "Process Monitor",
        "nav_private_chat": "แชตส่วนตัว",
        "nav_status": "เปลี่ยนสถานะ",
    },
}


class UnifiedClientWindow:
    """Single-window shell for agent chat, runtime monitoring, and Camfrog tools.

    Private Chat and Status Changer embed their source modules; the standalone
    feature executables are not launched by the unified client.
    """

    PAGES = ("agent", "monitor", "private_chat", "status")

    def __init__(
        self,
        root: tk.Tk,
        *,
        private_chat_data_dir: Path | None = None,
        status_data_dir: Path | None = None,
    ) -> None:
        self.root = root
        self.private_chat_data_dir = private_chat_data_dir
        self.private_app = None
        self.private_error: Exception | None = None
        self.status_data_dir = status_data_dir
        self.status_app = None
        self.status_error: Exception | None = None
        self._closing = False
        _apply_application_icon(self.root)
        self.language = tk.StringVar(value="en")
        self._set_window_title(SHELL_TEXT["en"]["title"])
        try:
            self.root.geometry("1100x700")
            self.root.minsize(900, 560)
        except (AttributeError, tk.TclError):
            pass

        self.sidebar = ttk.Frame(self.root, padding=8)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.container = ttk.Frame(self.root, padding=(4, 8, 8, 8))
        self.container.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.nav_buttons: dict[str, ttk.Button] = {}
        for page in self.PAGES:
            btn = ttk.Button(self.sidebar, command=lambda p=page: self.show_page(p))
            btn.pack(fill=tk.X, pady=2)
            self.nav_buttons[page] = btn

        self.pages: dict[str, ttk.Frame] = {}
        agent_host = ttk.Frame(self.container)
        self.agent_page = AgentChatWindow(agent_host, language=self.language)
        self.pages["agent"] = agent_host
        self.monitor_page = ProcessMonitorPage(
            self.container, get_language=self.language.get
        )
        self.pages["monitor"] = self.monitor_page
        # The standalone manager configures its host's background directly.
        self.private_host = tk.Frame(self.container, background=PALETTE["background"])
        self.pages["private_chat"] = self.private_host
        # The shared Status Changer applies a Tk frame background directly.
        self.status_host = tk.Frame(self.container, background=PALETTE["background"])
        self.pages["status"] = self.status_host

        self.language.trace_add("write", lambda *_: self._apply_shell_language())
        self._current: str | None = None
        self._apply_shell_language()
        self.show_page("agent")
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _set_window_title(self, title: str) -> None:
        setter = getattr(self.root, "title", None)
        if callable(setter):
            try:
                setter(title)
            except (AttributeError, tk.TclError):
                pass

    def _apply_shell_language(self) -> None:
        lang = self.language.get()
        table = SHELL_TEXT.get(lang, SHELL_TEXT["en"])
        self._set_window_title(table["title"])
        self.nav_buttons["agent"].configure(text=table["nav_agent"])
        self.nav_buttons["monitor"].configure(text=table["nav_monitor"])
        self.nav_buttons["private_chat"].configure(text=table["nav_private_chat"])
        self.nav_buttons["status"].configure(text=table["nav_status"])
        self.monitor_page.apply_language()

    def show_page(self, name: str) -> None:
        """Display one page; unknown names fail closed on the current page."""
        if name not in self.pages:
            return
        if name == "private_chat" and self.private_app is None:
            self._create_private_chat_page()
        if name == "status" and self.status_app is None:
            self._create_status_page()
        if self._current is not None:
            self.pages[self._current].pack_forget()
        self._current = name
        self.pages[name].pack(fill=tk.BOTH, expand=True)

    def _create_private_chat_page(self) -> None:
        """Embed the read-only private-window manager with isolated app data."""
        try:
            if self.private_chat_data_dir is None:
                if getattr(sys, "frozen", False):
                    base = Path(sys.executable).resolve().parent / "zchat-client-data"
                else:
                    base = Path(__file__).resolve().parents[2] / "data"
                self.private_chat_data_dir = base / "private-chat"
            data_dir = self.private_chat_data_dir.resolve()
            data_dir.mkdir(parents=True, exist_ok=True)
            os.environ["CAMFROG_APP_DATA_DIR"] = str(data_dir)

            # The module derives its app-local paths when imported.
            import chat_im_private_gui

            self.private_app = chat_im_private_gui.PrivateChatManager(
                chat_im_private_gui.CONFIG_PATH,
                parent=self.private_host,
            )
        except (
            ImportError,
            OSError,
            RuntimeError,
            SystemExit,
            tk.TclError,
            TypeError,
            ValueError,
        ) as exc:
            self.private_error = exc
            for child in self.private_host.winfo_children():
                child.destroy()
            ttk.Label(
                self.private_host,
                text=f"Private Chat could not initialize: {type(exc).__name__}",
                wraplength=600,
            ).pack(anchor=tk.W, padx=16, pady=16)

    def _create_status_page(self) -> None:
        """Embed the shared Status Changer module with isolated client data."""
        try:
            if self.status_data_dir is None:
                if getattr(sys, "frozen", False):
                    base = Path(sys.executable).resolve().parent / "zchat-client-data"
                else:
                    base = Path(__file__).resolve().parents[2] / "data"
                self.status_data_dir = base / "status-changer"
            data_dir = self.status_data_dir.resolve()
            data_dir.mkdir(parents=True, exist_ok=True)
            os.environ["ZCFATO_DATA_DIR"] = str(data_dir)
            os.environ["CAMFROG_APP_PROFILE"] = "status"

            # The entry module selects the Camfrog status profile before the
            # shared implementation is imported.
            import status_changer_entry  # noqa: F401
            import status_changer_gui

            config_path = status_changer_gui.DATA_DIR / "camfrog-status-config.json"
            self.status_app = status_changer_gui.build_app()(
                config_path,
                parent=self.status_host,
                on_closed=self._finish_close,
                on_close_failed=self._allow_close_retry,
            )
        except (
            ImportError,
            KeyError,
            OSError,
            RuntimeError,
            SystemExit,
            tk.TclError,
            TypeError,
            ValueError,
        ) as exc:
            self.status_error = exc
            for child in self.status_host.winfo_children():
                child.destroy()
            ttk.Label(
                self.status_host,
                text=f"Status Changer could not initialize: {type(exc).__name__}",
                wraplength=600,
            ).pack(anchor=tk.W, padx=16, pady=16)

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self.status_app is not None:
            self.status_app.close_app()
            return
        self._finish_close()

    def _finish_close(self) -> None:
        self.agent_page._close()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _allow_close_retry(self) -> None:
        self._closing = False


def main_unified() -> None:
    root = tk.Tk()
    UnifiedClientWindow(root)
    root.mainloop()


def main() -> None:
    root = tk.Tk()
    AgentChatWindow(root)
    root.mainloop()
