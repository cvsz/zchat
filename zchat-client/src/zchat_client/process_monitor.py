"""Process Monitor page for the unified ZChat client.

Read-only view over the shared Camfrog Windows runtime modules
(:mod:`camfrog_windows_runtime`). The same modules back the standalone
feature executables; this page reuses them 1:1 without duplicating logic
and without requiring any other ZChat executable.

Safety rules enforced here:
- Read-only: this page never launches, focuses, or terminates any process.
- Fail-closed: missing module, absent client, or ambiguous identity only
  updates the status line; refresh() never raises.
- No private data: only executable identity and structural window roles are
  shown; message bodies are never collected or displayed.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

PM_TEXT = {
    "en": {
        "title": "Process Monitor",
        "refresh": "Refresh",
        "status_ready": "Press Refresh to discover the Camfrog client.",
        "status_unavailable": "Camfrog client not running or not verifiable.",
        "status_error": "Discovery unavailable: {detail}",
        "col_pid": "PID",
        "col_exe": "Executable",
        "col_version": "Version",
        "col_publisher": "Publisher",
        "col_windows": "Windows",
        "col_state": "State",
        "state_running": "RUNNING",
        "state_no_ui": "RUNNING (no UI)",
        "detail_title": "Evidence",
    },
    "th": {
        "title": "Process Monitor",
        "refresh": "รีเฟรช",
        "status_ready": "กดรีเฟรชเพื่อค้นหา Camfrog client",
        "status_unavailable": "Camfrog client ไม่ทำงานหรือยืนยันตัวตนไม่ได้",
        "status_error": "ค้นหาไม่ได้: {detail}",
        "col_pid": "PID",
        "col_exe": "Executable",
        "col_version": "Version",
        "col_publisher": "Publisher",
        "col_windows": "Windows",
        "col_state": "State",
        "state_running": "RUNNING",
        "state_no_ui": "RUNNING (no UI)",
        "detail_title": "หลักฐาน",
    },
}


def _lang(get_language: Callable[[], str] | None) -> str:
    try:
        lang = get_language() if get_language else "en"
    except Exception:  # noqa: BLE001 - a broken language callback must not break the UI.
        return "en"
    return lang if lang in PM_TEXT else "en"


class ProcessMonitorPage(ttk.Frame):
    """Read-only Camfrog process/window inspector (shared runtime modules)."""

    COLUMNS = ("pid", "exe", "version", "publisher", "windows", "state")

    def __init__(
        self,
        parent: tk.Widget,
        get_language: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_language = get_language
        self.status_text = tk.StringVar(value=self._text("status_ready"))

        header = ttk.Frame(self, padding=(10, 8))
        header.pack(fill=tk.X)
        self.title_label = ttk.Label(header)
        self.title_label.pack(side=tk.LEFT)
        self.refresh_button = ttk.Button(header, command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT)
        self.status_label = ttk.Label(header, textvariable=self.status_text)
        self.status_label.pack(side=tk.RIGHT, padx=(0, 10))

        self.tree = ttk.Treeview(self, columns=self.COLUMNS, show="headings", height=10)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))
        self.tree.bind("<<TreeviewSelect>>", self._show_selected_evidence)

        self.detail_label = ttk.Label(self)
        self.detail_label.pack(anchor=tk.W, padx=10)
        self.detail = tk.Text(self, height=6, wrap=tk.WORD, state=tk.DISABLED)
        self.detail.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._evidence: dict[str, list[str]] = {}
        self.apply_language()

    def _text(self, key: str, **values: Any) -> str:
        return PM_TEXT[_lang(self._get_language)][key].format(**values)

    def apply_language(self) -> None:
        """Re-render static labels after a language change."""
        self.title_label.configure(text=self._text("title"))
        self.refresh_button.configure(text=self._text("refresh"))
        self.detail_label.configure(text=self._text("detail_title"))
        for col in self.COLUMNS:
            self.tree.heading(col, text=self._text(f"col_{col}"))

    def refresh(self) -> None:
        """Rediscover verified Camfrog processes. Never raises."""
        try:
            from camfrog_windows_runtime import (
                WindowsCamfrogProcessDiscovery,
                WindowsCamfrogWindowClassifier,
                evaluate_client_runtime_state,
            )
        except Exception as exc:  # noqa: BLE001 - this optional read-only panel fails closed.
            self._clear(self._text("status_error", detail=str(exc)[:120]))
            return

        try:
            discovery = WindowsCamfrogProcessDiscovery()
            identities = discovery.discover_processes()
        except Exception as exc:  # noqa: BLE001 - discovery failures are rendered in the panel.
            self._clear(self._text("status_error", detail=str(exc)[:120]))
            return

        if not identities:
            self._clear(self._text("status_unavailable"))
            return

        classifier = WindowsCamfrogWindowClassifier()
        rows = 0
        self.tree.delete(*self.tree.get_children())
        self._evidence = {}
        for identity in identities:
            try:
                windows = classifier.classify_process_windows(identity)
                state = evaluate_client_runtime_state(identity, windows)
            except Exception:  # noqa: BLE001, S112 - skip one bad process row and continue.
                continue
            state_text = self._text(
                "state_running" if state.ui_visible else "state_no_ui"
            )
            self.tree.insert(
                "",
                tk.END,
                iid=str(identity.pid),
                values=(
                    identity.pid,
                    identity.exe_path,
                    identity.version,
                    identity.publisher,
                    len(windows),
                    state_text,
                ),
            )
            self._evidence[str(identity.pid)] = list(state.evidence_notes)
            rows += 1

        if rows == 0:
            self._clear(self._text("status_unavailable"))
        else:
            self.status_text.set(f"{rows} verified Camfrog process(es)")

    def _clear(self, status: str) -> None:
        self.tree.delete(*self.tree.get_children())
        self._evidence = {}
        self._set_detail([])
        self.status_text.set(status)

    def _show_selected_evidence(self, _event: Any = None) -> None:
        selected = self.tree.selection()
        notes = self._evidence.get(selected[0], []) if selected else []
        self._set_detail(notes)

    def _set_detail(self, notes: list[str]) -> None:
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        for note in notes:
            self.detail.insert(tk.END, f"• {note}\n")
        self.detail.configure(state=tk.DISABLED)
