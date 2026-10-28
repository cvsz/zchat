"""Auto-launch Camfrog and join room."""
from __future__ import annotations
import logging
import os
import subprocess
import time
from .config import Config
from .windows import resolve, list_all

log = logging.getLogger("autodj")


def ensure_room(cfg: Config) -> bool:
    """Launch Camfrog if not running, wait for room window."""
    # Check if room window already exists
    c = resolve(cfg.window_title_re, cfg.window_exclude_re, cfg.room, cfg.window_index, 2, cfg.process_re)
    if c:
        log.info("Room window already open: %s", c.title)
        return True

    if not cfg.camfrog_exe:
        log.warning("auto_launch enabled but camfrog_exe not set")
        return False

    exe = cfg.camfrog_exe
    if not os.path.exists(exe):
        log.error("camfrog_exe not found: %s", exe)
        return False

    # Build launch command
    if cfg.join_uri_template and cfg.room:
        uri = cfg.join_uri_template.format(room=cfg.room)
        cmd = [exe, uri]
    else:
        cmd = [exe]

    log.info("Launching Camfrog: %s", " ".join(cmd))
    try:
        subprocess.Popen(cmd, creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception as e:
        log.error("Failed to launch Camfrog: %s", e)
        return False

    # Wait for room window to appear
    end = time.time() + cfg.launch_timeout
    while time.time() < end:
        c = resolve(cfg.window_title_re, cfg.window_exclude_re, cfg.room, cfg.window_index, 2, cfg.process_re)
        if c:
            log.info("Room window appeared: %s", c.title)
            return True
        time.sleep(1)

    log.warning("Room window did not appear within %ds", cfg.launch_timeout)
    return False