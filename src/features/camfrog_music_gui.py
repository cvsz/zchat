"""Camfrog Music DJ bot - self-contained (engine + clipboard + GUI inlined, profile: music)."""
import argparse
import base64
import copy
import ctypes
import datetime as dt
import hashlib
import hmac
import http.server
import json
import logging
import logging.handlers
import os
import ntpath
import random
import re
import secrets
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional, Union
import contextlib
import io
import queue
import threading
from types import SimpleNamespace


def system_taskkill_path():
    """Return taskkill.exe from Windows' system directory, never from PATH/cwd."""
    if os.name != "nt":
        return "taskkill.exe"
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_system_directory = kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = (ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint)
    get_system_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_system_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return os.path.join(buffer.value, "taskkill.exe")

# ============================================================
# Inlined engine (camfrog_auto.py) - no shared imports.
# ============================================================


# Windowed/no-console builds have no stdout/stderr; force UTF-8 so Thai prints safely.
for _n in ("stdout", "stderr"):
    _s = getattr(sys, _n)
    if _s is None:
        setattr(sys, _n, open(os.devnull, "w", encoding="utf-8"))
    elif hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# Frozen (PyInstaller) -> files live next to the exe, not the temp unpack dir.
def _source_base() -> Path:
    """Repository root when running from a source checkout.

    Source modules live under src/<area>/ while runtime data and example
    configs live at the repository root (their pre-reorganization home).
    Frozen executables and explicit *_DATA_DIR overrides are unaffected.
    """
    here = Path(__file__).resolve().parent
    root = here.parent.parent
    if here.parent.name == "src" and (root / "config.example.json").is_file():
        return root
    return here


BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
    else _source_base()


def app_resource_path(relative_path):
    """Resolve immutable assets from source or PyInstaller's extraction folder."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / relative_path

APP_PROFILE = "music"
APP_PROFILES = {"full", "room", "im_reply", "status", "music"}

# Standalone executables can pin runtime state to their own directory. Status
# workers use ZCFATO_DATA_DIR, which takes precedence over the GUI app directory.
if os.environ.get("ZCFATO_DATA_DIR"):
    BASE = Path(os.environ["ZCFATO_DATA_DIR"])
elif os.environ.get("CAMFROG_APP_DATA_DIR"):
    BASE = Path(os.environ["CAMFROG_APP_DATA_DIR"])

# Nickname regex: Unicode word chars + space . - (Camfrog allows Thai in nicknames).
# LINE_RE nick capture is permissive; validation happens via NICK_RE.
NICK_RE = re.compile(r"^[\w][\w .\-]{0,31}$", re.UNICODE)
LINE_RE = re.compile(r"^(?P<nick>[^:\r\n]{1,32}):\s(?P<msg>.+)$")
CTRL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
log = logging.getLogger("camfrog_music")
LOG_TEXT = True
LANG = "en"

# ---------- i18n (TH / EN) ----------
S = {
    "cfg_ok": ("config OK", "คอนฟิกถูกต้อง"),
    "cfg_errs": ("{n} error(s)", "พบข้อผิดพลาด {n} รายการ"),
    "cfg_hint": ("config errors (run `check`):", "คอนฟิกมีข้อผิดพลาด (รัน `check`):"),
    "cannot_load": ("cannot load config: {e}  (try `init` to create one)", "อ่านคอนฟิกไม่ได้: {e}  (ลองรัน `init` เพื่อสร้างใหม่)"),
    "warn": ("WARN : {m}", "คำเตือน: {m}"),
    "err": ("ERROR: {m}", "ผิดพลาด: {m}"),
    "e_regex": ("window_title_regex invalid: {e}", "window_title_regex ไม่ถูกต้อง: {e}"),
    "e_hours": ("active_hours start/end must be HH:MM", "active_hours start/end ต้องเป็นรูปแบบ HH:MM"),
    "e_msgs": ("status.messages is empty", "status.messages ว่างเปล่า"),
    "e_interval": ("status.interval_seconds must be >= 0.3", "status.interval_seconds ต้องไม่น้อยกว่า 0.3"),
    "e_sel": ("{k} must be a selector object", "{k} ต้องเป็น object ของ selector"),
    "e_delay": ("autoreply.delay_range_seconds must be [min, max] with 0 <= min <= max",
                "autoreply.delay_range_seconds ต้องเป็น [min, max] โดย 0 <= min <= max"),
    "e_hour": ("autoreply.max_per_hour must be >= 1", "autoreply.max_per_hour ต้องไม่น้อยกว่า 1"),
    "e_cool": ("autoreply cooldowns must be >= 0", "ค่า cooldown ของ autoreply ต้องไม่ติดลบ"),
    "e_rule": ("autoreply.rules[{i}] invalid: {e}", "autoreply.rules[{i}] ไม่ถูกต้อง: {e}"),
    "e_reply": ("autoreply.rules[{i}] has no reply", "autoreply.rules[{i}] ไม่มี reply"),
    "e_struct": ("config has a wrong value type or missing section: {e}",
                 "คอนฟิกมีชนิดข้อมูลผิดหรือขาดส่วนที่จำเป็น: {e}"),
    "e_lang": ("language must be auto, th or en", "language ต้องเป็น auto, th หรือ en"),
    "e_mode": ("status.language_mode must be th, en, both or alternate",
               "status.language_mode ต้องเป็น th, en, both หรือ alternate"),
    "w_norules": ("autoreply.rules is empty (nothing will be replied)",
                  "autoreply.rules ว่างเปล่า (จะไม่มีการตอบ)"),
    "w_nick": ("autoreply.own_nickname is empty (required for live mode)",
               "autoreply.own_nickname ว่าง (จำเป็นเมื่อรันจริง)"),
    "w_live": ("dry_run is false: messages WILL be sent", "dry_run เป็น false: จะส่งข้อความจริง"),
    "dry_send": ("[dry-run] would send: {text}", "[ทดลอง] จะส่ง: {text}"),
    "mismatch": ("read-back mismatch; not sending", "ข้อความที่อ่านกลับไม่ตรง จึงไม่ส่ง"),
    "not_fg": ("Camfrog not foreground; not sending", "Camfrog ไม่ได้อยู่หน้าสุด จึงไม่ส่ง"),
    "no_window": ("Camfrog window not found (check window_title_regex)",
                  "ไม่พบหน้าต่าง Camfrog (ตรวจ window_title_regex)"),
    "verified_ambiguous": ("verified Camfrog discovery is ambiguous: {detail}",
                           "การค้นหา Camfrog แบบยืนยันตัวตนกำกวม: {detail}"),
    "no_ctrl": ("control not found: {spec} (matches={n})", "ไม่พบ control: {spec} (พบ {n} รายการ)"),
    "status_set": ("status submission dispatched (provider outcome unverified): {text}", "ส่งคำสั่ง status แล้ว (ยังไม่ยืนยันผลจาก Camfrog): {text}"),
    "status_fail": ("status commit failed", "ตั้ง status ไม่สำเร็จ"),
    "reply_fail": ("reply commit failed", "ส่งข้อความตอบไม่สำเร็จ"),
    "replied": ("replied to {nick}: {text}", "ตอบ {nick} แล้ว: {text}"),
    "refuse_live": ("refusing to run live: set autoreply.own_nickname first",
                    "ไม่ยอมรันจริง: ตั้ง autoreply.own_nickname ก่อน"),
    "running": ("running (dry_run={dry}). Stop: Ctrl+C or `stop` / {stop}",
                "กำลังทำงาน (dry_run={dry}) หยุดด้วย Ctrl+C หรือคำสั่ง `stop` / ไฟล์ {stop}"),
    "stopped": ("stopped", "หยุดทำงานแล้ว"),
    "interrupted": ("interrupted", "ถูกขัดจังหวะ"),
    "fatal": ("fatal error", "เกิดข้อผิดพลาดร้ายแรง"),
    "tick_failed": ("tick failed ({n}/{m})", "รอบทำงานล้มเหลว ({n}/{m})"),
    "too_many": ("too many consecutive failures; stopping", "ล้มเหลวติดต่อกันมากเกินไป จึงหยุด"),
    "re_resolve": ("re-attach failed: {e}", "จับหน้าต่างใหม่ไม่สำเร็จ: {e}"),
    "discover_done": ("wrote {p}. Copy auto_id/class_name/index into config.json selectors.",
                      "เขียนไฟล์ {p} แล้ว คัดลอก auto_id/class_name/index ไปใส่ selector ใน config.json"),
    "win_hdr": ("visible windows (title | class | PID); * = matches window_title_regex:",
                "หน้าต่างที่เห็น (ชื่อ | class | PID) เครื่องหมาย * = ตรงกับ window_title_regex:"),
    "win_none": ("no visible windows found", "ไม่พบหน้าต่างที่มองเห็น"),
    "win_hint": ("Camfrog not listed? Open it (not minimized to tray) and run as the same user / same admin level. "
                 "If its title has no 'Camfrog', set window_title_regex in config.json to part of the title above.",
                 "ไม่เห็น Camfrog? เปิดโปรแกรมให้เห็นหน้าต่าง (ไม่ซ่อนในถาดระบบ) และรันด้วยผู้ใช้/สิทธิ์ระดับเดียวกัน "
                 "ถ้าชื่อหน้าต่างไม่มีคำว่า Camfrog ให้แก้ window_title_regex ใน config.json เป็นส่วนหนึ่งของชื่อด้านบน"),
    "det_none": ("detect: found no candidate controls. Open a chat room (not tray-minimized) and retry.",
                 "detect: ไม่พบ control ที่ใช่ เปิดห้องแชทให้เห็นหน้าต่างแล้วลองใหม่"),
    "det_found": ("detect: {k} -> {sel}  [{why}]", "detect: {k} -> {sel}  [{why}]"),
    "det_miss": ("detect: {k} not found", "detect: ไม่พบ {k}"),
    "det_applied": ("detect: wrote selectors to {p}. Run `check`, then test with dry_run on.",
                    "detect: เขียน selector ลง {p} แล้ว รัน `check` แล้วทดสอบด้วย dry_run"),
    "det_report": ("detect: structure report (no chat text) -> {p}", "detect: รายงานโครงสร้าง (ไม่มีข้อความแชท) -> {p}"),
    "no_room": ("no chat-room window yet (status still works). Open a room; it is picked up automatically.",
                "ยังไม่พบหน้าต่างห้องแชท (ตั้ง status ได้ตามปกติ) เปิดห้องแชทแล้วจะจับให้เอง"),
    "room_attached": ("attached to chat room window {title}", "จับหน้าต่างห้องแชทแล้ว {title}"),
    "ok": ("ok", "สำเร็จ"),
    "failed": ("failed", "ล้มเหลว"),
    "already_running": ("already running (PID {pid})", "ทำงานอยู่แล้ว (PID {pid})"),
    "bg_started": ("started in background (PID {pid}). Log: {log}",
                   "เริ่มทำงานเบื้องหลังแล้ว (PID {pid}) ล็อก: {log}"),
    "bg_failed": ("background process exited (code {code}). See log: {log}",
                  "โปรเซสเบื้องหลังจบการทำงาน (code {code}) ดูล็อก: {log}"),
    "bg_needs_log": ("background mode needs log.file set in config",
                     "โหมดเบื้องหลังต้องตั้ง log.file ใน config"),
    "win_only": ("this command is Windows-only", "คำสั่งนี้ใช้ได้เฉพาะ Windows"),
    "not_running": ("not running", "ไม่ได้ทำงานอยู่"),
    "stopping": ("stopping PID {pid} ...", "กำลังหยุด PID {pid} ..."),
    "stopped_ok": ("stopped", "หยุดแล้ว"),
    "killed": ("did not stop in time; terminated", "หยุดไม่ทันเวลา จึงบังคับปิด"),
    "stop_failed": ("could not confirm process exit (PID {pid}); STOP and PID files were kept so you can retry",
                    "ยืนยันไม่ได้ว่าโปรเซสหยุดแล้ว (PID {pid}) จึงเก็บไฟล์ STOP/PID ไว้ให้ลองใหม่"),
    "state_running": ("RUNNING (PID {pid})", "กำลังทำงาน (PID {pid})"),
    "state_stopped": ("STOPPED", "หยุดอยู่"),
    "autostart_on": ("autostart enabled: {cmd}", "เปิดการรันอัตโนมัติแล้ว: {cmd}"),
    "autostart_off": ("autostart removed", "ยกเลิกการรันอัตโนมัติแล้ว"),
    "autostart_none": ("autostart was not set", "ไม่ได้ตั้งการรันอัตโนมัติไว้"),
    "e_sched": ("status.schedules[{i}] invalid (needs start/end HH:MM and non-empty messages)",
                "status.schedules[{i}] ไม่ถูกต้อง (ต้องมี start/end แบบ HH:MM และ messages ไม่ว่าง)"),
    "e_skip": ("autoreply.skip_patterns[{i}] invalid: {e}", "autoreply.skip_patterns[{i}] ไม่ถูกต้อง: {e}"),
    "w_mention": ("rule #{i} uses `mention` but own_nickname is empty (it will never match)",
                  "กฎ #{i} ใช้ `mention` แต่ own_nickname ว่าง (จะไม่ตรงเลย)"),
    "reloaded": ("config reloaded", "โหลดคอนฟิกใหม่แล้ว"),
    "reload_rejected": ("config reload rejected: {m}", "ไม่รับคอนฟิกที่แก้ใหม่: {m}"),
    "init_done": ("wrote {p}", "สร้างไฟล์ {p} แล้ว"),
    "init_exists": ("{p} already exists (use --force to overwrite)", "{p} มีอยู่แล้ว (ใช้ --force เพื่อเขียนทับ)"),
    "tr_lang": ("detected language: {l}", "ภาษาที่ตรวจพบ: {l}"),
    "tr_skip": ("SKIPPED ({why})", "ข้าม ({why})"),
    "tr_match": ("rule #{i} -> {reply}", "กฎ #{i} -> {reply}"),
    "tr_none": ("no rule matches", "ไม่มีกฎที่ตรง"),
    "tr_blocked": ("(blocked: empty or starts with /)", "(ถูกบล็อก: ว่างหรือขึ้นต้นด้วย /)"),
    "tr_badline": ("ignored line (not `name: message`): {line}", "ข้ามบรรทัด (ไม่ใช่รูปแบบ `ชื่อ: ข้อความ`): {line}"),
    "why_sender": ("sender filtered", "กรองผู้ส่ง"),
    "why_link": ("contains a link", "มีลิงก์"),
    "why_pattern": ("matches skip_patterns", "ตรงกับ skip_patterns"),
    "e_marquee": ("status.marquee invalid: width 8-80 (and <= status.max_length), stride 1..width, step_seconds >= 0.3, cycles 1-5, max_frames 5-300",
                  "status.marquee ไม่ถูกต้อง: width 8-80 (และไม่เกิน status.max_length), stride 1..width, step_seconds >= 0.3, cycles 1-5, max_frames 5-300"),
    "e_cycle": ("status.language_cycle items must be th or en", "status.language_cycle ต้องเป็น th หรือ en เท่านั้น"),
    "e_hmode": ("status.history.mode must be rotate, random or most_used", "status.history.mode ต้องเป็น rotate, random หรือ most_used"),
    "e_hmax": ("status.history.max_items must be 5-500 and file must be set", "status.history.max_items ต้อง 5-500 และต้องตั้ง file"),
    "w_sched_hist": ("status.schedules are ignored while status.history.use_as_source is true",
                     "status.schedules จะไม่ถูกใช้ขณะที่ status.history.use_as_source เป็น true"),
    "status_blank": ("chosen status is blank; skipped", "status ที่เลือกว่างเปล่า จึงข้าม"),
    "hist_empty": ("history is empty", "ประวัติว่างเปล่า"),
    "hist_item": ("{i:>3}. [{lang}] x{n}  {text}", "{i:>3}. [{lang}] ใช้ {n} ครั้ง  {text}"),
    "hist_added": ("added to history", "เพิ่มเข้าประวัติแล้ว"),
    "hist_exists": ("already in history (or blank)", "มีในประวัติแล้ว (หรือข้อความว่าง)"),
    "hist_imported": ("imported {n} new status(es)", "นำเข้า status ใหม่ {n} รายการ"),
    "mq_frames": ("{n} frame(s), {s}s apart -> about {t}s total", "{n} เฟรม ห่างกัน {s} วินาที -> รวมประมาณ {t} วินาที"),
    "mq_static": ("text fits in the window: no scrolling, shown as-is", "ข้อความสั้นพอดีหน้าต่าง: ไม่เลื่อน แสดงตามเดิม"),
    "e_range": ("{k} is out of range ({lo}..{hi})", "{k} อยู่นอกช่วงที่ยอมรับ ({lo}..{hi})"),
    "w_selclash": ("selectors {a} and {b} are identical: they point at the same control (run `discover`)",
                   "selector {a} กับ {b} เหมือนกัน คือ control ตัวเดียวกัน (รัน `discover`)"),
    "e_selclash": ("live mode refused: selectors {a} and {b} are identical (run `discover` and fix config.json)",
                   "ไม่ยอมรันจริง: selector {a} กับ {b} เหมือนกัน (รัน `discover` แล้วแก้ config.json)"),
    "burst_skip": ("{n} new chat lines at once (room switch / history reload?): not answered",
                   "มีข้อความใหม่ {n} บรรทัดพร้อมกัน (เปลี่ยนห้อง/โหลดประวัติ?) จึงไม่ตอบ"),
    "e_selempty": ("{k} has no criteria (set control_type, auto_id, class_name or title)",
                   "{k} ไม่มีเงื่อนไขเลย (ใส่ control_type, auto_id, class_name หรือ title)"),
    "e_im_only": ("autoreply_im.only_nicknames must list at least one friend (IM auto-reply never answers 'everyone')",
                  "autoreply_im.only_nicknames ต้องระบุเพื่อนอย่างน้อย 1 คน (ตอบ IM แบบ 'ทุกคน' ไม่ได้)"),
    "e_im_nick": ("autoreply_im needs autoreply.own_nickname (otherwise the bot would answer its own replies)",
                  "autoreply_im ต้องตั้ง autoreply.own_nickname (ไม่งั้นบอทจะตอบตัวเอง)"),
    "e_im_kind": ("autoreply.log_kind must be room while autoreply_im is enabled (room rules must never answer private chats)",
                  "autoreply.log_kind ต้องเป็น room ขณะเปิด autoreply_im (กฎห้องห้ามตอบแชทส่วนตัว)"),
    "e_im_prefix": ("autoreply_im.prefix must be text and must not start with /",
                    "autoreply_im.prefix ต้องเป็นข้อความและห้ามขึ้นต้นด้วย /"),
    "e_im_rule": ("autoreply_im.rules[{i}] invalid: {e}", "autoreply_im.rules[{i}] ไม่ถูกต้อง: {e}"),
    "e_im_skip": ("autoreply_im.skip_patterns[{i}] invalid: {e}", "autoreply_im.skip_patterns[{i}] ไม่ถูกต้อง: {e}"),
    "e_im_delay": ("autoreply_im.delay_range_seconds must be [min, max] with 0 <= min <= max",
                   "autoreply_im.delay_range_seconds ต้องเป็น [min, max] โดย 0 <= min <= max"),
    "w_im_norules": ("autoreply_im has no rules: nothing will be answered", "autoreply_im ไม่มีกฎ: จะไม่ตอบอะไรเลย"),
    "w_im_noprefix": ("autoreply_im.prefix is empty: friends cannot tell the reply is automatic",
                      "autoreply_im.prefix ว่าง: เพื่อนจะไม่รู้ว่าเป็นข้อความตอบอัตโนมัติ"),
    "im_attached": ("attached to private-chat window {title}", "จับหน้าต่างแชทส่วนตัวแล้ว {title}"),
    "im_replied": ("IM reply to {nick}: {text}", "ตอบ IM {nick} แล้ว: {text}"),
    "refuse_im": ("refusing to run: autoreply_im needs autoreply.own_nickname",
                  "ไม่ยอมรัน: autoreply_im ต้องตั้ง autoreply.own_nickname"),
    "why_bot": ("looks like an automatic reply (prefix)", "ดูเหมือนข้อความตอบอัตโนมัติ (prefix)"),
    "e_im_kinds": ("autoreply_im.log_kinds must be a non-empty list of im / mtim",
                   "autoreply_im.log_kinds ต้องเป็นรายการของ im / mtim อย่างน้อย 1 ค่า"),
    "w_im_mtim": ("autoreply_im.log_kinds includes mtim: that window kind is unverified on a live Camfrog (run `im-probe`)",
                  "autoreply_im.log_kinds มี mtim: หน้าต่างชนิดนี้ยังไม่ได้ทดสอบกับ Camfrog จริง (รัน `im-probe`)"),
    "im_on": ("IM auto-reply ON: {mode}, friends={n}, rules={r}, kinds={k}",
              "ตอบ IM อัตโนมัติ: เปิด {mode} เพื่อน={n} กฎ={r} ชนิด={k}"),
    "im_off": ("IM auto-reply is OFF (autoreply_im.enabled=false)", "ตอบ IM อัตโนมัติ: ปิดอยู่ (autoreply_im.enabled=false)"),
    "im_none": ("no private-chat window found yet. Open one, or run `im-probe` to see how Camfrog's windows are classified",
                "ยังไม่พบหน้าต่างแชทส่วนตัว เปิดหน้าต่างแชท หรือรัน `im-probe` เพื่อดูว่า Camfrog แต่ละหน้าต่างเป็นชนิดไหน"),
    "im_skip_nick": ("IM line from {nick} ignored: not in autoreply_im.only_nicknames",
                     "ข้ามข้อความ IM จาก {nick}: ไม่อยู่ใน autoreply_im.only_nicknames"),
    "stats_hdr": ("stats:", "สถิติ:"),
    "stats_line": ("  started {started} | statuses {st} | replies {rp} | failures {fl}",
                   "  เริ่ม {started} | ตั้ง status {st} | ตอบ {rp} | ล้มเหลว {fl}"),
}


def t(key: str, **kw: Any) -> str:
    en, th = S[key]
    return (th if LANG == "th" else en).format(**kw)


def resolve_lang(pref: Optional[str]) -> str:
    pref = (pref or "auto").lower()
    if pref in ("th", "en"):
        return pref
    try:
        if os.name == "nt" and (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x1E:
            return "th"
    except Exception:
        pass
    env = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
    return "th" if env.startswith("th") else "en"


def detect_lang(text: str) -> str:
    return "th" if THAI_RE.search(text) else "en"


def pick_status(item: Union[str, dict], mode: str, idx: int) -> str:
    if isinstance(item, str):
        return item
    th, en = item.get("th", ""), item.get("en", "")
    if mode == "th":
        return th or en
    if mode == "en":
        return en or th
    if mode == "alternate":
        return (th or en) if idx % 2 == 0 else (en or th)
    return " | ".join(x for x in (th, en) if x)  # both


def pick_reply(spec: Union[str, list, dict], msg_lang: str) -> Optional[str]:
    if isinstance(spec, dict):
        pool = spec.get(msg_lang) or spec.get("en" if msg_lang == "th" else "th") or []
    else:
        pool = spec
    if isinstance(pool, str):
        pool = [pool]
    return random.choice(pool) if pool else None


# ---------- config ----------
DEFAULTS = {
    "language": "auto",
    "reload_config": True,
    "stats": {"enabled": True, "file": "stats.json", "write_seconds": 60},
    "window_title_regex": ".*Camfrog.*",
    "dry_run": True,
    "poll_seconds": 1.5,
    "log": {"level": "INFO", "file": "camfrog_auto.log", "max_bytes": 1000000,
            "backups": 3, "log_message_text": True},
    "active_hours": {"enabled": False, "start": "09:00", "end": "23:30"},
    "safety": {"stop_file": "STOP", "pid_file": "camfrog_auto.pid",
               "require_foreground": True, "restore_previous_window": True,
               "discovery_mode": "legacy",  # or "verified" (executable identity + signature + role)
               "max_consecutive_failures": 5},
    "status": {
        "enabled": True,
        "edit": {"class_name": "Edit", "auto_id": "1001", "index": 0},
        "apply_button": None,
        "background_enter_target": "edit",  # experimental "combo" targets the parent CComboBoxTS
        "interval_seconds": 600, "set_on_start": True, "random": False,
        "language_mode": "both", "retry_seconds": 30, "max_length": 120, "messages": [],
        "schedules": [],
        "language_cycle": [],
        "marquee": {"enabled": False, "scroll": False, "width": 28, "stride": 2, "step_seconds": 0.5,
                    "separator": "   \u2022   ", "cycles": 1, "max_frames": 80, "infinite_loop": False},
        "history": {"enabled": True, "file": "status_history.json", "max_items": 50,
                    "record": True, "use_as_source": False, "seed_from_messages": True,
                    "mode": "rotate"},
    },
    "autoreply": {
        "enabled": True, "own_nickname": "", "window_title_regex": "", "log_kind": "room",
        "history_tail": 150,
        "history": {"class_name": "AtlAxWinLic140", "auto_id": "1002", "index": 0},
        "input": {"class_name": "AtlAxWinLic140", "auto_id": "1002", "index": 1},
        "ignore_nicknames": [], "only_nicknames": [],
        "per_sender_cooldown_seconds": 300, "global_min_gap_seconds": 20,
        "max_per_hour": 30, "delay_range_seconds": [2.0, 5.0],
        "max_reply_length": 200, "max_incoming_length": 500, "rules": [],
        "skip_patterns": [], "ignore_links": True,
    },
    # Private-message (IM) auto-reply: separate opt-in, own rules, own limits. Reuses the
    # autoreply.history / autoreply.input selectors (IM windows have the same two web panes).
    "autoreply_im": {
        "enabled": False, "dry_run": True, "window_title_regex": "", "max_windows": 5,
        "log_kinds": ["im"],
        "only_nicknames": [], "rules": [], "skip_patterns": [], "ignore_links": True,
        "prefix": "[auto] ", "answer_first_message": True,
        "per_sender_cooldown_seconds": 3600, "max_per_sender_per_day": 3,
        "global_min_gap_seconds": 20, "max_per_hour": 10, "delay_range_seconds": [2.0, 5.0],
        "max_reply_length": 200, "max_incoming_length": 500,
    },
    # Room music DJ: chat commands (!request/!randommusic/!queue/!current/!skip/!help) with a
    # persistent queue. Needs autoreply.enabled for chat plumbing. Audio backends:
    # "chat" announces only; "local" plays Windows-supported audio files; "youtube"
    # uses the official visible YouTube IFrame player in an app-owned browser window.
    "dj": {
        "enabled": False, "prefix": "!", "queue_file": "dj_queue.json",
        "max_per_user": 3, "music_dir": "music", "audio_backend": "chat",
        "announce_now": "[dj] Now playing: {title} (requested by {user})",
        "announce_queued": "[dj] Queued #{pos}: {title}",
    },
}


def _im(cfg):
    return cfg.get("autoreply_im") or DEFAULTS["autoreply_im"]


def apply_app_profile(cfg):
    """Disable features owned by other standalone executables."""
    if APP_PROFILE not in APP_PROFILES:
        raise ValueError(f"Unknown Camfrog app profile: {APP_PROFILE}")
    if APP_PROFILE == "full":
        return cfg
    result = copy.deepcopy(cfg)
    if APP_PROFILE == "room":
        result["status"]["enabled"] = False
        result["autoreply_im"]["enabled"] = False
    elif APP_PROFILE == "im_reply":
        result["status"]["enabled"] = False
        result["autoreply"]["enabled"] = False
    elif APP_PROFILE == "status":
        result["autoreply"]["enabled"] = False
        result["autoreply_im"]["enabled"] = False
    elif APP_PROFILE == "music":
        result["status"]["enabled"] = False
        result["autoreply_im"]["enabled"] = False
    return result


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_cfg(path: Union[str, Path]) -> dict:
    with Path(path).open(encoding="utf-8-sig") as f:  # Notepad may add a BOM
        return deep_merge(DEFAULTS, json.load(f))


def parse_hm(s: str) -> dt.time:
    h, m = str(s).split(":")
    return dt.time(int(h), int(m))


def validate(cfg: dict) -> tuple[list[str], list[str]]:
    """Never raises: a malformed value becomes an error message, not a traceback."""
    errs, warns = [], []
    try:
        _validate(cfg, errs, warns)
    except (TypeError, ValueError, KeyError, AttributeError) as e:
        errs.append(t("e_struct", e=f"{type(e).__name__}: {e}"))
    return errs, warns


SEL_KEYS = ("control_type", "auto_id", "class_name", "title", "title_re")
RANGES = (  # (path, lo, hi): numeric sanity so a typo cannot hammer UIA or crash the loop
    (("poll_seconds",), 0.2, 60),
    (("safety", "max_consecutive_failures"), 1, 100),
    (("status", "max_length"), 1, 500),
    (("status", "retry_seconds"), 1, 3600),
    (("autoreply", "max_reply_length"), 1, 500),
    (("autoreply", "max_incoming_length"), 1, 2000),
)


def _dig(cfg, path):
    for k in path:
        cfg = cfg[k]
    return cfg


def selector_clashes(cfg):
    """Pairs of enabled selectors that are identical (would drive one control for two jobs)."""
    sels = []
    if cfg["status"]["enabled"]:
        sels.append(("status.edit", cfg["status"]["edit"]))
    if cfg["autoreply"]["enabled"] or _im(cfg)["enabled"]:
        sels += [("autoreply.history", cfg["autoreply"]["history"]),
                 ("autoreply.input", cfg["autoreply"]["input"])]
    out = []
    for i, (na, a) in enumerate(sels):
        for nb, b in sels[i + 1:]:
            if isinstance(a, dict) and a == b:
                out.append((na, nb))
    return out


def _validate(cfg, errs, warns):
    st, ar = cfg["status"], cfg["autoreply"]
    for path, lo, hi in RANGES:
        v = _dig(cfg, path)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            errs.append(t("e_range", k=".".join(path), lo=lo, hi=hi))
    to_check = []
    if st["enabled"]:
        to_check.append(("status.edit", st["edit"]))
        if st["apply_button"]:
            to_check.append(("status.apply_button", st["apply_button"]))
    if ar["enabled"] or _im(cfg)["enabled"]:
        to_check += [("autoreply.history", ar["history"]), ("autoreply.input", ar["input"])]
    for name, sel in to_check:
        if isinstance(sel, dict) and not any(sel.get(k) not in (None, "") for k in SEL_KEYS):
            errs.append(t("e_selempty", k=name))
    for na, nb in selector_clashes(cfg):
        (errs if not cfg["dry_run"] else warns).append(
            t("e_selclash" if not cfg["dry_run"] else "w_selclash", a=na, b=nb))
    if ar.get("log_kind", "room") not in ("room", "im", "any"):
        errs.append("autoreply.log_kind must be room, im or any")
    if str(cfg["language"]).lower() not in ("auto", "th", "en"):
        errs.append(t("e_lang"))
    try:
        re.compile(cfg["window_title_regex"])
    except re.error as e:
        errs.append(t("e_regex", e=e))
    if cfg.get("safety", {}).get("discovery_mode", "legacy") not in ("legacy", "verified"):
        errs.append("safety.discovery_mode must be legacy or verified")
    if cfg["active_hours"]["enabled"]:
        try:
            parse_hm(cfg["active_hours"]["start"]); parse_hm(cfg["active_hours"]["end"])
        except Exception:
            errs.append(t("e_hours"))
    if st["enabled"]:
        if not st["messages"]:
            errs.append(t("e_msgs"))
        if st["interval_seconds"] < 0.3:
            errs.append(t("e_interval"))
        if st["language_mode"] not in ("th", "en", "both", "alternate"):
            errs.append(t("e_mode"))
        if not isinstance(st["edit"], dict):
            errs.append(t("e_sel", k="status.edit"))
        if st.get("background_enter_target", "edit") not in ("edit", "combo"):
            errs.append("status.background_enter_target must be edit or combo")
        mq, hs = st["marquee"], st["history"]
        try:
            inf = mq.get("infinite_loop", False)
            scroll = mq.get("scroll", False)
            if mq["enabled"] and scroll and not (8 <= mq["width"] <= 80 and mq["width"] <= st["max_length"]
                                      and 1 <= mq["stride"] <= mq["width"]
                                      and mq["step_seconds"] >= 0.3
                                      and (inf or (1 <= mq["cycles"] <= 5))
                                      and 5 <= mq["max_frames"] <= 300):
                errs.append(t("e_marquee"))
        except TypeError:
            errs.append(t("e_marquee"))
        if any(x not in ("th", "en") for x in st["language_cycle"]):
            errs.append(t("e_cycle"))
        if hs["enabled"]:
            if hs["mode"] not in ("rotate", "random", "most_used"):
                errs.append(t("e_hmode"))
            if not (5 <= hs["max_items"] <= 500) or not hs["file"]:
                errs.append(t("e_hmax"))
            if hs["use_as_source"] and st["schedules"]:
                warns.append(t("w_sched_hist"))
        for i, sc in enumerate(st["schedules"]):
            try:
                parse_hm(sc["start"]); parse_hm(sc["end"])
                if not sc["messages"]:
                    raise ValueError
            except Exception:
                errs.append(t("e_sched", i=i))
    if ar["enabled"]:
        for k in ("history", "input"):
            if not isinstance(ar[k], dict):
                errs.append(t("e_sel", k=f"autoreply.{k}"))
        lo, hi = ar["delay_range_seconds"]
        if not (0 <= lo <= hi):
            errs.append(t("e_delay"))
        if ar["max_per_hour"] < 1:
            errs.append(t("e_hour"))
        if ar["per_sender_cooldown_seconds"] < 0 or ar["global_min_gap_seconds"] < 0:
            errs.append(t("e_cool"))
        if not ar["rules"]:
            warns.append(t("w_norules"))
        for i, r in enumerate(ar["rules"]):
            try:
                re.compile(r["pattern"], re.I)
            except (re.error, KeyError) as e:
                errs.append(t("e_rule", i=i, e=e))
            if not r.get("reply"):
                errs.append(t("e_reply", i=i))
            if r.get("mention") and not ar["own_nickname"].strip():
                warns.append(t("w_mention", i=i + 1))
        for i, sp in enumerate(ar["skip_patterns"]):
            try:
                re.compile(sp, re.I)
            except re.error as e:
                errs.append(t("e_skip", i=i, e=e))
        if not ar["own_nickname"].strip():
            warns.append(t("w_nick"))
    _validate_im(cfg, errs, warns)
    _validate_dj(cfg, errs, warns)
    if not cfg["dry_run"]:
        warns.append(t("w_live"))


IM_RANGES = (  # (key, lo, hi)
    ("per_sender_cooldown_seconds", 60, 604800),
    ("max_per_sender_per_day", 1, 50),
    ("global_min_gap_seconds", 0, 3600),
    ("max_per_hour", 1, 100),
    ("max_reply_length", 1, 500),
    ("max_incoming_length", 1, 2000),
    ("max_windows", 1, 20),
)


def _validate_im(cfg, errs, warns):
    """Private-message auto-reply is stricter than room auto-reply: allowlist and own nick are
    mandatory, the prefix cannot be a command, and room rules can never reach a private chat."""
    im, ar = _im(cfg), cfg["autoreply"]
    if not im["enabled"]:
        return
    if not [n for n in im["only_nicknames"] if str(n).strip()]:
        errs.append(t("e_im_only"))
    if not ar["own_nickname"].strip():
        errs.append(t("e_im_nick"))
    if ar["enabled"] and ar.get("log_kind", "room") != "room":
        errs.append(t("e_im_kind"))
    for k in ("history", "input"):
        if not isinstance(ar[k], dict):
            errs.append(t("e_sel", k=f"autoreply.{k}"))
    for key, lo, hi in IM_RANGES:
        v = im[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            errs.append(t("e_range", k=f"autoreply_im.{key}", lo=lo, hi=hi))
    try:
        lo, hi = im["delay_range_seconds"]
        if not (0 <= lo <= hi):
            raise ValueError
    except (TypeError, ValueError):
        errs.append(t("e_im_delay"))
    try:
        re.compile(im["window_title_regex"])
    except re.error as e:
        errs.append(t("e_regex", e=e))
    ks = im["log_kinds"]
    if not isinstance(ks, list) or not ks or any(k not in ("im", "mtim") for k in ks):
        errs.append(t("e_im_kinds"))
    elif "mtim" in ks:
        warns.append(t("w_im_mtim"))
    pre = im["prefix"]
    if not isinstance(pre, str) or pre.lstrip().startswith("/"):
        errs.append(t("e_im_prefix"))
    elif not pre.strip():
        warns.append(t("w_im_noprefix"))
    for i, r in enumerate(im["rules"]):
        try:
            re.compile(r["pattern"], re.I)
        except (re.error, KeyError) as e:
            errs.append(t("e_im_rule", i=i, e=e))
        if not r.get("reply"):
            errs.append(t("e_reply", i=i))
    for i, sp in enumerate(im["skip_patterns"]):
        try:
            re.compile(sp, re.I)
        except re.error as e:
            errs.append(t("e_im_skip", i=i, e=e))
    if not im["rules"]:
        warns.append(t("w_im_norules"))


def _validate_dj(cfg, errs, warns):
    """Room music DJ: needs chat plumbing, a sane prefix, caps and a backend."""
    dj = cfg.get("dj") or DEFAULTS["dj"]
    try:
        dj_queue_path_for_config(cfg)
    except (OSError, ValueError):
        errs.append("dj.queue_file must stay inside the Music DJ data folder")
    if not dj["enabled"]:
        return
    if not cfg["autoreply"]["enabled"]:
        errs.append("dj needs autoreply.enabled (chat plumbing)")
    pre = dj.get("prefix", "!")
    if not isinstance(pre, str) or not pre.strip() or pre.lstrip().startswith("/"):
        errs.append("dj.prefix must be a non-empty, non-command string")
    v = dj.get("max_per_user", 3)
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 50:
        errs.append("dj.max_per_user must be 1-50")
    if dj.get("audio_backend", "chat") not in ("chat", "local", "youtube"):
        errs.append('dj.audio_backend must be "chat", "local" or "youtube"')
    for key in ("announce_now", "announce_queued"):
        if not isinstance(dj.get(key), str) or not dj[key]:
            errs.append(f"dj.{key} must be a non-empty string")


def in_window(start: str, end: str, now: Optional[dt.time] = None) -> bool:
    now = now or dt.datetime.now().time()
    s, e = parse_hm(start), parse_hm(end)
    return s <= now <= e if s <= e else (now >= s or now <= e)  # overnight OK


def in_active_hours(cfg: dict) -> bool:
    a = cfg["active_hours"]
    return True if not a["enabled"] else in_window(a["start"], a["end"])


def shown(text: str) -> str:
    return repr(text) if LOG_TEXT else f"<{len(text)} chars>"


# ---------- UI helpers ----------
def is_own_gui_window(win) -> bool:
    """True for our own Tk GUI windows (they contain 'Camfrog' in the title
    and would otherwise match window_title_regex). Camfrog itself never
    uses the TkTopLevel window class."""
    try:
        if win.class_name() == "TkTopLevel":
            return True
    except Exception:
        pass
    try:
        title = win.window_text()
        if title and title.startswith((
                "Camfrog Status Changer", "Camfrog Random Status", "Camfrog Marquee Status")):
            return True
    except Exception:
        pass
    return False


def _window_process_id(window):
    """Return a positive process ID for a UIA top-level window, or None."""
    try:
        pid = int(window.process_id())
        return pid if pid > 0 else None
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def _require_single_window_process(windows):
    """Fail closed when a title filter spans more than one/unverifiable process."""
    if len(windows) <= 1:
        return
    pids = [_window_process_id(window) for window in windows]
    if any(pid is None for pid in pids) or len(set(pids)) != 1:
        raise RuntimeError(
            "Multiple or unverifiable Camfrog processes match the configured window filter; "
            "narrow window_title_regex or close extra clients."
        )


def get_verified_main_window():
    """Resolve the Camfrog main window via verified process identity.

    Uses WindowsCamfrogProcessDiscovery (executable identity, Authenticode
    signature, PID + creation time; CEF and ZChat own-process exclusion) and
    WindowsCamfrogWindowClassifier (structural MAIN_DIRECTORY role). Fails
    closed on ambiguous process or window identity instead of guessing.
    """
    from camfrog_windows_runtime import (
        WindowRole,
        WindowsCamfrogProcessDiscovery,
        WindowsCamfrogWindowClassifier,
    )
    discovery = WindowsCamfrogProcessDiscovery()
    try:
        identity = discovery.get_verified_process()
    except ProcessLookupError as exc:
        raise RuntimeError(t("verified_ambiguous", detail=str(exc)))
    except RuntimeError as exc:
        raise RuntimeError(t("verified_ambiguous", detail=str(exc)))
    classifier = WindowsCamfrogWindowClassifier()
    windows = classifier.classify_process_windows(identity, visible_only=True)
    mains = [w for w in windows
             if w.role == WindowRole.MAIN_DIRECTORY and w.element is not None]
    if len(mains) != 1:
        raise RuntimeError(t("verified_ambiguous", detail=(
            f"expected exactly one visible MAIN_DIRECTORY window for PID {identity.pid}, "
            f"found {len(mains)}")))
    return mains[0].element


def get_window(cfg: dict):
    if cfg.get("safety", {}).get("discovery_mode", "legacy") == "verified":
        return get_verified_main_window()
    from pywinauto import Desktop  # lazy: `check` works without pywinauto
    wins = Desktop(backend="uia").windows(
        title_re=cfg["window_title_regex"], visible_only=True)
    wins = [w for w in wins if not is_own_gui_window(w)]
    if not wins:
        raise RuntimeError(t("no_window"))
    _require_single_window_process(wins)

    # Find the window that actually contains the status edit control.
    # The status edit is in the buddy list window (has CComboBoxTS with auto_id 1436
    # containing an Edit with auto_id 1001). Try each window until we find it.
    st_edit_spec = cfg.get("status", {}).get("edit", {})
    if st_edit_spec:
        for w in wins:
            try:
                find(w, st_edit_spec)
                return w
            except Exception:
                continue

    # Fallback: return first window if status edit not found in any (for detect/initial setup)
    log.warning("Status edit control not found in any matching window; using first window")
    return wins[0]


def process_windows(main) -> list:
    """All visible top-level windows of the same process as `main` (Camfrog opens each chat room,
    IM and the buddy list as separate windows)."""
    from pywinauto import Desktop
    return Desktop(backend="uia").windows(process=main.process_id(), visible_only=True)


def get_chat_window(cfg: dict, main) -> Optional[Any]:
    """Chat-room window: another window of Camfrog's process (optionally matching
    autoreply.window_title_regex) that actually contains the configured input control."""
    rx = cfg["autoreply"].get("window_title_regex") or ""
    for w in process_windows(main):
        if w.handle == main.handle:
            continue
        try:
            if rx and not re.search(rx, w.window_text()):
                continue
            find(w, cfg["autoreply"]["input"])
            hist = find(w, cfg["autoreply"]["history"])
            want = cfg["autoreply"].get("log_kind", "room")
            if want != "any" and is_web(hist) and log_kind(hist) != want:
                continue  # e.g. a private-message window: never auto-answer it
            return w
        except Exception:
            continue
    return None


def _ctrl_attr(ctrl, attr: str) -> str:
    try:
        return getattr(ctrl.element_info, attr) or ""
    except Exception:
        return ""


def get_im_windows(cfg, main, limit=None):
    """Private-message windows of Camfrog's process as [(window, history, input)].
    Only panes that identify as `wb-log-im-data` qualify; room windows and the unverified
    `wb-log-mtim-data` kind are never returned (fail-safe)."""
    ar, im = cfg["autoreply"], _im(cfg)
    rx, limit = im["window_title_regex"], limit or im["max_windows"]
    out = []
    for w in process_windows(main):
        if w.handle == main.handle:
            continue
        try:
            if rx and not re.search(rx, w.window_text()):
                continue
            inp, hist = find(w, ar["input"]), find(w, ar["history"])
            if not (is_web(hist) and log_kind(hist) in im["log_kinds"]):
                continue
        except Exception:
            continue
        out.append((w, hist, inp))
        if len(out) >= limit:
            break
    return out


def find(win, spec: dict):
    """Locate a control. Only class_name/control_type/title go to pywinauto's descendants():
    older pywinauto (0.6.x) raises TypeError on auto_id / title_re there, so those two are
    filtered here instead."""
    kw = {k: v for k, v in spec.items() if k in ("class_name", "control_type", "title")}
    items = win.descendants(**kw)
    if spec.get("auto_id") not in (None, ""):
        want = str(spec["auto_id"])
        items = [c for c in items if str(_ctrl_attr(c, "automation_id")) == want]
    if spec.get("title_re"):
        rx = re.compile(spec["title_re"])
        items = [c for c in items if rx.match(str(_ctrl_attr(c, "name")))]
    idx = spec.get("index", 0)
    if len(items) <= idx:
        raise LookupError(t("no_ctrl", spec=spec, n=len(items)))
    return items[idx]


def read_text(ctrl) -> str:
    try:
        v = ctrl.iface_value.CurrentValue
        if v:
            return v
    except Exception:
        pass
    v = ctrl.window_text()
    return v if v else "\n".join(ctrl.texts() or [])


# ---- Camfrog 8.x room log: an embedded IE/MSHTML pane (verified from a real dump) ----
TIME_RE = re.compile(r"^\s*\(?\d{1,2}[:.]\d{2}([:.]\d{2})?\s*([AaPp][Mm])?\)?\s*$")


def parse_web_log(items):
    """Flatten UIA items [{'type','name'}] of the room log into 'nick: message' lines.
    Layout seen in the real tree: Image(avatar) > Hyperlink(nick) [> Text(nick)] > Text(time) >
    Text(message...). Rows without a Hyperlink (join/leave notices) and time stamps are dropped."""
    lines, nick, parts, want_child = [], None, [], False

    def flush():
        if nick and parts:
            lines.append(f"{nick}: {' '.join(parts)}")
    for it in items:
        ty, name = it.get("type", ""), (it.get("name") or "").strip()
        if ty == "Image":
            flush(); nick, parts, want_child = None, [], False
        elif ty == "Hyperlink":
            flush(); parts = []
            nick, want_child = (name.rstrip(":").strip() or None), not name
        elif ty == "Text" and name:
            if want_child:
                nick, want_child = name.rstrip(":").strip(), False
            elif nick and not TIME_RE.match(name) and name.rstrip(":") != nick:
                parts.append(name)
    flush()
    return lines


def web_items(ctrl, tail=150):
    out = []
    for c in ctrl.descendants():
        ty = _ctrl_attr(c, "control_type")
        if ty in ("Image", "Hyperlink", "Text"):
            out.append({"type": ty, "name": "" if ty == "Image" else str(_ctrl_attr(c, "name"))})
    return out[-tail:] if tail else out


def parse_web_dom_rows(rows, tail=150):
    """Convert current Camfrog MSHTML rows into 'nick: message' lines.

    Current Camfrog exposes message rows as `span.msg-user`, with sibling
    `nickname-field` and `msgid` spans. Validate the nickname before returning
    it so UI text that is not a message row cannot enter the reply pipeline.
    """
    lines = []
    for row in rows:
        row_classes = set(str(row.get("class_name", "")).lower().split())
        if "msg-user" not in row_classes:
            continue
        nick = message = ""
        for child in row.get("children", ()):
            classes = set(str(child.get("class_name", "")).lower().split())
            value = str(child.get("text", "")).strip()
            if "nickname-field" in classes:
                nick = value
            elif "msgid" in classes:
                message = value
        if not NICK_RE.fullmatch(nick) or not message:
            continue
        message = " ".join(message.split())
        if message:
            lines.append(f"{nick}: {message}")
    return lines[-tail:] if tail else lines


def web_dom_rows(ctrl):
    """Read Camfrog's IE/MSHTML message rows without logging private text.

    UI Automation no longer exposes the sender hyperlink in current Camfrog
    builds. `WM_HTML_GETOBJECT` gives the same embedded document and preserves
    its explicit sender/message classes. Return None when this path is not
    available so callers can retain the older UIA parser as a fallback.
    """
    try:
        import pythoncom
        import win32con
        import win32gui
        from win32com.client import Dispatch

        servers = ctrl.descendants(class_name="Internet Explorer_Server")
        if not servers:
            return None
        msg = win32gui.RegisterWindowMessage("WM_HTML_GETOBJECT")
        sent, result = win32gui.SendMessageTimeout(
            servers[0].handle, msg, 0, 0, win32con.SMTO_ABORTIFHUNG, 1000
        )
        if not sent or not result:
            return None
        document = Dispatch(pythoncom.ObjectFromLresult(result, pythoncom.IID_IDispatch, 0))
        elements = document.all
        rows = []
        for index in range(int(elements.length)):
            nickname = elements.item(index)
            if "nickname-field" not in str(nickname.className or "").lower().split():
                continue
            parent = nickname.parentElement
            row_classes = str(parent.className or "")
            if "msg-user" not in row_classes.lower().split():
                continue
            children = []
            for child_index in range(int(parent.children.length)):
                child = parent.children.item(child_index)
                children.append({
                    "class_name": str(child.className or ""),
                    "text": str(child.innerText or ""),
                })
            rows.append({"class_name": row_classes, "children": children})
        return rows
    except Exception as exc:
        log.debug("MSHTML chat history read failed: %s", type(exc).__name__)
        return None


def is_web(ctrl):
    return _ctrl_attr(ctrl, "class_name") == "AtlAxWinLic140"


def log_kind(ctrl):
    """'room' / 'im' / 'mtim' for a Camfrog web log pane (so a private-message window is never auto-answered).
    Returns None if the pane kind cannot be determined."""
    try:
        for d in ctrl.descendants(class_name="Internet Explorer_Server"):
            n = str(_ctrl_attr(d, "name"))
            if "wb-log-room-data" in n:
                return "room"
            if "wb-log-im-data" in n:
                return "im"
            if "wb-log-mtim-data" in n:
                return "mtim"
    except Exception:
        pass
    return None


def read_chat(ctrl, tail=150):
    """Chat history as text lines. Web log panes are parsed into 'nick: message'; other controls
    (older native builds) go through read_text()."""
    if is_web(ctrl):
        dom_rows = web_dom_rows(ctrl)
        if dom_rows is not None:
            dom_lines = parse_web_dom_rows(dom_rows, tail)
            if dom_lines:
                return "\n".join(dom_lines)
        return "\n".join(parse_web_log(web_items(ctrl, tail)))
    return read_text(ctrl)


def _wm_settext(ctrl, text):
    """Last resort for real HWND edit/richedit controls (Camfrog's input is RICHEDIT50W)."""
    WM_SETTEXT = 0x000C
    u = ctypes.windll.user32
    u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_wchar_p]
    u.SendMessageW(ctrl.handle, WM_SETTEXT, None, text)


