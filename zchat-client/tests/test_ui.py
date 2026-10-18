from __future__ import annotations

import ast
import ctypes
import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest
from zchat_client import ui
from zchat_client.session import WorkerEvent
from zchat_client.ui import AgentChatWindow


def test_window_starts_without_server_or_saved_credentials() -> None:
    root = tk.Tk()
    try:
        window = AgentChatWindow(root)

        assert window.server_url.get() == ""
        assert window.agent_token.get() == ""
        assert window.token_entry.cget("show") == "*"
        assert window.server_label.cget("text") == "Server URL"
        assert window.message_label.cget("text") == "Message"
        assert str(window.message_input.cget("state")) == tk.DISABLED
        assert str(window.send_button.cget("state")) == tk.DISABLED
    finally:
        root.destroy()


def test_window_uses_application_icon() -> None:
    root = tk.Tk()
    try:
        AgentChatWindow(root)
        icon_path = ui._application_icon_path()

        assert icon_path.is_file()
        if sys.platform == "win32":
            root.update()
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            user32.GetAncestor.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = (
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )
            user32.SendMessageW.restype = ctypes.c_ssize_t
            top_level = user32.GetAncestor(root.winfo_id(), 2)
            icon_handle = user32.SendMessageW(
                top_level, 0x007F, ctypes.c_void_p(1), ctypes.c_void_p(0)
            )
            assert icon_handle
    finally:
        root.destroy()


