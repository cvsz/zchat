"""MPV player wrapper for local files and YouTube (via yt-dlp)."""
from __future__ import annotations
import itertools
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading

from .library import find_ytdlp

_ids = itertools.count()
log = logging.getLogger("autodj")
_WHY = ("error", "fail", "not found", "unable", "[ytdl_hook]", "cannot", "no such")


def failure_reasons(log_text: str, keep: int = 4) -> list[str]:
    """The last few lines of an mpv log that explain why playback failed."""
    lines = [ln.strip() for ln in log_text.splitlines() if any(w in ln.lower() for w in _WHY)]
    return lines[-keep:]


class MpvPlayer:
    """One mpv process per track. mpv plays local files and YouTube (via yt-dlp) alike."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self._ipc: str | None = None
        self.skipped = False
        self._lock = threading.Lock()

    def available(self) -> bool:
        return shutil.which(self.cfg.mpv_path) is not None

    def _base(self) -> list[str]:
        a = [self.cfg.mpv_path, "--no-video", "--force-window=no", "--no-terminal", "--really-quiet"]
        if self.cfg.audio_device:
            a.append(f"--audio-device={self.cfg.audio_device}")
        return a

    def play(self, source: str, volume: int) -> bool:
        n = next(_ids)
        ipc = (rf"\\.\pipe\camfrog-autodj-{os.getpid()}-{n}" if os.name == "nt"
               else os.path.join(tempfile.gettempdir(), f"camfrog-autodj-{os.getpid()}-{n}.sock"))
        logf = os.path.join(tempfile.gettempdir(), f"camfrog-autodj-mpv-{os.getpid()}-{n}.log")
        cmd = self._base() + ["--ytdl-format=bestaudio/best", f"--input-ipc-server={ipc}",
                              f"--log-file={logf}", f"--volume={volume}"]
        ytdlp = getattr(self.cfg, "ytdlp_path", "") or find_ytdlp("", self.cfg.mpv_path)
        if ytdlp:
            cmd.append(f"--script-opts=ytdl_hook-ytdl_path={ytdlp}")
        cmd += ["--", source]
        kw: dict = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        with self._lock:
            self.skipped = False
            self._ipc = ipc
            self.proc = p = subprocess.Popen(cmd, **kw)
        try:
            rc = p.wait(timeout=self.cfg.mpv_timeout or None)
        except subprocess.TimeoutExpired:
            log.warning("mpv hung after %ss for %s: terminating", self.cfg.mpv_timeout, source)
            with self._lock:
                self.skipped = True
                p.terminate()
            try:
                rc = p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("mpv did not exit after terminate; killing")
                with self._lock:
                    p.kill()
                rc = p.wait()
            ok = False
        else:
            with self._lock:
                self.proc = None
                self._ipc = None
                ok = rc == 0 or self.skipped
        self._report(ok, rc, source, logf)
        return ok

    @staticmethod
    def _report(ok: bool, rc: int, source: str, logf: str):
        """On failure put mpv's own explanation in our log (mpv itself is run silent); always clean up."""
        try:
            if not ok:
                with open(logf, encoding="utf-8", errors="replace") as f:
                    why = failure_reasons(f.read())
                log.warning("mpv exited with code %s for %s%s", rc, source,
                            "".join("\n    " + ln for ln in why) if why else " (no error line in mpv's log)")
        except OSError:
            log.warning("mpv exited with code %s for %s (no log written: is mpv_path correct?)", rc, source)
        finally:
            try:
                os.remove(logf)
            except OSError:
                pass

    def stop(self):
        with self._lock:
            p = self.proc
            if p and p.poll() is None:
                self.skipped = True
                p.terminate()

    def _cmd(self, *cmd) -> bool:
        ipc = self._ipc
        if not ipc:
            return False
        data = (json.dumps({"command": list(cmd)}) + "\n").encode()
        try:
            if os.name == "nt":
                with open(ipc, "r+b", buffering=0) as f:
                    f.write(data)
            else:
                import socket
                with socket.socket(socket.AF_UNIX) as s:
                    s.connect(ipc)
                    s.sendall(data)
            return True
        except OSError:
            return False

    def set_pause(self, v: bool) -> bool:
        return self._cmd("set_property", "pause", v)

    def set_volume(self, v: int) -> bool:
        return self._cmd("set_property", "volume", v)

    def play_file(self, path: str):
        kw: dict = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        subprocess.run(self._base() + ["--volume=100", "--", path], **kw)