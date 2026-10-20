"""Tests for the ZChat client shell and embedded shared feature pages.

The Process Monitor reuses camfrog_windows_runtime 1:1 (same modules as the
standalone executables) in read-only mode. All tests run without a Camfrog
client and without another ZChat executable.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest
from zchat_client.process_monitor import PM_TEXT, ProcessMonitorPage
from zchat_client.ui import SHELL_TEXT, AgentChatWindow, UnifiedClientWindow


@pytest.fixture()
def root():
    widget = tk.Tk()
    widget.withdraw()
    try:
        yield widget
    finally:
        try:
            widget.destroy()
        except tk.TclError:
            pass


def test_process_monitor_refresh_without_camfrog_never_raises(root, monkeypatch) -> None:
    from camfrog_windows_runtime import WindowsCamfrogProcessDiscovery

    monkeypatch.setattr(
        WindowsCamfrogProcessDiscovery,
        "discover_processes",
        lambda self: [],
    )

    page = ProcessMonitorPage(root)
    page.refresh()

    assert page.tree.get_children() == ()
    assert page.status_text.get() == PM_TEXT["en"]["status_unavailable"]


def test_process_monitor_language_toggle(root) -> None:
    lang = {"value": "en"}
    page = ProcessMonitorPage(root, get_language=lambda: lang["value"])
    assert page.refresh_button.cget("text") == PM_TEXT["en"]["refresh"]
    lang["value"] = "th"
    page.apply_language()
    assert page.refresh_button.cget("text") == PM_TEXT["th"]["refresh"]
    lang["value"] = "xx-unknown"
    page.apply_language()  # unknown language falls back, never raises
    assert page.refresh_button.cget("text") == PM_TEXT["en"]["refresh"]


def test_shell_hosts_agent_and_monitor_pages(root) -> None:
    shell = UnifiedClientWindow(root)
    assert set(shell.pages) == {
        "agent",
        "monitor",
        "private_chat",
        "status",
        "keep_log",
    }
    assert shell._current == "agent"
    assert isinstance(shell.agent_page, AgentChatWindow)
    assert isinstance(shell.monitor_page, ProcessMonitorPage)
    shell.show_page("monitor")
    assert shell._current == "monitor"
    shell.show_page("no-such-page")  # unknown names fail closed on the current page
    assert shell._current == "monitor"


def test_private_chat_embeds_read_only_manager_without_update_controls(
    root, monkeypatch, tmp_path
) -> None:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repo_root / "src" / "features"))
    data_dir = tmp_path / "private-chat-data"
    shell = UnifiedClientWindow(root, private_chat_data_dir=data_dir)
    assert shell.private_app is None

    shell.show_page("private_chat")

    assert shell.private_error is None
    assert shell.private_app is not None
    assert shell.private_app.embedded is True
    assert shell.private_app.root is shell.private_host
    assert shell.private_app.__class__.__module__ == "chat_im_private_gui"
    assert data_dir.is_dir()
    assert not any(
        isinstance(child, ttk.Button) and "update" in child.cget("text").lower()
        for child in shell.private_host.winfo_children()
    )
    assert root.winfo_exists()


def test_status_changer_embeds_shared_module_and_shutdown_closes_host(
    root, monkeypatch, tmp_path
) -> None:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repo_root / "src" / "features"))
    monkeypatch.setenv("CAMFROG_APP_PROFILE", "status")
    monkeypatch.setenv("ZCFATO_DATA_DIR", str(tmp_path / "status-data"))

    shell = UnifiedClientWindow(root, status_data_dir=tmp_path / "status-data")
    shell.show_page("status")

    assert shell.status_app is not None, [
        repr(shell.status_error),
        *(
            child.cget("text")
            for child in shell.status_host.winfo_children()
            if isinstance(child, ttk.Label)
        ),
    ]
    assert shell.status_app.embedded is True
    assert shell.status_app.root is shell.status_host
    assert shell.status_app.__class__.__module__ == "status_changer_gui"
    tabs = set(shell.status_app.tabs.tabs())
    assert str(shell.status_app.pages["random"]) in tabs
    assert str(shell.status_app.pages["marquee"]) in tabs
    assert root.winfo_exists()

    import status_changer_gui

    stop_callbacks = []

    def begin_stop(_runtime, _done_text, on_done=None, action=None):
        stop_callbacks.append(on_done)
        return True

    monkeypatch.setattr(status_changer_gui, "running_pid", lambda _runtime: 1234)
    monkeypatch.setattr(shell.status_app, "_start_stop_task", begin_stop)
    shell._close()
    assert root.winfo_exists()
    assert shell._closing is True

    stop_callbacks.pop(0)(False)
    assert root.winfo_exists()
    assert shell._closing is False

    shell._close()
    stop_callbacks.pop(0)(True)
    with pytest.raises(tk.TclError):
        root.winfo_exists()


def test_shell_language_toggle_updates_nav_and_pages(root) -> None:
    shell = UnifiedClientWindow(root)
    assert shell.nav_buttons["monitor"].cget("text") == SHELL_TEXT["en"]["nav_monitor"]
    shell.language.set("th")
    root.update()
    assert shell.nav_buttons["monitor"].cget("text") == SHELL_TEXT["th"]["nav_monitor"]
    assert shell.nav_buttons["private_chat"].cget("text") == SHELL_TEXT["th"]["nav_private_chat"]
    assert shell.monitor_page.refresh_button.cget("text") == PM_TEXT["th"]["refresh"]
    # Agent page shares the same language variable.
    assert shell.agent_page.language is shell.language


def test_agent_window_still_works_standalone(root) -> None:
    window = AgentChatWindow(root)
    assert window.server_url.get() == ""
    assert window.language.get() == "en"


def test_icon_helper_windows_hosts_and_embedded_frames(root, monkeypatch, tmp_path) -> None:
    """Exercise the Windows branch, including real Tk/Toplevel host types."""
    from zchat_client import ui

    icon = tmp_path / "app.ico"
    icon.write_bytes(b"test-icon")
    top = tk.Toplevel(root)
    calls = []
    try:
        with monkeypatch.context() as m:
            m.setattr(ui.sys, "platform", "win32")
            m.setattr(ui, "_application_icon_path", lambda: icon)
            m.setattr(root, "iconbitmap", lambda path: calls.append(("root", path)))
            m.setattr(top, "iconbitmap", lambda path: calls.append(("top", path)))
            ui._apply_application_icon(root)
            ui._apply_application_icon(top)
            ui._apply_application_icon(ttk.Frame(root))
        assert calls == [("root", str(icon)), ("top", str(icon))]
    finally:
        top.destroy()


def test_unified_shell_applies_icon_to_real_toplevel(root, monkeypatch) -> None:
    from zchat_client import ui

    calls = []
    with monkeypatch.context() as m:
        m.setattr(ui, "_apply_application_icon", lambda widget: calls.append(widget))
        shell = UnifiedClientWindow(root)
    assert root in calls
    assert shell.agent_page.root in calls


def test_agent_page_icon_is_skipped_on_embedded_frame(root) -> None:
    """iconbitmap() is toplevel-only; an embedded ttk.Frame must not raise.

    Regression for AttributeError: 'Frame' object has no attribute 'iconbitmap'
    when AgentChatWindow is hosted inside UnifiedClientWindow.
    """
    shell = UnifiedClientWindow(root)
    page = shell.agent_page
    assert isinstance(page.root, (tk.Frame, ttk.Frame))
    # Calling the icon helper directly on the embedded frame must be a no-op,
    # not an AttributeError.
    from zchat_client.ui import _apply_application_icon

    _apply_application_icon(page.root)  # must not raise
    _apply_application_icon(shell.container)  # must not raise