def write_text(ctrl, text: str) -> bool:
    for fn in (lambda t_: ctrl.set_edit_text(t_), lambda t_: ctrl.iface_value.SetValue(t_),
               lambda t_: _wm_settext(ctrl, t_)):
        try:
            fn(text)
        except Exception:
            continue
        if read_text(ctrl).strip() == text.strip():
            return True
    return False


def foreground_ok(win, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        win.set_focus()
        if ctypes.windll.user32.GetForegroundWindow() == win.handle:
            return True
        time.sleep(0.1)
    return False


def _clip_get():
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetClipboardData.restype = ctypes.c_void_p
    k.GlobalLock.restype, k.GlobalLock.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u.OpenClipboard(None):
        return None
    try:
        h = u.GetClipboardData(13)  # CF_UNICODETEXT
        if not h:
            return ""
        p = k.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ""
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def _clip_set(text):
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    k.GlobalAlloc.restype, k.GlobalAlloc.argtypes = ctypes.c_void_p, [ctypes.c_uint, ctypes.c_size_t]
    k.GlobalLock.restype, k.GlobalLock.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k.GlobalFree.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = (text + "\0").encode("utf-16-le")
    if not u.OpenClipboard(None):
        return False
    try:
        u.EmptyClipboard()
        h = k.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not h:
            return False
        p = k.GlobalLock(h)
        if not p:
            k.GlobalFree(h)
            return False
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        return bool(u.SetClipboardData(13, h))
    finally:
        u.CloseClipboard()


def _escape_keys(text):
    return "".join("{%s}" % c if c in "{}()+^%~" else c for c in text)


def commit_web(win, ctrl, text: str, require_fg: bool = True, restore: bool = False) -> bool:
    """Send into Camfrog's web chat box. There is no text control to write to, so: bring the room to
    front, click the box, paste (clipboard is restored) or type, press Enter. Never types unless the
    room window is verified foreground, re-checked right before Enter."""
    from pywinauto import keyboard
    user32 = ctypes.windll.user32
    prev = user32.GetForegroundWindow() if restore else 0
    if not foreground_ok(win):
        log.error(t("not_fg"))
        return False
    ctrl.click_input()  # caret into the editable area
    time.sleep(0.15)
    if user32.GetForegroundWindow() != win.handle:
        log.error(t("not_fg"))
        return False
    old = _clip_get()
    pasted = _clip_set(text)
    try:
        keyboard.send_keys("^a", pause=0.02)
        keyboard.send_keys("^v" if pasted else _escape_keys(text), pause=0.01, with_spaces=True)
        time.sleep(0.2)
    finally:
        if pasted and old is not None:
            time.sleep(0.2)
            _clip_set(old)
    if user32.GetForegroundWindow() != win.handle:
        log.error(t("not_fg"))
        return False
    keyboard.send_keys("{ENTER}")
    if prev and prev != win.handle and user32.IsWindow(prev):
        time.sleep(0.15)
        user32.SetForegroundWindow(prev)
    return True


def press_button(btn):
    """Camfrog's CButtonTS buttons are owner-drawn and expose no UIA Invoke pattern: try Invoke,
    then background messages, then a real click."""
    try:
        btn.invoke()
        return
    except Exception:
        pass
    handle = getattr(btn, "handle", 0)
    if not handle and hasattr(btn, "element_info"):
        handle = getattr(btn.element_info, "handle", 0)
    if handle:
        import ctypes
        u = ctypes.windll.user32
        u.PostMessageW(handle, 0x0201, 1, 0)
        u.PostMessageW(handle, 0x0202, 0, 0)
        time.sleep(0.05)
        return
    btn.click_input()


def _background_enter(ctrl, target="edit"):
    """Post one Enter to the status control, optionally its verified combo parent.

    The combo target is experimental: only the parent of a Camfrog status Edit
    qualifies. Do not silently fall back to another control on mismatch.
    """
    if target == "combo":
        parent = getattr(getattr(ctrl, "element_info", None), "parent", None)
        if getattr(parent, "class_name", None) != "CComboBoxTS":
            raise LookupError("Status Edit has no CComboBoxTS parent; cannot try combo Enter")
        handle = getattr(parent, "handle", 0)
    else:
        handle = getattr(ctrl, "handle", 0)
        if not handle and hasattr(ctrl, "element_info"):
            handle = getattr(ctrl.element_info, "handle", 0)
        if not handle:
            ctrl.send_keystrokes("{ENTER}")
            return
    if not handle:
        raise LookupError("Status combo has no HWND; cannot try combo Enter")
    u = ctypes.windll.user32
    if not u.PostMessageW(handle, 0x0100, 0x0D, 0):
        raise RuntimeError("Could not post Enter key-down to status control")
    if not u.PostMessageW(handle, 0x0101, 0x0D, 0):
        raise RuntimeError("Could not post Enter key-up to status control")


# --- user-activity guard (kept identical in every self-contained module) ---
class UserActivityGuard:
    """Yield the desktop to the human before any command that injects global input.

    Commands that move the mouse, steal focus or send real keystrokes must not run
    while the user is working. Wait (bounded) until the user has been idle for
    `idle_ms`; input produced by our own injection is not counted as user activity.
    Windows only: elsewhere `user_idle_ms()` is None and the guard never blocks.
    """

    def __init__(self, idle_ms=None, poll=0.25, max_wait=120.0, tick=None,
                 last_input=None, sleep=None, clock=None):
        if idle_ms is None:
            try:
                idle_ms = int(os.environ.get("ZCHAT_USER_IDLE_MS", "2000"))
            except ValueError:
                idle_ms = 2000
        self.idle_ms = max(0, idle_ms)
        self.poll = poll
        self.max_wait = max_wait
        self._tick = tick or self._win_tick
        self._last_input = last_input or self._win_last_input
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._own = None  # (start_tick, end_tick) of our latest injection
        self._user_tick = None

    @staticmethod
    def _win_tick():
        return ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF

    @staticmethod
    def _win_last_input():
        if os.name != "nt":
            return None

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return info.dwTime

    def mark_own(self, start, end, slack=400):
        self._own = (start, (end + slack) & 0xFFFFFFFF)

    def _is_own(self, last):
        if not self._own:
            return False
        start, end = self._own
        return ((last - start) & 0xFFFFFFFF) <= ((end - start) & 0xFFFFFFFF)

    def user_idle_ms(self):
        """Milliseconds since the last *user* input, or None when unknown."""
        try:
            last = self._last_input()
            if last is None:
                return None
            if not self._is_own(last):
                self._user_tick = last
            if self._user_tick is None:
                return 10 ** 9  # only our own input seen so far
            return (self._tick() - self._user_tick) & 0xFFFFFFFF
        except Exception:
            return None

    def wait_until_idle(self):
        """True once the user is idle (or activity cannot be measured); False on timeout."""
        if self.idle_ms <= 0:
            return True
        deadline = self._clock() + self.max_wait
        noted = False
        while True:
            idle = self.user_idle_ms()
            if idle is None or idle >= self.idle_ms:
                return True
            if self._clock() >= deadline:
                return False
            if not noted:
                log.info("User is active; deferring input injection until the desktop is idle")
                noted = True
            self._sleep(self.poll)

    def run(self, func, *args, **kwargs):
        start = self._tick() if os.name == "nt" else 0
        try:
            return func(*args, **kwargs)
        finally:
            if os.name == "nt":
                self.mark_own(start, self._tick())


USER_GUARD = UserActivityGuard()
_BACKGROUND_MODES = ("strict_background", "best_effort_background")


def _injects_global_input(bound) -> bool:
    if bound.get("dry"):
        return False
    if bound.get("execution_mode", "interactive") in _BACKGROUND_MODES:
        return False
    if is_web(bound.get("ctrl")):
        return True
    if bound.get("apply_btn") is not None:
        return True
    if bound.get("background_enter_target") == "combo" or not bound.get("require_fg", True):
        return False
    return True


def commit(*args, **kwargs) -> bool:
    """Gate `_commit_impl` so input-injecting sends never run while the user is active."""
    import inspect
    bound = inspect.signature(_commit_impl).bind(*args, **kwargs)
    bound.apply_defaults()
    if not _injects_global_input(bound.arguments):
        return _commit_impl(*args, **kwargs)
    if not USER_GUARD.wait_until_idle():
        log.error("Deferred: user stayed active; not taking over mouse/keyboard")
        return False
    return USER_GUARD.run(_commit_impl, *args, **kwargs)
# --- end user-activity guard ---


def _commit_impl(win, ctrl, text: str, dry: bool, require_fg: bool = True, apply_btn=None,
           restore: bool = False, background_enter_target: str = "edit") -> bool:
    """Write text, verify read-back, confirm Camfrog is foreground, send, restore focus."""
    if dry:
        log.info(t("dry_send", text=shown(text)))
        return True
    if is_web(ctrl):
        return commit_web(win, ctrl, text, require_fg, restore)
    if not write_text(ctrl, text):
        log.error(t("mismatch"))
        return False
    user32 = ctypes.windll.user32
    prev = user32.GetForegroundWindow() if restore else 0
    
    needs_foreground = require_fg and apply_btn is None
    if needs_foreground and not foreground_ok(win):
        log.error(t("not_fg"))
        return False

    if apply_btn is not None:
        press_button(apply_btn)
    else:
        if require_fg:
            ctrl.set_focus()
            if user32.GetForegroundWindow() != win.handle:  # focus stolen: do not type
                log.error(t("not_fg"))
                return False
            ctrl.type_keys("{ENTER}")
        else:
            _background_enter(ctrl, background_enter_target)
    if prev and prev != win.handle and user32.IsWindow(prev):
        time.sleep(0.15)
        user32.SetForegroundWindow(prev)  # best effort: give focus back to what you were doing
    return True


RESET_BURST = 25  # more fresh chat lines than this in one poll = history reset, not live chat
LINK_RE = re.compile(r"(https?://|www\.)", re.I)


def expand(text: str, nick: str = "", own: str = "") -> str:
    """Replace {sender} {me} {time} {date}. Plain replace (never str.format)."""
    now = dt.datetime.now()
    for k, v in (("{sender}", nick), ("{me}", own), ("{time}", now.strftime("%H:%M")),
                 ("{date}", now.strftime("%Y-%m-%d"))):
        text = text.replace(k, v)
    return text


def compile_rules(ar: dict) -> list[dict]:
    return [{
        "idx": i + 1, "rx": re.compile(r["pattern"], re.I), "spec": r["reply"],
        "lang": r.get("lang"), "mention": bool(r.get("mention")),
        "cooldown": r.get("cooldown_seconds", 0), "last": -1e9,
    } for i, r in enumerate(ar["rules"]) if r.get("enabled", True)]


def compile_skips(ar: dict) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in ar["skip_patterns"]]