def test_frozen_application_icon_resolves_from_bundle_root(
    monkeypatch, tmp_path
) -> None:
    icon_path = tmp_path / "app.ico"
    icon_path.write_bytes(b"icon")
    monkeypatch.setattr(ui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ui.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert ui._application_icon_path() == icon_path


def test_connect_rejects_blank_server_url_before_using_token(monkeypatch) -> None:
    root = tk.Tk()
    errors: list[tuple[str, str]] = []
    try:
        window = AgentChatWindow(root)
        window.agent_token.set("unused-agent-token")
        monkeypatch.setattr(
            ui.messagebox,
            "showerror",
            lambda title, message, **_kwargs: errors.append((title, message)),
        )

        window._connect()

        assert window.worker is None
        assert window.agent_token.get() == "unused-agent-token"
        assert errors == [
            ("Invalid server URL", "Server URL must be an absolute HTTP(S) URL.")
        ]
    finally:
        root.destroy()


def test_new_session_clears_account_scoped_conversation_and_draft() -> None:
    root = tk.Tk()
    try:
        window = AgentChatWindow(root)
        window.assignment.set("Account: previous-account")
        window._set_chat_enabled(True)
        window.message_input.insert("1.0", "Draft for previous account")
        window._set_chat_enabled(False)
        window._append_entry("Operator", "Private account message")

        window._clear_conversation()

        assert list(window._entries) == []
        assert window.message_input.get("1.0", "end-1c") == ""
        assert window.assignment.get() == "No account assigned"
        assert window.transcript.get("1.0", "end-1c") == ""
    finally:
        root.destroy()


def test_client_uses_the_web_status_palette_and_supports_thai(monkeypatch) -> None:
    root = tk.Tk()
    try:
        window = AgentChatWindow(root)

        assert window.PALETTE["background"] == "#f4f1e8"
        assert window.PALETTE["surface"] == "#fffdfa"
        assert window.PALETTE["accent"] == "#8b672b"
        assert window.PALETTE["text"] == "#283648"
        assert window.transcript.cget("background") == window.PALETTE["surface"]
        assert window.root.title() == "ZChat Agent Chat"

        window._toggle_language()
        assert window.language.get() == "th"
        assert window.root.title() == "แชตเอเจนต์ ZChat"
        assert window.server_label.cget("text") == "URL เซิร์ฟเวอร์"
        assert window.connect_button.cget("text") == "เชื่อมต่อ"
        assert window.context_menu.entrycget(0, "label") == "ตัด"
    finally:
        root.destroy()


def test_vip_membership_card_shows_provenance_and_expiry_in_both_languages() -> None:
    root = tk.Tk()
    try:
        window = AgentChatWindow(root)
        window._set_membership(
            {
                "subscription_tier": "gold",
                "subscription_source": "temporary_overlay",
                "subscription_evidence_source": "manual_entry",
                "subscription_expires_at": "2026-10-23T00:00:00+00:00",
            }
        )

        assert window.membership_badge.cget("text") == "Gold VIP"
        assert window.membership_badge.cget("style") == "MembershipVIP.TLabel"
        assert "Manual record" in window.membership_detail.cget("text")
        assert "Temporary record" in window.membership_detail.cget("text")
        assert "2026-10-23" in window.membership_detail.cget("text")
        assert "not been checked live" in window.membership_disclaimer.cget("text")

        window._toggle_language()

        assert window.membership_title.cget("text") == "สถานะ VIP"
        assert "บันทึกด้วยตนเอง" in window.membership_detail.cget("text")
        assert "ระเบียนชั่วคราว" in window.membership_detail.cget("text")
        assert "ยังไม่ได้ตรวจสอบ Camfrog แบบสด" in window.membership_disclaimer.cget(
            "text"
        )
    finally:
        root.destroy()


def test_assignment_changes_clear_previous_account_chat_and_membership() -> None:
    root = tk.Tk()
    try:
        window = AgentChatWindow(root)
        first_assignment = ui.WorkerEvent(
            "assignment",
            {
                "account_id": "first-account",
                "account_nickname": "Seaza",
                "subscription_tier": "pro",
                "subscription_source": "durable_baseline",
                "subscription_evidence_source": "operator_report",
            },
        )
        window._handle_event(None, first_assignment)
        window._append_entry("Operator", "Private message for first account")

        next_assignment = ui.WorkerEvent(
            "assignment",
            {
                "account_id": "second-account",
                "account_nickname": "AnotherNick",
                "subscription_tier": "gold",
                "subscription_source": "temporary_overlay",
                "subscription_evidence_source": "manual_entry",
            },
        )
        window._handle_event(None, next_assignment)

        assert window.assignment.get() == "Account: AnotherNick"
        assert "Private message for first account" not in window.transcript.get(
            "1.0", "end-1c"
        )
        assert window.membership_badge.cget("text") == "Gold VIP"

        window._handle_event(None, ui.WorkerEvent("assignment", {"account_id": None}))

        assert window.assignment.get() == "No account assigned"
        assert window.membership_badge.cget("text") == "VIP: Not verified"
        assert window.transcript.get("1.0", "end-1c") == ""
        assert str(window.message_input.cget("state")) == tk.DISABLED
    finally:
        root.destroy()


def test_client_palette_stays_identical_to_web_status_theme() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    module = ast.parse(
        (repo_root / "src" / "features" / "web_status_gui.py").read_text(
            encoding="utf-8"
        )
    )
    assignments = [
        node.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "WEB_STATUS_THEME"
            for target in node.targets
        )
    ]

    assert ast.literal_eval(assignments[-1]) == AgentChatWindow.PALETTE


