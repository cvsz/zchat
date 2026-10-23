"""Private chat window manager. It lists and focuses windows without reading or sending messages."""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

import camfrog_auto as ca
from clipboard_support import ClipboardController


CONFIG_PATH = ca.BASE / "private-chat-config.json"


def private_config_path(path):
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ca.BASE / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(ca.BASE.resolve())
    except ValueError as exc:
        raise ValueError(
            "Private chat config must stay beside this executable."
        ) from exc
    return candidate


def load_private_config(path=CONFIG_PATH):
    path = private_config_path(path)
    if not path.exists():
        config = copy.deepcopy(ca.DEFAULTS)
        config["status"]["enabled"] = False
        config["autoreply"]["enabled"] = False
        config["autoreply_im"]["enabled"] = False
        ca.atomic_write(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    config = ca.load_cfg(path)
    config["status"]["enabled"] = False
    config["autoreply"]["enabled"] = False
    config["autoreply_im"]["enabled"] = False
    errors, _warnings = ca.validate(config)
    if errors:
        raise ValueError("Invalid private chat config: " + "; ".join(errors))
    return config


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = argparse.ArgumentParser(description="Camfrog private chat window manager")
    parser.add_argument("--config", help="use an alternate app-local config")
    try:
        args = parser.parse_args(argv)
        if args.config is not None and not args.config.strip():
            parser.error("argument --config: expected a path")
        config_path = (
            private_config_path(args.config) if args.config is not None else CONFIG_PATH
        )
    except SystemExit as exc:
        return exc.code
    except ValueError as exc:
        print(exc)
        return 2

    try:
        app = PrivateChatManager(config_path)
    except Exception as exc:
        print(f"Private chat manager failed: {exc}")
        return 1
    app.root.mainloop()
    return 0


class PrivateChatManager:
    def __init__(self, config_path):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.config_path = Path(config_path)
        self.config = load_private_config(self.config_path)
        self.windows = []
        self.root = self.tk.Tk()
        self.root.configure(background="#f4f1e8")
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass
        style.configure("TFrame", background="#f4f1e8")
        style.configure(
            "TLabel", background="#f4f1e8", foreground="#283648", font=("Segoe UI", 9)
        )
        style.configure("TCheckbutton", background="#f4f1e8", foreground="#283648")
        style.configure("TRadiobutton", background="#f4f1e8", foreground="#283648")
        style.configure("TLabelframe", background="#f4f1e8", bordercolor="#d5c8ac")
        style.configure(
            "TLabelframe.Label",
            background="#f4f1e8",
            foreground="#8b672b",
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "TButton",
            padding=(7, 4),
            font=("Segoe UI", 9),
            background="#ebe5d9",
            foreground="#283648",
        )
        style.map("TButton", background=[("active", "#d6e9e8"), ("pressed", "#d5c8ac")])
        style.configure(
            "TEntry", padding=(4, 4), fieldbackground="#ffffff", foreground="#283648"
        )
        style.configure("TNotebook", background="#f4f1e8")
        style.configure("TNotebook.Tab", padding=(11, 6), font=("Segoe UI", 9))
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#fffdfa")],
            foreground=[("selected", "#8b672b")],
        )
        self.root.title("Camfrog Private Chat")
        self.root.geometry("460x360")
        self.root.minsize(360, 280)

        self.ttk.Label(
            self.root,
            text=(
                "Open a private chat in Camfrog, then refresh this list. "
                "This tool only lists and focuses private chat windows; it does not read or send messages."
            ),
            wraplength=520,
        ).pack(fill="x", padx=12, pady=(12, 8))

        filters = self.ttk.Frame(self.root, padding=(12, 0, 12, 8))
        filters.pack(fill="x")
        self.ttk.Label(filters, text="Window title filter").grid(
            row=0, column=0, sticky="w"
        )
        self.title_filter = self.tk.StringVar(
            value=self.config["autoreply_im"].get("window_title_regex", "")
        )
        self.ttk.Entry(filters, textvariable=self.title_filter).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        self.ttk.Label(filters, text="Max windows").grid(
            row=1, column=0, sticky="w", pady=(5, 0)
        )
        self.max_windows = self.tk.StringVar(
            value=str(self.config["autoreply_im"].get("max_windows", 5))
        )
        self.ttk.Spinbox(
            filters, from_=1, to=20, textvariable=self.max_windows, width=6
        ).grid(row=1, column=1, sticky="w", padx=6, pady=(5, 0))
        filters.columnconfigure(1, weight=1)

        list_frame = self.ttk.Frame(self.root, padding=(12, 0, 12, 8))
        list_frame.pack(fill="both", expand=True)
        self.listbox = self.tk.Listbox(
            list_frame,
            exportselection=False,
            background="#fffdfa",
            foreground="#283648",
            selectbackground="#8b672b",
            selectforeground="#fffdfa",
            highlightbackground="#d5c8ac",
            highlightcolor="#8b672b",
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = self.ttk.Scrollbar(
            list_frame, orient="vertical", command=self.listbox.yview
        )
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.bind("<Double-Button-1>", lambda _event: self.activate_selected())

        actions = self.ttk.Frame(self.root, padding=(12, 0, 12, 8))
        actions.pack(fill="x")
        self.ttk.Button(actions, text="Save filters", command=self.save_filters).pack(
            side="left", padx=5
        )
        self.ttk.Button(actions, text="Discover controls", command=self.discover).pack(
            side="left"
        )
        self.ttk.Button(actions, text="Refresh", command=self.refresh).pack(
            side="right"
        )
        self.ttk.Button(
            actions, text="Activate selected", command=self.activate_selected
        ).pack(side="right", padx=5)
        self.status = self.tk.StringVar(value=f"Private settings: {self.config_path}")
        self.ttk.Label(
            self.root, textvariable=self.status, anchor="w", padding=(12, 0, 12, 10)
        ).pack(fill="x")
        self.clipboard_controller = ClipboardController(self.root)
        self.clipboard_controller.install(self.root)
        self.root.after(200, self.refresh)

    def save_filters(self, refresh_after=True, silent=False):
        try:
            limit = int(self.max_windows.get())
            if not 1 <= limit <= 20:
                raise ValueError
            config = load_private_config(self.config_path)
            config["autoreply_im"]["window_title_regex"] = (
                self.title_filter.get().strip()
            )
            config["autoreply_im"]["max_windows"] = limit
            try:
                re.compile(config["autoreply_im"]["window_title_regex"])
            except Exception as exc:
                raise ValueError(f"Invalid title filter: {exc}") from exc
            ca.atomic_write(
                self.config_path,
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            )
            self.config = config
            self.status.set("Private chat filters saved to this app's private config.")
            if refresh_after:
                self.refresh()
        except (OSError, ValueError) as exc:
            if silent:
                self.status.set(f"Private chat filters not saved: {exc}")
                return
            from tkinter import messagebox

            messagebox.showerror("Private chat filters", str(exc), parent=self.root)

    def discover(self):
        """Dump the Camfrog control tree to controls.txt for selector setup."""
        import contextlib
        import io

        try:
            self.save_filters(refresh_after=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ca.cmd_discover(self.config)
            lines = buf.getvalue().strip().splitlines()
            self.status.set(lines[-1] if lines else "Controls written to controls.txt.")
        except Exception as exc:
            self.status.set(f"Could not discover controls: {exc}")

    def refresh(self):
        try:
            self.save_filters(refresh_after=False, silent=True)
            main_window = ca.get_window(self.config)
            self.windows = ca.get_im_windows(
                self.config, main_window, self.config["autoreply_im"]["max_windows"]
            )
            self.listbox.delete(0, "end")
            for window, _history, _input in self.windows:
                try:
                    title = window.window_text().strip()
                except Exception:
                    title = ""
                self.listbox.insert(
                    "end", title or f"Private chat window ({window.handle})"
                )
            if self.windows:
                self.listbox.selection_set(0)
                self.status.set(
                    f"Found {len(self.windows)} private chat window(s). No message text is read."
                )
            else:
                self.status.set(
                    "No private chat windows found. Open one in Camfrog and refresh."
                )
        except Exception as exc:
            self.windows = []
            self.listbox.delete(0, "end")
            self.status.set(f"Could not list private chat windows: {exc}")

    def activate_selected(self):
        selected = self.listbox.curselection()
        if not selected or selected[0] >= len(self.windows):
            self.status.set("Select a private chat window first.")
            return
        window = self.windows[selected[0]][0]
        try:
            try:
                window.restore()
            except Exception:
                pass
            window.set_focus()
            self.status.set("Activated the selected Camfrog private chat window.")
        except Exception as exc:
            self.status.set(f"Could not activate the selected window: {exc}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
