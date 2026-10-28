"""Windows UI Automation adapter for Camfrog chat."""
from __future__ import annotations
import logging
import re
import threading
import time
from .base import ChatAdapter, diff_lines, parse_line

log = logging.getLogger("autodj")


def _read(ctrl) -> str:
    for fn in (lambda: ctrl.iface_value.CurrentValue, ctrl.window_text, lambda: "\n".join(ctrl.texts())):
        try:
            t = fn()
            if t:
                return t
        except Exception:
            pass
    return ""


class UiaAdapter(ChatAdapter):
    """Windows UI Automation. Two layouts:
    - web (Camfrog room window): history/message box are embedded-web panes, see webchat.py
    - text: plain Document/Edit controls chosen by view_type/view_index, input_type/input_index
    view_kind = auto | web | text. `AutoDJ.exe --discover` fills in what it finds."""

    def __init__(self, cfg):
        from pywinauto import Desktop
        self.cfg = cfg
        self.rx = re.compile(cfg.line_regex)
        from ..windows import resolve
        c = resolve(cfg.window_title_re, cfg.window_exclude_re, cfg.room, cfg.window_index, 30, cfg.process_re)
        if c is None:
            raise RuntimeError(f"no visible window matches {cfg.window_title_re!r} "
                               f"(open the room window, or run AutoDJ.exe --windows)")
        log.info("attached to window %r (pid %s, class %s)", c.title, c.pid, c.cls)
        self.handle = c.handle
        self.com_lock = threading.RLock()
        self._view = self._input = None
        self._web = False
        self._bg_fails = 0
        self._attach()

    def _attach(self):
        from pywinauto import Desktop
        self.win = Desktop(backend="uia").window(handle=self.handle)
        self._view = self._input = None
        self._web = False

    def _find(self, ctype, idx):
        return self.win.descendants(control_type=ctype)[idx]

    def _resolve(self):
        if self.win is None:
            self._attach()
        if self.cfg.view_kind in ("auto", "web"):
            from ..webchat import find_web_panes
            view, inp = find_web_panes(self.win, self.cfg.web_view_title, self.cfg.web_input_title)
            if view is not None:
                self._web, self._view, self._input = True, view, inp
                log.info("chat: web view found (message box %s)", "found" if inp is not None else "MISSING")
                return
            if self.cfg.view_kind == "web":
                raise RuntimeError(f"no pane titled like {self.cfg.web_view_title!r} in the window")
        self._web = False
        self._view = self._find(self.cfg.view_type, self.cfg.view_index)
        self._input = None
        log.info("chat: text control %s[%d]", self.cfg.view_type, self.cfg.view_index)

    def _lines(self) -> list[str]:
        with self.com_lock:
            if self._view is None:
                self._resolve()
            if self._web:
                from ..webchat import read_web
                return read_web(self._view, strict=self.cfg.web_strict_body)[-300:]
            return _read(self._view).splitlines()[-300:]

    def messages(self):
        prev = None
        while True:
            try:
                lines = self._lines()
            except Exception as e:
                log.debug("chat read failed: %s", e)
                self._view = self.win = None
                time.sleep(1)
                continue
            if prev is not None:
                for line in diff_lines(prev, lines):
                    m = parse_line(line, self.rx)
                    if m:
                        yield m
            prev = lines
            time.sleep(self.cfg.poll_interval)

    def _ensure_controls(self):
        if self._view is None:
            self._resolve()
        if not self._web and self._input is None:
            self._input = self._find(self.cfg.input_type, self.cfg.input_index)
        if self._input is None:
            raise RuntimeError("chat message box not found")

    def _norm(self, s: str) -> str:
        return " ".join(s.replace("\xa0", " ").split())

    def _appeared(self, text: str, before: list[str]) -> bool:
        want = self._norm(text)
        end = time.time() + 3.0
        while time.time() < end:
            time.sleep(0.4)
            try:
                lines = self._lines()
                new = diff_lines(before, lines) if before else lines
            except Exception:
                return True
            if any(self._norm(l).endswith(": " + want) or self._norm(l).endswith(want) for l in new):
                return True
        return False

    def _send_bg(self, text) -> bool:
        from .. import winmsg
        top = int(self.win.handle)
        winmsg.show_noactivate(top)
        try:
            vp = self._input.iface_value
            if self._input.iface.CurrentIsEnabled and not vp.CurrentIsReadOnly:
                vp.SetValue(text)
            else:
                raise RuntimeError("editor not enabled or is read-only")
        except Exception as e:
            log.debug("UIA SetValue skipped/failed (%s); falling back to posted keystrokes", e)
            r = self._input.rectangle()
            pt = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            if not winmsg.post_text(top, pt, text, enter=False):
                return False
        r = self._input.rectangle()
        pt = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        return winmsg.post_text(top, pt, "", enter=True)

    def _send_fg(self, text):
        import pyperclip
        from pywinauto.keyboard import send_keys
        from .. import winmsg
        prev = winmsg.foreground()
        try:
            old_clip = pyperclip.paste()
        except Exception:
            old_clip = None
        try:
            try:
                if self.win.is_minimized():
                    self.win.restore()
            except Exception:
                pass
            self.win.set_focus()
            if self._web:
                self._input.click_input()
            else:
                self._input.set_focus()
            pyperclip.copy(text)
            send_keys("^v")
            time.sleep(0.15)
            send_keys("{ENTER}")
        finally:
            time.sleep(0.1)
            if old_clip is not None:
                try:
                    pyperclip.copy(old_clip)
                except Exception:
                    pass
            if prev and prev != int(self.win.handle):
                winmsg.restore_foreground(prev)

    def send(self, text):
        with self.com_lock:
            try:
                self._do_send(text)
            except Exception:
                self.win = self._view = self._input = None
                raise

    def _do_send(self, text):
        mode = self.cfg.send_mode
        try:
            self._ensure_controls()
            if mode in ("auto", "background"):
                allow_bg = mode == "background" or (mode == "auto" and self._bg_fails < 2)
                if allow_bg:
                    before = []
                    if mode == "auto" and self.cfg.send_verify:
                        try:
                            before = self._lines()
                        except Exception:
                            before = None
                    try:
                        ok = self._send_bg(text)
                    except Exception as e:
                        log.warning("background send failed: %s", e)
                        ok = False
                    if mode == "background":
                        if not ok:
                            raise RuntimeError("background send failed (try send_mode = 'auto' or 'foreground')")
                        return
                    if ok and (not self.cfg.send_verify or before is None or self._appeared(text, before)):
                        log.debug("sent in background: %s", text)
                        self._bg_fails = 0
                        return
                    self._bg_fails += 1
                    log.info("background send not confirmed in the room (%d in a row): falling back to foreground",
                             self._bg_fails)
                self._send_fg(text)
                if self.cfg.send_verify and not self._appeared(text, []):
                    log.warning("sent via foreground paste but NOT seen in the room within 3s "
                                "(the message box may not have received the paste/enter)")
                log.debug("sent to Camfrog: %s", text)
        except Exception:
            self._view = self._input = None
            raise

    def close(self) -> None:
        self.win = self._view = self._input = None