def is_skipped(msg: str, skips: list[re.Pattern], ignore_links: bool) -> Optional[str]:
    if ignore_links and LINK_RE.search(msg):
        return "link"
    return "pattern" if any(rx.search(msg) for rx in skips) else None


def match_rule(rules: list[dict], msg: str, mlang: str, own: str) -> Optional[dict]:
    low = msg.lower()
    for r in rules:
        if r["lang"] and r["lang"] != mlang:
            continue
        if r["mention"] and (not own or own not in low):
            continue
        if r["rx"].search(msg):
            return r
    return None


def atomic_write(path: Union[str, Path], text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # the config subfolder may not exist yet
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(5):  # AV/indexers can briefly lock the target
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2 * (i + 1))
    os.replace(tmp, path)


LEAD_VOWELS = set("\u0e40\u0e41\u0e42\u0e43\u0e44")  # เ แ โ ใ ไ  (written before the consonant)


def clusters(text: str) -> list[str]:
    """Split into display clusters: combining marks (Thai vowels/tone marks, emoji
    modifiers) stay with their base; Thai leading vowels stay with the next consonant."""
    out, prefix = [], ""
    for ch in text:
        if ch in LEAD_VOWELS:
            prefix += ch
            continue
        attach = (unicodedata.category(ch) in ("Mn", "Mc", "Me") or ch in "\u200d\ufe0f\ufe0e"
                  or (out and out[-1].endswith("\u200d")))
        if out and not prefix and attach:
            out[-1] += ch
        else:
            out.append(prefix + ch)
            prefix = ""
    if prefix:
        out.append(prefix)
    return out


def clip(text: str, limit: int) -> str:
    """Truncate to `limit` characters without cutting a Thai cluster (no dangling vowel/mark)."""
    if len(text) <= limit:
        return text
    out, n = [], 0
    for c in clusters(text):
        if n + len(c) > limit:
            break
        out.append(c)
        n += len(c)
    return "".join(out)


