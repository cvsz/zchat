"""AutoDJ GUI (tkinter)."""
from __future__ import annotations
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional

from .engine import Engine
from .messages import tr

log = logging.getLogger("autodj")


class App:
    def __init__(self, engine: Engine):
        self.e = engine
        self.root = tk.Tk()
        self.root.title(f"AutoDJ - {engine.cfg.bot_nick}")
        self.root.geometry("900x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_ms = 200
        self._build_ui()
        self._start_poll()

    def _build_ui(self):
        # Top bar
        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, padx=8, pady=4)
        self.lbl_now = ttk.Label(top, text=tr(self.e.lang, "g_nothing"), font=("Segoe UI", 11, "bold"))
        self.lbl_now.pack(side=tk.LEFT)
        self.lbl_next = ttk.Label(top, text=tr(self.e.lang, "g_up_next") + ": " + tr(self.e.lang, "g_nothing"))
        self.lbl_next.pack(side=tk.LEFT, padx=12)
        ttk.Label(top, text=tr(self.e.lang, "g_vol")).pack(side=tk.RIGHT, padx=(12, 0))
        self.vol_var = tk.IntVar(value=self.e.volume)
        vol_scale = ttk.Scale(top, from_=0, to=100, variable=self.vol_var, command=self._on_vol, length=120)
        vol_scale.pack(side=tk.RIGHT)

        # Main paned window
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # Left: queue + controls
        left = ttk.Frame(paned)
        paned.add(left, weight=2)

        qf = ttk.LabelFrame(left, text=tr(self.e.lang, "g_queue"))
        qf.pack(fill=tk.BOTH, expand=True)
        self.queue_list = tk.Listbox(qf, font=("Consolas", 10))
        self.queue_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        qbtns = ttk.Frame(qf)
        qbtns.pack(fill=tk.X, padx=4, pady=(0, 4))
        ttk.Button(qbtns, text=tr(self.e.lang, "g_add"), command=self._add_dialog).pack(side=tk.LEFT)
        ttk.Button(qbtns, text=tr(self.e.lang, "g_folder"), command=self._load_folder).pack(side=tk.LEFT, padx=4)
        ttk.Button(qbtns, text=tr(self.e.lang, "g_file"), command=self._load_files).pack(side=tk.LEFT)
        ttk.Button(qbtns, text=tr(self.e.lang, "g_remove"), command=self._remove_sel).pack(side=tk.LEFT, padx=4)
        ttk.Button(qbtns, text=tr(self.e.lang, "g_clear"), command=self._clear_queue).pack(side=tk.LEFT)

        # Playback controls
        ctl = ttk.Frame(left)
        ctl.pack(fill=tk.X, pady=4)
        ttk.Button(ctl, text=tr(self.e.lang, "g_pause"), command=self.e.pause).pack(side=tk.LEFT)
        ttk.Button(ctl, text=tr(self.e.lang, "g_resume"), command=self.e.resume).pack(side=tk.LEFT, padx=4)
        ttk.Button(ctl, text=tr(self.e.lang, "g_skip"), command=self.e.skip).pack(side=tk.LEFT)
        self.shuffle_var = tk.BooleanVar(value=self.e.queue.shuffle)
        ttk.Checkbutton(ctl, text=tr(self.e.lang, "g_shuffle"), variable=self.shuffle_var,
                        command=lambda: self.e.queue.set_shuffle(self.shuffle_var.get())).pack(side=tk.LEFT, padx=8)
        self.repeat_var = tk.StringVar(value=self.e.queue.repeat)
        ttk.Combobox(ctl, textvariable=self.repeat_var, values=["off", "one"], width=6,
                     state="readonly").pack(side=tk.LEFT)
        self.repeat_var.trace_add("write", lambda *_: setattr(self.e.queue, "repeat", self.repeat_var.get()))

        # Right: chat + history
        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        cf = ttk.LabelFrame(right, text=tr(self.e.lang, "g_chat"))
        cf.pack(fill=tk.BOTH, expand=True)
        self.chat_log = tk.Text(cf, height=15, state=tk.DISABLED, font=("Consolas", 9))
        self.chat_log.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        chat_in = ttk.Frame(cf)
        chat_in.pack(fill=tk.X, padx=4, pady=(0, 4))
        self.chat_entry = ttk.Entry(chat_in)
        self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_entry.bind("<Return>", lambda e: self._send_chat())
        ttk.Button(chat_in, text=tr(self.e.lang, "g_say"), command=self._send_chat).pack(side=tk.LEFT, padx=4)
        ttk.Button(chat_in, text=tr(self.e.lang, "g_speak"), command=self._speak_chat).pack(side=tk.LEFT)

        hf = ttk.LabelFrame(right, text=tr(self.e.lang, "g_history"))
        hf.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.history_list = tk.Listbox(hf, font=("Consolas", 9))
        self.history_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    def _start_poll(self):
        self._poll()
        self.root.after(self.poll_ms, self._start_poll)

    def _poll(self):
        snap = self.e.snapshot()
        # Now playing
        cur = snap.get("current")
        if cur:
            self.lbl_now.config(text=f"{tr(self.e.lang, 'g_now')}: {cur.title} [{cur.dur}]")
        else:
            self.lbl_now.config(text=tr(self.e.lang, "g_nothing"))
        # Up next
        nxt = snap.get("next")
        if nxt:
            self.lbl_next.config(text=f"{tr(self.e.lang, 'g_up_next')}: {nxt.title} [{nxt.dur}]")
        else:
            self.lbl_next.config(text=f"{tr(self.e.lang, 'g_up_next')}: {tr(self.e.lang, 'g_nothing')}")
        # Queue
        self.queue_list.delete(0, tk.END)
        for i, t in enumerate(snap.get("queue", []), 1):
            self.queue_list.insert(tk.END, f"{i}) {t.title} [{t.dur}] ({t.requester})")
        # History
        self.history_list.delete(0, tk.END)
        for i, t in enumerate(snap.get("history", []), 1):
            self.history_list.insert(tk.END, f"{i}) {t.title} [{t.dur}] ({t.requester})")
        # Chat log (only show last 20)
        self.chat_log.config(state=tk.NORMAL)
        self.chat_log.delete(1.0, tk.END)
        for who, txt in list(self.e.chat_log)[-20:]:
            prefix = ">> " if who == self.e.cfg.bot_nick else "<< "
            self.chat_log.insert(tk.END, f"{prefix}{who}: {txt}\n")
        self.chat_log.config(state=tk.DISABLED)
        self.chat_log.see(tk.END)

    def _on_vol(self, val):
        self.e.set_volume(int(float(val)))

    def _add_dialog(self):
        d = tk.Toplevel(self.root)
        d.title(tr(self.e.lang, "g_add"))
        d.geometry("500x120")
        ttk.Label(d, text=tr(self.e.lang, "g_add_hint")).pack(anchor=tk.W, padx=8, pady=8)
        entry = ttk.Entry(d, width=60)
        entry.pack(fill=tk.X, padx=8)
        entry.focus()
        def ok():
            src = entry.get().strip()
            if src:
                self.e.request(src, "GUI")
            d.destroy()
        ttk.Button(d, text="OK", command=ok).pack(pady=8)
        entry.bind("<Return>", lambda e: ok())

    def _load_folder(self):
        path = filedialog.askdirectory(title=tr(self.e.lang, "g_folder"))
        if path:
            self.e.load_async(path)

    def _load_files(self):
        paths = filedialog.askopenfilenames(title=tr(self.e.lang, "g_file"),
                                            filetypes=[("Media", "*.mp3 *.m4a *.wav *.flac *.ogg *.opus *.aac *.mp4 *.mkv *.webm"),
                                                       ("All", "*.*")])
        for p in paths:
            self.e.load_async(p)

    def _remove_sel(self):
        sel = self.queue_list.curselection()
        if not sel:
            return
        idx = sel[0] + 1
        t = self.e.queue.get(idx)
        if t and self.e.queue.remove_track(t):
            self._poll()

    def _clear_queue(self):
        if messagebox.askyesno(tr(self.e.lang, "g_clear"), "Clear all requests?"):
            self.e.queue.clear()
            self._poll()

    def _send_chat(self):
        txt = self.chat_entry.get().strip()
        if txt:
            self.e.say(txt)
            self.chat_entry.delete(0, tk.END)

    def _speak_chat(self):
        txt = self.chat_entry.get().strip()
        if txt:
            self.e.speak(txt)
            self.chat_entry.delete(0, tk.END)

    def on_close(self):
        self.e.shutdown()
        self.root.destroy()

    def mainloop(self):
        self.root.mainloop()