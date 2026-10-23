"""Standalone zGPU & LLM Testing and Configuration Panel.

Allows operators to test connections to zGPU RPC backends, inspect inference performance,
and preview LLM responses.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, Optional

# Ensure src is in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from camfrog.zgpu_client import DEFAULT_ZGPU_RPC_URL, ZGPUClient, ZGPUResponse

log = logging.getLogger(__name__)


class ZGPUTesterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("zGPU LLM Acceleration Studio")
        self.root.geometry("640x580")
        self.root.minsize(500, 450)

        self.endpoint_var = tk.StringVar(value=DEFAULT_ZGPU_RPC_URL)
        self.api_key_var = tk.StringVar(value="")
        self.model_var = tk.StringVar(value="default")
        self.timeout_var = tk.StringVar(value="15")
        self.temperature_var = tk.DoubleVar(value=0.7)
        self.max_tokens_var = tk.IntVar(value=128)
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Config section
        cfg_frame = ttk.LabelFrame(
            main_frame, text="zGPU RPC Endpoint Configuration", padding=8
        )
        cfg_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(cfg_frame, text="Endpoint URL:").grid(
            row=0, column=0, sticky=tk.W, pady=2
        )
        ttk.Entry(cfg_frame, textvariable=self.endpoint_var, width=40).grid(
            row=0, column=1, sticky=tk.EW, padx=5, pady=2
        )

        ttk.Label(cfg_frame, text="API Key (optional):").grid(
            row=1, column=0, sticky=tk.W, pady=2
        )
        ttk.Entry(cfg_frame, textvariable=self.api_key_var, show="*", width=40).grid(
            row=1, column=1, sticky=tk.EW, padx=5, pady=2
        )

        ttk.Label(cfg_frame, text="Model:").grid(row=2, column=0, sticky=tk.W, pady=2)
        ttk.Entry(cfg_frame, textvariable=self.model_var, width=20).grid(
            row=2, column=1, sticky=tk.W, padx=5, pady=2
        )

        test_btn = ttk.Button(
            cfg_frame, text="Ping / Health Check", command=self.on_health_check
        )
        test_btn.grid(row=0, column=2, rowspan=2, padx=5, sticky=tk.NSEW)

        cfg_frame.columnconfigure(1, weight=1)

        # Parameters section
        param_frame = ttk.Frame(main_frame)
        param_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(param_frame, text="Max Tokens:").pack(side=tk.LEFT)
        ttk.Spinbox(
            param_frame, from_=16, to=4096, textvariable=self.max_tokens_var, width=6
        ).pack(side=tk.LEFT, padx=(5, 15))

        ttk.Label(param_frame, text="Temperature:").pack(side=tk.LEFT)
        ttk.Spinbox(
            param_frame,
            from_=0.0,
            to=2.0,
            increment=0.1,
            textvariable=self.temperature_var,
            width=5,
        ).pack(side=tk.LEFT, padx=5)

        # Prompt input
        prompt_frame = ttk.LabelFrame(main_frame, text="Prompt Input", padding=8)
        prompt_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.prompt_text = tk.Text(prompt_frame, height=5, wrap=tk.WORD)
        self.prompt_text.pack(fill=tk.BOTH, expand=True)
        self.prompt_text.insert(
            tk.END, "Hello, can you give me a friendly Camfrog status message?"
        )

        # Output / Results
        out_frame = ttk.LabelFrame(
            main_frame, text="LLM Output & Performance", padding=8
        )
        out_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.output_text = tk.Text(
            out_frame, height=6, wrap=tk.WORD, state=tk.DISABLED, bg="#f8f9fa"
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)

        # Action Buttons & Status bar
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))

        self.send_btn = ttk.Button(
            btn_frame, text="⚡ Generate (zGPU Accelerated)", command=self.on_generate
        )
        self.send_btn.pack(side=tk.RIGHT)

        status_lbl = ttk.Label(
            main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W
        )
        status_lbl.pack(fill=tk.X, side=tk.BOTTOM)

    def _get_client(self) -> ZGPUClient:
        try:
            to = float(self.timeout_var.get())
        except ValueError:
            to = 15.0
        return ZGPUClient(
            endpoint_url=self.endpoint_var.get(),
            api_key=self.api_key_var.get() or None,
            timeout=to,
            default_model=self.model_var.get(),
        )

    def on_health_check(self):
        self.status_var.set("Pinging zGPU RPC endpoint...")
        client = self._get_client()

        def run():
            res = client.health_check()
            self.root.after(0, lambda: self._handle_health_res(res))

        threading.Thread(target=run, daemon=True).start()

    def _handle_health_res(self, res: Dict[str, Any]):
        if res.get("ok"):
            self.status_var.set("Endpoint healthy and reachable!")
            messagebox.showinfo(
                "zGPU Status",
                f"Endpoint is reachable!\n\nDetails: {json.dumps(res.get('details', {}), indent=2)}",
            )
        else:
            self.status_var.set("Health check failed")
            messagebox.showerror(
                "zGPU Status", f"Connection error:\n{res.get('error')}"
            )

    def on_generate(self):
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("Empty Prompt", "Please enter a prompt to generate.")
            return

        self.send_btn.config(state=tk.DISABLED)
        self.status_var.set("Sending inference request to zGPU...")
        client = self._get_client()
        temp = self.temperature_var.get()
        max_t = self.max_tokens_var.get()

        def run():
            resp = client.generate(prompt=prompt, temperature=temp, max_tokens=max_t)
            self.root.after(0, lambda: self._handle_generate_res(resp))

        threading.Thread(target=run, daemon=True).start()

    def _handle_generate_res(self, resp: ZGPUResponse):
        self.send_btn.config(state=tk.NORMAL)
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)

        if resp.ok:
            self.output_text.insert(tk.END, resp.text)
            self.status_var.set(
                f"Completed in {resp.latency_ms:.1f}ms | Tokens: {resp.tokens_used}"
            )
        else:
            err = resp.error or "Unknown error"
            self.output_text.insert(tk.END, f"[Error]: {err}")
            self.status_var.set(f"Failed ({resp.latency_ms:.1f}ms): {err}")

        self.output_text.config(state=tk.DISABLED)


def main():
    root = tk.Tk()
    app = ZGPUTesterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