def marquee_frames(text: str, width: int, stride: int = 2, separator: str = "   \u2022   ",
                   cycles: int = 1, max_frames: int = 80, infinite_loop: bool = False) -> list[str]:
    """Frames of a cyclic ticker. Never blank, never splits a Thai cluster, bounded length.
    Returns [text] unchanged when it already fits in `width` clusters.
    If infinite_loop=True, returns loop frames (no final settle frame) up to max_frames."""
    cl = clusters(text.strip())
    if not cl or len(cl) <= width:
        return [text.strip()] if cl else []
    ticker = cl + clusters(separator)
    n = len(ticker)
    if infinite_loop:
        steps = max_frames
    else:
        steps = min(max_frames, max(1, -(-n * cycles // stride)))
    frames = []
    for k in range(steps):
        start = (k * stride) % n
        f = "".join(ticker[(start + j) % n] for j in range(width)).strip()
        if f and (not frames or frames[-1] != f):
            frames.append(f)
    return frames


class StatusHistory:
    """Local status history (JSON). Switch modes: rotate (round-robin), random, most_used."""

    def __init__(self, path, max_items=50):
        self.path, self.max_items, self.items = Path(path), max_items, []

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            self.items = [{"text": i["text"], "lang": detect_lang(i["text"]),
                           "count": int(i.get("count", 0)), "last": float(i.get("last", 0))}
                          for i in raw if isinstance(i, dict) and str(i.get("text", "")).strip()]
        except Exception:
            self.items = []

    def save(self):
        try:
            atomic_write(self.path, json.dumps(self.items, ensure_ascii=False, indent=2))
        except Exception as e:
            log.debug("history save failed: %s", e)

    def find(self, text):
        return next((i for i in self.items if i["text"] == text), None)

    def add(self, text, count=0):
        text = str(text).strip()
        if not text or self.find(text):
            return False
        new = {"text": text, "lang": detect_lang(text), "count": count, "last": 0.0}
        self.items.append(new)
        while len(self.items) > self.max_items:  # drop least used, then oldest; never the newcomer
            self.items.remove(min((i for i in self.items if i is not new),
                                  key=lambda i: (i["count"], i["last"])))
        return True

    def record(self, text):
        text = str(text).strip()
        if not text:
            return
        self.add(text)
        it = self.find(text)
        if it:
            it["count"] += 1
            it["last"] = time.time()
        self.save()

    def pick(self, mode="rotate", lang=None, exclude=None):
        pool = [i for i in self.items if i["text"] != exclude and (not lang or i["lang"] == lang)]
        if not pool:  # no item in that language: fall back to any language
            pool = [i for i in self.items if i["text"] != exclude]
        if not pool:
            return None
        if mode == "random":
            return random.choice(pool)["text"]
        if mode == "most_used":
            return max(pool, key=lambda i: (i["count"], -i["last"]))["text"]
        return min(pool, key=lambda i: i["last"])["text"]  # rotate: least recently used first


def safe_reply(template, nick, limit, own=""):
    text = CTRL_CHARS.sub(" ", expand(template, nick, own)).strip()
    if not text or text.startswith("/"):
        return None  # never emit room commands from the auto-replier
    return clip(text, limit)


def new_lines(prev, cur):
    for k in range(min(len(prev), len(cur)), -1, -1):
        if prev[len(prev) - k:] == cur[:k]:
            return cur[k:]
    return cur


# ---------- process control (background mode) ----------
def pid_path(cfg):
    return BASE / cfg["safety"]["pid_file"]


def read_pid(cfg):
    try:
        return int(pid_path(cfg).read_text().strip())
    except Exception:
        return None


def _process_query_api():
    """Return kernel32 process-query APIs with pointer-safe Windows types."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD))
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32, wintypes


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        try:
            pid = int(pid)
            if not 0 < pid <= 0xFFFFFFFF:
                return False
            kernel32, wintypes = _process_query_api()
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, TypeError, ValueError, OverflowError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_image(pid):
    """Executable path of a process (Windows), or None when unknown."""
    try:
        pid = int(pid)
        if not 0 < pid <= 0xFFFFFFFF:
            return None
        kernel32, wintypes = _process_query_api()
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            ok = kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size))
            return buffer.value if ok else None
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def pid_is_ours(pid, image=None):
    """Only trust this executable or its Python worker interpreter."""
    if os.name != "nt":
        return True
    image = image if image is not None else pid_image(pid)
    if not image:
        return False
    candidate = ntpath.normcase(ntpath.normpath(str(image)))
    executables = [sys.executable]
    base_executable = getattr(sys, "_base_executable", None)
    if not getattr(sys, "frozen", False) and base_executable:
        executables.append(base_executable)
    expected = set()
    for executable in executables:
        current = ntpath.normcase(ntpath.normpath(str(executable)))
        expected.add(current)
        current_name = ntpath.basename(current).casefold()
        if current_name in {"python.exe", "pythonw.exe"}:
            sibling = "pythonw.exe" if current_name == "python.exe" else "python.exe"
            expected.add(ntpath.normcase(
                ntpath.join(ntpath.dirname(current), sibling)))
    return candidate in expected


def running_pid(cfg):
    pid = read_pid(cfg)
    return pid if pid_alive(pid) and pid_is_ours(pid) else None


def child_cmd(args):
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        py = Path(sys.executable)
        pyw = py.with_name("pythonw.exe")
        cmd = [str(pyw if pyw.exists() else py), str(Path(__file__).resolve())]
    if getattr(args, "config", None):
        cmd += ["--config", str(Path(args.config).resolve())]
    if getattr(args, "lang", None):
        cmd += ["--lang", args.lang]
    return cmd


def child_env():
    """A frozen onefile exe that re-launches itself must NOT reuse the parent's unpack
    dir (it is deleted when the parent exits -> the hidden child would crash)."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # PyInstaller >= 6.9
        env.pop("_MEIPASS2", None)                  # older PyInstaller
    return env


def cmd_start(cfg, args):
    pid = running_pid(cfg)
    if pid:
        print(t("already_running", pid=pid))
        return 1
    if os.name != "nt":
        print(t("win_only"))
        return 2
    if not cfg["log"]["file"]:
        print(t("bg_needs_log"))
        return 2
    logf = BASE / cfg["log"]["file"]
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(child_cmd(args) + ["run"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True, cwd=str(BASE),
                         env=child_env())
    time.sleep(2.5)
    if p.poll() is not None:
        print(t("bg_failed", code=p.returncode, log=logf))
        return 1
    print(t("bg_started", pid=p.pid, log=logf))
    return 0


def cmd_stop(cfg):
    pid = running_pid(cfg)
    stop_file = BASE / cfg["safety"]["stop_file"]
    if not pid:
        print(t("not_running"))
        pid_path(cfg).unlink(missing_ok=True)
        return 0
    print(t("stopping", pid=pid))
    stop_file.write_text("stop")
    for _ in range(200):  # up to 20 s (a reply delay may be in progress)
        if not pid_alive(pid):
            break
        time.sleep(0.1)
    forced = False
    if pid_alive(pid):
        try:
            subprocess.run([system_taskkill_path(), "/PID", str(pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=10)
            forced = True
        except (OSError, subprocess.TimeoutExpired):
            log.exception("taskkill failed for PID %s", pid)
        for _ in range(30):  # confirm exit after force termination
            if not pid_alive(pid) or not pid_is_ours(pid):
                break
            time.sleep(0.1)
    if pid_alive(pid) and pid_is_ours(pid):
        print(t("stop_failed", pid=pid))
        return 1  # retain STOP/PID files so another stop attempt can finish cleanup
    print(t("killed") if forced else t("stopped_ok"))
    stop_file.unlink(missing_ok=True)
    pid_path(cfg).unlink(missing_ok=True)
    return 0


def stats_path(cfg):
    return BASE / cfg["stats"]["file"]


def cmd_state(cfg):
    pid = running_pid(cfg)
    running = bool(pid)
    print(t("state_running", pid=pid) if running else t("state_stopped"))
    try:
        d = json.loads(stats_path(cfg).read_text(encoding="utf-8"))
        print(t("stats_hdr"))
        print(t("stats_line", started=d.get("started", "?"), st=d.get("statuses", 0),
                rp=d.get("replies", 0), fl=d.get("failures", 0)))
    except Exception:
        pass
    return 0 if running else 1


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = {
    "room": "CamfrogRoomControl",
    "im_reply": "CamfrogIMAutoReply",
    "status": f"CamfrogStatus{os.environ.get('ZCFATO_STATUS_MODE', 'App').title()}",
    "music": "CamfrogMusicDJ",
}.get(APP_PROFILE, "CamfrogAuto")


PROFILE_ASSETS = {
    "room": "room-control.exe",
    "im_reply": "im-autoreply.exe",
    "status": "status-{mode}.exe",
    "music": "music-dj.exe",
    "chat_im_private": "chat-im-private.exe",
    "full": "",
}


def cmd_update(args):
    """Self-update this executable from the GitHub release (no config needed)."""
    asset = getattr(args, "asset", None) or PROFILE_ASSETS.get(APP_PROFILE, "")
    if "{mode}" in asset:
        mode = os.environ.get("ZCFATO_STATUS_MODE", "").lower()
        asset = asset.format(mode=mode if mode in ("random", "marquee") else "random")
    if not asset:
        print("no release asset for this app; pass one: update <asset-name>")
        return 2
    try:
        state, message = self_update(asset)
    except Exception as exc:
        print("update check failed: {0}: {1}".format(type(exc).__name__, exc))
        return 2
    print(message)
    return 0 if state in ("ready", "no-update") else 2


def cmd_autostart(args, enable):
    if os.name != "nt":
        print(t("win_only"))
        return 2
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enable:
            cmd = subprocess.list2cmdline(child_cmd(args) + ["start"])  # quotes paths with spaces
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
            print(t("autostart_on", cmd=cmd))
        else:
            try:
                winreg.DeleteValue(k, RUN_VALUE)
                print(t("autostart_off"))
            except FileNotFoundError:
                print(t("autostart_none"))
    return 0


# ---------- runner ----------
DJ_AUDIO_EXTENSIONS = frozenset({
    ".aac", ".adts", ".aif", ".aifc", ".aiff", ".au", ".flac", ".m4a",
    ".mid", ".midi", ".mp2", ".mp3", ".mpa", ".ogg", ".opus", ".rmi",
    ".snd", ".wav", ".wma",
})
DJ_CONTROL_PROTOCOL = 1
DJ_CONTROL_REQUEST_TTL_SECONDS = 30
DJ_CONTROL_ACTIONS = frozenset({"status", "skip", "clear", "speak"})
DJ_CONTROL_DIRNAME = "dj-control"
_DJ_MCI_PLAY_ALIAS = "camfrog_music_dj"
_DJ_MCI_DURATION_ALIAS = "camfrog_music_duration"


class DJError(ValueError):
    """A refused DJ request with a user-facing reason."""


def format_dj_time(seconds):
    seconds = max(0, int(float(seconds)))
    minutes, seconds = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


def dj_status_summary(current, next_item=None, elapsed=None, duration=None):
    if elapsed is None:
        time_text = bi("unknown|ไม่ทราบ")
    elif duration and duration > 0:
        time_text = "{0}/{1}".format(format_dj_time(elapsed), format_dj_time(duration))
    else:
        time_text = bi("{0} elapsed|เล่นแล้ว {0}").format(format_dj_time(elapsed))
    following = ("{0} ({1})".format(next_item["title"], next_item["user"])
                 if next_item else bi("none|ไม่มี"))
    return bi("[dj] current: {title}; requested by {user}; time {time}; next: {next}|"
              "[dj] กำลังเล่น: {title}; ผู้ขอ: {user}; เวลา: {time}; เพลงถัดไป: {next}").format(
                  title=current["title"], user=current["user"], time=time_text,
                  next=following)


def dj_status_speech(current, next_item=None, elapsed=None, duration=None):
    def clipped(item, limit):
        value = str(item or "").strip()
        return value if len(value) <= limit else value[:limit - 1] + "…"

    if elapsed is None:
        time_text = bi("play time unknown|เวลาเล่นไม่ทราบ")
    elif duration and duration > 0:
        time_text = "{0} of {1}".format(format_dj_time(elapsed), format_dj_time(duration))
    else:
        time_text = bi("played {0}|เล่นแล้ว {0}").format(format_dj_time(elapsed))
    following = (bi("{title}, requested by {user}|เพลง {title} ผู้ขอ {user}").format(
        title=clipped(next_item.get("title"), 50),
        user=clipped(next_item.get("user"), 24))
        if next_item else bi("none|ไม่มี"))
    return bi("Now playing {title}, requested by {user}. {time_text}. Next song: {next}.|"
              "กำลังเล่นเพลง {title} ผู้ขอคือ {user} {time_text} เพลงถัดไป {next}").format(
                  title=clipped(current.get("title"), 65),
                  user=clipped(current.get("user"), 24),
                  time_text=time_text, next=following)


class DJQueue:
    """Persistent song queue; YouTube items also retain a canonical video_id."""

    def __init__(self, path):
        self.path = Path(path)
        self.current, self.queue = None, []

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        cur = raw.get("current")
        if isinstance(cur, dict) and str(cur.get("title", "")).strip():
            self.current = self._normalize_item(cur)
        items = raw.get("queue")
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and str(it.get("title", "")).strip():
                    item = self._normalize_item(it)
                    if item is not None:
                        self.queue.append(item)

    @staticmethod
    def _normalize_item(raw):
        item = {"title": str(raw.get("title", "")).strip(),
                "user": str(raw.get("user", ""))}
        video_id = raw.get("video_id")
        if video_id:
            try:
                item["video_id"] = youtube_video_id(video_id)
            except ValueError:
                return None
        return item

    def save(self):
        try:
            atomic_write(self.path, json.dumps(
                {"current": self.current, "queue": self.queue},
                ensure_ascii=False, indent=2) + "\n")
            return True
        except Exception as e:  # queue must never break the bot
            log.debug("dj save failed: %s", e)
            return False

    def add(self, title, user, max_per_user=3, video_id=None):
        title, user = str(title).strip(), str(user).strip()
        if not title:
            raise DJError("give a song name: !request <song>")
        if video_id:
            try:
                video_id = youtube_video_id(video_id)
            except ValueError:
                raise DJError("use a valid YouTube video ID") from None
        mine = [q for q in self.queue if q["user"].lower() == user.lower()]
        if len(mine) >= max(1, max_per_user):
            raise DJError(f"{user} already has {len(mine)} songs queued")
        def is_duplicate(item):
            if video_id and item.get("video_id"):
                return item["video_id"] == video_id
            return item["title"].lower() == title.lower()

        if any(is_duplicate(item) for item in self.queue):
            raise DJError(f'"{title}" is already queued')
        item = {"title": title, "user": user}
        if video_id:
            item["video_id"] = video_id
        self.queue.append(item)
        return len(self.queue)

    def skip(self, nick, owner):
        """Advance if nick requested the current song or owns the bot. Returns next or None."""
        if self.current is None:
            raise DJError("nothing is playing")
        nick, owner = nick.lower(), (owner or "").lower()
        if nick != self.current["user"].lower() and nick != owner:
            raise DJError("only the requester or the bot owner can skip")
        self.current = self.queue.pop(0) if self.queue else None
        return self.current

    def advance(self):
        self.current = self.queue.pop(0) if self.queue else None
        return self.current


def dj_control_directory(base=None):
    """Return the local, file-backed IPC directory shared with Web Status."""
    return Path(base or BASE) / DJ_CONTROL_DIRNAME


def dj_queue_path_for_config(cfg, base=None):
    """Resolve the configured queue file without allowing data-folder escape."""
    data_dir = Path(base or BASE).resolve()
    dj = cfg.get("dj") or DEFAULTS["dj"]
    filename = str(dj.get("queue_file") or "").strip()
    if not filename:
        raise ValueError("dj.queue_file must be set")
    path = (data_dir / filename).resolve()
    try:
        path.relative_to(data_dir)
    except ValueError:
        raise ValueError("dj.queue_file must stay inside the Music DJ data folder") from None
    return path


def dj_control_request_path(request_id, base=None):
    if not re.fullmatch(r"[0-9a-f]{32}", str(request_id or "")):
        raise ValueError("Invalid DJ control request id.")
    return dj_control_directory(base) / (request_id + ".request.json")


def process_dj_control_request(cfg, now=None, base=None, runner=None):
    """Process local UI commands on the DJ owner loop; callers must be single-threaded."""
    directory = dj_control_directory(base)
    timestamp = time.time() if now is None else float(now)
    try:
        requests = sorted(directory.glob("*.request.json"))[:32]
    except OSError:
        return 0
    for old_response in directory.glob("*.response.json"):
        try:
            if timestamp - old_response.stat().st_mtime > 120:
                old_response.unlink(missing_ok=True)
        except OSError:
            continue
    processed = 0
    now = timestamp
    dj = cfg.get("dj") or DEFAULTS["dj"]
    try:
        queue_path = dj_queue_path_for_config(cfg, base)
    except (OSError, ValueError):
        log.error("DJ control refused an unsafe queue path")
        return 0

    for request_path in requests:
        request_id = request_path.name.removesuffix(".request.json")
        response_path = directory / (request_id + ".response.json")
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            request_path.unlink(missing_ok=True)
            continue
        if response_path.exists():
            request_path.unlink(missing_ok=True)
            continue
        try:
            raw = request_path.read_text(encoding="utf-8")
            request_path.unlink(missing_ok=True)
            if len(raw) > 4096:
                raise ValueError("Request is too large.")
            request = json.loads(raw)
            if (not isinstance(request, dict) or
                    request.get("protocol") != DJ_CONTROL_PROTOCOL or
                    request.get("request_id") != request_id or
                    request.get("action") not in DJ_CONTROL_ACTIONS):
                raise ValueError("Invalid DJ control request.")
            created = float(request.get("created_at", 0))
            if not now - DJ_CONTROL_REQUEST_TTL_SECONDS <= created <= now + 5:
                raise ValueError("DJ control request expired.")

            queue = DJQueue(queue_path)
            queue.load()
            action = request["action"]
            if action == "skip":
                if not dj.get("enabled"):
                    raise DJError("Music DJ is disabled in config.json.")
                owner = str(cfg["autoreply"].get("own_nickname", "")).strip()
                queue.skip(owner, owner)
                started = (runner.dj_start_playback(queue, dj) if runner is not None
                           else process_dj_control_start_next(queue, cfg, base))
                if not started:
                    raise RuntimeError("The next DJ track could not be started.")
                if not queue.save():
                    raise OSError("Could not persist the DJ queue.")
            elif action == "clear":
                if runner is not None:
                    runner.dj_stop_playback()
                else:
                    dj_stop()
                queue.current, queue.queue = None, []
                if not queue.save():
                    raise OSError("Could not persist the DJ queue.")
            elif action == "speak":
                if runner is None:
                    raise RuntimeError("The Music DJ worker cannot start voice output.")
                speech_text = validate_room_speech_text(request.get("text", ""))
                start_room_speech(speech_text)
            payload = {
                "protocol": DJ_CONTROL_PROTOCOL,
                "request_id": request_id,
                "ok": True,
                "action": action,
                "current": queue.current,
                "queue": queue.queue[:50],
                "updated_at": now,
                "playback": {
                    "elapsed_seconds": (max(0.0, time.monotonic() - runner.dj_started)
                                        if runner and getattr(runner, "dj_started", 0) else None),
                    "duration_seconds": (float(getattr(runner, "dj_length", 0))
                                         if runner and getattr(runner, "dj_length", 0) > 0 else None),
                },
            }
        except Exception as exc:
            payload = {
                "protocol": DJ_CONTROL_PROTOCOL,
                "request_id": request_id,
                "ok": False,
                "error": str(exc)[:240],
                "updated_at": now,
            }
        try:
            atomic_write(response_path, json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            log.exception("Could not write DJ control response")
        processed += 1
    return processed


def process_dj_control_start_next(queue, cfg, base=None):
    """Restart local MCI playback after a manager-initiated skip."""
    if queue.current is None:
        dj_stop()
        return True
    if cfg.get("dry_run"):
        dj_stop()
        return True
    dj = cfg.get("dj") or DEFAULTS["dj"]
    if dj.get("audio_backend", "chat") == "youtube":
        return False  # YouTube requires the live Runner and its visible browser host.
    if dj.get("audio_backend", "chat") != "local":
        return True
    dj_stop()
    song = dj_find_song(Path(base or BASE) / dj.get("music_dir", "music"),
                        queue.current["title"])
    return bool(song and dj_play(song))


def publish_dj_control_state(cfg, now=None, base=None, runner=None):
    """Publish a bounded queue snapshot for the local Bot Manager UI."""
    now = time.time() if now is None else float(now)
    dj = cfg.get("dj") or DEFAULTS["dj"]
    try:
        queue_path = dj_queue_path_for_config(cfg, base)
    except (OSError, ValueError):
        log.error("DJ control refused an unsafe queue path")
        return None
    queue = DJQueue(queue_path)
    queue.load()
    payload = {
        "protocol": DJ_CONTROL_PROTOCOL,
        "running": True,
        "enabled": bool(dj.get("enabled")),
        "current": queue.current,
        "queue": queue.queue[:50],
        "updated_at": now,
        "playback": {
            "elapsed_seconds": (max(0.0, time.monotonic() - runner.dj_started)
                                if runner and getattr(runner, "dj_started", 0) else None),
            "duration_seconds": (float(getattr(runner, "dj_length", 0))
                                 if runner and getattr(runner, "dj_length", 0) > 0 else None),
        },
    }
    try:
        atomic_write(dj_control_directory(base) / "state.json",
                     json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        log.exception("Could not publish DJ control state")
    return payload


def dj_playback_times(base=None, now=None, max_age=10.0):
    """Read fresh elapsed/duration data from the local Music DJ state snapshot."""
    try:
        state = json.loads((dj_control_directory(base) / "state.json").read_text(
            encoding="utf-8"))
        if not isinstance(state, dict):
            return None, None
        updated_at = float(state.get("updated_at"))
        age = (time.time() if now is None else float(now)) - updated_at
        playback = state.get("playback")
        if age < 0 or age > max_age or not isinstance(playback, dict):
            return None, None
        elapsed = playback.get("elapsed_seconds")
        duration = playback.get("duration_seconds")
        elapsed = float(elapsed) + age if elapsed is not None else None
        duration = float(duration) if duration is not None else None
        if elapsed is not None and (elapsed < 0 or elapsed > 24 * 60 * 60):
            elapsed = None
        if duration is not None and (duration <= 0 or duration > 24 * 60 * 60):
            duration = None
        return elapsed, duration
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return None, None


def youtube_search_url(query):
    """Build a fixed-origin, escaped YouTube search URL from operator text."""
    query = str(query or "").strip()
    if not query:
        raise ValueError("Enter a YouTube search phrase.")
    if len(query) > 200 or CTRL_CHARS.search(query):
        raise ValueError("The YouTube search phrase must be 1 to 200 printable characters.")
    return "https://www.youtube.com/results?" + urllib.parse.urlencode(
        {"search_query": query})


YOUTUBE_RANDOM_MUSIC_QUERIES = (
    "Thai pop official music video",
    "Thai luk thung music",
    "lofi chill instrumental music",
    "jazz piano instrumental",
    "acoustic pop songs",
    "classic rock songs",
    "city pop music",
    "EDM dance music",
    "movie soundtrack instrumental",
    "indie pop music",
)


def random_youtube_music_url():
    """Return a safe YouTube search link for a randomly selected music style."""
    return youtube_search_url(random.choice(YOUTUBE_RANDOM_MUSIC_QUERIES))


def random_youtube_music_link():
    """Return both the selected search phrase and its fixed-origin YouTube URL."""
    query = random.choice(YOUTUBE_RANDOM_MUSIC_QUERIES)
    return query, youtube_search_url(query)


YOUTUBE_RANDOM_MUSIC_FILE = "youtube_random_music.json"
YOUTUBE_RANDOM_MUSIC_HISTORY_LIMIT = 200


def save_random_youtube_music_link(query, url, base=None, now=None):
    """Append a generated YouTube music search to bounded local JSON history."""
    if query not in YOUTUBE_RANDOM_MUSIC_QUERIES or url != youtube_search_url(query):
        raise ValueError("Only generated YouTube music searches may be saved.")
    data_dir = Path(base or BASE).resolve()
    path = data_dir / YOUTUBE_RANDOM_MUSIC_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {"version": 1, "links": []}
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("The saved YouTube music link history cannot be read.") from None
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("links"), list)
            or any(not isinstance(item, dict)
                   or not all(isinstance(item.get(key), str)
                              for key in ("query", "url", "created_at"))
                   for item in data["links"])):
        raise ValueError("The saved YouTube music link history has an invalid format.")
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    links = data["links"][-(YOUTUBE_RANDOM_MUSIC_HISTORY_LIMIT - 1):]
    links.append({"query": query, "url": url, "created_at": timestamp})
    payload = {"version": 1, "links": links}
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


ROOM_SPEECH_MAX_CHARS = 240


def validate_room_speech_text(value):
    """Validate short text entered by the local operator for offline TTS."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Enter the text to speak.")
    if len(text) > ROOM_SPEECH_MAX_CHARS or CTRL_CHARS.search(text):
        raise ValueError("Speech text must be at most 240 printable characters.")
    return text


def windows_speech_powershell_command(value):
    """Return an encoded, fixed PowerShell script; speech text is carried as Base64."""
    text = validate_room_speech_text(value)
    speech_data = base64.b64encode(text.encode("utf-8")).decode("ascii")
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "  Add-Type -AssemblyName System.Speech\n"
        "  $payload = [Convert]::FromBase64String('" + speech_data + "')\n"
        "  $utterance = [Text.Encoding]::UTF8.GetString($payload)\n"
        "  $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "  try { $speaker.Speak($utterance) } finally { $speaker.Dispose() }\n"
        "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }\n"
    )
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def speak_room_text(value):
    """Speak operator-provided text through Windows' default playback device."""
    command = windows_speech_powershell_command(value)
    if os.name != "nt":
        raise OSError("Room voice output requires Windows PowerShell and System.Speech.")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise OSError("Windows PowerShell was not found.")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-EncodedCommand", command],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=45, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError("Windows speech synthesis failed; check the installed speech voice.")


_ROOM_SPEECH_LOCK = threading.Lock()


def start_room_speech(value, on_complete=None):
    """Start one bounded speech job without blocking the bot owner loop or GUI."""
    text = validate_room_speech_text(value)
    if not _ROOM_SPEECH_LOCK.acquire(blocking=False):
        raise RuntimeError("A voice announcement is already playing.")

    def worker():
        error = None
        try:
            speak_room_text(text)
        except Exception as exc:
            log.exception("Room voice output failed")
            error = str(exc) or "Windows voice output failed."
        finally:
            _ROOM_SPEECH_LOCK.release()
        if on_complete is not None:
            try:
                on_complete(error)
            except Exception:
                log.debug("Room voice completion callback failed", exc_info=True)

    thread = threading.Thread(target=worker, name="room-voice-output", daemon=True)
    try:
        thread.start()
    except Exception:
        _ROOM_SPEECH_LOCK.release()
        raise
    return thread


def youtube_video_id(value):
    """Accept an 11-character ID or supported HTTPS YouTube video URL only."""
    value = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        raise ValueError("Use a YouTube video URL or its 11-character video ID.") from None
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise ValueError("Use a YouTube video URL or its 11-character video ID.")
    host = (parsed.hostname or "").casefold()
    video_id = ""
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            values = urllib.parse.parse_qs(parsed.query).get("v", [])
            video_id = values[0] if len(values) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:shorts|embed)/([A-Za-z0-9_-]{11})/?", parsed.path)
            video_id = match.group(1) if match else ""
    elif host == "youtu.be":
        match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", parsed.path)
        video_id = match.group(1) if match else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Use a YouTube video URL or its 11-character video ID.")
    return video_id


def dj_youtube_video_id(item):
    """Get the canonical playback ID from a queued item with old-format support."""
    return youtube_video_id(item.get("video_id") or item.get("title", ""))


def youtube_display_title(value, fallback=""):
    """Bound player metadata before persisting or sending it into chat."""
    title = " ".join(
        "".join(char if char.isprintable() else " " for char in str(value or "")).split()
    ).strip()
    return title[:160] or str(fallback or "YouTube video")[:160]


def find_chromium_browser():
    """Find an installed Edge or Chrome executable without invoking a shell."""
    candidates = [shutil.which("msedge"), shutil.which("msedge.exe"),
                  shutil.which("chrome"), shutil.which("chrome.exe")]
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = [Path(os.environ.get(name, "")) for name in
                     ("ProgramFiles", "ProgramFiles(x86)")]
    candidates.extend((
        local / "Microsoft/Edge/Application/msedge.exe",
        local / "Google/Chrome/Application/chrome.exe",
    ))
    for root in program_files:
        if str(root) != ".":
            candidates.extend((root / "Microsoft/Edge/Application/msedge.exe",
                               root / "Google/Chrome/Application/chrome.exe"))
    return next((Path(path) for path in candidates
                 if path and Path(path).is_file()), None)


def open_youtube_search(query):
    """Open a visible YouTube search in a dedicated browser profile."""
    if os.name != "nt":
        raise OSError("The owned browser window is supported in the Windows app only.")
    browser = find_chromium_browser()
    if browser is None:
        raise FileNotFoundError("Install Microsoft Edge or Google Chrome first.")
    url = youtube_search_url(query)
    profile = youtube_profile_path()
    profile.mkdir(parents=True, exist_ok=True)
    flags = 0x08000000  # CREATE_NO_WINDOW; the browser remains a visible GUI app.
    return subprocess.Popen(
        [str(browser), "--user-data-dir=" + str(profile), "--new-window", url],
        cwd=str(browser.parent), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, creationflags=flags)


def youtube_profile_path():
    local_app_data = Path(os.environ.get(
        "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return (local_app_data / "Camfrog" / "MusicDJ" / "YouTubeProfile").resolve()


YOUTUBE_PLAYER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Music DJ YouTube Player</title>
<style>
html,body{margin:0;background:#101820;color:#edf3f7;font:15px "Segoe UI",sans-serif}
main{max-width:960px;margin:0 auto;padding:12px;box-sizing:border-box}
h1{font-size:18px;margin:0 0 5px}.hint{margin:0 0 10px;color:#c2d0da}
#state{min-height:20px;margin:0 0 6px;color:#9de5d0}
#player{width:100%;max-width:800px;aspect-ratio:16/9;background:#000}
@media(max-width:520px){main{padding:8px}h1{font-size:16px}}
</style>
</head>
<body>
<main>
<h1>Music DJ YouTube Player</h1>
<p class="hint">Keep this player visible. Use the standard YouTube controls. Press Play if autoplay is blocked.</p>
<p id="state" aria-live="polite">Connecting to YouTube…</p>
<div id="player"></div>
</main>
<script>
const params=new URLSearchParams(location.search);
const token=params.get('token')||'';
let lastRevision=Number(params.get('revision')||0);
let activeVideoId=params.get('video')||'';
let player=null;
let pollBusy=false;
let lastState='ready';
const stateLabel=document.getElementById('state');
function say(text){stateLabel.textContent=text;}
async function postEvent(kind,errorCode){
  let videoTitle='';
  try{videoTitle=player?player.getVideoData().title||'':'';}catch(_e){}
  try{await fetch('/event?token='+encodeURIComponent(token),{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({kind:kind,error_code:errorCode||0,
      video_id:activeVideoId,revision:lastRevision,video_title:videoTitle})});}catch(_e){}
}
async function pollCommand(){
  if(pollBusy||!player)return;
  pollBusy=true;
  try{
    const response=await fetch('/state?token='+encodeURIComponent(token),{cache:'no-store'});
    const state=await response.json();
    if(state.closing){player.pauseVideo();say('Music DJ stopped. You can close this window.');postEvent('stopped');return;}
    if(state.revision>lastRevision){
      lastRevision=state.revision;
      activeVideoId=state.video_id||'';
      if(state.video_id){say('Loading YouTube video…');player.loadVideoById(state.video_id);}
      else{player.stopVideo();say('Playback stopped.');}
    }
  }catch(_e){}
  finally{pollBusy=false;}
}
window.onYouTubeIframeAPIReady=function(){
  const video=activeVideoId;
  player=new YT.Player('player',{
    width:'800',height:'450',videoId:video,
    playerVars:{autoplay:1,controls:1,enablejsapi:1,playsinline:1,origin:location.origin},
    events:{
      onReady:function(){say('Player ready.');postEvent('ready');pollCommand();},
      onStateChange:function(event){
        const names={'-1':'ready',0:'ended',1:'playing',2:'paused',3:'buffering',5:'cued'};
        lastState=names[event.data]||'ready';
        say(lastState==='playing'?'Playing':lastState==='ended'?'Track ended':'Player '+lastState);
        postEvent(lastState);
      },
      onError:function(event){say('YouTube player error '+event.data+'. Skip or choose another video.');postEvent('error',event.data);},
      onAutoplayBlocked:function(){say('Autoplay was blocked. Press Play in the YouTube player.');postEvent('blocked');}
    }
  });
  setInterval(function(){postEvent('heartbeat');pollCommand();},1000);
};
document.addEventListener('visibilitychange',function(){
  if(document.hidden){if(player)player.pauseVideo();say('Player hidden; playback paused.');postEvent('hidden');}
  else{say('Player visible. Press Play to resume if needed.');postEvent('visible');}
});
const script=document.createElement('script');
script.src='https://www.youtube.com/iframe_api';
document.head.appendChild(script);
</script>
</body>
</html>"""


class YouTubePlayerHost:
    """Visible standard YouTube iframe player hosted by a loopback-only local page."""

    ALLOWED_EVENTS = frozenset({
        "ready", "playing", "paused", "buffering", "ended", "blocked",
        "error", "hidden", "visible", "heartbeat", "cued", "stopped",
    })

    def __init__(self):
        self._lock = threading.Lock()
        self._events = []
        self._server = None
        self._server_thread = None
        self._browser_process = None
        self._browser_launched = False
        self._closing = False
        self._closed_ack = threading.Event()
        self._status = "stopped"
        self._last_heartbeat = 0.0
        self._launched_at = 0.0
        self._visible = False
        self._video_id = ""
        self._revision = 0
        self._token = secrets.token_urlsafe(32)

    @property
    def origin(self):
        if self._server is None:
            return ""
        return "http://127.0.0.1:{0}".format(self._server.server_address[1])

    @property
    def status(self):
        with self._lock:
            if self._closing:
                return "stopped"
            now = time.monotonic()
            heartbeat_lost = (self._last_heartbeat and
                              now - self._last_heartbeat > 8.0)
            startup_lost = (not self._last_heartbeat and self._launched_at and
                            now - self._launched_at > 20.0)
            if self._browser_launched and (heartbeat_lost or startup_lost):
                self._status = "disconnected"
                self._browser_launched = False
            return self._status

    @property
    def revision(self):
        with self._lock:
            return self._revision

    @property
    def video_id(self):
        with self._lock:
            return self._video_id

    @property
    def visible(self):
        with self._lock:
            return self._visible

    def start_server(self):
        if self._server is not None:
            return self.origin
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _authorized(self, parsed, require_origin=False):
                query = urllib.parse.parse_qs(parsed.query)
                supplied = query.get("token", [""])[0]
                expected_host = "127.0.0.1:{0}".format(owner._server.server_address[1])
                if (self.client_address[0] != "127.0.0.1" or
                        self.headers.get("Host", "").casefold() != expected_host.casefold() or
                        not hmac.compare_digest(str(supplied), owner._token)):
                    return False
                if require_origin and self.headers.get("Origin") != owner.origin:
                    return False
                return True

            def _respond(self, code, content=b"", content_type="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                if content:
                    try:
                        self.wfile.write(content)
                    except OSError:
                        pass

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                if not self._authorized(parsed):
                    self._respond(403)
                    return
                if parsed.path == "/":
                    query = urllib.parse.parse_qs(parsed.query)
                    try:
                        video_id = youtube_video_id(query.get("video", [""])[0])
                        revision = int(query.get("revision", ["0"])[0])
                    except (ValueError, TypeError):
                        self._respond(400)
                        return
                    with owner._lock:
                        if (owner._closing or video_id != owner._video_id or
                                revision != owner._revision):
                            self._respond(409)
                            return
                    page = YOUTUBE_PLAYER_HTML.replace(
                        "const token=params.get('token')||'';",
                        "const token=params.get('token')||'';")
                    page = page.replace(
                        "let lastRevision=Number(params.get('revision')||0);",
                        "let lastRevision={0};".format(max(0, revision)))
                    page = page.replace(
                        "const video=params.get('video')||'';",
                        "const video={0};".format(json.dumps(video_id)))
                    self._respond(200, page.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if parsed.path == "/state":
                    with owner._lock:
                        state = {"revision": owner._revision,
                                 "video_id": owner._video_id,
                                 "closing": owner._closing}
                    self._respond(200, json.dumps(state).encode("utf-8"))
                    return
                self._respond(404)

            def do_POST(self):
                parsed = urllib.parse.urlsplit(self.path)
                if parsed.path != "/event" or not self._authorized(parsed, True):
                    self._respond(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError
                    kind = payload.get("kind")
                    if not isinstance(kind, str) or kind not in owner.ALLOWED_EVENTS:
                        raise ValueError
                    error_code = payload.get("error_code", 0)
                    if isinstance(error_code, bool) or not isinstance(error_code, int):
                        raise ValueError
                    video_id = payload.get("video_id")
                    revision = payload.get("revision")
                    video_title = payload.get("video_title", "")
                    if (not isinstance(video_id, str) or
                            isinstance(revision, bool) or not isinstance(revision, int) or
                            not isinstance(video_title, str) or len(video_title) > 512):
                        raise ValueError
                    if not owner.record_event(
                            kind, error_code, video_id, revision, video_title):
                        self._respond(409)
                        return
                except (UnicodeError, ValueError, json.JSONDecodeError):
                    self._respond(400)
                    return
                self._respond(204)

        class LocalHTTPServer(http.server.ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        try:
            self._server = LocalHTTPServer(("127.0.0.1", 0), Handler)
            self._server_thread = threading.Thread(
                target=self._server.serve_forever,
                name="music-dj-youtube-http", daemon=True)
            self._server_thread.start()
        except OSError:
            self._server = None
            self._server_thread = None
            raise OSError("Could not start the local YouTube player bridge.") from None
        return self.origin

    def record_event(self, kind, error_code=0, video_id=None, revision=None,
                     video_title=""):
        now = time.monotonic()
        with self._lock:
            if video_id != self._video_id or revision != self._revision:
                return False
            self._last_heartbeat = now
            if kind == "heartbeat":
                self._browser_launched = True
                self._launched_at = now
                if self._status == "disconnected":
                    self._status = "ready"
                return True
            self._status = kind
            self._visible = kind not in ("hidden", "disconnected", "stopped")
            if kind == "stopped":
                self._closed_ack.set()
            event = {"kind": kind, "video_id": video_id, "revision": revision,
                     "video_title": youtube_display_title(video_title, video_id)}
            if kind == "error":
                event["error_code"] = max(0, min(int(error_code), 999))
            self._events.append(event)
            del self._events[:-32]
            return True

    def consume_events(self):
        with self._lock:
            events, self._events = self._events, []
            return [event for event in events
                    if event.get("video_id") == self._video_id and
                    event.get("revision") == self._revision]

    def _open_player_window(self, video_id):
        if os.name != "nt":
            raise OSError("The YouTube player window is supported in the Windows app only.")
        browser = find_chromium_browser()
        if browser is None:
            raise FileNotFoundError("Install Microsoft Edge or Google Chrome first.")
        profile = youtube_profile_path()
        profile.mkdir(parents=True, exist_ok=True)
        with self._lock:
            revision = self._revision
        url = self.origin + "/?" + urllib.parse.urlencode({
            "token": self._token, "video": video_id, "revision": revision})
        try:
            self._browser_process = subprocess.Popen(
                [str(browser), "--user-data-dir=" + str(profile), "--new-window", url],
                cwd=str(browser.parent), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=0x08000000)
        except OSError:
            raise OSError("Could not open the YouTube player browser window.") from None
        with self._lock:
            self._browser_launched = True
            self._launched_at = time.monotonic()
            self._status = "loading"

    def load_video(self, value):
        video_id = youtube_video_id(value)
        self.start_server()
        with self._lock:
            self._video_id = video_id
            self._revision += 1
            self._status = "loading"
            launched = self._browser_launched
        if not launched:
            try:
                self._open_player_window(video_id)
            except Exception:
                self.close()
                raise
        return True

    def stop_video(self):
        with self._lock:
            self._video_id = ""
            self._revision += 1
            self._status = "stopped"
            self._visible = False

    def close(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
        server = self._server
        thread = self._server_thread
        if server is not None:
            # The visible page polls once per second and acknowledges after pausing.
            self._closed_ack.wait(1.5)
            try:
                server.shutdown()
            except (OSError, RuntimeError):
                pass
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._server = None
        self._server_thread = None


def dj_find_song(music_dir, title):
    """Match a request against supported audio files in music_dir."""
    want = str(title).strip().lower()
    if not want:
        return None
    try:
        songs = sorted(path for path in Path(music_dir).iterdir()
                       if path.is_file() and path.suffix.lower() in DJ_AUDIO_EXTENSIONS)
    except OSError:
        return None
    for song in songs:
        if want in song.stem.lower():
            return song
    return None


def _dj_windows_audio():
    return os.name == "nt"


def _dj_mci_command(command):
    """Send one Unicode MCI command; raise OSError when Windows rejects it."""
    if not _dj_windows_audio():
        raise OSError("Windows audio playback is unavailable")
    winmm = ctypes.WinDLL("winmm", use_last_error=True)
    send = winmm.mciSendStringW
    send.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar),
                     ctypes.c_uint, ctypes.c_void_p)
    send.restype = ctypes.c_uint
    result = ctypes.create_unicode_buffer(1024)
    code = send(command, result, len(result), None)
    if code:
        raise OSError(f"MCI command failed ({code})")
    return result.value


def _dj_mci_close(alias):
    for action in ("stop", "close"):
        try:
            _dj_mci_command(f"{action} {alias}")
        except Exception:
            pass


def dj_audio_duration(path):
    """Seconds of an MCI-readable audio file, or None when unavailable."""
    if not _dj_windows_audio():
        return None
    alias = _DJ_MCI_DURATION_ALIAS
    opened = False
    try:
        # Let MCI select the device from the extension registry. This supports
        # the formats installed on the user's Windows system without guessing
        # that every file is a WAV or MPEG device.
        _dj_mci_command(f'open "{Path(path).resolve()}" alias {alias}')
        opened = True
        _dj_mci_command(f"set {alias} time format milliseconds")
        raw = _dj_mci_command(f"status {alias} length").strip()
        milliseconds = int(raw)
        return milliseconds / 1000 if milliseconds > 0 else None
    except Exception as exc:
        log.debug("dj audio duration unavailable: %s", exc)
        return None
    finally:
        if opened:
            try:
                _dj_mci_command(f"close {alias}")
            except Exception:
                pass


def dj_audio_mode():
    """Return the current MCI playback mode, or None when it cannot be read."""
    if not _dj_windows_audio():
        return None
    try:
        return _dj_mci_command(f"status {_DJ_MCI_PLAY_ALIAS} mode").strip().lower()
    except Exception:
        return None


def dj_play(path):
    """Start an MCI-readable audio file asynchronously on Windows."""
    if not _dj_windows_audio():
        return False
    try:
        _dj_mci_close(_DJ_MCI_PLAY_ALIAS)
        _dj_mci_command(f'open "{Path(path).resolve()}" alias {_DJ_MCI_PLAY_ALIAS}')
        _dj_mci_command(f"play {_DJ_MCI_PLAY_ALIAS}")
        return True
    except Exception as exc:
        _dj_mci_close(_DJ_MCI_PLAY_ALIAS)
        log.debug("dj play failed: %s", exc)
        return False


def dj_stop():
    if _dj_windows_audio():
        _dj_mci_close(_DJ_MCI_PLAY_ALIAS)


class Runner:
    def __init__(self, cfg, cfg_path=None):
        self.cfg_path = Path(cfg_path) if cfg_path else None
        self._mtime = self.cfg_path.stat().st_mtime if self.cfg_path and self.cfg_path.exists() else 0
        self._next_reload = 0.0
        self._next_resolve_retry = 0.0
        self._next_dj_control_state = 0.0
        self.marq, self.lang_i, self.msg_i, self.current_text = None, 0, {}, None
        self.next_attach = 0.0
        self.im_wins, self.im_next_attach, self.im_hint_at = {}, 0.0, 0.0
        # Resolve targets: stay None until _do_resolve() succeeds, so the
        # retry loop degrades to warnings instead of AttributeError.
        self.win = self.status_edit = self.apply_btn = None
        self.history = self.input = self.chat_win = None
        self.youtube_player = None
        self._dj_youtube_requested = None
        self._dj_youtube_announced = None
        self._dj_youtube_blocked = None
        self._dj_youtube_error = None
        self._dj_youtube_retries = 0
        self._dj_youtube_disconnected_notice = None
        self.apply_cfg(cfg)
        self.sender_last, self.sent = {}, []
        self.im_sender_last, self.im_day, self.im_sent, self.im_next_ok = {}, {}, [], 0.0
        self.next_reply_ok = 0.0
        self.status_i = 0
        self.dj_prev = []
        self.dj_started = 0.0
        self.dj_length = 0.0
        self.last_status = -1e9 if cfg["status"]["set_on_start"] else time.monotonic()
        self.prev = []
        self.stats = {"started": dt.datetime.now().isoformat(timespec="seconds"),
                      "statuses": 0, "marquee_frames": 0, "replies": 0, "im_replies": 0, "failures": 0,
                      "by_rule": {}}
        self._next_stats = 0.0

    def apply_cfg(self, cfg):
        cfg = apply_app_profile(cfg)
        previous = getattr(self, "cfg", None)
        if previous is not None:
            old_dj, new_dj = previous.get("dj", {}), cfg.get("dj", {})
            if (old_dj.get("audio_backend", "chat") != new_dj.get("audio_backend", "chat") or
                    old_dj.get("enabled", False) != new_dj.get("enabled", False)):
                self.dj_stop_playback()
                if self.youtube_player is not None:
                    self.youtube_player.close()
                    self.youtube_player = None
        self.cfg = cfg
        self.dry = cfg["dry_run"]
        self.stop_file = BASE / cfg["safety"]["stop_file"]
        ar = cfg["autoreply"]
        old = {(r["idx"], r["rx"].pattern): r["last"] for r in getattr(self, "rules", [])}
        self.rules = compile_rules(ar)
        for r in self.rules:  # a hot reload must not reset rule cooldowns
            r["last"] = old.get((r["idx"], r["rx"].pattern), r["last"])
        self.skips = compile_skips(ar)
        self.own = ar["own_nickname"].strip().lower()
        self.ignore = {n.lower() for n in ar["ignore_nicknames"]}
        self.only = {n.lower() for n in ar["only_nicknames"]}
        im = _im(cfg)
        self.im_dry = self.dry or im["dry_run"]  # the global dry_run always wins
        view = im_view(cfg)["autoreply"]
        old_im = {(r["idx"], r["rx"].pattern): r["last"] for r in getattr(self, "im_rules", [])}
        self.im_rules = compile_rules(view)
        for r in self.im_rules:
            r["last"] = old_im.get((r["idx"], r["rx"].pattern), r["last"])
        self.im_skips = compile_skips(view)
        h = cfg["status"]["history"]
        self.hist = StatusHistory(BASE / h["file"], h["max_items"])
        if h["enabled"]:
            self.hist.load()
            if h["seed_from_messages"]:
                changed = False
                for it in cfg["status"]["messages"]:
                    for txt in ([it] if isinstance(it, str) else [it.get("th", ""), it.get("en", "")]):
                        changed |= self.hist.add(txt)
                if changed:
                    self.hist.save()

    def maybe_reload(self, now):
        """Hot-reload config.json when it changes (invalid edits are rejected)."""
        if not (self.cfg["reload_config"] and self.cfg_path) or now < self._next_reload:
            return
        self._next_reload = now + 3
        try:
            mt = self.cfg_path.stat().st_mtime
        except OSError:
            return
        if mt == self._mtime:
            return
        self._mtime = mt
        try:
            new = apply_app_profile(load_cfg(self.cfg_path))
            errs, _ = validate(new)
            if not new["dry_run"] and new["autoreply"]["enabled"] \
                    and not new["autoreply"]["own_nickname"].strip():
                errs.append(t("refuse_live"))
        except Exception as e:
            errs = [str(e)]
        if errs:
            log.warning(t("reload_rejected", m="; ".join(errs)))
            return
        self.apply_cfg(new)
        self.marq = None
        global LOG_TEXT, LANG
        LOG_TEXT = new["log"]["log_message_text"]
        if not LANG_LOCKED:
            LANG = resolve_lang(new["language"])
        self.resolve()
        log.info(t("reloaded"))

    def write_stats(self, now, force=False):
        sc = self.cfg["stats"]
        if not sc["enabled"] or (not force and now < self._next_stats):
            return
        self._next_stats = now + sc["write_seconds"]
        try:
            atomic_write(BASE / sc["file"], json.dumps(
                self.stats, ensure_ascii=False, indent=2))
        except Exception as e:  # stats must never break the bot
            log.debug("stats write failed: %s", e)

    def send(self, ctrl, text, btn=None, win=None):
        s = self.cfg["safety"]
        target = (self.cfg["status"].get("background_enter_target", "edit")
                  if ctrl is getattr(self, "status_edit", None) and win is None else "edit")
        return commit(win or self.win, ctrl, text, self.dry, s["require_foreground"], btn,
                      s["restore_previous_window"], target)

    def resolve(self):
        try:
            self._do_resolve()
        except LookupError as e:
            if not self.cfg_path:
                raise
            log.warning(f"UI element missing ({e}). Auto-detecting and updating config...")
            if cmd_detect(self.cfg, self.cfg_path, apply=True, as_json=False) == 0:
                log.info("Auto-detect successfully updated config. Reloading...")
                try:
                    new_cfg = load_cfg(self.cfg_path)
                    self.cfg = new_cfg
                    self.apply_cfg(new_cfg)
                except Exception as ex:
                    log.error(f"Failed to reload after auto-detect: {ex}")
                    raise
                self._do_resolve()
            else:
                log.error("Auto-detect failed to find missing elements. Retrying on next tick.")
                self.status_edit = None
                self.apply_btn = None
                self.win = None

    def _do_resolve(self):
        cfg = self.cfg
        st, ar = cfg["status"], cfg["autoreply"]
        self.win = get_window(cfg)
        self.status_edit, self.apply_btn = None, None
        self.status_edit = find(self.win, st["edit"]) if st["enabled"] else None
        self.apply_btn = find(self.win, st["apply_button"]) \
            if st["enabled"] and st["apply_button"] else None
        self.history = self.input = self.chat_win = None
        self.prev = []
        self.next_attach = 0.0
        self.im_wins, self.im_next_attach = {}, 0.0
        if ar["enabled"]:
            self.attach_chat(time.monotonic(), force=True)
        if _im(cfg)["enabled"]:
            self.attach_im(time.monotonic(), force=True)

    def attach_chat(self, now, force=False):
        """Find the chat-room window; retried every 5 s so a room opened later is picked up."""
        if not force and now < self.next_attach:
            return
        self.next_attach = now + 5
        ar = self.cfg["autoreply"]
        w = get_chat_window(self.cfg, self.win)
        if w is None:
            if force:
                log.warning(t("no_room"))
            return
        self.history, self.input, self.chat_win = find(w, ar["history"]), find(w, ar["input"]), w
        self.prev = read_chat(self.history, ar["history_tail"]).splitlines()
        log.info(t("room_attached", title=shown(w.window_text())))

    def im_summary(self):
        im = _im(self.cfg)
        if not im["enabled"]:
            log.info(t("im_off"))
            return
        mode = "dry-run" if self.im_dry else "LIVE"
        log.info(t("im_on", mode=mode, n=len([n for n in im["only_nicknames"] if str(n).strip()]),
                   r=len(im["rules"]), k="+".join(im["log_kinds"])))

    def attach_im(self, now, force=False):
        """Track private-chat windows (retried every 5 s). Windows already open when we (re)attach
        are baselined silently. A window that appears later with exactly one line, from a listed
        friend, is that friend's opening message and is answered (answer_first_message)."""
        if not force and now < self.im_next_attach:
            return
        self.im_next_attach = now + 5
        ar, im = self.cfg["autoreply"], _im(self.cfg)
        only = {str(n).strip().lower() for n in im["only_nicknames"]}
        live = {}
        found = get_im_windows(self.cfg, self.win, im["max_windows"])
        if not found and not self.im_wins and now >= self.im_hint_at:
            self.im_hint_at = now + 120
            log.info(t("im_none"))
        for w, hist, inp in found:
            st = self.im_wins.get(w.handle)
            if st is None:
                try:
                    cur = read_chat(hist, ar["history_tail"]).splitlines()
                except Exception:
                    continue  # closed while we looked; retried in 5 s
                prev = cur
                if not force and im["answer_first_message"] and len(cur) == 1:
                    m = LINE_RE.match(cur[0].strip())
                    if m and m["nick"].strip().lower() in only:
                        prev = []
                st = {"win": w, "hist": hist, "inp": inp, "prev": prev}
                log.info(t("im_attached", title=shown(w.window_text())))
            live[w.handle] = st
        self.im_wins = live

    def do_im(self, can_reply):
        im, ar = _im(self.cfg), self.cfg["autoreply"]
        s = self.cfg["safety"]
        for h, st in list(self.im_wins.items()):
            try:
                cur = read_chat(st["hist"], ar["history_tail"]).splitlines()
            except Exception as e:  # window closed: forget it, it is not a bot failure
                log.debug("IM window gone: %s", e)
                self.im_wins.pop(h, None)
                continue
            fresh, st["prev"] = new_lines(st["prev"], cur), cur
            if not can_reply:
                continue  # consume backlog so it isn't answered later
            if len(fresh) > RESET_BURST:
                log.warning(t("burst_skip", n=len(fresh)))
                continue
            for line in fresh:
                m = LINE_RE.match(line.strip())
                if not m:
                    continue
                nick, msg = m["nick"].strip(), m["msg"][: im["max_incoming_length"]]
                lk = nick.lower()
                if not NICK_RE.match(nick) or lk == self.own or lk in self.ignore:
                    continue
                d = evaluate_im(self.cfg, nick, msg, self.im_rules, self.im_skips)
                if d["skip"] == "sender":
                    log.info(t("im_skip_nick", nick=nick))
                    continue
                if d["skip"] or d["rule"] is None or d["reply"] is None:
                    continue
                now = time.monotonic()
                self.im_sent = [x for x in self.im_sent if now - x < 3600]
                day = [x for x in self.im_day.get(lk, []) if now - x < 86400]
                self.im_day[lk] = day
                if (now < self.im_next_ok or len(self.im_sent) >= im["max_per_hour"]
                        or len(day) >= im["max_per_sender_per_day"]
                        or now - self.im_sender_last.get(lk, -1e9) < im["per_sender_cooldown_seconds"]
                        or now - d["rule"]["last"] < d["rule"]["cooldown"]):
                    continue
                time.sleep(random.uniform(*im["delay_range_seconds"]))
                if not commit(st["win"], st["inp"], d["reply"], self.im_dry,
                              s["require_foreground"], None, s["restore_previous_window"]):
                    raise RuntimeError(t("reply_fail"))
                ts = time.monotonic()
                self.im_sender_last[lk] = d["rule"]["last"] = ts
                self.im_sent.append(ts)
                day.append(ts)
                self.im_next_ok = ts + im["global_min_gap_seconds"]
                self.stats["im_replies"] += 1
                key = f"im:{d['rule']['idx']}"
                self.stats["by_rule"][key] = self.stats["by_rule"].get(key, 0) + 1
                log.info(t("im_replied", nick=nick, text=shown(d["reply"])))
        if len(self.im_day) > 200:
            now = time.monotonic()
            self.im_day = {k: v for k, v in self.im_day.items() if any(now - x < 86400 for x in v)}
            # Prune sender last-seen older than the per-sender cooldown (no longer blocks new messages)
            self.im_sender_last = {k: v for k, v in self.im_sender_last.items()
                                   if now - v < im["per_sender_cooldown_seconds"]}

    def status_pool(self):
        st, now = self.cfg["status"], dt.datetime.now().time()
        for sc in st["schedules"]:
            if in_window(sc["start"], sc["end"], now):
                return sc["messages"]
        return st["messages"]

    def pick_from_messages(self, desired):
        st = self.cfg["status"]
        pool = self.status_pool()
        if desired:  # language switch: only items that exist in the wanted language
            cands = []
            for it in pool:
                if isinstance(it, dict):
                    if it.get(desired):
                        cands.append(it[desired])
                elif detect_lang(it) == desired:
                    cands.append(it)
            if cands:
                if st["random"]:
                    return random.choice(cands)
                i = self.msg_i.get(desired, 0)
                self.msg_i[desired] = i + 1
                return cands[i % len(cands)]
        item = random.choice(pool) if st["random"] else pool[self.status_i % len(pool)]
        return pick_status(item, st["language_mode"], self.status_i)

    def choose_status(self):
        """Pick the next status (raw template). Language cycle switches TH/EN automatically."""
        st = self.cfg["status"]
        h, cyc = st["history"], st["language_cycle"]
        desired = cyc[self.lang_i % len(cyc)] if cyc else None
        raw = None
        if h["enabled"] and h["use_as_source"]:
            raw = self.hist.pick(h["mode"], desired, exclude=self.current_text)
        if raw is None:
            raw = self.pick_from_messages(desired)
        if cyc:
            self.lang_i += 1
        return raw

    def build_frames(self, text):
        st, mq = self.cfg["status"], self.cfg["status"]["marquee"]
        limit = st["max_length"]
        final = clip(text, limit)
        if not mq["enabled"] or not mq.get("scroll", False):
            return [final]  # per-line rotation: one whole line per tick
        inf = mq.get("infinite_loop", False)
        frames = [clip(f, limit) for f in marquee_frames(
            text, mq["width"], mq["stride"], mq["separator"], mq["cycles"], mq["max_frames"], inf)]
        if not inf and (not frames or frames[-1] != final):
            frames.append(final)  # settle on full text unless infinite loop
        return frames

    def do_status(self, now):
        st = self.cfg["status"]
        if not self.status_edit:
            if st["enabled"] and now >= getattr(self, "_next_status_warn", 0):
                log.warning(t("no_ctrl", spec={"section": "status.edit"}, n=0))
                self._next_status_warn = now + 60
            return
        if self.marq is None:
            if now - self.last_status < st["interval_seconds"]:
                return
            raw = self.choose_status()
            text = expand(raw, own=self.cfg["autoreply"]["own_nickname"])
            if not text.strip():
                log.warning(t("status_blank"))
                self.last_status = now
                return
            self.marq = {"raw": raw, "frames": self.build_frames(text), "i": 0, "next": now}
        m = self.marq
        if now < m["next"]:
            return
        frame = m["frames"][m["i"]]
        if not self.send(self.status_edit, frame, self.apply_btn):
            self.marq = None
            self.last_status = now - st["interval_seconds"] + st["retry_seconds"]
            raise RuntimeError(t("status_fail"))
        if m["i"] == 0 and st["history"]["enabled"] and st["history"]["record"]:
            self.hist.record(m["raw"])
        if len(m["frames"]) > 1:
            self.stats["marquee_frames"] += 1
        m["i"] += 1
        if m["i"] >= len(m["frames"]):
            self.marq = None
            self.status_i += 1
            self.stats["statuses"] += 1
            self.current_text = m["raw"]
            self.last_status = now
            log.info(t("status_set", text=shown(frame)))
        else:
            # Schedule from completion of the UI send, not the tick start. UIA
            # writes can be slow; using `now` here could make delayed sends run
            # back-to-back and destabilize the ticker.
            m["next"] = time.monotonic() + max(
                0.3, float(st["marquee"]["step_seconds"]))
            log.debug(t("status_set", text=shown(frame)))

    def do_chat(self, can_reply):
        ar = self.cfg["autoreply"]
        dj = self.cfg.get("dj") or DEFAULTS["dj"]
        dj_pre = dj.get("prefix", "!") if dj["enabled"] else ""
        cur = read_chat(self.history, ar["history_tail"]).splitlines()
        fresh, self.prev = new_lines(self.prev, cur), cur
        if not can_reply:
            return  # consume backlog so it isn't answered later
        if len(fresh) > RESET_BURST:  # room switch / reload dumped old lines: never answer them
            log.warning(t("burst_skip", n=len(fresh)))
            return
        if len(self.sender_last) > 500:
            now = time.monotonic()
            self.sender_last = {k: v for k, v in self.sender_last.items()
                                if now - v < ar["per_sender_cooldown_seconds"]}
        for line in fresh:
            m = LINE_RE.match(line.strip())
            if not m:
                continue  # continuation / system line: never act on it
            nick, msg = m["nick"].strip(), m["msg"][: ar["max_incoming_length"]]
            if dj_pre and msg.startswith(dj_pre):
                continue  # DJ commands belong to do_dj, never to room rules
            lk, mlang = nick.lower(), detect_lang(m["msg"])
            if (not NICK_RE.match(nick) or lk == self.own or lk in self.ignore
                    or (self.only and lk not in self.only)):
                continue
            if is_skipped(msg, self.skips, ar["ignore_links"]):
                continue
            now = time.monotonic()
            self.sent = [x for x in self.sent if now - x < 3600]
            if now < self.next_reply_ok or len(self.sent) >= ar["max_per_hour"]:
                continue
            if now - self.sender_last.get(lk, -1e9) < ar["per_sender_cooldown_seconds"]:
                continue
            rule = match_rule(self.rules, msg, mlang, self.own)
            if rule is None or now - rule["last"] < rule["cooldown"]:
                continue
            template = pick_reply(rule["spec"], mlang)
            reply = safe_reply(template, nick, ar["max_reply_length"],
                               ar["own_nickname"]) if template else None
            if reply is None:
                continue
            time.sleep(random.uniform(*ar["delay_range_seconds"]))
            if not self.send(self.input, reply, None, self.chat_win):
                raise RuntimeError(t("reply_fail"))
            ts = time.monotonic()
            self.sender_last[lk] = rule["last"] = ts
            self.sent.append(ts)
            self.next_reply_ok = ts + ar["global_min_gap_seconds"]
            self.stats["replies"] += 1
            key = str(rule["idx"])
            self.stats["by_rule"][key] = self.stats["by_rule"].get(key, 0) + 1
            log.info(t("replied", nick=nick, text=shown(reply)))

    def dj_command(self, queue, dj, cmd, arg, nick, owner):
        """One DJ chat command -> reply text (None = not a DJ command)."""
        if cmd in ("randommusic", "musicrandom", "ytmusic"):
            query, url = random_youtube_music_link()
            try:
                save_random_youtube_music_link(query, url)
            except (OSError, UnicodeError, ValueError):
                log.exception("Could not persist generated YouTube music search")
                raise DJError(bi("could not save random YouTube music link; check the local data folder|"
                                 "บันทึกลิงก์เพลง YouTube ไม่ได้ โปรดตรวจสอบโฟลเดอร์ข้อมูลของแอป")) from None
            return bi("[dj] Random YouTube music search (saved): {url}|"
                      "[dj] สุ่มลิงก์ค้นหาเพลง YouTube (บันทึกแล้ว): {url}").format(url=url)
        if cmd in ("play", "request", "req", "song"):
            title = arg
            video_id = None
            backend = dj.get("audio_backend", "chat")
            if backend == "local":
                song = dj_find_song(BASE / dj.get("music_dir", "music"), arg)
                if song is None:
                    raise DJError(f'no supported audio file found in music/: "{arg}"')
                title = song.stem
            elif backend == "youtube":
                try:
                    video_id = youtube_video_id(arg)
                    title = video_id
                except ValueError:
                    raise DJError("use !request <YouTube video URL or 11-character ID>") from None
            if queue.current is not None:
                current_key = queue.current["title"].lower()
                if backend == "youtube":
                    try:
                        current_key = dj_youtube_video_id(queue.current)
                    except ValueError:
                        pass
                if current_key == (video_id if video_id else title.lower()):
                    raise DJError(f'"{title}" is playing now')
            pos = queue.add(title, nick, dj.get("max_per_user", 3), video_id)
            if queue.current is None:
                queue.current = queue.queue.pop(0)
                if not self.dj_start_playback(queue, dj):
                    queue.current = None
                    if backend == "youtube":
                        raise DJError("could not open the visible YouTube player window")
                    raise DJError(bi("could not start local audio; check the file and Windows audio support|"
                                     "เริ่มเล่นเสียงในเครื่องไม่ได้ โปรดตรวจสอบไฟล์และ codec ของ Windows"))
                if backend == "youtube":
                    return bi("[dj] Loading YouTube video {title}. Press Play in the player if needed.|"
                              "[dj] กำลังโหลดวิดีโอ YouTube {title} หากระบบไม่เล่นอัตโนมัติให้กด Play ในหน้าต่างผู้เล่น").format(
                                  title=title)
                return dj["announce_now"].format(title=queue.current["title"],
                                                user=queue.current["user"])
            return dj["announce_queued"].format(pos=pos, title=title)
        if cmd == "queue":
            if not queue.queue:
                return "[dj] queue is empty"
            lines = [f"{i}. {q['title']} ({q['user']})"
                     for i, q in enumerate(queue.queue[:10], 1)]
            return "[dj] queue: " + " | ".join(lines)
        if cmd in ("current", "np"):
            if queue.current is None:
                return "[dj] nothing is playing"
            elapsed = (max(0.0, time.monotonic() - self.dj_started)
                       if self.dj_started else None)
            return dj_status_summary(queue.current, queue.queue[0] if queue.queue else None,
                                     elapsed, self.dj_length)
        if cmd == "skip":
            nxt = queue.skip(nick, owner)
            if not self.dj_start_playback(queue, dj):
                queue.current = None
                if dj.get("audio_backend", "chat") == "youtube":
                    raise DJError("skipped, but could not open the YouTube player window")
                raise DJError(bi("skipped, but could not start local audio|ข้ามเพลงแล้ว แต่เริ่มเล่นเสียงในเครื่องไม่ได้"))
            if nxt is None:
                return "[dj] skipped, queue is empty"
            if dj.get("audio_backend", "chat") == "youtube":
                return bi("[dj] Loading YouTube video {title}. Press Play in the player if needed.|"
                          "[dj] กำลังโหลดวิดีโอ YouTube {title} หากระบบไม่เล่นอัตโนมัติให้กด Play ในหน้าต่างผู้เล่น").format(
                              title=nxt["title"])
            return dj["announce_now"].format(title=nxt["title"], user=nxt["user"])
        if cmd == "help":
            request_help = ("<YouTube video URL or ID>" if
                            dj.get("audio_backend", "chat") == "youtube" else "<song>")
            return "[dj] commands: !request {0} !randommusic !queue !current !skip !help".format(
                request_help)
        return None

    def dj_stop_playback(self):
        """Stop local audio and tell any visible YouTube player to pause."""
        self.dj_started, self.dj_length = 0.0, 0.0
        dj_stop()
        if self.youtube_player is not None:
            self.youtube_player.stop_video()
        self._dj_youtube_requested = None
        self._dj_youtube_announced = None
        self._dj_youtube_blocked = None
        self._dj_youtube_error = None
        self._dj_youtube_disconnected_notice = None

    def dj_start_playback(self, queue, dj):
        """Begin the selected backend for the current song (or stop when empty)."""
        self.dj_started, self.dj_length = 0.0, 0.0
        if queue.current is None:
            self.dj_stop_playback()
            return True
        if self.dry:
            if dj.get("audio_backend", "chat") == "youtube":
                try:
                    self._dj_youtube_requested = (
                        dj_youtube_video_id(queue.current), queue.current["user"])
                except ValueError:
                    return False
            return True
        backend = dj.get("audio_backend", "chat")
        if backend == "youtube":
            try:
                video_id = dj_youtube_video_id(queue.current)
            except ValueError:
                return False
            key = (video_id, queue.current["user"])
            if key != self._dj_youtube_requested:
                self._dj_youtube_announced = None
                self._dj_youtube_blocked = None
                self._dj_youtube_error = None
                self._dj_youtube_retries = 0
                self._dj_youtube_disconnected_notice = None
            player = self.youtube_player
            try:
                if player is None:
                    player = YouTubePlayerHost()
                    self.youtube_player = player
                player.load_video(video_id)
            except (OSError, ValueError) as exc:
                log.warning("Could not start YouTube player: %s", exc)
                if player is not None:
                    # A failed startup must not leave a dead bridge bound to a port.
                    try:
                        player.close()
                    except Exception:
                        log.debug("Failed to close YouTube player bridge", exc_info=True)
                    if self.youtube_player is player:
                        self.youtube_player = None
                self._dj_youtube_requested = None
                return False
            self._dj_youtube_requested = key
            return True
        if self.youtube_player is not None:
            self.youtube_player.close()
            self.youtube_player = None
        dj_stop()
        if backend != "local":
            return True
        song = dj_find_song(BASE / dj.get("music_dir", "music"), queue.current["title"])
        if song is None:
            return False
        length = dj_audio_duration(song)
        if dj_play(song):
            self.dj_started = time.monotonic()
            self.dj_length = length or 0.0
            return True
        return False

    def dj_auto_advance(self, queue, dj):
        """Advance finished tracks and recover queued work when none is current."""
        if dj.get("audio_backend", "chat") == "youtube":
            return self.dj_auto_advance_youtube(queue, dj)
        if queue.current is None:
            if not queue.queue:
                return False
            queue.current = queue.queue.pop(0)
            started = self.dj_start_playback(queue, dj)
            if not started:
                queue.current = None
                self.say_now(bi("could not start local audio; check the file and Windows audio support|"
                                 "เริ่มเล่นเสียงในเครื่องไม่ได้ โปรดตรวจสอบไฟล์และ codec ของ Windows"))
            else:
                self.say_now(dj["announce_now"].format(
                    title=queue.current["title"], user=queue.current["user"]))
            return True
        if not self.dj_started:
            if not self.dry and dj.get("audio_backend", "chat") == "local":
                if time.monotonic() >= getattr(self, "dj_retry_at", 0.0):
                    self.dj_retry_at = time.monotonic() + 5.0
                    if self.dj_start_playback(queue, dj):  # resume after restart
                        self.dj_retry_at = 0.0
                        return True
            return False
        elapsed = time.monotonic() - self.dj_started
        if self.dj_length:
            if elapsed < self.dj_length:
                return False
        elif dj_audio_mode() in (None, "playing"):
            return False
        nxt = queue.advance()
        started = self.dj_start_playback(queue, dj)
        if nxt is None:
            self.say_now("[dj] queue finished")
        elif not started:
            queue.current = None
            self.say_now(bi("could not start local audio; check the file and Windows audio support|"
                             "เริ่มเล่นเสียงในเครื่องไม่ได้ โปรดตรวจสอบไฟล์และ codec ของ Windows"))
        else:
            self.say_now(dj["announce_now"].format(title=nxt["title"], user=nxt["user"]))
        return True

    def dj_auto_advance_youtube(self, queue, dj):
        """Use current player events to announce and advance YouTube requests."""
        dirty = False
        if queue.current is None:
            if not queue.queue:
                if self._dj_youtube_requested is not None:
                    self.dj_stop_playback()
                return False
            queue.current = queue.queue.pop(0)
            dirty = True
        try:
            video_id = dj_youtube_video_id(queue.current)
        except ValueError:
            queue.current = None
            self.say_now("[dj] current queue item is not a valid YouTube video")
            return True
        key = (video_id, queue.current["user"])
        if self._dj_youtube_requested != key:
            if not self.dj_start_playback(queue, dj):
                queue.current = None
                self.say_now("[dj] could not open the visible YouTube player window")
                return True
            if self.dry:
                return dirty
        player = self.youtube_player
        if player is None:
            return dirty
        for event in player.consume_events():
            if (event.get("revision") != player.revision or
                    event.get("video_id") != player.video_id or
                    queue.current is None or event.get("video_id") != video_id):
                continue
            kind = event.get("kind")
            if kind in ("ready", "playing"):
                title = youtube_display_title(event.get("video_title"), video_id)
                if title != queue.current["title"]:
                    queue.current["title"] = title
                    dirty = True
            if kind == "playing":
                if self._dj_youtube_announced != key:
                    self.dj_started = time.monotonic()
                    self.say_now(dj["announce_now"].format(
                        title=queue.current["title"], user=queue.current["user"]))
                    self._dj_youtube_announced = key
                self._dj_youtube_retries = 0
                self._dj_youtube_disconnected_notice = None
            elif kind == "blocked" and self._dj_youtube_blocked != key:
                self._dj_youtube_blocked = key
                self.say_now("[dj] YouTube blocked autoplay; press Play in the visible player window")
            elif kind == "error":
                marker = (key, event.get("error_code", 0))
                if self._dj_youtube_error != marker:
                    self._dj_youtube_error = marker
                    self.say_now("[dj] YouTube player error {0}; use !skip to continue".format(
                        event.get("error_code", 0)))
            elif kind == "ended":
                nxt = queue.advance()
                dirty = True
                self._dj_youtube_requested = None
                if nxt is None:
                    self.say_now("[dj] queue finished")
                    self.dj_stop_playback()
                elif not self.dj_start_playback(queue, dj):
                    queue.current = None
                    self.say_now("[dj] could not load the next YouTube video")
                break

        if (queue.current is not None and player.status == "disconnected"):
            retry_at = getattr(self, "dj_retry_at", 0.0)
            now = time.monotonic()
            if self._dj_youtube_retries < 2 and now >= retry_at:
                self._dj_youtube_retries += 1
                self.dj_retry_at = now + 15.0
                if not self.dj_start_playback(queue, dj):
                    self.say_now("[dj] YouTube player could not reconnect; use !skip to continue")
            elif (self._dj_youtube_retries >= 2 and
                  self._dj_youtube_disconnected_notice != key):
                self._dj_youtube_disconnected_notice = key
                self.say_now("[dj] YouTube player is disconnected; reopen the player or use !skip")
        return dirty

    def say_now(self, text):
        if not self.send(self.input, text, None, self.chat_win):
            raise RuntimeError(t("reply_fail"))
        log.info(t("replied", nick="dj", text=shown(text)))

    def do_dj(self, active):
        dj = self.cfg.get("dj") or DEFAULTS["dj"]
        if not (dj["enabled"] and active and self.history is not None
                and self.input is not None):
            return
        ar = self.cfg["autoreply"]
        cur = read_chat(self.history, ar["history_tail"]).splitlines()
        fresh, self.dj_prev = new_lines(getattr(self, "dj_prev", []), cur), cur
        queue = DJQueue(dj_queue_path_for_config(self.cfg))
        queue.load()
        pre = dj.get("prefix", "!") or "!"
        owner = self.own
        dirty = False
        for line in fresh:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            nick, msg = m["nick"].strip(), m["msg"][:200]
            lk = nick.lower()
            if not NICK_RE.match(nick) or lk == self.own:
                continue
            if not msg.startswith(pre):
                continue
            cmd, _, arg = msg[len(pre):].strip().partition(" ")
            try:
                reply = self.dj_command(queue, dj, cmd.lower(), arg.strip(), nick, owner)
            except DJError as e:
                reply = f"[dj] {e}"
            if reply is None:
                continue
            self.say_now(reply)
            dirty = True
        if self.dj_auto_advance(queue, dj):
            dirty = True
        if dirty:
            queue.save()

    def _watch_dj_control_requests(self, stop_event):
        """Wake the owner loop promptly without processing queue state off-thread."""
        directory = dj_control_directory()
        while not stop_event.wait(0.1):
            try:
                if next(directory.glob("*.request.json"), None) is not None:
                    self._dj_control_wakeup.set()
            except OSError:
                continue

    def run(self):
        cfg = self.cfg
        if not self.dry and cfg["autoreply"]["enabled"] and not self.own:
            log.error(t("refuse_live"))
            return 2
        if _im(cfg)["enabled"] and not self.own:
            log.error(t("refuse_im"))
            return 2
        self.stop_file.unlink(missing_ok=True)  # clear a stale STOP from a previous run
        try:
            self.resolve()
        except Exception as e:
            log.warning(f"Initial resolve failed ({e}); entering retry loop.")
        log.info(t("running", dry=self.dry, stop=self.stop_file.name))
        self.im_summary()
        fails = 0
        self._dj_control_wakeup = threading.Event()
        control_watch_stop = threading.Event()
        control_watch = threading.Thread(
            target=self._watch_dj_control_requests,
            args=(control_watch_stop,), name="music-dj-control-watch", daemon=True)
        control_watch.start()
        try:
            while not self.stop_file.exists():
                try:
                    now = time.monotonic()
                    self.maybe_reload(now)
                    active = in_active_hours(self.cfg)
                    if active and self.cfg["status"]["enabled"]:
                        if self.status_edit is None and now >= self._next_resolve_retry:
                            self._next_resolve_retry = now + 30
                            try:
                                self.resolve()
                            except Exception as e:
                                log.warning(t("re_resolve", e=e))
                        self.do_status(now)
                    if self.cfg["autoreply"]["enabled"] and self.history is None:
                        self.attach_chat(now)
                    if self.history is not None:
                        self.do_chat(active)
                    self.do_dj(active)
                    process_dj_control_request(self.cfg, runner=self)
                    if now >= self._next_dj_control_state:
                        publish_dj_control_state(self.cfg, runner=self)
                        self._next_dj_control_state = now + 1.0
                    if _im(self.cfg)["enabled"]:
                        self.attach_im(now)
                        self.do_im(active)
                    self.write_stats(now)
                    fails = 0
                except KeyboardInterrupt:
                    raise
                except Exception:
                    fails += 1
                    self.stats["failures"] += 1
                    limit = self.cfg["safety"]["max_consecutive_failures"]
                    log.exception(t("tick_failed", n=fails, m=limit))
                    if fails >= limit:
                        log.error(t("too_many"))
                        return 1
                    time.sleep(min(30, 2 ** fails))
                    try:
                        self.resolve()
                    except Exception as e:
                        log.warning(t("re_resolve", e=e))
                wait = self.cfg["poll_seconds"]
                if self.marq is not None:
                    # Wake at the next marquee deadline instead of adding a
                    # full poll interval to every frame. The frame interval
                    # itself remains rate-limited by status.marquee.step_seconds.
                    wait = min(wait, max(0.01, self.marq["next"] - time.monotonic()))
                self._dj_control_wakeup.wait(wait)
                self._dj_control_wakeup.clear()
            log.info(t("stopped"))
            return 0
        finally:
            control_watch_stop.set()
            self._dj_control_wakeup.set()
            control_watch.join(timeout=1.0)
            self.dj_stop_playback()
            if self.youtube_player is not None:
                self.youtube_player.close()
                self.youtube_player = None
            self.write_stats(time.monotonic(), force=True)


def run_guarded(cfg, cfg_path=None):
    """Single-instance guard + PID file around Runner.run()."""
    existing = running_pid(cfg)
    if existing and existing != os.getpid():
        print(t("already_running", pid=existing))
        return 3
    pf = pid_path(cfg)
    pf.write_text(str(os.getpid()))
    try:
        return Runner(cfg, cfg_path).run()
    finally:
        if read_pid(cfg) == os.getpid():
            pf.unlink(missing_ok=True)


# ---------- commands ----------
def cmd_check(cfg):
    errs, warns = validate(cfg)
    for w in warns:
        print(t("warn", m=w))
    for e in errs:
        print(t("err", m=e))
    print(t("cfg_ok") if not errs else t("cfg_errs", n=len(errs)))
    return 1 if errs else 0


def cmd_init(path, force):
    if path.exists() and not force:
        print(t("init_exists", p=path))
        return 1
    sample = deep_merge(DEFAULTS, {
        "status": {"messages": [{"th": "สวัสดีครับ ยินดีต้อนรับ", "en": "Welcome everyone"}]},
        "autoreply": {"rules": [{
            "pattern": "สวัสดี|หวัดดี|\\bhello\\b|\\bhi\\b",
            "reply": {"th": ["สวัสดีครับคุณ {sender}"], "en": ["Hi {sender}, welcome!"]},
            "cooldown_seconds": 60}]},
    })
    atomic_write(path, json.dumps(sample, ensure_ascii=False, indent=2) + "\n")
    print(t("init_done", p=path))
    return 0


def evaluate_message(cfg, nick, msg, rules=None, skips=None):
    """Offline decision for one chat line: same filters and matching as the live loop.
    Returns dict(lang, skip, rule, reply). `skip` is a why_* suffix or None."""
    ar = cfg["autoreply"]
    rules = compile_rules(ar) if rules is None else rules
    skips = compile_skips(ar) if skips is None else skips
    own = ar["own_nickname"].strip().lower()
    ignore = {n.lower() for n in ar["ignore_nicknames"]}
    only = {n.lower() for n in ar["only_nicknames"]}
    lk, mlang = nick.lower(), detect_lang(msg)
    out = {"lang": mlang, "skip": None, "rule": None, "reply": None}
    if not NICK_RE.match(nick) or lk == own or lk in ignore or (only and lk not in only):
        out["skip"] = "sender"
        return out
    msg = msg[: ar["max_incoming_length"]]
    out["skip"] = {"link": "link", "pattern": "pattern"}.get(is_skipped(msg, skips, ar["ignore_links"]))
    if out["skip"]:
        return out
    rule = match_rule(rules, msg, mlang, own)
    out["rule"] = rule
    if rule is not None:
        tpl = pick_reply(rule["spec"], mlang)
        out["reply"] = safe_reply(tpl, nick, ar["max_reply_length"], ar["own_nickname"]) if tpl else None
    return out


def im_view(cfg):
    """cfg whose `autoreply` section carries the IM rules/limits, so the room-tested matching code
    (evaluate_message, compile_rules, ...) is reused unchanged for private chats."""
    ar, im = cfg["autoreply"], _im(cfg)
    return {"autoreply": {**ar, "rules": im["rules"], "skip_patterns": im["skip_patterns"],
                          "ignore_links": im["ignore_links"], "only_nicknames": im["only_nicknames"],
                          "max_reply_length": im["max_reply_length"],
                          "max_incoming_length": im["max_incoming_length"]}}


def im_text(cfg, reply):
    """prefix + reply, clipped to max_reply_length (the prefix is never cut)."""
    im = _im(cfg)
    pre = im["prefix"]
    return pre + clip(reply, max(1, im["max_reply_length"] - len(pre)))


def evaluate_im(cfg, nick, msg, rules=None, skips=None):
    """Offline decision for one private-chat line. Same shape as evaluate_message(), plus:
    an empty allowlist answers nobody, and lines that carry our own prefix are never answered
    (two auto-repliers must not ping-pong)."""
    im = _im(cfg)
    only = {str(n).strip().lower() for n in im["only_nicknames"] if str(n).strip()}
    out = {"lang": detect_lang(msg), "skip": None, "rule": None, "reply": None}
    if nick.strip().lower() not in only:
        out["skip"] = "sender"
        return out
    pre = im["prefix"].strip()
    if pre and msg.strip().startswith(pre):
        out["skip"] = "bot"
        return out
    r = evaluate_message(im_view(cfg), nick, msg, rules, skips)
    if r["reply"]:
        r["reply"] = im_text(cfg, r["reply"])
    return r


def cmd_test_rules(cfg, a):
    """Offline rule tester: same filters and matching as the live loop, no Camfrog needed."""
    use_im = getattr(a, "im", False)
    ar = im_view(cfg)["autoreply"] if use_im else cfg["autoreply"]
    rules, skips = compile_rules(ar), compile_skips(ar)
    items = []
    if a.file:
        for line in Path(a.file).read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            m = LINE_RE.match(line.strip())
            if m:
                items.append((m["nick"].strip(), m["msg"]))
            else:
                print(t("tr_badline", line=line))
    elif a.text:
        items.append((a.sender, a.text))
    for nick, msg in items:
        print(f"> {nick}: {msg}")
        r = (evaluate_im if use_im else evaluate_message)(cfg, nick, msg, rules, skips)
        print("  " + t("tr_lang", l=r["lang"]))
        if r["skip"]:
            print("  " + t("tr_skip", why=t("why_" + r["skip"])))
        elif r["rule"] is None:
            print("  " + t("tr_none"))
        else:
            print("  " + t("tr_match", i=r["rule"]["idx"],
                           reply=r["reply"] if r["reply"] else t("tr_blocked")))
    return 0


def _history(cfg):
    h = cfg["status"]["history"]
    hist = StatusHistory(BASE / h["file"], h["max_items"])
    hist.load()
    return hist


def cmd_history(cfg):
    hist = _history(cfg)
    if not hist.items:
        print(t("hist_empty"))
    for i, it in enumerate(hist.items, 1):
        print(t("hist_item", i=i, lang=it["lang"], n=it["count"], text=it["text"]))
    return 0


def cmd_history_add(cfg, text):
    hist = _history(cfg)
    ok = hist.add(text)
    if ok:
        hist.save()
    print(t("hist_added") if ok else t("hist_exists"))
    return 0 if ok else 1


def cmd_history_import(cfg, path):
    hist = _history(cfg)
    n = sum(hist.add(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines())
    hist.save()
    print(t("hist_imported", n=n))
    return 0


def cmd_marquee(cfg, a):
    mq = cfg["status"]["marquee"]
    width = a.width or mq["width"]
    stride = a.stride or mq["stride"]
    inf = a.infinite_loop or mq.get("infinite_loop", False)
    frames = marquee_frames(a.text, width, stride, mq["separator"],
                            a.cycles or mq["cycles"], a.max_frames or mq["max_frames"], inf)
    if len(frames) <= 1:
        print(t("mq_static"))
    for i, f in enumerate(frames, 1):
        print(f"{i:>3} |{f}")
    step = mq["step_seconds"]
    print(t("mq_frames", n=len(frames), s=step, t=len(frames) * step))
    return 0


def cmd_windows(cfg):
    from pywinauto import Desktop
    rx = re.compile(cfg["window_title_regex"])
    rows = []
    for w in Desktop(backend="uia").windows(visible_only=True):
        try:
            title = w.window_text()
            if title.strip():
                rows.append((title, w.class_name(), w.process_id()))
        except Exception:
            continue
    if not rows:
        print(t("win_none"))
        return 1
    print(t("win_hdr"))
    for title, cls, pid in rows:
        print(f" {'*' if rx.match(title) else ' '} {title} | {cls} | {pid}")
    print("\n" + t("win_hint"))
    return 0


def dump_controls(win, f):
    """Indented control tree (class | type | name | automation_id | rect). Works with every pywinauto
    version: UIAWrapper has no print_control_identifiers (only WindowSpecification does)."""
    def walk(c, depth):
        try:
            r = c.rectangle()
            rect = f"{r.left},{r.top},{r.width()}x{r.height()}"
        except Exception:
            rect = "?"
        ctype = _ctrl_attr(c, "control_type")
        name = "" if ctype in STRUCT_SKIP else str(_ctrl_attr(c, "name"))[:60]  # no nicknames/chat text
        f.write(f"{'  ' * depth}{_ctrl_attr(c, 'class_name')} | {ctype} | "
                f"{name!r} | id={_ctrl_attr(c, 'automation_id')!r} | {rect}\n")
        try:
            kids = c.children()
        except Exception:
            return
        for k in kids:
            walk(k, depth + 1)
    walk(win, 0)


def cmd_discover(cfg):
    win = get_window(cfg)
    out = BASE / "controls.txt"
    with out.open("w", encoding="utf-8") as f:
        for w in process_windows(win):
            f.write(f"=== window: {w.window_text()[:60]!r} class={w.class_name()} ===\n")
            dump_controls(w, f)
    print(t("discover_done", p=out))
    return 0


# ---------- Camfrog 8.x control detection ----------
# Static analysis of Camfrog Video Chat 8.5.0.51219 (see docs/CAMFROG-8.5-FINDINGS.md): the input is a
# WTL RICHEDIT50W, the custom status is a combo box (inner Edit), the room log is a CEF Document.
STRUCT_SKIP = {"Text", "Hyperlink", "Image", "ListItem", "DataItem", "TreeItem"}  # may hold chat text


def describe_controls(win):
    """Live UIA tree -> plain dicts (no chat text: Text-like rows keep no name)."""
    rows = []
    for c in win.descendants():
        try:
            r = c.rectangle()
            rows.append({
                "class_name": _ctrl_attr(c, "class_name"), "control_type": _ctrl_attr(c, "control_type"),
                "name": str(_ctrl_attr(c, "name"))[:40], "auto_id": str(_ctrl_attr(c, "automation_id")),
                "visible": bool(c.is_visible()), "enabled": bool(c.is_enabled()),
                "left": r.left, "top": r.top, "w": r.width(), "h": r.height(),
                "parent_class": str(getattr(getattr(c.element_info, "parent", None), "class_name", "") or ""),
            })
        except Exception:
            continue
    return rows


def _nth(rows, row, field):
    """Index of `row` among rows sharing its `field` value(s), in traversal order - the same order
    find() uses (descendants(class_name=...) / (control_type=...), then auto_id)."""
    fields = (field,) if isinstance(field, str) else field
    same = [r for r in rows if all(r.get(f) == row.get(f) for f in fields)]
    return next(i for i, r in enumerate(same) if r is row)


def _sel(rows, row, key="class_name"):
    """Selector for `row`: class (or type) plus its automation id when it has one."""
    sel = {key: row[key]}
    fields = (key,)
    if row.get("auto_id") and key == "class_name":
        sel["auto_id"], fields = row["auto_id"], (key, "auto_id")
    sel["index"] = _nth(rows, row, fields)
    return sel


def detect_camfrog(rows):
    """rows (describe_controls) -> {key: (selector, why)}. Pure function, unit-tested offline."""
    live = [r for r in rows if r.get("visible") and r.get("enabled") and r["w"] > 0 and r["h"] > 0]
    out = {}
    hosts = [r for r in live if r["class_name"] == "AtlAxWinLic140"]

    def kind(h):  # the IE pane sharing the host's rectangle names the page: wb-log-room-data / wb-edit-data
        for r in rows:
            if (r["class_name"] == "Internet Explorer_Server"
                    and (r["left"], r["top"], r["w"], r["h"]) == (h["left"], h["top"], h["w"], h["h"])):
                return r["name"]
        return ""
    logs = [h for h in hosts if "wb-log-room-data" in kind(h)]
    edits_ = [h for h in hosts if "wb-edit-data" in kind(h)]
    if logs and edits_:
        out["autoreply.history"] = (_sel(rows, max(logs, key=lambda r: r["w"] * r["h"])),
                                    "room chat log (web pane wb-log-room-data)")
        out["autoreply.input"] = (_sel(rows, max(edits_, key=lambda r: r["top"])),
                                  "room chat input (web pane wb-edit-data)")
        rich = []
    else:
        rich = [r for r in live if r["class_name"].upper().startswith("RICHEDIT")]
    inp = max(rich, key=lambda r: (r["top"], r["w"]), default=None) if rich else None
    if inp:
        out["autoreply.input"] = (_sel(rows, inp),
                                  "bottom-most RichEdit = chat input")
    docs = [r for r in live if r["control_type"] == "Document"] if not out.get("autoreply.history") else []
    if out.get("autoreply.history"):
        pass
    elif docs:
        d = max(docs, key=lambda r: r["w"] * r["h"])
        out["autoreply.history"] = ({"control_type": "Document", "index": _nth(rows, d, "control_type")},
                                    "largest Chromium Document = room log")
    else:
        rest = [r for r in rich if r is not inp]
        if rest:
            h = max(rest, key=lambda r: r["w"] * r["h"])
            out["autoreply.history"] = (_sel(rows, h),
                                        "largest other RichEdit (no Document found)")
    edits = [r for r in live if r["class_name"].lower() == "edit" and "combo" in r["parent_class"].lower()]
    if edits:
        e = min(edits, key=lambda r: (r["top"], r["left"]))
        out["status.edit"] = (_sel(rows, e),
                              "top-most combo-box Edit = custom status")
    else:
        combos = [r for r in live if "combobox" in r["class_name"].lower()]
        if combos:
            c = min(combos, key=lambda r: (r["top"], r["left"]))
            out["status.edit"] = (_sel(rows, c),
                                  "top-most ComboBox (no inner Edit exposed)")
        else:
            edits_any = [r for r in live if r["class_name"].lower() == "edit"]
            if edits_any:
                e2 = min(edits_any, key=lambda r: (r["top"], r["left"]))
                out["status.edit"] = (_sel(rows, e2),
                                      "top-most Edit (broad fallback, verify manually)")
    return out


def write_detect_report(rows, path):
    with Path(path).open("w", encoding="utf-8") as f:
        f.write("class_name | control_type | name(<=40) | auto_id | visible | left,top,w,h | parent_class\n")
        for r in rows:
            if r["control_type"] in STRUCT_SKIP:
                continue
            f.write(f"{r['class_name']} | {r['control_type']} | {r['name']} | {r['auto_id']} | "
                    f"{int(r['visible'])} | {r['left']},{r['top']},{r['w']},{r['h']} | {r['parent_class']}\n")


def cmd_detect(cfg, cfg_path=None, apply=False, as_json=False):
    win = get_window(cfg)
    per_window = [describe_controls(win)] + [describe_controls(w) for w in process_windows(win)
                                             if w.handle != win.handle]
    rows = [r for rs in per_window for r in rs]
    found = {}
    for rs in per_window:  # selectors are per window (find() indexes inside one window)
        for k, v in detect_camfrog(rs).items():
            found.setdefault(k, v)
    rep = BASE / "detect_report.txt"
    write_detect_report(rows, rep)
    if as_json:
        print("PROPOSAL " + json.dumps({k: v[0] for k, v in found.items()}))
    for key in ("status.edit", "autoreply.history", "autoreply.input"):
        if key in found:
            print(t("det_found", k=key, sel=json.dumps(found[key][0]), why=found[key][1]))
        else:
            print(t("det_miss", k=key))
    print(t("det_report", p=rep))
    if not found:
        print(t("det_none"))
        return 1
    if apply:
        new = copy.deepcopy(cfg)
        for key, (sel, _why) in found.items():
            _dig(new, key.split(".")[:-1])[key.split(".")[-1]] = sel
        errs, _ = validate(new)
        if errs:
            print(*errs, sep="\n  ")
            return 2
        path = Path(cfg_path) if cfg_path else BASE / "config.json"
        atomic_write(path, json.dumps(new, ensure_ascii=False, indent=2) + "\n")
        print(t("det_applied", p=path))
    return 0


def cmd_chat_probe(cfg):
    """Attach to the room window and print what the reader parses (to this console only)."""
    main = get_window(cfg)
    w = get_chat_window(cfg, main)
    if w is None:
        print(t("no_room"))
        return 1
    ar = cfg["autoreply"]
    hist = find(w, ar["history"])
    print(f"room window: {w.window_text()!r}  log_kind={log_kind(hist) or '?'}")
    t0 = time.monotonic()
    lines = read_chat(hist, ar["history_tail"]).splitlines()
    print(f"parsed {len(lines)} lines in {time.monotonic() - t0:.2f}s; last 8:")
    for ln in lines[-8:]:
        m = LINE_RE.match(ln.strip())
        print(("  OK   " if m else "  SKIP ") + ln[:100])
    return 0 if lines else 1


def cmd_im_probe(cfg):
    """Why is IM auto-reply (not) answering? Prints the config state and how every Camfrog window is
    classified. Console only; no chat text is read or printed."""
    im, ar = _im(cfg), cfg["autoreply"]
    friends = [n for n in im["only_nicknames"] if str(n).strip()]
    print("== config ==")
    problems = []
    if not im["enabled"]:
        problems.append("autoreply_im.enabled is false (nothing is answered)")
    if not friends:
        problems.append("autoreply_im.only_nicknames is empty (nobody is answered)")
    if not ar["own_nickname"].strip():
        problems.append("autoreply.own_nickname is empty")
    if not im["rules"]:
        problems.append("autoreply_im.rules is empty")
    if cfg["dry_run"]:
        problems.append("global dry_run is true: nothing is sent (dry-run only logs)")
    if im["dry_run"]:
        problems.append("autoreply_im.dry_run is true: nothing is sent (dry-run only logs)")
    if not in_active_hours(cfg):
        problems.append("outside active_hours: the bot is idle")
    print(f"  enabled={im['enabled']}  friends={len(friends)}  rules={len(im['rules'])}  "
          f"log_kinds={im['log_kinds']}  dry_run(global/IM)={cfg['dry_run']}/{im['dry_run']}")
    for pr in problems:
        print("  PROBLEM: " + pr)
    if not problems:
        print("  config looks ready")
    print("== Camfrog windows ==")
    main = get_window(cfg)
    n_im = 0
    for w in process_windows(main):
        try:
            title = w.window_text()
        except Exception:
            title = "?"
        if w.handle == main.handle:
            print(f"  [main]  {title!r}: buddy list / main window (never used)")
            continue
        try:
            _, hist = find(w, ar["input"]), find(w, ar["history"])
        except Exception:
            print(f"  [other] {title!r}: no chat input/history panes found with the autoreply selectors")
            continue
        kind = log_kind(hist) if is_web(hist) else ""
        if kind in im["log_kinds"]:
            n_im += 1
            verdict = f"{kind.upper()} window: WILL be tracked"
        elif kind == "mtim":
            verdict = "MTIM window: ignored. If this is your private chat, add \"mtim\" to autoreply_im.log_kinds (unverified)"
        elif kind == "room":
            verdict = "room window: never used for IM"
        else:
            verdict = "chat panes found but the page kind is unknown (UIA names not exposed?)"
        print(f"  [{kind or '?'}] {title!r}: {verdict}")
    if not n_im:
        print("  PROBLEM: no private-chat window would be tracked. Open a private chat (not minimized to the tray) and retry.")
    return 0 if (n_im and not problems) else 1


def cmd_status(cfg, text):
    win = get_window(cfg)
    st, sf = cfg["status"], cfg["safety"]
    ctrl = find(win, st["edit"])
    btn = find(win, st["apply_button"]) if st["apply_button"] else None
    text = clip(expand(text, own=cfg["autoreply"]["own_nickname"]), st["max_length"])
    ok = commit(win, ctrl, text, cfg["dry_run"], sf["require_foreground"], btn,
                sf["restore_previous_window"], st.get("background_enter_target", "edit"))
    print(t("ok") if ok else t("failed"))
    return 0 if ok else 1


LANG_LOCKED = False


def setup_logging(cfg):
    global LOG_TEXT
    lc = cfg["log"]
    LOG_TEXT = lc["log_message_text"]
    handlers = [logging.StreamHandler()]
    if lc["file"]:
        handlers.append(logging.handlers.RotatingFileHandler(
            BASE / lc["file"], maxBytes=lc["max_bytes"], backupCount=lc["backups"],
            encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, lc["level"].upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers, force=True)


# Public command names used by GUI launchers when forwarding CLI arguments.
CLI_COMMANDS = {
    "check", "discover", "windows", "run", "start", "stop", "state",
    "autostart-on", "autostart-off", "chat-probe", "im-probe", "detect",
    "status", "init", "marquee", "history", "history-add", "history-import",
    "test-rules",
}

PROFILE_COMMANDS = {
    "room": {
        "check", "discover", "windows", "run", "start", "stop", "state",
        "autostart-on", "autostart-off", "chat-probe", "detect", "init",
        "test-rules",
        "update",
    },
    "im_reply": {
        "check", "discover", "windows", "run", "start", "stop", "state",
        "autostart-on", "autostart-off", "im-probe", "detect", "init",
        "test-rules",
        "update",
    },
    "status": {
        "check", "discover", "windows", "run", "start", "stop", "state",
        "status", "marquee", "history", "history-add", "history-import",
        "detect", "init",
        "update",
    },
    "music": {
        "check", "discover", "windows", "run", "start", "stop", "state",
        "autostart-on", "autostart-off", "chat-probe", "detect", "init",
        "test-rules",
        "update",
    },
}


def profile_command_error(profile, args):
    """Return an error for a command outside this executable's feature boundary."""
    if profile == "full":
        return None
    if profile not in PROFILE_COMMANDS:
        return f"Unknown Camfrog app profile: {profile}"
    if args.cmd not in PROFILE_COMMANDS[profile]:
        return f"Command '{args.cmd}' is not available in the {profile} app."
    if args.cmd == "test-rules":
        if profile == "im_reply" and not args.im:
            return "The IM Auto-reply app requires 'test-rules --im'."
        if profile == "room" and args.im:
            return "The Room Control app cannot test private IM rules."
    return None


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS)
    common.add_argument("--lang", choices=["auto", "th", "en"], default=argparse.SUPPRESS)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=None)
    p.add_argument("--lang", choices=["auto", "th", "en"], default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "discover", "windows", "run", "start", "stop", "state",
                 "autostart-on", "autostart-off"):
        sub.add_parser(name, parents=[common])
    up = sub.add_parser("update", parents=[common],
                        help="self-update this executable from the GitHub release")
    up.add_argument("asset", nargs="?", default=None)
    sub.add_parser("chat-probe", parents=[common])
    sub.add_parser("im-probe", parents=[common])
    dp = sub.add_parser("detect", parents=[common])
    dp.add_argument("--apply", action="store_true", help="write detected selectors into config.json")
    s = sub.add_parser("status", parents=[common])
    s.add_argument("text")
    i = sub.add_parser("init", parents=[common])
    i.add_argument("--force", action="store_true")
    mqp = sub.add_parser("marquee", parents=[common])
    mqp.add_argument("text")
    mqp.add_argument("--width", type=int)
    mqp.add_argument("--stride", type=int)
    mqp.add_argument("--cycles", type=int)
    mqp.add_argument("--max-frames", dest="max_frames", type=int)
    mqp.add_argument("--infinite-loop", action="store_true", help="loop continuously without settling")
    sub.add_parser("history", parents=[common])
    ha = sub.add_parser("history-add", parents=[common])
    ha.add_argument("text")
    hi = sub.add_parser("history-import", parents=[common])
    hi.add_argument("file")
    tr = sub.add_parser("test-rules", parents=[common])
    tr.add_argument("text", nargs="?")
    tr.add_argument("--sender", default="TestUser")
    tr.add_argument("--file")
    tr.add_argument("--im", action="store_true", help="test with the autoreply_im rules and limits")
    return p


def cli_main(argv=None):
    global LANG, LANG_LOCKED
    a = build_parser().parse_args(argv)
    LANG = resolve_lang(a.lang)
    LANG_LOCKED = bool(a.lang and a.lang != "auto")
    profile_error = profile_command_error(APP_PROFILE, a)
    if profile_error:
        print(profile_error)
        return 2
    if a.cmd == "update":
        return cmd_update(a)
    cfg_path = Path(a.config) if a.config else BASE / "config.json"
    if APP_PROFILE in ("room", "im_reply", "status"):
        if not cfg_path.is_absolute():
            cfg_path = BASE / cfg_path
        try:
            cfg_path.resolve().relative_to(BASE.resolve())
        except ValueError:
            print("This app's config must stay inside its own folder.")
            return 2
    if a.cmd == "init":
        return cmd_init(cfg_path, a.force)
    try:
        cfg = apply_app_profile(load_cfg(cfg_path))
    except Exception as e:
        print(t("cannot_load", e=e))
        return 2
    if not a.lang:
        LANG = resolve_lang(cfg["language"])
    if a.cmd == "check":
        return cmd_check(cfg)
    if a.cmd == "stop":
        return cmd_stop(cfg)
    if a.cmd == "state":
        return cmd_state(cfg)
    if a.cmd in ("autostart-on", "autostart-off"):
        return cmd_autostart(a, a.cmd == "autostart-on")
    errs, _ = validate(cfg)
    if errs:
        print(t("cfg_hint"), *errs, sep="\n  ")
        return 2
    if a.cmd == "test-rules":
        return cmd_test_rules(cfg, a)
    if a.cmd == "marquee":
        return cmd_marquee(cfg, a)
    if a.cmd == "history":
        return cmd_history(cfg)
    if a.cmd == "history-add":
        return cmd_history_add(cfg, a.text)
    if a.cmd == "history-import":
        return cmd_history_import(cfg, a.file)
    if a.cmd == "start":
        return cmd_start(cfg, a)
    setup_logging(cfg)
    try:
        if a.cmd == "windows":
            return cmd_windows(cfg)
        if a.cmd == "discover":
            return cmd_discover(cfg)
        if a.cmd == "chat-probe":
            return cmd_chat_probe(cfg)
        if a.cmd == "im-probe":
            return cmd_im_probe(cfg)
        if a.cmd == "detect":
            return cmd_detect(cfg, cfg_path, a.apply)
        if a.cmd == "status":
            return cmd_status(cfg, a.text)
        return run_guarded(cfg, cfg_path)
    except KeyboardInterrupt:
        log.info(t("interrupted"))
        return 0
    except (RuntimeError, LookupError) as e:  # expected setup problems: message, no traceback
        log.error("%s", e)
        if "window_title_regex" in str(e):
            print(t("win_hint"))
        return 1
    except Exception:
        log.exception(t("fatal"))
        return 1




def set_lang(value):
    """Set the module-global language used by t()/bi()."""
    global LANG
    LANG = value



# ============================================================
# Inlined clipboard support (clipboard_support.py).
# ============================================================

_WIDGET_CLASSES = {
    "Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text", "Listbox",
}
_EDITABLE_CLASSES = {"Entry", "TEntry", "TCombobox", "Spinbox", "TSpinbox", "Text"}


def _windows_open_clipboard(clipboard, attempts=20, interval=0.025):
    """Windows clipboard is often briefly busy; retry opening it for 0.5 seconds."""
    last_error = None
    for attempt in range(attempts):
        try:
            clipboard.OpenClipboard(None)
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(interval)
    raise OSError(f"Could not open the Windows clipboard: {last_error}") from last_error


def set_clipboard_text(root, text):
    text = str(text)
    if os.name == "nt":
        import win32clipboard
        import win32con

        _windows_open_clipboard(win32clipboard)
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update_idletasks()


def get_clipboard_text(root):
    if os.name == "nt":
        import win32clipboard
        import win32con

        _windows_open_clipboard(win32clipboard)
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                value = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            elif win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                value = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                if isinstance(value, bytes):
                    value = value.decode("mbcs", errors="replace")
            else:
                return ""
            return value if isinstance(value, str) else str(value)
        finally:
            win32clipboard.CloseClipboard()
    try:
        return root.clipboard_get()
    except Exception:
        return ""


def _widget_class(widget):
    try:
        return widget.winfo_class()
    except Exception:
        return ""


def _has_selection(widget, widget_class):
    try:
        if widget_class == "Text":
            return bool(widget.tag_ranges("sel"))
        if widget_class == "Listbox":
            return bool(widget.curselection())
        return bool(widget.selection_present())
    except Exception:
        return False


def _selected_text(widget, widget_class):
    if widget_class == "Text":
        return widget.get("sel.first", "sel.last")
    if widget_class == "Listbox":
        selected = widget.curselection()
        return "\n".join(widget.get(index) for index in selected)
    value = widget.get()
    start, end = int(widget.index("sel.first")), int(widget.index("sel.last"))
    return value[start:end]


def _editable(widget, widget_class):
    if widget_class not in _EDITABLE_CLASSES:
        return False
    try:
        return str(widget.cget("state")) not in {"disabled", "readonly"}
    except Exception:
        return True


def _delete_selection(widget, widget_class):
    if widget_class == "Text":
        widget.delete("sel.first", "sel.last")
    else:
        widget.delete("sel.first", "sel.last")


def _operate(root, widget, action):
    widget_class = _widget_class(widget)
    if widget_class not in _WIDGET_CLASSES:
        return False

    if action in {"copy", "cut"}:
        if not _has_selection(widget, widget_class):
            return False
        if action == "cut" and not _editable(widget, widget_class):
            return False
        selected = _selected_text(widget, widget_class)
        set_clipboard_text(root, selected)
        if action == "cut":
            _delete_selection(widget, widget_class)
        return True

    if action == "paste":
        if not _editable(widget, widget_class):
            return False
        value = get_clipboard_text(root)
        if not value:
            return False
        if widget_class == "Text":
            if _has_selection(widget, widget_class):
                widget.delete("sel.first", "sel.last")
            widget.insert("insert", value)
        else:
            if _has_selection(widget, widget_class):
                _delete_selection(widget, widget_class)
            widget.insert("insert", value)
        return True
    return False


class ClipboardController:
    """Attach reliable Ctrl+C/X/V/A, Insert shortcuts, and a right-click menu."""

    def __init__(self, root, translate=None, on_error=None):
        import tkinter as tk

        self.root = root
        self.translate = translate or (lambda text: str(text).partition("|")[0])
        self.on_error = on_error
        self.target = None
        self.widgets = []
        import weakref
        self._installed_widgets = weakref.WeakSet()
        self.menu = tk.Menu(root, tearoff=False)
        for label, action in (("Cut|ตัด", "cut"), ("Copy|คัดลอก", "copy"),
                              ("Paste|วาง", "paste")):
            self.menu.add_command(label=self.translate(label),
                                  command=lambda op=action: self.perform(self.target, op))
        self.menu.add_separator()
        self.menu.add_command(label=self.translate("Select all|เลือกทั้งหมด"),
                              command=lambda: self.select_all(self.target))

    def refresh_menu(self):
        """Refresh localized labels after the GUI language changes."""
        labels = ("Cut|ตัด", "Copy|คัดลอก", "Paste|วาง",
                  "Select all|เลือกทั้งหมด")
        for index, label in enumerate(labels):
            self.menu.entryconfigure(index if index < 3 else 4,
                                     label=self.translate(label))

    def install(self, parent):
        """Bind editors recursively, including controls added after the first build."""
        for widget in parent.winfo_children():
            widget_class = _widget_class(widget)
            if widget_class in _WIDGET_CLASSES and widget not in self._installed_widgets:
                self._installed_widgets.add(widget)
                self.widgets.append(widget)
                if widget_class in {"Text", "Listbox"}:
                    try:
                        widget.configure(
                            background="#ffffff" if widget_class == "Text" else "#fffdfa",
                            foreground="#283648", selectbackground="#8b672b",
                            selectforeground="#fffdfa", insertbackground="#283648",
                            highlightbackground="#d5c8ac", highlightcolor="#8b672b")
                    except Exception:
                        pass
                for sequence, action in (
                    ("<Control-c>", "copy"), ("<Control-x>", "cut"),
                    ("<Control-v>", "paste"), ("<Control-Insert>", "copy"),
                    ("<Shift-Delete>", "cut"), ("<Shift-Insert>", "paste"),
                ):
                    widget.bind(sequence, lambda event, op=action: self.handle(event, op), add="+")
                # The generic key binding also recognizes Windows virtual-key
                # codes and control characters when a Thai/non-Latin layout
                # reports an unknown keysym for Ctrl+A/C/V/X.
                widget.bind("<Control-KeyPress>", self.handle_control_key, add="+")
                widget.bind("<Control-a>", self._select_all_event, add="+")
                widget.bind("<Button-3>", self.show_menu, add="+")
                widget.bind("<Shift-F10>", self.show_menu, add="+")
            self.install(widget)

    def handle_control_key(self, event):
        """Dispatch Ctrl+A/C/V/X consistently across keyboard layouts."""
        keysym = str(getattr(event, "keysym", "")).lower()
        char = str(getattr(event, "char", "") or "")
        try:
            keycode = int(getattr(event, "keycode", -1))
        except (TypeError, ValueError):
            keycode = -1
        shortcuts = {
            "a": ({"a", "\x01"}, 65, None),
            "c": ({"c", "\x03"}, 67, "copy"),
            "v": ({"v", "\x16"}, 86, "paste"),
            "x": ({"x", "\x18"}, 88, "cut"),
        }
        key = next((name for name, (chars, code, _action) in shortcuts.items()
                    if keysym == name or char in chars or keycode == code), None)
        if key is None:
            return None
        self.target = event.widget
        _chars, _code, action = shortcuts[key]
        if action is None:
            return self._select_all_event(event)
        return self.handle(event, action)

    def perform(self, widget, action):
        if widget is None:
            return False
        try:
            return _operate(self.root, widget, action)
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
            return False

    def handle(self, event, action):
        self.target = event.widget
        self.perform(self.target, action)
        return "break"

    def select_all(self, widget):
        if widget is None:
            return "break"
        widget_class = _widget_class(widget)
        try:
            if widget_class == "Text":
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "end-1c")
            elif widget_class == "Listbox":
                widget.selection_set(0, "end")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
            widget.focus_set()
        except Exception:
            pass
        return "break"

    def _select_all_event(self, event):
        self.target = event.widget
        return self.select_all(event.widget)

    def show_menu(self, event):
        self.target = event.widget
        try:
            event.widget.focus_set()
            self.menu.tk_popup(event.x_root, event.y_root)
        except Exception as exc:
            if self.on_error:
                self.on_error(exc)
        finally:
            try:
                self.menu.grab_release()
            except Exception:
                pass
        return "break"


# ============================================================
# GUI (from camfrog_gui.py.py), profile: music.
# ============================================================



APP_NAME = "ZeaZDev room Music DJ"




CLI_COMMANDS = {"check", "discover", "detect", "chat-probe", "im-probe", "windows", "run", "start", "stop", "state", "update", "status",
                "autostart-on", "autostart-off", "init", "marquee", "history", "history-add",
                "history-import", "test-rules"}


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
    parser.add_argument("--config", default=None, help="use an alternate app-local config")
    args = parser.parse_args(argv)
    if args.config is not None and not args.config.strip():
        parser.error("argument --config: expected a path")
    return args
SEL_FIELDS = ("control_type", "auto_id", "class_name", "title", "title_re", "index")


def bi(s):
    """'English|ไทย' -> text for the current language."""
    en, _, th = s.partition("|")
    return (th if (LANG == "th" and th) else en).replace("{pipe}", "||")  # {pipe}: literal ||


# ---------------------------------------------------------------- Tk-free logic
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
        if APP_PROFILE == "music":
            try:
                self.path.relative_to(BASE.resolve())
            except ValueError as exc:
                raise ValueError("This app's config must stay inside its own folder.") from exc
        self.cfg = None

    def load(self):
        if not self.path.exists():
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_init(self.path, False)
        self.cfg = apply_app_profile(load_cfg(self.path))
        return self.cfg

    @staticmethod
    def check(cfg):
        errs, warns = validate(cfg)
        ar = cfg["autoreply"]
        if (not cfg["dry_run"] and ar["enabled"] and not ar["own_nickname"].strip()
                and t("refuse_live") not in errs):
            errs.append(t("refuse_live"))
        return errs, warns

    def save(self, cfg):
        cfg = apply_app_profile(cfg)
        errs, warns = self.check(cfg)
        if errs:
            return errs, warns
        atomic_write(self.path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
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
        return json.loads(stats_path(cfg).read_text(encoding="utf-8"))
    except Exception:
        return {}


def explain(cfg, nick, msg, im=False):
    """Human text for the offline room or private-message rule tester."""
    r = (evaluate_im if im else evaluate_message)(cfg, nick, msg)
    head = t("tr_lang", l=r["lang"])
    if r["skip"]:
        return head + "\n" + t("tr_skip", why=t("why_" + r["skip"]))
    if r["rule"] is None:
        return head + "\n" + t("tr_none")
    return head + "\n" + t("tr_match", i=r["rule"]["idx"],
                              reply=r["reply"] or t("tr_blocked"))


def autosave_needs_confirmation(cfg, bot_running):
    """Live config changes need an explicit Save confirmation while the bot runs."""
    return bool(bot_running and not cfg.get("dry_run", True))


# ---------------------------------------------------------------- GUI
def build_app():
    import tkinter as tk
    from tkinter import messagebox, ttk

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
            ttk.Label(f, text=bi("Pattern (regex)|รูปแบบ (regex)")).grid(row=0, column=0, sticky="w")
            ttk.Entry(f, textvariable=self.pat, width=60).grid(row=0, column=1, columnspan=3, sticky="we")
            th, en, anyl = reply_to_fields(rule.get("reply"))
            self.boxes = []
            for r, (lab, val) in enumerate(((bi("Reply TH (one per line = random)|ตอบไทย (บรรทัดละแบบ สุ่ม)"), th),
                                            (bi("Reply EN|ตอบอังกฤษ"), en),
                                            (bi("Reply any language|ตอบทุกภาษา"), anyl)), 1):
                ttk.Label(f, text=lab).grid(row=r, column=0, sticky="nw", pady=2)
                box = tk.Text(f, width=60, height=3)
                box.insert("1.0", val)
                box.grid(row=r, column=1, columnspan=3, sticky="we", pady=2)
                self.boxes.append(box)
            ttk.Label(f, text=bi("Only language|เฉพาะภาษา")).grid(row=4, column=0, sticky="w")
            ttk.Combobox(f, textvariable=self.lang, values=("", "th", "en"), width=6,
                         state="readonly").grid(row=4, column=1, sticky="w")
            ttk.Label(f, text=bi("Cooldown (s)|คูลดาวน์ (วินาที)")).grid(row=4, column=2, sticky="e")
            ttk.Entry(f, textvariable=self.cool, width=8).grid(row=4, column=3, sticky="w")
            ttk.Checkbutton(f, text=bi("Only when my nick is mentioned|ตอบเมื่อมีคนพิมพ์นิคเรา"),
                            variable=self.mention).grid(row=5, column=0, columnspan=2, sticky="w")
            ttk.Checkbutton(f, text=bi("Enabled|เปิดใช้"), variable=self.enabled).grid(
                row=5, column=2, columnspan=2, sticky="w")
            ttk.Label(f, text=bi("Variables: {sender} {me} {time} {date}. Replies starting with / are blocked.|"
                                 "ตัวแปร: {sender} {me} {time} {date} คำตอบที่ขึ้นต้นด้วย / จะถูกบล็อก"),
                      foreground="#626d7a").grid(row=6, column=0, columnspan=4, sticky="w", pady=4)
            bar = ttk.Frame(f)
            bar.grid(row=7, column=0, columnspan=4, sticky="e")
            ttk.Button(bar, text="OK", command=self.ok).pack(side="left", padx=4)
            ttk.Button(bar, text=bi("Cancel|ยกเลิก"), command=self.destroy).pack(side="left")
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
                    raise ValueError(bi("pattern and reply are required|ต้องมี pattern และ reply"))
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
            style.configure("TLabel", background="#f4f1e8", foreground="#283648", font=("Segoe UI", 9))
            style.configure("TCheckbutton", background="#f4f1e8", foreground="#283648")
            style.configure("TRadiobutton", background="#f4f1e8", foreground="#283648")
            style.configure("TLabelframe", background="#f4f1e8", bordercolor="#d5c8ac")
            style.configure("TLabelframe.Label", background="#f4f1e8", foreground="#8b672b",
                            font=("Segoe UI", 9, "bold"))
            style.configure("TButton", padding=(7, 4), font=("Segoe UI", 9),
                            background="#ebe5d9", foreground="#283648")
            style.map("TButton", background=[("active", "#d6e9e8"), ("pressed", "#d5c8ac")])
            style.configure("TEntry", padding=(4, 4), fieldbackground="#ffffff",
                            foreground="#283648")
            style.configure("TNotebook", background="#f4f1e8")
            style.configure("TNotebook.Tab", padding=(11, 6), font=("Segoe UI", 9))
            style.map("TNotebook.Tab", background=[("selected", "#fffdfa")],
                      foreground=[("selected", "#8b672b")])
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
                messagebox.showerror(APP_NAME, t("cannot_load", e=e))
                self.root.destroy()
                raise SystemExit(2)
            set_lang(resolve_lang(self.draft["language"]))
            self.build()
            self._closing = False
            self.root.protocol("WM_DELETE_WINDOW", self.close_app)
            self.root.bind("<Control-s>", lambda _e: self.save())
            self.root.bind("<F3>", lambda _e: self.start())
            self.root.bind("<F4>", lambda _e: self.stop())
            self.root.after(150, self.pump)
            self.root.after(500, self.tick)
            self.root.after(1000, self._discover_controls_on_start)
            self.root.after(2500, self.auto_update_check)

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
            ttk.Button(top, text="TH / EN", width=8, command=self.switch_lang).pack(side="right")
            bar = ttk.Frame(self.root, padding=(8, 6))
            bar.pack(side="bottom", fill="x")
            ttk.Button(bar, text=bi("Save (Ctrl+S)|บันทึก (Ctrl+S)"), command=self.save).pack(side="left")
            ttk.Button(bar, text=bi("Reload from disk|โหลดจากไฟล์ใหม่"), command=self.reload).pack(side="left", padx=6)
            ttk.Button(bar, text=bi("Check config|ตรวจคอนฟิก"), command=self.check_cfg).pack(side="left")
            ttk.Button(bar, text=bi("Update|อัปเดต"), command=self.check_update).pack(side="left", padx=6)
            if not hasattr(self, "auto_apply_var"):
                self.auto_apply_var = tk.BooleanVar(master=self.root, value=True)
            ttk.Checkbutton(bar, text=bi("Auto-apply valid changes|ใช้ค่าที่ถูกต้องอัตโนมัติ"),
                            variable=self.auto_apply_var, command=self.auto_apply_toggled).pack(side="right")
            self.msg = ttk.Label(bar, text="", foreground="#626d7a")
            self.msg.pack(side="left", padx=12)
            self.nb = ttk.Notebook(self.root)
            self.nb.pack(fill="both", expand=True, padx=8)
            tab_specs = (("Dashboard|แดชบอร์ด", "Dashboard", self.tab_dashboard),
                         ("Setup|ตั้งค่าเริ่มต้น", "Setup", self.tab_setup),
                         ("Auto-reply|ตอบอัตโนมัติ", "Auto-reply", self.tab_reply),
                         ("Music DJ|ดีเจเพลง", "Music", self.tab_music),
                         ("Advanced|ขั้นสูง", "Advanced", self.tab_advanced))
            for name, key, fn in tab_specs:
                f = ttk.Frame(self.nb, padding=8)
                fn(f)
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
                self.root, translate=bi, on_error=self.clipboard_error)
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
                    self.msg.configure(text=bi("Clipboard error: {error}|คลิปบอร์ดผิดพลาด: {error}").format(
                        error=exc))
                except tk.TclError:
                    pass

        def clipboard_virtual(self, event, virtual):
            self.clipboard_target = event.widget
            action = {"<<Copy>>": "copy", "<<Cut>>": "cut", "<<Paste>>": "paste"}.get(virtual)
            if action:
                self.clipboard_controller.perform(event.widget, action)
            return "break"

        def clipboard_action(self, virtual):
            widget = self.clipboard_target
            action = {"<<Copy>>": "copy", "<<Cut>>": "cut", "<<Paste>>": "paste"}.get(virtual)
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
            ttk.Label(parent, text=bi(label)).grid(row=row, column=0, sticky="w", padx=4, pady=2)
            if kind == "bool":
                var = tk.BooleanVar()
                w = ttk.Checkbutton(parent, variable=var)
            elif kind == "choice":
                var = tk.StringVar()
                w = ttk.Combobox(parent, textvariable=var, values=choices, state="readonly", width=width - 3)
            else:
                var = tk.StringVar()
                w = ttk.Entry(parent, textvariable=var, width=width)
            w.grid(row=row, column=1, sticky="w", padx=4, pady=2)
            if hint:
                ttk.Label(parent, text=bi(hint), foreground="#626d7a").grid(row=row, column=2, sticky="w")
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
            self.btn_start = ttk.Button(row, text=bi("Save & Start (background)|บันทึกและเริ่ม (เบื้องหลัง)"), command=self.start)
            self.btn_start.pack(side="left")
            self.btn_bg = ttk.Button(row, text=bi("Start & close window|เริ่มเบื้องหลังแล้วปิดหน้าต่าง"),
                                     command=lambda: self.start(close=True))
            self.btn_bg.pack(side="left", padx=6)
            self.btn_stop = ttk.Button(row, text=bi("Stop|หยุด"), command=self.stop)
            self.btn_stop.pack(side="left", padx=6)
            ttk.Button(row, text=bi("EMERGENCY STOP|หยุดฉุกเฉิน"), command=self.emergency).pack(side="left", padx=6)
            self.live_var = tk.BooleanVar()
            self.binds.append((("dry_run",), "live", self.live_var, "dry_run"))
            ttk.Checkbutton(row, text=bi("LIVE: really send messages|โหมดจริง: ส่งข้อความจริง"), variable=self.live_var,
                            command=self.live_toggled).pack(side="right")
            self.stats_lbl = ttk.Label(f, text="")
            self.stats_lbl.pack(anchor="w", pady=6)
            ttk.Label(f, text=bi("Closing this window does not stop the bot. Dry-run only logs what it would send.|"
                                 "ปิดหน้าต่างนี้ไม่หยุดบอท โหมดทดลองแค่บันทึกว่าจะส่งอะไร"), foreground="#626d7a").pack(anchor="w")
            lf = ttk.Frame(f)
            lf.pack(fill="both", expand=True, pady=6)
            self.log = tk.Text(lf, state="disabled", wrap="none", height=18, font=("Consolas", 9))
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
            self.field(g, "Window title regex|regex ชื่อหน้าต่าง", ("window_title_regex",), width=40)
            self.field(g, "My nickname|นิคของฉัน", ("autoreply", "own_nickname"), width=40,
                       hint="required for live mode|จำเป็นเมื่อรันจริง")
            row = ttk.Frame(f)
            row.pack(fill="x", pady=4)
            ttk.Button(row, text=bi("List windows|แสดงหน้าต่าง"), command=lambda: self.helper("windows")).pack(side="left")
            ttk.Button(row, text=bi("Auto-detect (Camfrog 8.x)|ตรวจหา control อัตโนมัติ"),
                       command=self.detect).pack(side="left", padx=6)
            ttk.Button(row, text=bi("Discover controls -> controls.txt|ดึง control -> controls.txt"),
                       command=lambda: self.helper("discover")).pack(side="left", padx=6)
            ttk.Label(row, text=bi("Windows only. Copy values from controls.txt into the boxes below.|"
                                   "เฉพาะ Windows คัดลอกค่าจาก controls.txt มาใส่ช่องด้านล่าง"), foreground="#626d7a").pack(side="left")
            self.selector_group(f, "Chat history selector|selector ประวัติแชท", ("autoreply", "history"))
            self.selector_group(f, "Chat input selector|selector ช่องพิมพ์แชท", ("autoreply", "input"))
            self.helper_out = tk.Text(f, height=8, state="disabled")
            self.helper_out.pack(fill="both", expand=True, pady=4)

        def tab_reply(self, f):
            top = ttk.Frame(f)
            top.pack(fill="x")
            self.field(top, "Auto-reply enabled|เปิดตอบอัตโนมัติ", ("autoreply", "enabled"), "bool",
                       hint="required for DJ chat commands|จำเป็นสำหรับคำสั่งดีเจในแชท")
            self.field(top, "Ignore links|ไม่ตอบข้อความที่มีลิงก์", ("autoreply", "ignore_links"), "bool")
            self.field(top, "Per-sender cooldown (s)|คูลดาวน์ต่อคน", ("autoreply", "per_sender_cooldown_seconds"), "int", width=8)
            self.field(top, "Global gap (s)|ระยะห่างทุกคำตอบ", ("autoreply", "global_min_gap_seconds"), "int", width=8)
            self.field(top, "Max per hour|สูงสุดต่อชั่วโมง", ("autoreply", "max_per_hour"), "int", width=8)
            self.field(top, "Delay range s (min, max)|หน่วงสุ่ม", ("autoreply", "delay_range_seconds"), "pair", width=12)
            self.field(top, "Ignore nicks|ข้ามนิค", ("autoreply", "ignore_nicknames"), "list", width=40, hint="comma separated|คั่นด้วย ,")
            self.field(top, "Only nicks|ตอบเฉพาะนิค", ("autoreply", "only_nicknames"), "list", width=40, hint="empty = everyone|ว่าง = ทุกคน")
            ttk.Label(f, text=bi("Optional room rules (first match wins). Lines starting with the DJ prefix go to the DJ.|"
                                 "กฎห้องเพิ่มเติม (ตัวแรกที่ตรงชนะ) บรรทัดที่ขึ้นต้นด้วยคำสั่งดีเจจะวิ่งไปที่ดีเจ")).pack(anchor="w", pady=(8, 0))
            tf = ttk.Frame(f)
            tf.pack(fill="both", expand=True)
            cols = ("n", "on", "lang", "men", "pat", "rep")
            self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=7, selectmode="browse")
            for c, w, h in (("n", 30, "#"), ("on", 40, "on"), ("lang", 45, "lang"), ("men", 50, "@me"),
                            ("pat", 300, "pattern"), ("rep", 380, "reply")):
                self.tree.heading(c, text=h)
                self.tree.column(c, width=w, anchor="w")
            self.tree.pack(side="left", fill="both", expand=True)
            self.tree.bind("<Double-1>", lambda _e: self.rule_edit())
            bf = ttk.Frame(tf)
            bf.pack(side="left", padx=6, anchor="n")
            for txt, fn in (("Add|เพิ่ม", self.rule_add), ("Edit|แก้", self.rule_edit), ("Delete|ลบ", self.rule_del),
                            ("Up|ขึ้น", lambda: self.rule_move(-1)), ("Down|ลง", lambda: self.rule_move(1))):
                ttk.Button(bf, text=bi(txt), command=fn, width=8).pack(pady=1)
            ttk.Label(f, text=bi("Skip patterns (regex, one per line)|skip patterns (regex บรรทัดละอัน)")).pack(anchor="w")
            self.textbox(f, 2, "t_skips").pack(fill="x")
            lf = ttk.LabelFrame(f, text=bi("Rule tester (offline, nothing is sent)|ทดสอบกฎ (ออฟไลน์ ไม่ส่งจริง)"), padding=6)
            lf.pack(fill="x", pady=6)
            self.tn = tk.StringVar(value="TestUser")
            self.tm = tk.StringVar(value="สวัสดีครับ")
            ttk.Entry(lf, textvariable=self.tn, width=14).pack(side="left")
            e = ttk.Entry(lf, textvariable=self.tm, width=44)
            e.pack(side="left", padx=4)
            e.bind("<Return>", lambda _e: self.test_rule())
            ttk.Button(lf, text=bi("Test|ทดลอง"), command=self.test_rule).pack(side="left")
            self.tout = ttk.Label(lf, text="", foreground="#28744c")
            self.tout.pack(side="left", padx=8)

        # ---- music DJ
        def dj_queue_path(self):
            cfg, errs = self.collect()
            draft = cfg if not errs else self.draft
            return dj_queue_path_for_config(draft)

        def dj_load_queue(self):
            q = DJQueue(self.dj_queue_path())
            q.load()
            return q

        def dj_skip(self):
            q = self.dj_load_queue()
            owner = self.draft["autoreply"]["own_nickname"]
            try:
                q.skip(owner, owner)
            except DJError as exc:
                self.say(str(exc), bad=True)
                return
            q.save()
            self.dj_refresh()
            self.say(bi("Skipped to the next song|ข้ามไปเพลงถัดไป"))

        def dj_clear(self):
            q = self.dj_load_queue()
            q.current, q.queue = None, []
            q.save()
            self.dj_refresh()
            self.say(bi("Queue cleared|ล้างคิวแล้ว"))

        def dj_refresh(self):
            if not hasattr(self, "dj_tree"):
                return
            self.dj_tree.delete(*self.dj_tree.get_children())
            q = self.dj_load_queue()
            if q.current is not None:
                self.dj_tree.insert("", "end", iid="cur", values=(
                    bi("now|กำลังเล่น"), q.current["title"], q.current["user"]))
            for i, item in enumerate(q.queue[:50], 1):
                self.dj_tree.insert("", "end", iid=f"q{i}", values=(i, item["title"], item["user"]))

        def dj_browse_music_folder(self):
            from tkinter import filedialog
            current = Path(self.dj_music_dir_var.get().strip() or "music")
            if not current.is_absolute():
                current = BASE / current
            initialdir = current if current.is_dir() else BASE
            selected = filedialog.askdirectory(
                parent=self.root,
                title=bi("Choose music folder|เลือกโฟลเดอร์เพลง"),
                initialdir=str(initialdir),
                mustexist=True,
            )
            if selected:
                self.dj_music_dir_var.set(str(Path(selected)))

        def dj_speak_text(self, value=None):
            try:
                text = validate_room_speech_text(
                    self.dj_speech_var.get() if value is None else value)
            except ValueError as exc:
                self.say(str(exc), bad=True)
                return

            def complete(error):
                message = ("Voice output failed: {0}".format(error) if error else
                           bi("Voice finished through Windows output.|อ่านเสียงผ่านเอาต์พุต Windows เสร็จแล้ว"))
                self.q.put(("speech", 1 if error else 0, message))

            try:
                start_room_speech(text, complete)
            except (RuntimeError, ValueError) as exc:
                self.say(str(exc), bad=True)
                return
            self.say(bi("Speaking through Windows default output...|กำลังอ่านเสียงผ่านเอาต์พุตหลักของ Windows..."))

        def dj_speak_current(self):
            queue = self.dj_load_queue()
            if queue.current is None:
                self.say(bi("No current song to speak.|ไม่มีเพลงที่กำลังเล่นให้อ่านเสียง"), bad=True)
                return
            elapsed, duration = dj_playback_times()
            self.dj_speak_text(dj_status_speech(
                queue.current, queue.queue[0] if queue.queue else None, elapsed, duration))

        def dj_open_youtube_search(self):
            try:
                process = open_youtube_search(self.dj_youtube_query_var.get())
            except (OSError, ValueError) as exc:
                self.msg.configure(text=bi("YouTube search could not open: {0}|เปิดค้นหา YouTube ไม่ได้: {0}").format(exc))
                return
            self.msg.configure(text=bi(
                "Opened YouTube in a separate visible browser profile. Choose a result and press Play.|"
                "เปิด YouTube ด้วยโปรไฟล์เบราว์เซอร์แยกแล้ว เลือกผลค้นหาและกดเล่นเอง")
                + " (PID {0})".format(process.pid))

        def tab_music(self, f):
            brand = ttk.Frame(f)
            brand.pack(fill="x", pady=(0, 4))
            try:
                original = tk.PhotoImage(
                    master=self.root, file=str(app_resource_path("assets/zeazdev-room.png")))
                factor = max(1, (original.width() + 47) // 48)
                self.zeazdev_room_photo = original.subsample(factor, factor)
                ttk.Label(brand, image=self.zeazdev_room_photo).pack(side="left", padx=(0, 6))
            except (OSError, tk.TclError):
                self.zeazdev_room_photo = None
            ttk.Label(brand, text="ZeaZDev room", font=("Segoe UI", 12, "bold")).pack(side="left")
            ttk.Label(brand, text=bi("Music DJ|ดีเจเพลง"), foreground="#8b672b").pack(
                side="left", padx=8)
            g = ttk.Frame(f)
            g.pack(fill="x")
            self.field(g, "DJ enabled|เปิดใช้ DJ", ("dj", "enabled"), "bool")
            self.field(g, "Command prefix|คำสั่งขึ้นต้น", ("dj", "prefix"), width=6,
                       hint="e.g. ! !request !randommusic !queue !current !skip !help")
            self.field(g, "Queue file|ไฟล์คิว", ("dj", "queue_file"), width=24)
            self.field(g, "Max songs per user|เพลงสูงสุดต่อคน", ("dj", "max_per_user"), "int", width=6)
            self.dj_music_dir_var = self.field(g, "Music folder|โฟลเดอร์เพลง", ("dj", "music_dir"), width=24)
            music_row = g.grid_size()[1] - 1
            self.btn_dj_browse_music = ttk.Button(
                g, text=bi("Browse...|เลือก..."), command=self.dj_browse_music_folder)
            self.btn_dj_browse_music.grid(row=music_row, column=2, sticky="w", padx=4, pady=2)
            ttk.Label(g, text=bi("Windows-supported audio files|ไฟล์เสียงที่ Windows รองรับ"),
                      foreground="#666").grid(row=music_row, column=3, sticky="w")
            self.field(g, "Audio backend|เสียง", ("dj", "audio_backend"), "choice", ("chat", "local", "youtube"), 10,
                       hint="chat = announce only; local = play a file on this PC; youtube = visible player using !request URL or ID|chat = ประกาศ; local = เล่นไฟล์เสียงบนเครื่องนี้; youtube = เปิดวิดีโอในหน้าต่างผู้เล่น ใช้ !request ตามด้วย URL หรือ ID")
            self.field(g, "Now playing template|แม่แบบกำลังเล่น", ("dj", "announce_now"), width=44,
                       hint="placeholders {title} {user}")
            self.field(g, "Queued template|แม่แบบคิว", ("dj", "announce_queued"), width=44,
                       hint="placeholders {pos} {title}")
            ttk.Label(f, text=bi(
                "Chat commands: !request <song>  !randommusic  !queue  !current  !skip  !help. "
                "Local audio plays on this PC - route it into Camfrog with a virtual cable or stereo mix.|"
                "คำสั่งในแชท: !request <เพลง> !randommusic !queue !current !skip !help "
                "เสียง local เล่นบนเครื่องนี้ - ส่งเข้า Camfrog ด้วย virtual cable หรือ stereo mix"),
                foreground="#666", wraplength=900).pack(anchor="w", pady=(8, 4))
            voice_row = ttk.LabelFrame(
                f, text=bi("Speak to room with Windows voice|ใช้เสียง Windows พูดเข้าห้อง"), padding=6)
            voice_row.pack(fill="x", pady=(2, 6))
            self.dj_speech_var = tk.StringVar()
            speech_entry = ttk.Entry(voice_row, textvariable=self.dj_speech_var, width=58)
            speech_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
            speech_entry.bind("<Return>", lambda _event: self.dj_speak_text())
            ttk.Button(voice_row, text=bi("Speak text|พูดข้อความ"),
                       command=self.dj_speak_text).pack(side="left", padx=(0, 4))
            ttk.Button(voice_row, text=bi("Speak current song|พูดข้อมูลเพลงปัจจุบัน"),
                       command=self.dj_speak_current).pack(side="left")
            ttk.Label(f, text=bi(
                "Voice uses the installed Windows speech voice and default playback device. Connect that output to Camfrog's microphone input; text is limited to 240 characters.|"
                "ใช้เสียงพูดที่ติดตั้งใน Windows และอุปกรณ์เล่นเสียงหลัก ต่อเอาต์พุตนั้นเข้าไมโครโฟน Camfrog จำกัดข้อความ 240 ตัวอักษร"),
                foreground="#666", wraplength=900).pack(anchor="w", pady=(0, 4))
            youtube_row = ttk.LabelFrame(f, text=bi("YouTube assisted playback|เปิด YouTube แบบช่วยควบคุม"), padding=6)
            youtube_row.pack(fill="x", pady=(2, 6))
            self.dj_youtube_query_var = tk.StringVar()
            ttk.Entry(youtube_row, textvariable=self.dj_youtube_query_var).pack(
                side="left", fill="x", expand=True, padx=(0, 6))
            ttk.Button(youtube_row, text=bi("Open search window|เปิดหน้าต่างค้นหา"),
                       command=self.dj_open_youtube_search).pack(side="left")
            ttk.Label(f, text=bi(
                "Queue playback: set audio backend to youtube and use !request <YouTube URL or ID>. "
                "The visible player announces on play and advances on end; press Play if autoplay is blocked.|"
                "เล่นจากคิว: ตั้ง audio backend เป็น youtube แล้วใช้ !request <URL หรือ ID YouTube> "
                "player จะประกาศเมื่อเริ่มเล่นและเลื่อนคิวเมื่อจบ หาก autoplay ถูกบล็อกให้กด Play"),
                foreground="#666", wraplength=900).pack(anchor="w", pady=(0, 4))
            lf = ttk.LabelFrame(f, text=bi("Queue|คิวเพลง"), padding=6)
            lf.pack(fill="both", expand=True)
            self.dj_tree = ttk.Treeview(lf, columns=("n", "title", "user"), show="headings", height=10)
            for c, w, h in (("n", 60, "#"), ("title", 380, "song|เพลง"), ("user", 160, "requested by|ขอโดย")):
                self.dj_tree.heading(c, text=h)
                self.dj_tree.column(c, width=w, anchor="w")
            self.dj_tree.pack(side="left", fill="both", expand=True)
            row = ttk.Frame(lf)
            row.pack(side="left", anchor="n", padx=6)
            ttk.Button(row, text=bi("Skip current|ข้ามเพลงปัจจุบัน"), command=self.dj_skip).pack(fill="x", pady=1)
            ttk.Button(row, text=bi("Clear queue|ล้างคิว"), command=self.dj_clear).pack(fill="x", pady=1)
            ttk.Button(row, text=bi("Refresh|รีเฟรช"), command=self.dj_refresh).pack(fill="x", pady=1)

        def tab_advanced(self, f):
            g = ttk.Frame(f)
            g.pack(fill="x")
            self.field(g, "Language|ภาษา", ("language",), "choice", ("auto", "th", "en"), 8)
            self.field(g, "Poll seconds (0.2-60)|ความถี่ลูป", ("poll_seconds",), "float", width=8)
            self.field(g, "Hot reload config|โหลดคอนฟิกใหม่เอง", ("reload_config",), "bool")
            self.field(g, "Active hours only|ทำงานเฉพาะช่วงเวลา", ("active_hours", "enabled"), "bool")
            self.field(g, "  start (HH:MM)|  เริ่ม", ("active_hours", "start"), width=8)
            self.field(g, "  end (HH:MM)|  สิ้นสุด", ("active_hours", "end"), width=8)
            self.field(g, "Require Camfrog foreground|ต้องอยู่หน้าสุดก่อนส่ง", ("safety", "require_foreground"), "bool")
            self.field(g, "Restore previous window|คืนโฟกัสหลังส่ง", ("safety", "restore_previous_window"), "bool")
            self.field(g, "Max consecutive failures|ล้มเหลวติดกันสูงสุด", ("safety", "max_consecutive_failures"), "int", width=6)
            self.field(g, "Log level|ระดับล็อก", ("log", "level"), "choice", ("DEBUG", "INFO", "WARNING", "ERROR"), 10)
            self.field(g, "Log chat text|เก็บเนื้อข้อความใน log", ("log", "log_message_text"), "bool",
                       hint="off = privacy|ปิด = ความเป็นส่วนตัว")
            self.field(g, "Stats enabled|เก็บสถิติ", ("stats", "enabled"), "bool")
            row = ttk.Frame(f)
            row.pack(fill="x", pady=10)
            ttk.Button(row, text=bi("Start at Windows login|เริ่มเมื่อเข้า Windows"), command=lambda: self.helper("autostart-on")).pack(side="left")
            ttk.Button(row, text=bi("Remove autostart|ยกเลิก autostart"), command=lambda: self.helper("autostart-off")).pack(side="left", padx=6)

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
            self.t_skips.insert("1.0", "\n".join(d["autoreply"]["skip_patterns"]))
            self.rules = copy.deepcopy(d["autoreply"]["rules"])
            self.rules_refresh()
            for box in (self.t_skips,):
                box.edit_modified(False)
            self.dj_refresh()

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
                    self._set(d, key, strs_to_sel({k: v.get() for k, v in vars_.items()}, optional))
                except ValueError:
                    errs.append(f"{'.'.join(key)}: index")
            d["autoreply"]["skip_patterns"] = [x.strip() for x in self.t_skips.get("1.0", "end").splitlines() if x.strip()]
            d["autoreply"]["rules"] = copy.deepcopy(self.rules)
            return d, errs

        # ---- actions
        # ---- self-update from the GitHub release
        def _confirm_update(self, tag):
            from tkinter import messagebox
            return messagebox.askyesno(
                "Update available",
                "Version " + tag + " is available (running " + APP_VERSION + ").\n"
                "Download and install it now?", parent=self.root)

        def check_update(self):
            def do():
                state, message = self_update("music-dj.exe", confirm=self._confirm_update)
                print(message)
                if state == "ready":
                    print("restart the app to run the new version.")
                return 0 if state in ("ready", "no-update") else 2
            self.run_bg(do, "checked")

        def auto_update_check(self):
            """Silent banner at startup: only speak up when something is newer."""
            def probe():
                found = check_for_update("music-dj.exe")
                print("update" if found else "none")
                return 0
            self.run_bg(probe, "auto-update")

        def say(self, text, bad=False):
            self.msg.configure(text=text, foreground="#a3303a" if bad else "#28744c")

        def attach_autosave_hooks(self):
            vars_ = [var for _path, _kind, var, _label in self.binds]
            vars_.extend(v for group, _optional in self.sel_vars.values() for v in group.values())
            for var in vars_:
                trace_id = var.trace_add("write", self.schedule_autosave)
                self._autosave_traces.append((var, trace_id))
            for box in (self.t_skips,):
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
            if not getattr(self, "auto_apply_var", None) or not self.auto_apply_var.get():
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
                self.say(bi("auto-apply waiting for valid config|รอคอนฟิกที่ถูกต้อง") + ": " + errs[0], True)
                return
            running = bool(running_pid(self.model.cfg))
            if autosave_needs_confirmation(cfg, running):
                self.say(bi("LIVE bot running: press Save to confirm changes|บอทโหมดจริงกำลังทำงาน: กด Save เพื่อยืนยัน"), True)
                return
            if cfg == self.model.cfg:
                return
            try:
                errs, warns = self.model.save(cfg)
            except OSError as e:
                self.say(bi("auto-apply failed|ใช้ค่าอัตโนมัติไม่สำเร็จ") + f": {e}", True)
                return
            if errs:
                self.say(bi("auto-apply waiting for valid config|รอคอนฟิกที่ถูกต้อง") + ": " + errs[0], True)
                return
            self.draft = copy.deepcopy(cfg)
            self.say(bi("auto-applied|ใช้ค่าและบันทึกแล้ว") + (f"  ({len(warns)} warning)" if warns else ""))
            self.refresh_state()

        def save(self):
            self.cancel_autosave()
            cfg, errs = self.collect()
            if not errs:
                running = bool(running_pid(self.model.cfg))
                if autosave_needs_confirmation(cfg, running) and cfg != self.model.cfg:
                    ok = messagebox.askyesno(
                        bi("Apply LIVE changes?|ใช้การเปลี่ยนแปลงในโหมดจริง?"),
                        bi("The running bot may hot-reload these settings and send real messages. Apply them now?|"
                           "บอทที่กำลังทำงานอาจโหลดค่านี้แล้วส่งข้อความจริง ต้องการใช้ค่าตอนนี้หรือไม่?"))
                    if not ok:
                        self.say(bi("not saved|ไม่ได้บันทึก"), True)
                        return False
                errs, warns = self.model.save(cfg)
            if errs:
                messagebox.showerror(bi("Not saved|ไม่ได้บันทึก"), "\n".join(errs))
                self.say(bi("not saved|ไม่ได้บันทึก"), True)
                return False
            self.draft = copy.deepcopy(cfg)
            self.say(bi("saved|บันทึกแล้ว") + (f"  ({len(warns)} warning)" if warns else ""))
            self.refresh_state()
            return True

        def reload(self):
            self.cancel_autosave()
            try:
                self.draft = copy.deepcopy(self.model.load())
            except Exception as e:
                messagebox.showerror(APP_NAME, t("cannot_load", e=e))
                return
            set_lang(resolve_lang(self.draft["language"]))
            self.build()

        def check_cfg(self):
            cfg, errs = self.collect()
            e2, warns = ConfigModel.check(cfg) if not errs else ([], [])
            lines = errs + e2 + [t("warn", m=w) for w in warns]
            messagebox.showinfo("check", "\n".join(lines) if lines else t("cfg_ok"))

        def switch_lang(self):
            cfg, errs = self.collect()
            if not errs:
                self.draft = cfg
            self.draft["language"] = "en" if LANG == "th" else "th"
            set_lang(self.draft["language"])
            self.build()

        def live_toggled(self):
            if not self.live_var.get():
                return
            ok = messagebox.askyesno(bi("Go LIVE?|เปลี่ยนเป็นโหมดจริง?"), bi(
                "Messages WILL be sent to Camfrog. Check your room rules and Camfrog terms first.\nContinue?|"
                "จะส่งข้อความไปยัง Camfrog จริง ตรวจกฎห้องและเงื่อนไข Camfrog ก่อน\nดำเนินการต่อ?"))
            if not ok:
                self.live_var.set(False)
            self.refresh_state()

        def start(self, close=False):
            self._close_after = close
            if not self.save():
                return
            cfg = self.model.cfg
            if not cfg["dry_run"] and not messagebox.askyesno(
                    bi("LIVE|โหมดจริง"), bi("Start in LIVE mode (real messages)?|เริ่มโหมดจริง (ส่งข้อความจริง)?")):
                return
            args = SimpleNamespace(config=str(self.model.path), lang=None)
            self.run_bg(lambda: cmd_start(cfg, args), "start")

        def stop(self):
            self.run_bg(lambda: cmd_stop(self.model.cfg), "stop")

        def emergency(self):
            try:
                (BASE / self.model.cfg["safety"]["stop_file"]).write_text("stop")
                self.say(bi("STOP file written|เขียนไฟล์ STOP แล้ว"))
            except OSError as e:
                messagebox.showerror("STOP", str(e))

        def helper(self, cmd):
            cfg, errs = self.collect()
            if errs or ConfigModel.check(cfg)[0]:
                messagebox.showerror(APP_NAME, "\n".join(errs or ConfigModel.check(cfg)[0]))
                return
            setup_logging(cfg)
            fn = {"windows": lambda: cmd_windows(cfg), "discover": lambda: cmd_discover(cfg),
                  "autostart-on": lambda: cmd_autostart(SimpleNamespace(config=str(self.model.path), lang=None), True),
                  "autostart-off": lambda: cmd_autostart(SimpleNamespace(config=None, lang=None), False)}[cmd]
            self.run_bg(fn, "helper")

        def detect(self):
            """Run detection and fill selectors; auto-apply saves valid proposals."""
            cfg, errs = self.collect()
            if errs or ConfigModel.check(cfg)[0]:
                messagebox.showerror(APP_NAME, "\n".join(errs or ConfigModel.check(cfg)[0]))
                return
            setup_logging(cfg)
            self.run_bg(lambda: cmd_detect(cfg, self.model.path, False, True), "detect")

        def _discover_controls_on_start(self):
            """Discover and persist valid selectors after the standalone UI opens."""
            if self._closing:
                return
            if self.busy:
                self.root.after(1000, self._discover_controls_on_start)
                return
            self.run_bg(
                lambda: cmd_detect(self.model.cfg, self.model.path, True, True),
                "startup-detect",
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
                    rc, _ = 2, buf.write(t("win_only"))
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
                    if kind == "speech":
                        self.say(out.splitlines()[-1] if out else t("ok"), rc not in (0, None))
                        continue
                    self.busy = False
                    self.say(out.splitlines()[-1] if out else t("ok"), rc not in (0, None))
                    if kind == "start" and rc == 0 and getattr(self, "_close_after", False):
                        self.close_app()  # bot keeps running hidden; reopen the GUI any time
                        return
                    if kind == "checked" and rc == 0:
                        self.say(out.splitlines()[-1] if out else "no update.")
                        if "restart the app" in out:
                            self.root.after(1200, self.close_app)
                    if kind == "auto-update" and rc == 0:
                        text = (out.splitlines()[-1] if out else "").strip()
                        if text == "update":
                            self.check_update()
                    if kind == "startup-detect" and rc == 0:
                        self.root.after(0, self.reload)
                    if kind in ("helper", "detect") and out:
                        extra = ""
                        if kind == "detect" and rc == 0:
                            skipped = self.apply_proposal(out)
                            if skipped:
                                extra = "\n" + "\n".join(
                                    bi("kept in config (no box in this app): {0}|"
                                       "อยู่ใน config แล้ว (ไม่มีช่องในแอปนี้): {0}").format(k)
                                    for k in skipped)
                        self.set_text(self.helper_out, "\n".join(
                            ln for ln in out.splitlines() if not ln.startswith("PROPOSAL ")) + extra)
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
            self.dj_refresh()
            cfg = self.model.cfg
            txt = tail_text(BASE / cfg["log"]["file"]) if cfg["log"]["file"] else ""
            if txt != self.last_log:
                self.last_log = txt
                self.set_text(self.log, txt)
                self.log.see("end")
            if not self._closing:
                self.root.after(1500, self.tick)

        def refresh_state(self):
            cfg = self.model.cfg
            pid = running_pid(cfg)
            self.state_lbl.configure(text=(t("state_running", pid=pid) if pid else t("state_stopped")),
                                     foreground="#28744c" if pid else "#a3303a")
            live = (not cfg["dry_run"]) if not hasattr(self, "live_var") else self.live_var.get()
            self.mode_lbl.configure(text=bi("LIVE|โหมดจริง") if live else bi("DRY-RUN|โหมดทดลอง"),
                                    foreground="#9f3740" if live else "#8b672b")
            self.btn_start.state(["disabled"] if pid else ["!disabled"])
            self.btn_stop.state(["!disabled"] if pid else ["disabled"])
            s = read_stats(cfg)
            self.stats_lbl.configure(text=t("stats_line", started=s.get("started", "?"), st=s.get("statuses", 0),
                                               rp=s.get("replies", 0), fl=s.get("failures", 0)) if s else "")

        # ---- rules
        def _rule_parts(self, scope):
            return self.tree, self.rules

        def rules_refresh(self, select=None, scope="room"):
            tree, rules = self._rule_parts(scope)
            tree.delete(*tree.get_children())
            for i, r in enumerate(rules):
                th, en, anyl = reply_to_fields(r.get("reply"))
                prev = (th or en or anyl).replace("\n", " / ")
                tree.insert("", "end", iid=str(i), values=(
                    i + 1, "" if r.get("enabled", True) else "off", r.get("lang", ""),
                    "yes" if r.get("mention") else "", r.get("pattern", ""), prev))
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
                self.tout.configure(text=explain(cfg, self.tn.get().strip(), self.tm.get()).replace("\n", "  |  "), foreground="#28744c")
            except re.error as e:
                self.tout.configure(text=str(e), foreground="#a3303a")

        def mainloop(self):
            self.root.mainloop()

    return App


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if has_cli_command(argv):  # CLI passthrough (also how `start` relaunches this exe)
        return cli_main(argv)
    try:
        args = parse_gui_args(argv)
    except SystemExit as exc:
        return exc.code
    if os.name == "nt":  # crisp text on Windows 11 high-DPI displays
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    cfg_path = Path(args.config) if args.config else BASE / "config.json"
    if not cfg_path.is_absolute():
        cfg_path = BASE / cfg_path
    cfg_path = cfg_path.resolve()
    if APP_PROFILE == "music":
        try:
            cfg_path.relative_to(BASE.resolve())
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


# ---------- self-update from the GitHub release ----------
# Read-only GitHub API (no token needed for public repos, stdlib only). Each
# packaged app checks the same repo's latest release and downloads only its
# own executable asset, verified against the release's SHA256SUMS.txt.
APP_VERSION = "2.19.3"
GITHUB_REPO = os.environ.get("CAMFROG_UPDATE_REPO", "cvsz/zchat")
UPDATE_ASSET_SUMS = "SHA256SUMS.txt"


def _version_key(text):
    parts = [int(p) for p in re.findall(r"\d+", str(text))[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def version_newer(latest, current=APP_VERSION):
    """True when `latest` sorts above `current` (2.19.0 > 2.18.3)."""
    return _version_key(latest) > _version_key(current)


def _validate_github_release_url(url):
    """Accept only HTTPS URLs on GitHub's release and asset hosts."""
    allowed_hosts = {
        "github.com", "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
    try:
        parsed = urllib.parse.urlsplit(str(url))
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid GitHub release URL") from exc
    if (parsed.scheme.casefold() != "https"
            or (parsed.hostname or "").casefold() not in allowed_hosts
            or parsed.username or parsed.password
            or port not in (None, 443)):
        raise ValueError("update URL must use HTTPS on a GitHub release host")
    return str(url)


def _validate_github_response_url(response, requested_url):
    geturl = getattr(response, "geturl", None)
    final_url = geturl() if callable(geturl) else requested_url
    _validate_github_release_url(final_url)


def github_latest_release(timeout=15.0):
    """(tag, notes, {asset_name: url}) for the newest release, or None."""
    request = urllib.request.Request(
        "https://api.github.com/repos/{0}/releases/latest".format(GITHUB_REPO),
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "camfrog-auto/{0}".format(APP_VERSION)})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    tag = str(data.get("tag_name") or "").lstrip("v")
    if not tag:
        return None
    assets = {}
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name and url:
            assets[name] = url
    return tag, str(data.get("body") or ""), assets


def check_for_update(asset_name, timeout=15.0):
    """(tag, url, notes) when a newer release carries this asset, else None."""
    found = check_release_assets(asset_name, timeout)
    return None if found is None else found[:3]


def check_release_assets(asset_name, timeout=15.0):
    """Return verified GitHub release URLs only when the asset has checksums."""
    release = github_latest_release(timeout)
    if release is None:
        return None
    tag, notes, assets = release
    url = assets.get(asset_name)
    if not url or not version_newer(tag):
        return None
    sums_url = assets.get(UPDATE_ASSET_SUMS, "")
    if not sums_url:  # e.g. release-SHA256SUMS.txt alongside SHA256SUMS.txt
        sums_url = next((value for name, value in sorted(assets.items())
                         if name.endswith("SHA256SUMS.txt")), "")
    if not sums_url:
        return None
    try:
        _validate_github_release_url(url)
        _validate_github_release_url(sums_url)
    except ValueError:
        return None
    return tag, url, sums_url, notes


def self_update(asset_name, confirm=None, timeout=15.0):
    """Check -> confirm -> download -> verify -> stage the swap.

    Returns (state, message) with state in
    ("no-update", "declined", "ready", "failed"); "ready" means the new file is
    staged as <asset>.new and camfrog-update.cmd will swap it after we exit.
    """
    found = check_release_assets(asset_name, timeout)
    if found is None:
        return "no-update", "no update available (running v{0}).".format(APP_VERSION)
    tag, url, sums_url, _notes = found
    if confirm is not None and not confirm(tag):
        return "declined", "update to v{0} declined.".format(tag)
    ok, message = stage_and_swap_update(url, asset_name, sums_url or None)
    return ("ready" if ok else "failed"), message


def _sha256_file(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def download_update(url, target, timeout=60.0):
    """Stream a GitHub release asset to `target`. Returns the bytes written."""
    _validate_github_release_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": "camfrog-auto/{0}".format(APP_VERSION)})
    written = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        _validate_github_response_url(response, url)
        with open(target, "wb") as handle:
            while True:
                block = response.read(1 << 16)
                if not block:
                    break
                handle.write(block)
                written += len(block)
    return written


def verify_against_sums(sums_url, asset_name, local_path, timeout=30.0):
    """True when local_path matches the GitHub release's SHA256SUMS entry."""
    _validate_github_release_url(sums_url)
    request = urllib.request.Request(
        sums_url, headers={"User-Agent": "camfrog-auto/{0}".format(APP_VERSION)})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        _validate_github_response_url(response, sums_url)
        text = response.read().decode("utf-8", "replace")
    want = ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name = parts[1].lstrip("*")
        # Release sums may be flat or dist-relative; match either, first hit wins.
        if name == asset_name or name.endswith("/" + asset_name):
            want = parts[0].lower()
            break
    return bool(want) and want == _sha256_file(local_path)


def stage_and_swap_update(url, asset_name, sums_url=None):
    """Download the new exe beside this one, then swap it in after we exit.

    A running .exe cannot be overwritten on Windows, so the new file lands as
    `<asset>.new` and a small `camfrog-update.cmd` moves it into place a few
    seconds after this process exits. Returns (ok, message).
    """
    if not getattr(sys, "frozen", False):
        return False, "self-update only applies to the packaged executable."
    if not sums_url:
        return False, "release checksum is unavailable; update refused."
    if not isinstance(asset_name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,123}\.exe",
            asset_name, flags=re.IGNORECASE):
        return False, "invalid update asset name."
    try:
        _validate_github_release_url(url)
        _validate_github_release_url(sums_url)
    except ValueError as exc:
        return False, str(exc)
    exe = Path(sys.executable).resolve()
    staged = exe.parent / (asset_name + ".new")
    try:
        written = download_update(url, staged)
        if not written:
            raise OSError("empty download")
        if sums_url and not verify_against_sums(sums_url, asset_name, staged):
            staged.unlink(missing_ok=True)
            return False, "checksum mismatch; update refused."
    except Exception as exc:
        staged.unlink(missing_ok=True)
        return False, "download failed: {0}: {1}".format(type(exc).__name__, exc)
    script = exe.parent / "camfrog-update.cmd"
    script.write_text(
        "@echo off\r\n"
        "ping -n 4 127.0.0.1 >nul\r\n"
        'move /Y "{staged}" "{exe}"\r\n'
        'del "%~f0"\r\n'.format(staged=staged, exe=exe), encoding="utf-8")
    command = ["cmd", "/c", str(script)]
    spawn = {"cwd": str(exe.parent)}
    if os.name == "nt":  # CREATE_NO_WINDOW: no console flash during the swap
        spawn["creationflags"] = 0x08000000
    subprocess.Popen(command, close_fds=True, **spawn)
    return True, "update is staged; it will replace {0} after the app exits.".format(asset_name)


