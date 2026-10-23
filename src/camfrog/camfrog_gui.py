"""Tkinter GUI source used by the Room Control and IM Auto-reply executables.

Each packaged entrypoint selects a feature profile and stores its own config
beside its executable. Running this module directly keeps the combined source UI.
"""

import ctypes
import os
import contextlib
import copy
import argparse
import io
import json
import queue
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import camfrog_auto as ca
from clipboard_support import ClipboardController

APP_PROFILE = os.environ.get("CAMFROG_APP_PROFILE", "full").lower()
APP_NAME = {"room": "Room Control", "im_reply": "IM Auto-reply"}.get(
    APP_PROFILE, "Camfrog Auto"
)


def apply_app_profile(cfg):
    cfg = copy.deepcopy(cfg)
    if APP_PROFILE not in ("full", "room", "im_reply"):
        raise ValueError(f"Unknown Camfrog app profile: {APP_PROFILE}")
    if APP_PROFILE == "room":
        cfg["status"]["enabled"] = False
        cfg["autoreply_im"]["enabled"] = False
    elif APP_PROFILE == "im_reply":
        cfg["status"]["enabled"] = False
        cfg["autoreply"]["enabled"] = False
    return cfg


CLI_COMMANDS = {
    "check",
    "discover",
    "detect",
    "chat-probe",
    "im-probe",
    "windows",
    "run",
    "start",
    "stop",
    "state",
    "status",
    "autostart-on",
    "autostart-off",
    "init",
    "marquee",
    "history",
    "history-add",
    "history-import",
    "test-rules",
}


def has_cli_command(argv):
    """Detect CLI passthrough without mistaking an option value for a command."""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("--config", "--lang"):
            index += 2
            continue
        if token in CLI_COMMANDS:
            return True
        index += 1
    return False


def parse_gui_args(argv):
    parser = argparse.ArgumentParser(description="Camfrog feature app")
    parser.add_argument(
        "--config", default=None, help="use an alternate app-local config"
    )
    args = parser.parse_args(argv)
    if args.config is not None and not args.config.strip():
        parser.error("argument --config: expected a path")
    return args


SEL_FIELDS = ("control_type", "auto_id", "class_name", "title", "title_re", "index")


def bi(s):
    """'English|ไทย' -> text for the current language."""
    en, _, th = s.partition("|")
    return (th if (ca.LANG == "th" and th) else en).replace(
        "{pipe}", "||"
    )  # {pipe}: literal ||


# ---------------------------------------------------------------- Tk-free logic
def msgs_to_text(msgs):
    """status.messages -> editor text: one per line, `ไทย || English` for pairs."""
    return "\n".join(
        m if isinstance(m, str) else f"{m.get('th', '')} || {m.get('en', '')}"
        for m in msgs
    )