def test_keyboard_shortcuts_and_context_menu_work_for_editable_and_read_only_fields(
    monkeypatch,
) -> None:
    root = tk.Tk()
    try:
        window = AgentChatWindow(root)
        window.server_entry.insert(0, "https://control.example")
        window.server_entry.focus_force()
        root.update()

        for key in "acvx":
            assert window.server_entry.bind(f"<Control-{key}>")
        window._handle_control_key(
            SimpleNamespace(
                widget=window.server_entry, keysym="a", char="\x01", keycode=65
            )
        )
        assert window.server_entry.selection_present()

        window._handle_control_key(
            SimpleNamespace(
                widget=window.server_entry, keysym="c", char="\x03", keycode=67
            )
        )
        assert root.clipboard_get() == "https://control.example"

        root.clipboard_clear()
        root.clipboard_append("https://pasted.example")
        window._handle_control_key(
            SimpleNamespace(
                widget=window.server_entry, keysym="v", char="\x16", keycode=86
            )
        )
        assert window.server_entry.get() == "https://pasted.example"

        window._handle_control_key(
            SimpleNamespace(
                widget=window.server_entry, keysym="a", char="\x01", keycode=65
            )
        )
        window._handle_control_key(
            SimpleNamespace(
                widget=window.server_entry, keysym="x", char="\x18", keycode=88
            )
        )
        assert window.server_entry.get() == ""
        assert root.clipboard_get() == "https://pasted.example"

        window._append_entry("Operator", "Read-only message")
        window.transcript.tag_add("sel", "1.0", "end-1c")
        window.transcript.focus_force()
        root.update()
        window._handle_control_key(
            SimpleNamespace(
                widget=window.transcript, keysym="c", char="\x03", keycode=67
            )
        )
        assert "Read-only message" in root.clipboard_get()

        popups: list[tuple[int, int]] = []
        monkeypatch.setattr(
            window.context_menu,
            "tk_popup",
            lambda x, y: popups.append((x, y)),
        )
        monkeypatch.setattr(window.context_menu, "grab_release", lambda: None)
        window._show_context_menu(
            SimpleNamespace(widget=window.transcript, x=5, y=5, x_root=20, y_root=30)
        )
        assert window.context_target is window.transcript
        assert popups == [(20, 30)]
        assert window.context_menu.entrycget(0, "state") == "disabled"
        assert window.context_menu.entrycget(1, "state") == "normal"
        window.context_menu.invoke(1)
        assert "Read-only message" in root.clipboard_get()
    finally:
        root.destroy()


def test_token_entry_is_never_copied_or_cut(monkeypatch) -> None:
    root = tk.Tk()
    try:
        window = AgentChatWindow(root)
        window.agent_token.set("private-agent-token")
        window.token_entry.selection_range(0, tk.END)
        root.clipboard_clear()
        root.clipboard_append("existing clipboard")

        window._handle_control_key(
            SimpleNamespace(
                widget=window.token_entry, keysym="c", char="\x03", keycode=67
            )
        )
        window._handle_control_key(
            SimpleNamespace(
                widget=window.token_entry, keysym="x", char="\x18", keycode=88
            )
        )
        window.token_entry.event_generate("<<Copy>>")
        window.token_entry.event_generate("<<Cut>>")
        window.token_entry.event_generate("<Control-Insert>")
        window.token_entry.event_generate("<Shift-Delete>")
        root.update()

        assert window.agent_token.get() == "private-agent-token"
        assert root.clipboard_get() == "existing clipboard"
    finally:
        root.destroy()


def test_invalid_agent_token_is_reported_before_worker_creation(monkeypatch) -> None:
    root = tk.Tk()
    errors: list[tuple[str, str]] = []
    try:
        window = AgentChatWindow(root)
        window.server_url.set("https://control.example")
        window.agent_token.set("Bearer invalid-token")
        monkeypatch.setattr(
            ui.messagebox,
            "showerror",
            lambda title, message, **_kwargs: errors.append((title, message)),
        )
        monkeypatch.setattr(
            ui,
            "AgentChatWorker",
            lambda *_args: pytest.fail("Invalid tokens must not reach the worker"),
        )

        window._connect()

        assert window.worker is None
        assert window.agent_token.get() == "Bearer invalid-token"
        assert errors[0][0] == "Invalid agent token"
        assert "raw one-time-issued agent token" in errors[0][1]
    finally:
        root.destroy()


def test_assignment_change_cancellation_is_explained_in_both_languages() -> None:
    root = tk.Tk()
    try:
        window = AgentChatWindow(root)
        event = WorkerEvent("send_canceled", {"message_id": "12345678-1234"})

        window._handle_event(None, event)
        assert (
            "canceled because the account assignment changed"
            in window.transcript.get("1.0", "end-1c")
        )

        window._toggle_language()
        window._handle_event(None, event)
        assert "ยกเลิกข้อความ 12345678 เนื่องจากมีการเปลี่ยนบัญชีที่กำหนด" in (
            window.transcript.get("1.0", "end-1c")
        )
    finally:
        root.destroy()
