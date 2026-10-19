"""Small real GUI process used only by Windows runtime-adapter tests.

The window can be closed while the process remains alive by sending ``close``
on stdin. Sending ``exit`` terminates the helper process.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk

commands: queue.Queue[str] = queue.Queue()


def read_commands() -> None:
    for line in sys.stdin:
        commands.put(line.strip())


threading.Thread(target=read_commands, daemon=True).start()
root = tk.Tk()
root.title(sys.argv[1] if len(sys.argv) > 1 else "zchat-runtime-fixture")
root.update()
print("READY", flush=True)

closed = False
while True:
    try:
        command = commands.get_nowait()
    except queue.Empty:
        command = ""
    if command == "exit":
        break
    if command == "close" and not closed:
        root.destroy()
        closed = True
    if not closed:
        root.update()
    time.sleep(0.02)