def text_to_msgs(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "||" in line:
            th, en = (x.strip() for x in line.split("||", 1))
            out.append({"th": th, "en": en} if th and en else (th or en))
            if not (th or en):
                out.pop()
        else:
            out.append(line)
    return out


def sel_to_strs(sel):
    sel = sel or {}
    return {k: ("" if sel.get(k) in (None, "") else str(sel[k])) for k in SEL_FIELDS}


def strs_to_sel(d, allow_empty=False):
    """Selector fields -> dict. Blank everywhere -> None (only valid for apply_button)."""
    out = {}
    for k in SEL_FIELDS:
        v = str(d.get(k, "")).strip()
        if v:
            out[k] = int(v) if k == "index" else v
    if not out:
        return None if allow_empty else {}
    out.setdefault("index", 0)
    return out


def iter_proposals(out):
    """Yield PROPOSAL dicts from detect output, skipping malformed lines.

    A corrupt PROPOSAL line must never kill the Tk pump loop.
    """
    for line in str(out).splitlines():
        if not line.startswith("PROPOSAL "):
            continue
        try:
            proposal = json.loads(line[9:])
        except ValueError:
            continue
        if isinstance(proposal, dict):
            yield proposal


def apply_proposal_to_vars(sel_vars, proposal):
    """Fill selector StringVars from a detect PROPOSAL dict.

    sel_vars maps key tuples to (vars_dict, optional) as built by
    selector_group. Keys with no visible box (e.g. status.edit in the
    room/im_reply profiles) are skipped, never KeyError.
    Returns (applied_keys, skipped_keys)."""
    applied, skipped = [], []
    for key, sel in proposal.items():
        pair = sel_vars.get(tuple(key.split(".")))
        if pair is None:
            skipped.append(key)
            continue
        vars_ = pair[0]
        for k in SEL_FIELDS:
            vars_[k].set(sel.get(k, "") if k != "index" else str(sel.get("index", 0)))
        applied.append(key)
    return applied, skipped


def reply_to_fields(spec):
    """rule reply (str | list | {th,en}) -> (th_text, en_text, any_text), one variant per line."""

    def lines(x):
        return "\n".join([x] if isinstance(x, str) else (x or []))

    if isinstance(spec, dict):
        return lines(spec.get("th")), lines(spec.get("en")), ""
    return "", "", lines(spec)


def fields_to_reply(th, en, anyl):
    def pool(x):
        v = [ln.strip() for ln in x.splitlines() if ln.strip()]
        return v[0] if len(v) == 1 else v

    th_p, en_p, any_p = pool(th), pool(en), pool(anyl)
    if not (th_p or en_p or any_p):
        return None
    if th_p or en_p:
        return {"th": th_p or any_p or en_p, "en": en_p or any_p or th_p}
    return any_p


class ConfigModel:
    """Load / validate / atomically save config.json. Never writes an invalid config."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        if APP_PROFILE in ("room", "im_reply"):
            try:
                self.path.relative_to(ca.BASE.resolve())
            except ValueError as exc:
                raise ValueError(
                    "This app's config must stay inside its own folder."
                ) from exc
        self.cfg = None

    def load(self):
        if not self.path.exists():
            with contextlib.redirect_stdout(io.StringIO()):
                ca.cmd_init(self.path, False)
        self.cfg = apply_app_profile(ca.load_cfg(self.path))
        return self.cfg

    @staticmethod
    def check(cfg):
        errs, warns = ca.validate(cfg)
        ar = cfg["autoreply"]
        if (
            not cfg["dry_run"]
            and ar["enabled"]
            and not ar["own_nickname"].strip()
            and ca.t("refuse_live") not in errs
        ):
            errs.append(ca.t("refuse_live"))
        return errs, warns

    def save(self, cfg):
        cfg = apply_app_profile(cfg)
        errs, warns = self.check(cfg)
        if errs:
            return errs, warns
        ca.atomic_write(self.path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        self.cfg = copy.deepcopy(cfg)
        return [], warns


def tail_text(path, nbytes=40000, lines=400):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - nbytes))
            data = f.read()
    except OSError:
        return ""
    return "\n".join(data.decode("utf-8", "replace").splitlines()[-lines:])


def read_stats(cfg):
    try:
        return json.loads(ca.stats_path(cfg).read_text(encoding="utf-8"))
    except Exception:
        return {}


def explain(cfg, nick, msg, im=False):
    """Human text for the offline room or private-message rule tester."""
    r = (ca.evaluate_im if im else ca.evaluate_message)(cfg, nick, msg)
    head = ca.t("tr_lang", l=r["lang"])
    if r["skip"]:
        return head + "\n" + ca.t("tr_skip", why=ca.t("why_" + r["skip"]))
    if r["rule"] is None:
        return head + "\n" + ca.t("tr_none")
    return (
        head
        + "\n"
        + ca.t("tr_match", i=r["rule"]["idx"], reply=r["reply"] or ca.t("tr_blocked"))
    )


def autosave_needs_confirmation(cfg, bot_running):
    """Live config changes need an explicit Save confirmation while the bot runs."""
    return bool(bot_running and not cfg.get("dry_run", True))


# ---------------------------------------------------------------- GUI
def build_app():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class RuleDialog(tk.Toplevel):
        def __init__(self, app, rule=None):
            super().__init__(app.root)
            self.result = None
            self.title(bi("Rule|กฎ"))
            self.transient(app.root)
            rule = rule or {"pattern": "", "reply": {"th": [], "en": []}}
            self.pat = tk.StringVar(value=rule.get("pattern", ""))
            self.lang = tk.StringVar(value=rule.get("lang", ""))
            self.cool = tk.StringVar(value=str(rule.get("cooldown_seconds", 0)))
            self.mention = tk.BooleanVar(value=bool(rule.get("mention")))
            self.enabled = tk.BooleanVar(value=rule.get("enabled", True))
            f = ttk.Frame(self, padding=10)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text=bi("Pattern (regex)|รูปแบบ (regex)")).grid(
                row=0, column=0, sticky="w"
            )
            ttk.Entry(f, textvariable=self.pat, width=60).grid(
                row=0, column=1, columnspan=3, sticky="we"
            )
            th, en, anyl = reply_to_fields(rule.get("reply"))
            self.boxes = []
            for r, (lab, val) in enumerate(
                (
                    (bi("Reply TH (one per line = random)|ตอบไทย (บรรทัดละแบบ สุ่ม)"), th),
                    (bi("Reply EN|ตอบอังกฤษ"), en),
                    (bi("Reply any language|ตอบทุกภาษา"), anyl),
                ),
                1,
            ):
                ttk.Label(f, text=lab).grid(row=r, column=0, sticky="nw", pady=2)
                box = tk.Text(f, width=60, height=3)
                box.insert("1.0", val)
                box.grid(row=r, column=1, columnspan=3, sticky="we", pady=2)
                self.boxes.append(box)
            ttk.Label(f, text=bi("Only language|เฉพาะภาษา")).grid(
                row=4, column=0, sticky="w"
            )
            ttk.Combobox(
                f,
                textvariable=self.lang,
                values=("", "th", "en"),
                width=6,
                state="readonly",
            ).grid(row=4, column=1, sticky="w")
            ttk.Label(f, text=bi("Cooldown (s)|คูลดาวน์ (วินาที)")).grid(
                row=4, column=2, sticky="e"
            )
            ttk.Entry(f, textvariable=self.cool, width=8).grid(
                row=4, column=3, sticky="w"
            )
            ttk.Checkbutton(
                f,
                text=bi("Only when my nick is mentioned|ตอบเมื่อมีคนพิมพ์นิคเรา"),
                variable=self.mention,
            ).grid(row=5, column=0, columnspan=2, sticky="w")
            ttk.Checkbutton(f, text=bi("Enabled|เปิดใช้"), variable=self.enabled).grid(
                row=5, column=2, columnspan=2, sticky="w"
            )
            ttk.Label(
                f,
                text=bi(
                    "Variables: {sender} {me} {time} {date}. Replies starting with / are blocked.|"
                    "ตัวแปร: {sender} {me} {time} {date} คำตอบที่ขึ้นต้นด้วย / จะถูกบล็อก"
                ),
                foreground="#626d7a",
            ).grid(row=6, column=0, columnspan=4, sticky="w", pady=4)
            bar = ttk.Frame(f)
            bar.grid(row=7, column=0, columnspan=4, sticky="e")
            ttk.Button(bar, text="OK", command=self.ok).pack(side="left", padx=4)
            ttk.Button(bar, text=bi("Cancel|ยกเลิก"), command=self.destroy).pack(
                side="left"
            )
            app.install_clipboard_support(self, remember=False)
            self.grab_set()
            self.wait_window()

        def ok(self):
            try:
                pat = self.pat.get()
                re.compile(pat, re.I)
                reply = fields_to_reply(*(b.get("1.0", "end") for b in self.boxes))
                cool = float(self.cool.get() or 0)
                if not pat or reply is None or cool < 0:
                    raise ValueError(
                        bi("pattern and reply are required|ต้องมี pattern และ reply")
                    )
            except (re.error, ValueError) as e:
                messagebox.showerror(bi("Rule|กฎ"), str(e), parent=self)
                return
            rule = {"pattern": pat, "reply": reply}
            if self.mention.get():
                rule["mention"] = True
            if self.lang.get():
                rule["lang"] = self.lang.get()
            if cool:
                rule["cooldown_seconds"] = int(cool) if cool == int(cool) else cool
            if not self.enabled.get():
                rule["enabled"] = False
            self.result = rule
            self.destroy()

    class App:
        def __init__(self, cfg_path):
            self.root = tk.Tk()
            self.root.configure(background="#f4f1e8")
            style = ttk.Style(self.root)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("TFrame", background="#f4f1e8")
            style.configure(
                "TLabel",
                background="#f4f1e8",
                foreground="#283648",
                font=("Segoe UI", 9),
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
            style.map(
                "TButton", background=[("active", "#d6e9e8"), ("pressed", "#d5c8ac")]
            )
            style.configure(
                "TEntry",
                padding=(4, 4),
                fieldbackground="#ffffff",
                foreground="#283648",
            )
            style.configure("TNotebook", background="#f4f1e8")
            style.configure("TNotebook.Tab", padding=(11, 6), font=("Segoe UI", 9))
            style.map(
                "TNotebook.Tab",
                background=[("selected", "#fffdfa")],
                foreground=[("selected", "#8b672b")],
            )
            self.root.geometry("800x560")
            self.root.minsize(660, 460)
            self.model = ConfigModel(cfg_path)
            self.q = queue.Queue()
            self.busy = False
            self.last_log = None
            self.binds = []
            self._autosave_after = None
            self._autosave_traces = []
            self.clipboard_widgets = []
            try:
                self.draft = copy.deepcopy(self.model.load())
            except Exception as e:
                messagebox.showerror(APP_NAME, ca.t("cannot_load", e=e))
                self.root.destroy()
                raise SystemExit(2)
            ca.LANG = ca.resolve_lang(self.draft["language"])
            self.build()
            self._closing = False
            self.root.protocol("WM_DELETE_WINDOW", self.close_app)
            self.root.bind("<Control-s>", lambda _e: self.save())
            self.root.bind("<F3>", lambda _e: self.start())
            self.root.bind("<F4>", lambda _e: self.stop())
            self.root.after(150, self.pump)
            self.root.after(500, self.tick)

        # ---- close
        def close_app(self):
            """Cleanly tear down all scheduled callbacks so the process exits."""
            if self._closing:
                return
            self._closing = True
            self.cancel_autosave()
            try:
                self.root.quit()
            except Exception:
                pass
            try:
                self.root.destroy()
            except Exception:
                pass

        # ---- layout
        def build(self):
            self.cancel_autosave()
            for var, trace_id in self._autosave_traces:
                try:
                    var.trace_remove("write", trace_id)
                except (tk.TclError, AttributeError):
                    pass
            self._autosave_traces = []
            self.clipboard_widgets = []
            for w in self.root.winfo_children():
                w.destroy()
            self.binds = []
            self.root.title(f"{APP_NAME}  -  {self.model.path}")
            self.setup_clipboard_menu()
            top = ttk.Frame(self.root, padding=(8, 6))
            top.pack(fill="x")
            self.state_lbl = ttk.Label(top, text="...", font=("Segoe UI", 14, "bold"))
            self.state_lbl.pack(side="left")
            self.mode_lbl = ttk.Label(top, text="", font=("Segoe UI", 11, "bold"))
            self.mode_lbl.pack(side="left", padx=14)
            ttk.Button(top, text="TH / EN", width=8, command=self.switch_lang).pack(
                side="right"
            )
            bar = ttk.Frame(self.root, padding=(8, 6))
            bar.pack(side="bottom", fill="x")
            ttk.Button(
                bar, text=bi("Save (Ctrl+S)|บันทึก (Ctrl+S)"), command=self.save
            ).pack(side="left")
            ttk.Button(
                bar, text=bi("Reload from disk|โหลดจากไฟล์ใหม่"), command=self.reload
            ).pack(side="left", padx=6)
            ttk.Button(
                bar, text=bi("Check config|ตรวจคอนฟิก"), command=self.check_cfg
            ).pack(side="left")
            if not hasattr(self, "auto_apply_var"):
                self.auto_apply_var = tk.BooleanVar(master=self.root, value=True)
            ttk.Checkbutton(
                bar,
                text=bi("Auto-apply valid changes|ใช้ค่าที่ถูกต้องอัตโนมัติ"),
                variable=self.auto_apply_var,
                command=self.auto_apply_toggled,
            ).pack(side="right")
            self.msg = ttk.Label(bar, text="", foreground="#626d7a")
            self.msg.pack(side="left", padx=12)
            self.nb = ttk.Notebook(self.root)
            self.nb.pack(fill="both", expand=True, padx=8)
            tab_specs = (
                ("Dashboard|แดชบอร์ด", "Dashboard", self.tab_dashboard),
                ("Setup|ตั้งค่าเริ่มต้น", "Setup", self.tab_setup),
                ("Status|สถานะ", "Status", self.tab_status),
                ("Auto-reply|ตอบอัตโนมัติ", "Auto-reply", self.tab_reply),
                ("History|ประวัติ", "History", self.tab_history),
                ("Advanced|ขั้นสูง", "Advanced", self.tab_advanced),
            )
            visible_tabs = (
                {"Dashboard", "Setup", "Auto-reply"}
                if APP_PROFILE in ("room", "im_reply")
                else None
            )
            for name, key, fn in tab_specs:
                f = ttk.Frame(self.nb, padding=8)
                fn(f)
                if visible_tabs is None or key in visible_tabs:
                    self.nb.add(f, text=bi(name))
            self.load_ui()
            self.install_clipboard_support(self.root)
            self.attach_autosave_hooks()
            self.refresh_state()

        def setup_clipboard_menu(self):
            old = getattr(self, "clipboard_controller", None)
            if old is not None:
                try:
                    old.menu.destroy()
                except tk.TclError:
                    pass
            self.clipboard_controller = ClipboardController(
                self.root, translate=bi, on_error=self.clipboard_error
            )
            self.clipboard_menu = self.clipboard_controller.menu
            self.clipboard_target = None

        def install_clipboard_support(self, parent, remember=True):
            """Install reliable clipboard shortcuts and context menus on editors."""
            self.clipboard_controller.install(parent)
            if remember:
                self.clipboard_widgets = list(self.clipboard_controller.widgets)

        def clipboard_error(self, exc):
            if hasattr(self, "msg"):
                try:
                    self.msg.configure(
                        text=bi(
                            "Clipboard error: {error}|คลิปบอร์ดผิดพลาด: {error}"
                        ).format(error=exc)
                    )
                except tk.TclError:
                    pass

        def clipboard_virtual(self, event, virtual):
            self.clipboard_target = event.widget
            action = {"<<Copy>>": "copy", "<<Cut>>": "cut", "<<Paste>>": "paste"}.get(
                virtual
            )
            if action:
                self.clipboard_controller.perform(event.widget, action)
            return "break"

        def clipboard_action(self, virtual):
            widget = self.clipboard_target
            action = {"<<Copy>>": "copy", "<<Cut>>": "cut", "<<Paste>>": "paste"}.get(
                virtual
            )
            if action:
                self.clipboard_controller.perform(widget, action)

        def select_all(self, widget_or_event):
            widget = getattr(widget_or_event, "widget", widget_or_event)
            return self.clipboard_controller.select_all(widget)

        def show_clipboard_menu(self, event):
            self.clipboard_target = event.widget
            return self.clipboard_controller.show_menu(event)

        def field(self, parent, label, path, kind="str", choices=(), width=26, hint=""):
            row = parent.grid_size()[1]
            ttk.Label(parent, text=bi(label)).grid(
                row=row, column=0, sticky="w", padx=4, pady=2
            )
            if kind == "bool":
                var = tk.BooleanVar()
                w = ttk.Checkbutton(parent, variable=var)
            elif kind == "choice":
                var = tk.StringVar()
                w = ttk.Combobox(
                    parent,
                    textvariable=var,
                    values=choices,
                    state="readonly",
                    width=width - 3,
                )
            else:
                var = tk.StringVar()
                w = ttk.Entry(parent, textvariable=var, width=width)
            w.grid(row=row, column=1, sticky="w", padx=4, pady=2)
            if hint:
                ttk.Label(parent, text=bi(hint), foreground="#626d7a").grid(
                    row=row, column=2, sticky="w"
                )
            self.binds.append((path, kind, var, label))
            return var

        def textbox(self, parent, height, key):
            box = tk.Text(parent, height=height, width=70, undo=True)
            setattr(self, key, box)
            return box

        # ---- tabs
        def tab_dashboard(self, f):
            row = ttk.Frame(f)
            row.pack(fill="x")
            self.btn_start = ttk.Button(
                row,
                text=bi("Save & Start (background)|บันทึกและเริ่ม (เบื้องหลัง)"),
                command=self.start,
            )
            self.btn_start.pack(side="left")
            self.btn_bg = ttk.Button(
                row,
                text=bi("Start & close window|เริ่มเบื้องหลังแล้วปิดหน้าต่าง"),
                command=lambda: self.start(close=True),
            )
            self.btn_bg.pack(side="left", padx=6)
            self.btn_stop = ttk.Button(row, text=bi("Stop|หยุด"), command=self.stop)
            self.btn_stop.pack(side="left", padx=6)
            ttk.Button(
                row, text=bi("EMERGENCY STOP|หยุดฉุกเฉิน"), command=self.emergency
            ).pack(side="left", padx=6)
            self.live_var = tk.BooleanVar()
            self.binds.append((("dry_run",), "live", self.live_var, "dry_run"))
            ttk.Checkbutton(
                row,
                text=bi("LIVE: really send messages|โหมดจริง: ส่งข้อความจริง"),
                variable=self.live_var,
                command=self.live_toggled,
            ).pack(side="right")
            self.stats_lbl = ttk.Label(f, text="")
            self.stats_lbl.pack(anchor="w", pady=6)
            ttk.Label(
                f,
                text=bi(
                    "Closing this window does not stop the bot. Dry-run only logs what it would send.|"
                    "ปิดหน้าต่างนี้ไม่หยุดบอท โหมดทดลองแค่บันทึกว่าจะส่งอะไร"
                ),
                foreground="#626d7a",
            ).pack(anchor="w")
            lf = ttk.Frame(f)
            lf.pack(fill="both", expand=True, pady=6)
            self.log = tk.Text(
                lf, state="disabled", wrap="none", height=18, font=("Consolas", 9)
            )
            sb = ttk.Scrollbar(lf, command=self.log.yview)
            self.log.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.log.pack(fill="both", expand=True)

        def selector_group(self, parent, title, key, optional=False):
            lf = ttk.LabelFrame(parent, text=bi(title), padding=6)
            lf.pack(fill="x", pady=4)
            vars_ = {}
            for i, k in enumerate(SEL_FIELDS):
                ttk.Label(lf, text=k).grid(row=0, column=i, sticky="w", padx=3)
                v = tk.StringVar()
                ttk.Entry(lf, textvariable=v, width=14).grid(row=1, column=i, padx=3)
                vars_[k] = v
            self.sel_vars[key] = (vars_, optional)

        def tab_setup(self, f):
            self.sel_vars = {}
            g = ttk.Frame(f)
            g.pack(fill="x")
            self.field(
                g,
                "Window title regex|regex ชื่อหน้าต่าง",
                ("window_title_regex",),
                width=40,
            )
            self.field(
                g,
                "My nickname|นิคของฉัน",
                ("autoreply", "own_nickname"),
                width=40,
                hint="required for live mode|จำเป็นเมื่อรันจริง",
            )
            row = ttk.Frame(f)
            row.pack(fill="x", pady=4)
            ttk.Button(
                row,
                text=bi("List windows|แสดงหน้าต่าง"),
                command=lambda: self.helper("windows"),
            ).pack(side="left")
            ttk.Button(
                row,
                text=bi("Auto-detect (Camfrog 8.x)|ตรวจหา control อัตโนมัติ"),
                command=self.detect,
            ).pack(side="left", padx=6)
            ttk.Button(
                row,
                text=bi("Discover controls -> controls.txt|ดึง control -> controls.txt"),
                command=lambda: self.helper("discover"),
            ).pack(side="left", padx=6)
            ttk.Label(
                row,
                text=bi(
                    "Windows only. Copy values from controls.txt into the boxes below.|"
                    "เฉพาะ Windows คัดลอกค่าจาก controls.txt มาใส่ช่องด้านล่าง"
                ),
                foreground="#626d7a",
            ).pack(side="left")
            if APP_PROFILE == "full":
                self.selector_group(
                    f, "Status box selector|selector ช่องสถานะ", ("status", "edit")
                )
                self.selector_group(
                    f,
                    "Status apply button (optional)|ปุ่มยืนยันสถานะ (ไม่จำเป็น)",
                    ("status", "apply_button"),
                    True,
                )
            self.selector_group(
                f, "Chat history selector|selector ประวัติแชท", ("autoreply", "history")
            )
            self.selector_group(
                f, "Chat input selector|selector ช่องพิมพ์แชท", ("autoreply", "input")
            )
            self.helper_out = tk.Text(f, height=8, state="disabled")
            self.helper_out.pack(fill="both", expand=True, pady=4)

        def tab_status(self, f):
            top = ttk.Frame(f)
            top.pack(fill="x")
            g = ttk.Frame(top)
            g.pack(side="left", anchor="n")
            lf = ttk.LabelFrame(
                top, text=bi("History as source|ใช้ประวัติเป็นแหล่งข้อความ"), padding=6
            )
            lf.pack(side="left", anchor="n", padx=30)
            h = ("status", "history")
            self.field(lf, "Record statuses|บันทึกสถานะ", h + ("record",), "bool")
            self.field(
                lf, "Use as source|ใช้เป็นแหล่งสถานะ", h + ("use_as_source",), "bool"
            )
            self.field(
                lf,
                "Mode|โหมด",
                h + ("mode",),
                "choice",
                ("rotate", "random", "most_used"),
                12,
            )
            self.field(lf, "Max items|จำนวนสูงสุด", h + ("max_items",), "int", width=6)
            self.field(g, "Enabled|เปิดใช้", ("status", "enabled"), "bool")
            self.field(
                g,
                "Interval (s, min 0.3)|ช่วงเวลา (วินาที ขั้นต่ำ 0.3)",
                ("status", "interval_seconds"),
                "float",
                width=8,
            )
            self.field(g, "Random order|สุ่มลำดับ", ("status", "random"), "bool")
            self.field(
                g,
                "Language mode|โหมดภาษา",
                ("status", "language_mode"),
                "choice",
                ("th", "en", "both", "alternate"),
                12,
            )
            self.field(
                g,
                "Language cycle|สลับภาษา",
                ("status", "language_cycle"),
                "list",
                width=12,
                hint="e.g. th, en",
            )
            self.field(
                g, "Max length|ความยาวสูงสุด", ("status", "max_length"), "int", width=8
            )
            self.field(
                g, "Set on start|ตั้งทันทีตอนเริ่ม", ("status", "set_on_start"), "bool"
            )
            ttk.Label(
                f,
                text=bi(
                    "Messages: one per line. Pair = Thai {pipe} English. Variables {time} {date} {me}|"
                    "ข้อความ: บรรทัดละอัน คู่ภาษา = ไทย {pipe} English ตัวแปร {time} {date} {me}"
                ),
            ).pack(anchor="w", pady=(8, 0))
            self.textbox(f, 5, "t_msgs").pack(fill="x")
            lf = ttk.LabelFrame(
                f, text=bi("Scrolling status (marquee)|สถานะเลื่อน"), padding=6
            )
            lf.pack(fill="x", pady=6)
            g1, g2 = ttk.Frame(lf), ttk.Frame(lf)
            g1.pack(side="left", anchor="n")
            g2.pack(side="left", padx=20, fill="x", expand=True)
            m = ("status", "marquee")
            self.field(g1, "Enabled|เปิดใช้", m + ("enabled",), "bool")
            self.field(g1, "Scroll frames|เลื่อนเฟรม", m + ("scroll",), "bool")
            self.field(g1, "Width (8-80)|ความกว้าง", m + ("width",), "int", width=6)
            self.field(g1, "Stride|เลื่อนต่อเฟรม", m + ("stride",), "int", width=6)
            self.field(
                g1,
                "Step seconds (min 0.3)|วินาทีต่อเฟรม",
                m + ("step_seconds",),
                "float",
                width=6,
            )
            self.field(g1, "Cycles (1-5)|จำนวนรอบ", m + ("cycles",), "int", width=6)
            self.field(g1, "Max frames|เฟรมสูงสุด", m + ("max_frames",), "int", width=6)
            self.mq_in = tk.StringVar(
                value="ข้อความยาว ๆ ที่อยากให้เลื่อน / a long status that scrolls"
            )
            ttk.Entry(g2, textvariable=self.mq_in).pack(fill="x")
            ttk.Button(
                g2, text=bi("Preview frames|ดูตัวอย่างเฟรม"), command=self.preview_marquee
            ).pack(anchor="w", pady=3)
            self.mq_out = tk.Text(g2, height=6, state="disabled")
            self.mq_out.pack(fill="x")

        def tab_reply(self, f):
            tabs = ttk.Notebook(f)
            tabs.pack(fill="both", expand=True)
            room = ttk.Frame(tabs, padding=6)
            im = ttk.Frame(tabs, padding=6)
            self.tab_room_reply(room)
            self.tab_im_reply(im)
            if APP_PROFILE in ("full", "room"):
                tabs.add(room, text=bi("Room chat|แชทในห้อง"))
            if APP_PROFILE in ("full", "im_reply"):
                tabs.add(im, text=bi("Private IM|แชทส่วนตัว IM"))

        def tab_room_reply(self, f):
            top = ttk.Frame(f)
            top.pack(fill="x")
            self.field(
                top, "Auto-reply enabled|เปิดตอบอัตโนมัติ", ("autoreply", "enabled"), "bool"
            )
            self.field(
                top,
                "Ignore links|ไม่ตอบข้อความที่มีลิงก์",
                ("autoreply", "ignore_links"),
                "bool",
            )
            self.field(
                top,
                "Per-sender cooldown (s)|คูลดาวน์ต่อคน",
                ("autoreply", "per_sender_cooldown_seconds"),
                "int",
                width=8,
            )
            self.field(
                top,
                "Global gap (s)|ระยะห่างทุกคำตอบ",
                ("autoreply", "global_min_gap_seconds"),
                "int",
                width=8,
            )
            self.field(
                top,
                "Max per hour|สูงสุดต่อชั่วโมง",
                ("autoreply", "max_per_hour"),
                "int",
                width=8,
            )
            self.field(
                top,
                "Delay range s (min, max)|หน่วงสุ่ม",
                ("autoreply", "delay_range_seconds"),
                "pair",
                width=12,
            )
            self.field(
                top,
                "Ignore nicks|ข้ามนิค",
                ("autoreply", "ignore_nicknames"),
                "list",
                width=40,
                hint="comma separated|คั่นด้วย ,",
            )
            self.field(
                top,
                "Only nicks|ตอบเฉพาะนิค",
                ("autoreply", "only_nicknames"),
                "list",
                width=40,
                hint="empty = everyone|ว่าง = ทุกคน",
            )
            ttk.Label(
                f,
                text=bi(
                    "Rules (first match wins - put specific rules first)|กฎ (ตัวแรกที่ตรงชนะ วางกฎเฉพาะไว้ก่อน)"
                ),
            ).pack(anchor="w", pady=(8, 0))
            tf = ttk.Frame(f)
            tf.pack(fill="both", expand=True)
            cols = ("n", "on", "lang", "men", "pat", "rep")
            self.tree = ttk.Treeview(
                tf, columns=cols, show="headings", height=7, selectmode="browse"
            )
            for c, w, h in (
                ("n", 30, "#"),
                ("on", 40, "on"),
                ("lang", 45, "lang"),
                ("men", 50, "@me"),
                ("pat", 300, "pattern"),
                ("rep", 380, "reply"),
            ):
                self.tree.heading(c, text=h)
                self.tree.column(c, width=w, anchor="w")
            self.tree.pack(side="left", fill="both", expand=True)
            self.tree.bind("<Double-1>", lambda _e: self.rule_edit())
            bf = ttk.Frame(tf)
            bf.pack(side="left", padx=6, anchor="n")
            for txt, fn in (
                ("Add|เพิ่ม", self.rule_add),
                ("Edit|แก้", self.rule_edit),
                ("Delete|ลบ", self.rule_del),
                ("Up|ขึ้น", lambda: self.rule_move(-1)),
                ("Down|ลง", lambda: self.rule_move(1)),
            ):
                ttk.Button(bf, text=bi(txt), command=fn, width=8).pack(pady=1)
            ttk.Label(
                f,
                text=bi(
                    "Skip patterns (regex, one per line)|skip patterns (regex บรรทัดละอัน)"
                ),
            ).pack(anchor="w")
            self.textbox(f, 2, "t_skips").pack(fill="x")
            lf = ttk.LabelFrame(
                f,
                text=bi(
                    "Rule tester (offline, nothing is sent)|ทดสอบกฎ (ออฟไลน์ ไม่ส่งจริง)"
                ),
                padding=6,
            )
            lf.pack(fill="x", pady=6)
            self.tn = tk.StringVar(value="TestUser")
            self.tm = tk.StringVar(value="สวัสดีครับ")
            ttk.Entry(lf, textvariable=self.tn, width=14).pack(side="left")
            e = ttk.Entry(lf, textvariable=self.tm, width=44)
            e.pack(side="left", padx=4)
            e.bind("<Return>", lambda _e: self.test_rule())
            ttk.Button(lf, text=bi("Test|ทดสอบ"), command=self.test_rule).pack(
                side="left"
            )
            self.tout = ttk.Label(lf, text="", foreground="#28744c")
            self.tout.pack(side="left", padx=8)

        def tab_im_reply(self, f):
            ttk.Label(
                f,
                text=bi(
                    "IM shares your own nickname and chat selectors from Setup; only listed nicks can be answered.|"
                    "IM ใช้นิคของคุณและ selector จากหน้า Setup ร่วมกัน และตอบได้เฉพาะนิคในรายการ"
                ),
                foreground="#626d7a",
                wraplength=900,
            ).pack(anchor="w", pady=(0, 4))
            columns = ttk.Frame(f)
            columns.pack(fill="x")
            left = ttk.LabelFrame(columns, text=bi("Private IM|แชทส่วนตัว"), padding=4)
            left.pack(side="left", fill="x", expand=True, padx=(0, 6))
            right = ttk.LabelFrame(
                columns, text=bi("Limits and safety|เพดานและความปลอดภัย"), padding=4
            )
            right.pack(side="left", fill="x", expand=True)
            self.field(left, "Enabled|เปิดใช้", ("autoreply_im", "enabled"), "bool")
            self.field(
                left, "IM dry-run|ทดลองส่ง IM", ("autoreply_im", "dry_run"), "bool"
            )
            self.field(
                left,
                "Window title regex|regex ชื่อหน้าต่าง",
                ("autoreply_im", "window_title_regex"),
                width=30,
            )
            self.field(
                left,
                "Window kinds|ชนิดหน้าต่าง",
                ("autoreply_im", "log_kinds"),
                "list",
                width=20,
            )
            self.field(
                left,
                "Allowed nicknames|นิคที่อนุญาต",
                ("autoreply_im", "only_nicknames"),
                "list",
                width=30,
            )
            self.field(
                left,
                "Reply prefix|ข้อความนำหน้าคำตอบ",
                ("autoreply_im", "prefix"),
                width=24,
            )
            self.field(
                left,
                "Answer first new message|ตอบข้อความแรกของหน้าต่างใหม่",
                ("autoreply_im", "answer_first_message"),
                "bool",
            )
            self.field(
                left,
                "Ignore links|ข้ามข้อความที่มีลิงก์",
                ("autoreply_im", "ignore_links"),
                "bool",
            )
            self.field(
                right,
                "Max tracked windows (1-20)|จำนวนหน้าต่างสูงสุด",
                ("autoreply_im", "max_windows"),
                "int",
                width=7,
            )
            self.field(
                right,
                "Per-sender cooldown (s)|คูลดาวน์ต่อคน (วินาที)",
                ("autoreply_im", "per_sender_cooldown_seconds"),
                "int",
                width=8,
            )
            self.field(
                right,
                "Per-sender daily cap|เพดานต่อคนต่อวัน",
                ("autoreply_im", "max_per_sender_per_day"),
                "int",
                width=7,
            )
            self.field(
                right,
                "Global gap (s)|ระยะห่างทุกคำตอบ (วินาที)",
                ("autoreply_im", "global_min_gap_seconds"),
                "int",
                width=7,
            )
            self.field(
                right,
                "Max per hour|สูงสุดต่อชั่วโมง",
                ("autoreply_im", "max_per_hour"),
                "int",
                width=7,
            )
            self.field(
                right,
                "Delay range s (min, max)|หน่วงสุ่ม (ต่ำสุด, สูงสุด)",
                ("autoreply_im", "delay_range_seconds"),
                "pair",
                width=12,
            )
            self.field(
                right,
                "Max reply length|ความยาวคำตอบสูงสุด",
                ("autoreply_im", "max_reply_length"),
                "int",
                width=7,
            )
            self.field(
                right,
                "Max incoming length|ความยาวข้อความขาเข้าสูงสุด",
                ("autoreply_im", "max_incoming_length"),
                "int",
                width=7,
            )
            ttk.Label(
                f,
                text=bi(
                    "When IM is enabled, allow at least one nickname. Keep window kinds at im unless im-probe confirms mtim.|"
                    "เมื่อเปิด IM ต้องใส่นิคอย่างน้อยหนึ่งชื่อ ใช้ชนิดหน้าต่าง im จนกว่า im-probe จะยืนยัน mtim"
                ),
                foreground="#626d7a",
                wraplength=900,
            ).pack(anchor="w", pady=(2, 0))
            rule_box = ttk.LabelFrame(
                f,
                text=bi("IM rules (first match wins)|กฎ IM (กฎแรกที่ตรงจะทำงาน)"),
                padding=4,
            )
            rule_box.pack(fill="both", expand=True, pady=(6, 2))
            tf = ttk.Frame(rule_box)
            tf.pack(fill="both", expand=True)
            cols = ("n", "on", "lang", "men", "pat", "rep")
            self.im_tree = ttk.Treeview(
                tf, columns=cols, show="headings", height=5, selectmode="browse"
            )
            for c, w, h in (
                ("n", 30, "#"),
                ("on", 40, "on"),
                ("lang", 45, "lang"),
                ("men", 50, "@me"),
                ("pat", 300, "pattern"),
                ("rep", 380, "reply"),
            ):
                self.im_tree.heading(c, text=h)
                self.im_tree.column(c, width=w, anchor="w")
            self.im_tree.pack(side="left", fill="both", expand=True)
            self.im_tree.bind("<Double-1>", lambda _e: self.rule_edit("im"))
            bf = ttk.Frame(tf)
            bf.pack(side="left", padx=6, anchor="n")
            for txt, fn in (
                ("Add|เพิ่ม", lambda: self.rule_add("im")),
                ("Edit|แก้", lambda: self.rule_edit("im")),
                ("Delete|ลบ", lambda: self.rule_del("im")),
                ("Up|ขึ้น", lambda: self.rule_move(-1, "im")),
                ("Down|ลง", lambda: self.rule_move(1, "im")),
            ):
                ttk.Button(bf, text=bi(txt), command=fn, width=8).pack(pady=1)
            ttk.Label(
                f,
                text=bi(
                    "IM skip patterns (regex, one per line)|รูปแบบที่ข้ามใน IM (regex บรรทัดละอัน)"
                ),
            ).pack(anchor="w")
            self.textbox(f, 2, "t_im_skips").pack(fill="x")
            tester = ttk.LabelFrame(
                f,
                text=bi("Private IM rule tester (offline)|ทดสอบกฎ IM (ออฟไลน์)"),
                padding=4,
            )
            tester.pack(fill="x", pady=(4, 0))
            self.tin = tk.StringVar(value="zdevz")
            self.tim = tk.StringVar(value="สวัสดีครับ")
            self.im_tester_sender_entry = ttk.Entry(
                tester, textvariable=self.tin, width=14
            )
            self.im_tester_sender_entry.pack(side="left")
            entry = ttk.Entry(tester, textvariable=self.tim, width=42)
            entry.pack(side="left", padx=4)
            entry.bind("<Return>", lambda _e: self.test_im_rule())
            ttk.Button(
                tester, text=bi("Test IM|ทดสอบ IM"), command=self.test_im_rule
            ).pack(side="left")
            self.tiout = ttk.Label(tester, text="", foreground="#28744c")
            self.tiout.pack(side="left", padx=8)

        def tab_history(self, f):
            self.hist_note = ttk.Label(f, text="", foreground="#8b672b")
            self.hist_note.pack(anchor="w")
            self.htree = ttk.Treeview(
                f, columns=("lang", "n", "text"), show="headings", height=14
            )
            for c, w, h in (
                ("lang", 50, "lang"),
                ("n", 60, "uses"),
                ("text", 700, "text"),
            ):
                self.htree.heading(c, text=h)
                self.htree.column(c, width=w, anchor="w")
            self.htree.pack(fill="both", expand=True)
            row = ttk.Frame(f)
            row.pack(fill="x", pady=4)
            self.hist_btns = []
            for txt, fn in (
                ("Refresh|รีเฟรช", self.hist_refresh),
                ("Add...|เพิ่ม...", self.hist_add),
                ("Import file...|นำเข้าไฟล์...", self.hist_import),
                ("Delete selected|ลบที่เลือก", self.hist_del),
            ):
                b = ttk.Button(row, text=bi(txt), command=fn)
                b.pack(side="left", padx=3)
                self.hist_btns.append(b)
            self.hist_entry = tk.StringVar()
            self.hist_entry_w = ttk.Entry(row, textvariable=self.hist_entry, width=50)
            self.hist_entry_w.pack(side="left", padx=6)

        def tab_advanced(self, f):
            g = ttk.Frame(f)
            g.pack(fill="x")
            self.field(
                g, "Language|ภาษา", ("language",), "choice", ("auto", "th", "en"), 8
            )
            self.field(
                g, "Poll seconds (0.2-60)|ความถี่ลูป", ("poll_seconds",), "float", width=8
            )
            self.field(
                g, "Hot reload config|โหลดคอนฟิกใหม่เอง", ("reload_config",), "bool"
            )
            self.field(
                g,
                "Active hours only|ทำงานเฉพาะช่วงเวลา",
                ("active_hours", "enabled"),
                "bool",
            )
            self.field(g, "  start (HH:MM)|  เริ่ม", ("active_hours", "start"), width=8)
            self.field(g, "  end (HH:MM)|  สิ้นสุด", ("active_hours", "end"), width=8)
            self.field(
                g,
                "Require Camfrog foreground|ต้องอยู่หน้าสุดก่อนส่ง",
                ("safety", "require_foreground"),
                "bool",
            )
            self.field(
                g,
                "Restore previous window|คืนโฟกัสหลังส่ง",
                ("safety", "restore_previous_window"),
                "bool",
            )
            self.field(
                g,
                "Max consecutive failures|ล้มเหลวติดกันสูงสุด",
                ("safety", "max_consecutive_failures"),
                "int",
                width=6,
            )
            self.field(
                g,
                "Log level|ระดับล็อก",
                ("log", "level"),
                "choice",
                ("DEBUG", "INFO", "WARNING", "ERROR"),
                10,
            )
            self.field(
                g,
                "Log chat text|เก็บเนื้อข้อความใน log",
                ("log", "log_message_text"),
                "bool",
                hint="off = privacy|ปิด = ความเป็นส่วนตัว",
            )
            self.field(g, "Stats enabled|เก็บสถิติ", ("stats", "enabled"), "bool")
            row = ttk.Frame(f)
            row.pack(fill="x", pady=10)
            ttk.Button(
                row,
                text=bi("Start at Windows login|เริ่มเมื่อเข้า Windows"),
                command=lambda: self.helper("autostart-on"),
            ).pack(side="left")
            ttk.Button(
                row,
                text=bi("Remove autostart|ยกเลิก autostart"),
                command=lambda: self.helper("autostart-off"),
            ).pack(side="left", padx=6)

        # ---- config <-> widgets
        @staticmethod
        def _get(d, path):
            for k in path:
                d = d[k]
            return d

        @staticmethod
        def _set(d, path, v):
            for k in path[:-1]:
                d = d[k]
            d[path[-1]] = v

        def load_ui(self):
            d = self.draft
            for path, kind, var, _lab in self.binds:
                v = self._get(d, path)
                if kind == "bool":
                    var.set(bool(v))
                elif kind == "live":
                    var.set(not v)
                elif kind in ("list", "pair"):
                    var.set(", ".join(str(x) for x in v))
                else:
                    var.set(str(v))
            for key, (vars_, _opt) in self.sel_vars.items():
                for k, s in sel_to_strs(self._get(d, key)).items():
                    vars_[k].set(s)
            self.t_msgs.insert("1.0", msgs_to_text(d["status"]["messages"]))
            self.t_skips.insert("1.0", "\n".join(d["autoreply"]["skip_patterns"]))
            self.t_im_skips.insert("1.0", "\n".join(d["autoreply_im"]["skip_patterns"]))
            self.rules = copy.deepcopy(d["autoreply"]["rules"])
            self.im_rules = copy.deepcopy(d["autoreply_im"]["rules"])
            self.rules_refresh()
            self.rules_refresh(scope="im")
            for box in (self.t_msgs, self.t_skips, self.t_im_skips):
                box.edit_modified(False)
            self.hist_refresh()

        def collect(self):
            """Widgets -> new cfg dict. Returns (cfg, errors)."""
            d, errs = copy.deepcopy(self.draft), []
            for path, kind, var, lab in self.binds:
                try:
                    raw = var.get()
                    if kind == "bool":
                        v = bool(raw)
                    elif kind == "live":
                        v = not raw
                    elif kind == "int":
                        v = int(str(raw).strip())
                    elif kind == "float":
                        v = float(str(raw).strip())
                    elif kind == "list":
                        v = [x.strip() for x in str(raw).split(",") if x.strip()]
                    elif kind == "pair":
                        v = [float(x) for x in str(raw).split(",")]
                        if len(v) != 2:
                            raise ValueError("need 2 numbers")
                    else:
                        v = str(raw)
                    self._set(d, path, v)
                except ValueError:
                    errs.append(f"{bi(lab)}: {bi('invalid value|ค่าไม่ถูกต้อง')}")
            for key, (vars_, optional) in self.sel_vars.items():
                try:
                    self._set(
                        d,
                        key,
                        strs_to_sel({k: v.get() for k, v in vars_.items()}, optional),
                    )
                except ValueError:
                    errs.append(f"{'.'.join(key)}: index")
            d["status"]["messages"] = text_to_msgs(self.t_msgs.get("1.0", "end"))
            d["autoreply"]["skip_patterns"] = [
                x.strip()
                for x in self.t_skips.get("1.0", "end").splitlines()
                if x.strip()
            ]
            d["autoreply"]["rules"] = copy.deepcopy(self.rules)
            d["autoreply_im"]["skip_patterns"] = [
                x.strip()
                for x in self.t_im_skips.get("1.0", "end").splitlines()
                if x.strip()
            ]
            d["autoreply_im"]["rules"] = copy.deepcopy(self.im_rules)
            return d, errs

        # ---- actions
        def say(self, text, bad=False):
            self.msg.configure(text=text, foreground="#a3303a" if bad else "#28744c")

        def attach_autosave_hooks(self):
            vars_ = [var for _path, _kind, var, _label in self.binds]
            vars_.extend(
                v for group, _optional in self.sel_vars.values() for v in group.values()
            )
            for var in vars_:
                trace_id = var.trace_add("write", self.schedule_autosave)
                self._autosave_traces.append((var, trace_id))
            for box in (self.t_msgs, self.t_skips, self.t_im_skips):
                box.bind("<<Modified>>", self.text_changed)

        def text_changed(self, event):
            box = event.widget
            if box.edit_modified():
                box.edit_modified(False)
                self.schedule_autosave()

        def cancel_autosave(self):
            if self._autosave_after is not None:
                try:
                    self.root.after_cancel(self._autosave_after)
                except tk.TclError:
                    pass
                self._autosave_after = None

        def schedule_autosave(self, *_args):
            if (
                not getattr(self, "auto_apply_var", None)
                or not self.auto_apply_var.get()
            ):
                return
            self.cancel_autosave()
            self._autosave_after = self.root.after(700, self.auto_apply)

        def auto_apply_toggled(self):
            if self.auto_apply_var.get():
                self.schedule_autosave()
            else:
                self.cancel_autosave()
                self.say(bi("auto-apply paused|หยุดใช้ค่าอัตโนมัติแล้ว"))

        def auto_apply(self):
            self._autosave_after = None
            if not self.auto_apply_var.get():
                return
            cfg, errs = self.collect()
            if not errs:
                errs, _warns = ConfigModel.check(cfg)
            if errs:
                self.say(
                    bi("auto-apply waiting for valid config|รอคอนฟิกที่ถูกต้อง")
                    + ": "
                    + errs[0],
                    True,
                )
                return
            running = bool(ca.running_pid(self.model.cfg))
            if autosave_needs_confirmation(cfg, running):
                self.say(
                    bi(
                        "LIVE bot running: press Save to confirm changes|บอทโหมดจริงกำลังทำงาน: กด Save เพื่อยืนยัน"
                    ),
                    True,
                )
                return
            if cfg == self.model.cfg:
                return
            try:
                errs, warns = self.model.save(cfg)
            except OSError as e:
                self.say(bi("auto-apply failed|ใช้ค่าอัตโนมัติไม่สำเร็จ") + f": {e}", True)
                return
            if errs:
                self.say(
                    bi("auto-apply waiting for valid config|รอคอนฟิกที่ถูกต้อง")
                    + ": "
                    + errs[0],
                    True,
                )
                return
            self.draft = copy.deepcopy(cfg)
            self.say(
                bi("auto-applied|ใช้ค่าและบันทึกแล้ว")
                + (f"  ({len(warns)} warning)" if warns else "")
            )
            self.refresh_state()

        def save(self):
            self.cancel_autosave()
            cfg, errs = self.collect()
            if not errs:
                running = bool(ca.running_pid(self.model.cfg))
                if autosave_needs_confirmation(cfg, running) and cfg != self.model.cfg:
                    ok = messagebox.askyesno(
                        bi("Apply LIVE changes?|ใช้การเปลี่ยนแปลงในโหมดจริง?"),
                        bi(
                            "The running bot may hot-reload these settings and send real messages. Apply them now?|"
                            "บอทที่กำลังทำงานอาจโหลดค่านี้แล้วส่งข้อความจริง ต้องการใช้ค่าตอนนี้หรือไม่?"
                        ),
                    )
                    if not ok:
                        self.say(bi("not saved|ไม่ได้บันทึก"), True)
                        return False
                errs, warns = self.model.save(cfg)
            if errs:
                messagebox.showerror(bi("Not saved|ไม่ได้บันทึก"), "\n".join(errs))
                self.say(bi("not saved|ไม่ได้บันทึก"), True)
                return False
            self.draft = copy.deepcopy(cfg)
            self.say(
                bi("saved|บันทึกแล้ว") + (f"  ({len(warns)} warning)" if warns else "")
            )
            self.refresh_state()
            return True

        def reload(self):
            self.cancel_autosave()
            try:
                self.draft = copy.deepcopy(self.model.load())
            except Exception as e:
                messagebox.showerror(APP_NAME, ca.t("cannot_load", e=e))
                return
            ca.LANG = ca.resolve_lang(self.draft["language"])
            self.build()

        def check_cfg(self):
            cfg, errs = self.collect()
            e2, warns = ConfigModel.check(cfg) if not errs else ([], [])
            lines = errs + e2 + [ca.t("warn", m=w) for w in warns]
            messagebox.showinfo("check", "\n".join(lines) if lines else ca.t("cfg_ok"))

        def switch_lang(self):
            cfg, errs = self.collect()
            if not errs:
                self.draft = cfg
            self.draft["language"] = "en" if ca.LANG == "th" else "th"
            ca.LANG = self.draft["language"]
            self.build()

        def live_toggled(self):
            if not self.live_var.get():
                return
            ok = messagebox.askyesno(
                bi("Go LIVE?|เปลี่ยนเป็นโหมดจริง?"),
                bi(
                    "Messages WILL be sent to Camfrog. Check your room rules and Camfrog terms first.\nContinue?|"
                    "จะส่งข้อความไปยัง Camfrog จริง ตรวจกฎห้องและเงื่อนไข Camfrog ก่อน\nดำเนินการต่อ?"
                ),
            )
            if not ok:
                self.live_var.set(False)
            self.refresh_state()

        def start(self, close=False):
            self._close_after = close
            if not self.save():
                return
            cfg = self.model.cfg
            if not cfg["dry_run"] and not messagebox.askyesno(
                bi("LIVE|โหมดจริง"),
                bi("Start in LIVE mode (real messages)?|เริ่มโหมดจริง (ส่งข้อความจริง)?"),
            ):
                return
            args = SimpleNamespace(config=str(self.model.path), lang=None)
            self.run_bg(lambda: ca.cmd_start(cfg, args), "start")

        def stop(self):
            self.run_bg(lambda: ca.cmd_stop(self.model.cfg), "stop")

        def emergency(self):
            try:
                (ca.BASE / self.model.cfg["safety"]["stop_file"]).write_text("stop")
                self.say(bi("STOP file written|เขียนไฟล์ STOP แล้ว"))
            except OSError as e:
                messagebox.showerror("STOP", str(e))

        def helper(self, cmd):
            cfg, errs = self.collect()
            if errs or ConfigModel.check(cfg)[0]:
                messagebox.showerror(
                    APP_NAME, "\n".join(errs or ConfigModel.check(cfg)[0])
                )
                return
            ca.setup_logging(cfg)
            fn = {
                "windows": lambda: ca.cmd_windows(cfg),
                "discover": lambda: ca.cmd_discover(cfg),
                "autostart-on": lambda: ca.cmd_autostart(
                    SimpleNamespace(config=str(self.model.path), lang=None), True
                ),
                "autostart-off": lambda: ca.cmd_autostart(
                    SimpleNamespace(config=None, lang=None), False
                ),
            }[cmd]
            self.run_bg(fn, "helper")

        def detect(self):
            """Run detection and fill selectors; auto-apply saves valid proposals."""
            cfg, errs = self.collect()
            if errs or ConfigModel.check(cfg)[0]:
                messagebox.showerror(
                    APP_NAME, "\n".join(errs or ConfigModel.check(cfg)[0])
                )
                return
            ca.setup_logging(cfg)
            self.run_bg(
                lambda: ca.cmd_detect(cfg, self.model.path, False, True), "detect"
            )

        def apply_proposal(self, out):
            skipped = []
            for proposal in iter_proposals(out):
                _applied, missed = apply_proposal_to_vars(self.sel_vars, proposal)
                for key in missed:
                    try:
                        self._set(self.draft, tuple(key.split(".")), proposal[key])
                    except (KeyError, TypeError, AttributeError):
                        pass
                skipped += missed
            return skipped

        def run_bg(self, fn, kind):
            if self.busy:
                return
            self.busy = True

            def work():
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        rc = fn()
                except ImportError:
                    rc, _ = 2, buf.write(ca.t("win_only"))
                except Exception as e:  # shown in the GUI, never a traceback window
                    rc, _ = 1, buf.write(f"{type(e).__name__}: {e}")
                self.q.put((kind, rc, buf.getvalue().strip()))

            self.say("...")
            threading.Thread(target=work, daemon=True).start()

        def pump(self):
            if self._closing:
                return
            try:
                while True:
                    kind, rc, out = self.q.get_nowait()
                    self.busy = False
                    self.say(
                        out.splitlines()[-1] if out else ca.t("ok"), rc not in (0, None)
                    )
                    if (
                        kind == "start"
                        and rc == 0
                        and getattr(self, "_close_after", False)
                    ):
                        self.close_app()  # bot keeps running hidden; reopen the GUI any time
                        return
                    if kind in ("helper", "detect") and out:
                        extra = ""
                        if kind == "detect" and rc == 0:
                            skipped = self.apply_proposal(out)
                            if skipped:
                                extra = "\n" + "\n".join(
                                    bi(
                                        "kept in config (no box in this app): {0}|"
                                        "อยู่ใน config แล้ว (ไม่มีช่องในแอปนี้): {0}"
                                    ).format(k)
                                    for k in skipped
                                )
                        self.set_text(
                            self.helper_out,
                            "\n".join(
                                ln
                                for ln in out.splitlines()
                                if not ln.startswith("PROPOSAL ")
                            )
                            + extra,
                        )
                    self.refresh_state()
            except queue.Empty:
                pass
            if not self._closing:
                self.root.after(150, self.pump)

        @staticmethod
        def set_text(widget, text):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="disabled")

        # ---- live state
        def tick(self):
            if self._closing:
                return
            self.refresh_state()
            cfg = self.model.cfg
            txt = tail_text(ca.BASE / cfg["log"]["file"]) if cfg["log"]["file"] else ""
            if txt != self.last_log:
                self.last_log = txt
                self.set_text(self.log, txt)
                self.log.see("end")
            if not self._closing:
                self.root.after(1500, self.tick)

        def refresh_state(self):
            cfg = self.model.cfg
            pid = ca.running_pid(cfg)
            self.state_lbl.configure(
                text=(ca.t("state_running", pid=pid) if pid else ca.t("state_stopped")),
                foreground="#28744c" if pid else "#a3303a",
            )
            live = (
                (not cfg["dry_run"])
                if not hasattr(self, "live_var")
                else self.live_var.get()
            )
            self.mode_lbl.configure(
                text=bi("LIVE|โหมดจริง") if live else bi("DRY-RUN|โหมดทดลอง"),
                foreground="#9f3740" if live else "#8b672b",
            )
            self.btn_start.state(["disabled"] if pid else ["!disabled"])
            self.btn_stop.state(["!disabled"] if pid else ["disabled"])
            s = read_stats(cfg)
            self.stats_lbl.configure(
                text=ca.t(
                    "stats_line",
                    started=s.get("started", "?"),
                    st=s.get("statuses", 0),
                    rp=s.get("replies", 0),
                    fl=s.get("failures", 0),
                )
                if s
                else ""
            )
            for b in getattr(self, "hist_btns", []):
                b.state(["disabled"] if pid else ["!disabled"])
            self.hist_note.configure(
                text=bi(
                    "Stop the bot to edit history (it rewrites this file while running).|"
                    "หยุดบอทก่อนแก้ประวัติ (บอทเขียนไฟล์นี้อยู่ตอนทำงาน)"
                )
                if pid
                else ""
            )

        # ---- status tab
        def preview_marquee(self):
            cfg, errs = self.collect()
            mq = cfg["status"]["marquee"]
            try:
                fr = ca.marquee_frames(
                    self.mq_in.get(),
                    mq["width"],
                    mq["stride"],
                    mq["separator"],
                    mq["cycles"],
                    mq["max_frames"],
                )
            except Exception as e:
                fr = [str(e)]
            head = (
                ca.t("mq_static")
                if len(fr) <= 1
                else ca.t(
                    "mq_frames",
                    n=len(fr),
                    s=mq["step_seconds"],
                    t=len(fr) * mq["step_seconds"],
                )
            )
            self.set_text(
                self.mq_out,
                head
                + "\n"
                + "\n".join(f"{i:>3} |{x}" for i, x in enumerate(fr[:40], 1)),
            )

        # ---- rules
        def _rule_parts(self, scope):
            if scope == "im":
                return self.im_tree, self.im_rules
            return self.tree, self.rules

        def rules_refresh(self, select=None, scope="room"):
            tree, rules = self._rule_parts(scope)
            tree.delete(*tree.get_children())
            for i, r in enumerate(rules):
                th, en, anyl = reply_to_fields(r.get("reply"))
                prev = (th or en or anyl).replace("\n", " / ")
                tree.insert(
                    "",
                    "end",
                    iid=str(i),
                    values=(
                        i + 1,
                        "" if r.get("enabled", True) else "off",
                        r.get("lang", ""),
                        "yes" if r.get("mention") else "",
                        r.get("pattern", ""),
                        prev,
                    ),
                )
            if select is not None and rules:
                tree.selection_set(str(min(select, len(rules) - 1)))

        def sel_rule(self, scope="room"):
            tree, _rules = self._rule_parts(scope)
            selected = tree.selection()
            return int(selected[0]) if selected else None

        def rule_add(self, scope="room"):
            rule = RuleDialog(self).result
            if rule:
                _tree, rules = self._rule_parts(scope)
                rules.append(rule)
                self.rules_refresh(len(rules) - 1, scope)
                self.schedule_autosave()

        def rule_edit(self, scope="room"):
            i = self.sel_rule(scope)
            if i is None:
                return
            _tree, rules = self._rule_parts(scope)
            rule = RuleDialog(self, rules[i]).result
            if rule:
                rules[i] = rule
                self.rules_refresh(i, scope)
                self.schedule_autosave()

        def rule_del(self, scope="room"):
            i = self.sel_rule(scope)
            if i is not None and messagebox.askyesno(bi("Delete|ลบ"), f"#{i + 1}?"):
                _tree, rules = self._rule_parts(scope)
                del rules[i]
                self.rules_refresh(i, scope)
                self.schedule_autosave()

        def rule_move(self, d, scope="room"):
            i = self.sel_rule(scope)
            _tree, rules = self._rule_parts(scope)
            if i is None or not 0 <= i + d < len(rules):
                return
            rules[i], rules[i + d] = rules[i + d], rules[i]
            self.rules_refresh(i + d, scope)
            self.schedule_autosave()

        def test_rule(self):
            cfg, errs = self.collect()
            if errs:
                self.tout.configure(text=errs[0], foreground="#a3303a")
                return
            try:
                self.tout.configure(
                    text=explain(cfg, self.tn.get().strip(), self.tm.get()).replace(
                        "\n", "  |  "
                    ),
                    foreground="#28744c",
                )
            except re.error as e:
                self.tout.configure(text=str(e), foreground="#a3303a")

        def test_im_rule(self):
            cfg, errs = self.collect()
            if errs:
                self.tiout.configure(text=errs[0], foreground="#a3303a")
                return
            try:
                result = explain(cfg, self.tin.get().strip(), self.tim.get(), im=True)
                self.tiout.configure(
                    text=result.replace("\n", "  |  "), foreground="#28744c"
                )
            except re.error as e:
                self.tiout.configure(text=str(e), foreground="#a3303a")

        # ---- history
        def hist_obj(self):
            h = self.draft["status"]["history"]
            obj = ca.StatusHistory(ca.BASE / h["file"], h["max_items"])
            obj.load()
            return obj

        def hist_refresh(self):
            if not hasattr(self, "htree"):
                return
            self.htree.delete(*self.htree.get_children())
            for i, it in enumerate(self.hist_obj().items):
                self.htree.insert(
                    "", "end", iid=str(i), values=(it["lang"], it["count"], it["text"])
                )

        def hist_add(self):
            h = self.hist_obj()
            if h.add(self.hist_entry.get()):
                h.save()
                self.hist_entry.set("")
            self.hist_refresh()

        def hist_import(self):
            p = filedialog.askopenfilename(
                filetypes=[("text", "*.txt"), ("all", "*.*")]
            )
            if p:
                h = self.hist_obj()
                n = sum(
                    h.add(ln)
                    for ln in Path(p).read_text(encoding="utf-8-sig").splitlines()
                )
                h.save()
                self.say(ca.t("hist_imported", n=n))
                self.hist_refresh()

        def hist_del(self):
            sel = {int(s) for s in self.htree.selection()}
            if sel:
                h = self.hist_obj()
                h.items = [it for i, it in enumerate(h.items) if i not in sel]
                h.save()
                self.hist_refresh()

        def mainloop(self):
            self.root.mainloop()

    return App


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if has_cli_command(argv):  # CLI passthrough (also how `start` relaunches this exe)
        return ca.main(argv)
    try:
        args = parse_gui_args(argv)
    except SystemExit as exc:
        return exc.code
    if os.name == "nt":  # crisp text on Windows 11 high-DPI displays
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    cfg_path = Path(args.config) if args.config else ca.BASE / "config.json"
    if not cfg_path.is_absolute():
        cfg_path = ca.BASE / cfg_path
    cfg_path = cfg_path.resolve()
    if APP_PROFILE in ("room", "im_reply"):
        try:
            cfg_path.relative_to(ca.BASE.resolve())
        except ValueError:
            print("This app's config must stay inside its own folder.")
            return 2
    try:
        app = build_app()(cfg_path)
    except SystemExit as e:
        return e.code
    except Exception as e:  # e.g. no display
        print(f"GUI failed: {e}")
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
