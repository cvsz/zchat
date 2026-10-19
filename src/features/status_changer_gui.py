"""ZChat Status Changer - Random and Marquee modes with an activity log."""

import argparse
import copy
import ctypes
import datetime as dt
import hashlib
import json
import logging
import logging.handlers
import math
import os
import ntpath
import random
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional, Union
from contextlib import closing
import sqlite3
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


BASE = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else _source_base()
)

APP_PROFILE = "status"
APP_PROFILES = {"full", "room", "im_reply", "status", "music"}
UNVERIFIED_STATUS_TEST_ENV = "ZCHAT_STATUS_TEST_UNVERIFIED_SUBMIT"


def _unverified_status_test_submit_enabled():
    """Enable the opt-in local test route without changing strict capability claims."""
    return os.environ.get(UNVERIFIED_STATUS_TEST_ENV, "").strip() == "1"

# Standalone executables can pin runtime state to their own directory. Status
# workers use ZCFATO_DATA_DIR, which takes precedence over the GUI app directory.
if os.environ.get("ZCFATO_DATA_DIR"):
    BASE = Path(os.environ["ZCFATO_DATA_DIR"])
elif os.environ.get("CAMFROG_APP_DATA_DIR"):
    BASE = Path(os.environ["CAMFROG_APP_DATA_DIR"])


def _application_icon_path() -> Path:
    """Resolve the packaged UI icon independently from the user data directory."""
    candidates = []
    if getattr(sys, "frozen", False):
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir) / "app.ico")
        candidates.append(Path(sys.executable).resolve().parent / "app.ico")
    else:
        candidates.append(_source_base() / "app.ico")
    return next((path for path in candidates if path.is_file()), candidates[0])

# Nickname regex: Unicode word chars + space . - (Camfrog allows Thai in nicknames).
# LINE_RE nick capture is permissive; validation happens via NICK_RE.
NICK_RE = re.compile(r"^[\w][\w .\-]{0,31}$", re.UNICODE)
LINE_RE = re.compile(r"^(?P<nick>[^:\r\n]{1,32}):\s(?P<msg>.+)$")
CTRL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
log = logging.getLogger("camfrog_status")
LOG_TEXT = True
LANG = "en"
STATUS_STARTUP_DELAY_SECONDS = 10
WORKER_START_TIMEOUT_SECONDS = 30
WORKER_LAUNCHER_EXIT_GRACE_SECONDS = 2
WORKER_START_GATE_TIMEOUT_SECONDS = 60

# ---------- i18n (TH / EN) ----------
S = {
    "cfg_ok": ("config OK", "คอนฟิกถูกต้อง"),
    "cfg_errs": ("{n} error(s)", "พบข้อผิดพลาด {n} รายการ"),
    "cfg_hint": ("config errors (run `check`):", "คอนฟิกมีข้อผิดพลาด (รัน `check`):"),
    "cannot_load": (
        "cannot load config: {e}  (try `init` to create one)",
        "อ่านคอนฟิกไม่ได้: {e}  (ลองรัน `init` เพื่อสร้างใหม่)",
    ),
    "warn": ("WARN : {m}", "คำเตือน: {m}"),
    "err": ("ERROR: {m}", "ผิดพลาด: {m}"),
    "e_regex": ("window_title_regex invalid: {e}", "window_title_regex ไม่ถูกต้อง: {e}"),
    "e_hours": (
        "active_hours start/end must be HH:MM",
        "active_hours start/end ต้องเป็นรูปแบบ HH:MM",
    ),
    "e_msgs": ("status.messages is empty", "status.messages ว่างเปล่า"),
    "e_interval": (
        "status.interval_seconds must be >= 10 seconds",
        "status.interval_seconds ต้องไม่น้อยกว่า 10 วินาที",
    ),
    "e_sel": ("{k} must be a selector object", "{k} ต้องเป็น object ของ selector"),
    "e_delay": (
        "autoreply.delay_range_seconds must be [min, max] with 0 <= min <= max",
        "autoreply.delay_range_seconds ต้องเป็น [min, max] โดย 0 <= min <= max",
    ),
    "e_hour": (
        "autoreply.max_per_hour must be >= 1",
        "autoreply.max_per_hour ต้องไม่น้อยกว่า 1",
    ),
    "e_cool": (
        "autoreply cooldowns must be >= 0",
        "ค่า cooldown ของ autoreply ต้องไม่ติดลบ",
    ),
    "e_rule": (
        "autoreply.rules[{i}] invalid: {e}",
        "autoreply.rules[{i}] ไม่ถูกต้อง: {e}",
    ),
    "e_reply": ("autoreply.rules[{i}] has no reply", "autoreply.rules[{i}] ไม่มี reply"),
    "e_struct": (
        "config has a wrong value type or missing section: {e}",
        "คอนฟิกมีชนิดข้อมูลผิดหรือขาดส่วนที่จำเป็น: {e}",
    ),
    "e_lang": ("language must be auto, th or en", "language ต้องเป็น auto, th หรือ en"),
    "e_mode": (
        "status.language_mode must be th, en, both or alternate",
        "status.language_mode ต้องเป็น th, en, both หรือ alternate",
    ),
    "w_norules": (
        "autoreply.rules is empty (nothing will be replied)",
        "autoreply.rules ว่างเปล่า (จะไม่มีการตอบ)",
    ),
    "w_nick": (
        "autoreply.own_nickname is empty (required for live mode)",
        "autoreply.own_nickname ว่าง (จำเป็นเมื่อรันจริง)",
    ),
    "w_live": (
        "dry_run is false: messages WILL be sent",
        "dry_run เป็น false: จะส่งข้อความจริง",
    ),
    "dry_send": ("[dry-run] would send: {text}", "[ทดลอง] จะส่ง: {text}"),
    "mismatch": ("read-back mismatch; not sending", "ข้อความที่อ่านกลับไม่ตรง จึงไม่ส่ง"),
    "not_fg": ("Camfrog not foreground; not sending", "Camfrog ไม่ได้อยู่หน้าสุด จึงไม่ส่ง"),
    "no_window": (
        "Camfrog window not found (check window_title_regex)",
        "ไม่พบหน้าต่าง Camfrog (ตรวจ window_title_regex)",
    ),
    "no_ctrl": (
        "control not found: {spec} (matches={n})",
        "ไม่พบ control: {spec} (พบ {n} รายการ)",
    ),
    "status_set": ("status submission dispatched (provider outcome unverified): {text}", "ส่งคำสั่ง status แล้ว (ยังไม่ยืนยันผลจาก Camfrog): {text}"),
    "bg_status_client_confirmed": (
        "Camfrog client shows the status (external state unverified): {text}",
        "หน้าต่าง Camfrog แสดงสถานะนี้แล้ว (ยังไม่ยืนยันสถานะภายนอก): {text}",
    ),
    "bg_status_failed": (
        "background status failed (state={state}, code={code})",
        "ตั้งสถานะเบื้องหลังไม่สำเร็จ (state={state}, code={code})",
    ),
    "status_slot_set": (
        "status slot {slot}/{total} dispatched (provider outcome unverified): {text}",
        "ส่งคำสั่ง status ช่อง {slot}/{total} แล้ว (ยังไม่ยืนยันผลจาก Camfrog): {text}",
    ),
    "status_fail": ("status commit failed", "ตั้ง status ไม่สำเร็จ"),
    "reply_fail": ("reply commit failed", "ส่งข้อความตอบไม่สำเร็จ"),
    "replied": ("replied to {nick}: {text}", "ตอบ {nick} แล้ว: {text}"),
    "refuse_live": (
        "refusing to run live: set autoreply.own_nickname first",
        "ไม่ยอมรันจริง: ตั้ง autoreply.own_nickname ก่อน",
    ),
    "running": (
        "running (dry_run={dry}). Stop: Ctrl+C or `stop` / {stop}",
        "กำลังทำงาน (dry_run={dry}) หยุดด้วย Ctrl+C หรือคำสั่ง `stop` / ไฟล์ {stop}",
    ),
    "stopped": ("stopped", "หยุดทำงานแล้ว"),
    "interrupted": ("interrupted", "ถูกขัดจังหวะ"),
    "fatal": ("fatal error", "เกิดข้อผิดพลาดร้ายแรง"),
    "tick_failed": ("tick failed ({n}/{m})", "รอบทำงานล้มเหลว ({n}/{m})"),
    "too_many": (
        "too many consecutive failures; stopping",
        "ล้มเหลวติดต่อกันมากเกินไป จึงหยุด",
    ),
    "re_resolve": ("re-attach failed: {e}", "จับหน้าต่างใหม่ไม่สำเร็จ: {e}"),
    "discover_done": (
        "wrote {p}. Copy auto_id/class_name/index into config.json selectors.",
        "เขียนไฟล์ {p} แล้ว คัดลอก auto_id/class_name/index ไปใส่ selector ใน config.json",
    ),
    "win_hdr": (
        "visible windows (title | class | PID); * = matches window_title_regex:",
        "หน้าต่างที่เห็น (ชื่อ | class | PID) เครื่องหมาย * = ตรงกับ window_title_regex:",
    ),
    "win_none": ("no visible windows found", "ไม่พบหน้าต่างที่มองเห็น"),
    "win_hint": (
        "Camfrog not listed? Open it (not minimized to tray) and run as the same user / same admin level. "
        "If its title has no 'Camfrog', set window_title_regex in config.json to part of the title above.",
        "ไม่เห็น Camfrog? เปิดโปรแกรมให้เห็นหน้าต่าง (ไม่ซ่อนในถาดระบบ) และรันด้วยผู้ใช้/สิทธิ์ระดับเดียวกัน "
        "ถ้าชื่อหน้าต่างไม่มีคำว่า Camfrog ให้แก้ window_title_regex ใน config.json เป็นส่วนหนึ่งของชื่อด้านบน",
    ),
    "det_none": (
        "detect: found no candidate controls. Open a chat room (not tray-minimized) and retry.",
        "detect: ไม่พบ control ที่ใช่ เปิดห้องแชทให้เห็นหน้าต่างแล้วลองใหม่",
    ),
    "det_found": ("detect: {k} -> {sel}  [{why}]", "detect: {k} -> {sel}  [{why}]"),
    "det_miss": ("detect: {k} not found", "detect: ไม่พบ {k}"),
    "det_applied": (
        "detect: wrote selectors to {p}. Run `check`, then test with dry_run on.",
        "detect: เขียน selector ลง {p} แล้ว รัน `check` แล้วทดสอบด้วย dry_run",
    ),
    "det_report": (
        "detect: structure report (no chat text) -> {p}",
        "detect: รายงานโครงสร้าง (ไม่มีข้อความแชท) -> {p}",
    ),
    "discover_timeout": (
        "Control discovery timed out after {seconds} seconds. Make sure Camfrog is open and try again.",
        "ค้นหา control เกิน {seconds} วินาที ตรวจว่าเปิด Camfrog อยู่แล้วลองใหม่",
    ),
    "discover_failed": (
        "Control discovery failed (exit code {code}).",
        "ค้นหา control ไม่สำเร็จ (exit code {code})",
    ),
    "no_room": (
        "no chat-room window yet (status still works). Open a room; it is picked up automatically.",
        "ยังไม่พบหน้าต่างห้องแชท (ตั้ง status ได้ตามปกติ) เปิดห้องแชทแล้วจะจับให้เอง",
    ),
    "room_attached": (
        "attached to chat room window {title}",
        "จับหน้าต่างห้องแชทแล้ว {title}",
    ),
    "ok": ("ok", "สำเร็จ"),
    "failed": ("failed", "ล้มเหลว"),
    "already_running": ("already running (PID {pid})", "ทำงานอยู่แล้ว (PID {pid})"),
    "bg_started": (
        "started in background (PID {pid}). Log: {log}",
        "เริ่มทำงานเบื้องหลังแล้ว (PID {pid}) ล็อก: {log}",
    ),
    "bg_failed": (
        "background process exited (code {code}). See log: {log}",
        "โปรเซสเบื้องหลังจบการทำงาน (code {code}) ดูล็อก: {log}",
    ),
    "bg_needs_log": (
        "background mode needs log.file set in config",
        "โหมดเบื้องหลังต้องตั้ง log.file ใน config",
    ),
    "win_only": ("this command is Windows-only", "คำสั่งนี้ใช้ได้เฉพาะ Windows"),
    "not_running": ("not running", "ไม่ได้ทำงานอยู่"),
    "stopping": ("stopping PID {pid} ...", "กำลังหยุด PID {pid} ..."),
    "stopped_ok": ("stopped", "หยุดแล้ว"),
    "killed": ("did not stop in time; terminated", "หยุดไม่ทันเวลา จึงบังคับปิด"),
    "stop_failed": (
        "could not confirm process exit (PID {pid}); STOP and PID files were kept so you can retry",
        "ยืนยันไม่ได้ว่าโปรเซสหยุดแล้ว (PID {pid}) จึงเก็บไฟล์ STOP/PID ไว้ให้ลองใหม่",
    ),
    "state_running": ("RUNNING (PID {pid})", "กำลังทำงาน (PID {pid})"),
    "state_stopped": ("STOPPED", "หยุดอยู่"),
    "autostart_on": ("autostart enabled: {cmd}", "เปิดการรันอัตโนมัติแล้ว: {cmd}"),
    "autostart_off": ("autostart removed", "ยกเลิกการรันอัตโนมัติแล้ว"),
    "autostart_none": ("autostart was not set", "ไม่ได้ตั้งการรันอัตโนมัติไว้"),
    "e_sched": (
        "status.schedules[{i}] invalid (needs start/end HH:MM and non-empty messages)",
        "status.schedules[{i}] ไม่ถูกต้อง (ต้องมี start/end แบบ HH:MM และ messages ไม่ว่าง)",
    ),
    "e_skip": (
        "autoreply.skip_patterns[{i}] invalid: {e}",
        "autoreply.skip_patterns[{i}] ไม่ถูกต้อง: {e}",
    ),
    "w_mention": (
        "rule #{i} uses `mention` but own_nickname is empty (it will never match)",
        "กฎ #{i} ใช้ `mention` แต่ own_nickname ว่าง (จะไม่ตรงเลย)",
    ),
    "reloaded": ("config reloaded", "โหลดคอนฟิกใหม่แล้ว"),
    "reload_rejected": ("config reload rejected: {m}", "ไม่รับคอนฟิกที่แก้ใหม่: {m}"),
    "init_done": ("wrote {p}", "สร้างไฟล์ {p} แล้ว"),
    "init_exists": (
        "{p} already exists (use --force to overwrite)",
        "{p} มีอยู่แล้ว (ใช้ --force เพื่อเขียนทับ)",
    ),
    "tr_lang": ("detected language: {l}", "ภาษาที่ตรวจพบ: {l}"),
    "tr_skip": ("SKIPPED ({why})", "ข้าม ({why})"),
    "tr_match": ("rule #{i} -> {reply}", "กฎ #{i} -> {reply}"),
    "tr_none": ("no rule matches", "ไม่มีกฎที่ตรง"),
    "tr_blocked": ("(blocked: empty or starts with /)", "(ถูกบล็อก: ว่างหรือขึ้นต้นด้วย /)"),
    "tr_badline": (
        "ignored line (not `name: message`): {line}",
        "ข้ามบรรทัด (ไม่ใช่รูปแบบ `ชื่อ: ข้อความ`): {line}",
    ),
    "why_sender": ("sender filtered", "กรองผู้ส่ง"),
    "why_link": ("contains a link", "มีลิงก์"),
    "why_pattern": ("matches skip_patterns", "ตรงกับ skip_patterns"),
    "e_marquee": (
        "status.marquee invalid: width 8-80 (and <= status.max_length), stride 1..width, step_seconds >= 0.3 seconds, cycles 1-5, max_frames 5-300",
        "status.marquee ไม่ถูกต้อง: width 8-80 (และไม่เกิน status.max_length), stride 1..width, step_seconds >= 0.3 วินาที, cycles 1-5, max_frames 5-300",
    ),
    "e_cycle": (
        "status.language_cycle items must be th or en",
        "status.language_cycle ต้องเป็น th หรือ en เท่านั้น",
    ),
    "e_hmode": (
        "status.history.mode must be rotate, random or most_used",
        "status.history.mode ต้องเป็น rotate, random หรือ most_used",
    ),
    "e_hmax": (
        "status.history.max_items must be 5-500 and file must be set",
        "status.history.max_items ต้อง 5-500 และต้องตั้ง file",
    ),
    "w_sched_hist": (
        "status.schedules are ignored while status.history.use_as_source is true",
        "status.schedules จะไม่ถูกใช้ขณะที่ status.history.use_as_source เป็น true",
    ),
    "status_blank": ("chosen status is blank; skipped", "status ที่เลือกว่างเปล่า จึงข้าม"),
    "hist_empty": ("history is empty", "ประวัติว่างเปล่า"),
    "hist_item": (
        "{i:>3}. [{lang}] x{n}  {text}",
        "{i:>3}. [{lang}] ใช้ {n} ครั้ง  {text}",
    ),
    "hist_added": ("added to history", "เพิ่มเข้าประวัติแล้ว"),
    "hist_exists": ("already in history (or blank)", "มีในประวัติแล้ว (หรือข้อความว่าง)"),
    "hist_imported": ("imported {n} new status(es)", "นำเข้า status ใหม่ {n} รายการ"),
    "mq_frames": (
        "{n} frame(s), {s}s apart -> about {t}s total",
        "{n} เฟรม ห่างกัน {s} วินาที -> รวมประมาณ {t} วินาที",
    ),
    "mq_static": (
        "text fits in the window: no scrolling, shown as-is",
        "ข้อความสั้นพอดีหน้าต่าง: ไม่เลื่อน แสดงตามเดิม",
    ),
    "e_range": ("{k} is out of range ({lo}..{hi})", "{k} อยู่นอกช่วงที่ยอมรับ ({lo}..{hi})"),
    "w_selclash": (
        "selectors {a} and {b} are identical: they point at the same control (run `discover`)",
        "selector {a} กับ {b} เหมือนกัน คือ control ตัวเดียวกัน (รัน `discover`)",
    ),
    "e_selclash": (
        "live mode refused: selectors {a} and {b} are identical (run `discover` and fix config.json)",
        "ไม่ยอมรันจริง: selector {a} กับ {b} เหมือนกัน (รัน `discover` แล้วแก้ config.json)",
    ),
    "burst_skip": (
        "{n} new chat lines at once (room switch / history reload?): not answered",
        "มีข้อความใหม่ {n} บรรทัดพร้อมกัน (เปลี่ยนห้อง/โหลดประวัติ?) จึงไม่ตอบ",
    ),
    "e_selempty": (
        "{k} has no criteria (set control_type, auto_id, class_name or title)",
        "{k} ไม่มีเงื่อนไขเลย (ใส่ control_type, auto_id, class_name หรือ title)",
    ),
    "e_im_only": (
        "autoreply_im.only_nicknames must list at least one friend (IM auto-reply never answers 'everyone')",
        "autoreply_im.only_nicknames ต้องระบุเพื่อนอย่างน้อย 1 คน (ตอบ IM แบบ 'ทุกคน' ไม่ได้)",
    ),
    "e_im_nick": (
        "autoreply_im needs autoreply.own_nickname (otherwise the bot would answer its own replies)",
        "autoreply_im ต้องตั้ง autoreply.own_nickname (ไม่งั้นบอทจะตอบตัวเอง)",
    ),
    "e_im_kind": (
        "autoreply.log_kind must be room while autoreply_im is enabled (room rules must never answer private chats)",
        "autoreply.log_kind ต้องเป็น room ขณะเปิด autoreply_im (กฎห้องห้ามตอบแชทส่วนตัว)",
    ),
    "e_im_prefix": (
        "autoreply_im.prefix must be text and must not start with /",
        "autoreply_im.prefix ต้องเป็นข้อความและห้ามขึ้นต้นด้วย /",
    ),
    "e_im_rule": (
        "autoreply_im.rules[{i}] invalid: {e}",
        "autoreply_im.rules[{i}] ไม่ถูกต้อง: {e}",
    ),
    "e_im_skip": (
        "autoreply_im.skip_patterns[{i}] invalid: {e}",
        "autoreply_im.skip_patterns[{i}] ไม่ถูกต้อง: {e}",
    ),
    "e_im_delay": (
        "autoreply_im.delay_range_seconds must be [min, max] with 0 <= min <= max",
        "autoreply_im.delay_range_seconds ต้องเป็น [min, max] โดย 0 <= min <= max",
    ),
    "w_im_norules": (
        "autoreply_im has no rules: nothing will be answered",
        "autoreply_im ไม่มีกฎ: จะไม่ตอบอะไรเลย",
    ),
    "w_im_noprefix": (
        "autoreply_im.prefix is empty: friends cannot tell the reply is automatic",
        "autoreply_im.prefix ว่าง: เพื่อนจะไม่รู้ว่าเป็นข้อความตอบอัตโนมัติ",
    ),
    "im_attached": (
        "attached to private-chat window {title}",
        "จับหน้าต่างแชทส่วนตัวแล้ว {title}",
    ),
    "im_replied": ("IM reply to {nick}: {text}", "ตอบ IM {nick} แล้ว: {text}"),
    "refuse_im": (
        "refusing to run: autoreply_im needs autoreply.own_nickname",
        "ไม่ยอมรัน: autoreply_im ต้องตั้ง autoreply.own_nickname",
    ),
    "why_bot": (
        "looks like an automatic reply (prefix)",
        "ดูเหมือนข้อความตอบอัตโนมัติ (prefix)",
    ),
    "e_im_kinds": (
        "autoreply_im.log_kinds must be a non-empty list of im / mtim",
        "autoreply_im.log_kinds ต้องเป็นรายการของ im / mtim อย่างน้อย 1 ค่า",
    ),
    "w_im_mtim": (
        "autoreply_im.log_kinds includes mtim: that window kind is unverified on a live Camfrog (run `im-probe`)",
        "autoreply_im.log_kinds มี mtim: หน้าต่างชนิดนี้ยังไม่ได้ทดสอบกับ Camfrog จริง (รัน `im-probe`)",
    ),
    "im_on": (
        "IM auto-reply ON: {mode}, friends={n}, rules={r}, kinds={k}",
        "ตอบ IM อัตโนมัติ: เปิด {mode} เพื่อน={n} กฎ={r} ชนิด={k}",
    ),
    "im_off": (
        "IM auto-reply is OFF (autoreply_im.enabled=false)",
        "ตอบ IM อัตโนมัติ: ปิดอยู่ (autoreply_im.enabled=false)",
    ),
    "im_none": (
        "no private-chat window found yet. Open one, or run `im-probe` to see how Camfrog's windows are classified",
        "ยังไม่พบหน้าต่างแชทส่วนตัว เปิดหน้าต่างแชท หรือรัน `im-probe` เพื่อดูว่า Camfrog แต่ละหน้าต่างเป็นชนิดไหน",
    ),
    "im_skip_nick": (
        "IM line from {nick} ignored: not in autoreply_im.only_nicknames",
        "ข้ามข้อความ IM จาก {nick}: ไม่อยู่ใน autoreply_im.only_nicknames",
    ),
    "stats_hdr": ("stats:", "สถิติ:"),
    "stats_line": (
        "  started {started} | statuses {st} | replies {rp} | failures {fl}",
        "  เริ่ม {started} | ตั้ง status {st} | ตอบ {rp} | ล้มเหลว {fl}",
    ),
}


def t(key: str, **kw: Any) -> str:
    en, th = S[key]
    return (th if LANG == "th" else en).format(**kw)


def resolve_lang(pref: Optional[str]) -> str:
    pref = (pref or "auto").lower()
    if pref in ("th", "en"):
        return pref
    try:
        if (
            os.name == "nt"
            and (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x1E
        ):
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
    "log": {
        "level": "INFO",
        "file": "camfrog_auto.log",
        "max_bytes": 1000000,
        "backups": 3,
        "log_message_text": True,
    },
    "active_hours": {"enabled": False, "start": "09:00", "end": "23:30"},
    "safety": {
        "stop_file": "STOP",
        "pid_file": "camfrog_auto.pid",
        "require_foreground": True,
        "restore_previous_window": True,
        "max_consecutive_failures": 5,
    },
    "status": {
        "enabled": True,
        "execution_mode": "strict_background",
        "edit": {"class_name": "Edit", "auto_id": "1001", "index": 0},
        "apply_button": None,
        "background_enter_target": "edit",  # experimental "combo" targets the parent CComboBoxTS
        "interval_seconds": 600,
        "set_on_start": True,
        "random": False,
        "language_mode": "both",
        "retry_seconds": 30,
        "max_length": 120,
        "messages": [],
        "schedules": [],
        "language_cycle": [],
        "marquee": {
            "enabled": False,
            "scroll": False,
            "width": 28,
            "stride": 2,
            "step_seconds": 0.5,
            "separator": "   \u2022   ",
            "cycles": 1,
            "max_frames": 80,
            "infinite_loop": False,
            "loop_slots": False,
        },
        "history": {
            "enabled": True,
            "file": "status_history.json",
            "max_items": 50,
            "record": True,
            "use_as_source": False,
            "seed_from_messages": True,
            "mode": "rotate",
        },
    },
    "autoreply": {
        "enabled": True,
        "own_nickname": "",
        "window_title_regex": "",
        "log_kind": "room",
        "history_tail": 150,
        "history": {"class_name": "AtlAxWinLic140", "auto_id": "1002", "index": 0},
        "input": {"class_name": "AtlAxWinLic140", "auto_id": "1002", "index": 1},
        "ignore_nicknames": [],
        "only_nicknames": [],
        "per_sender_cooldown_seconds": 300,
        "global_min_gap_seconds": 20,
        "max_per_hour": 30,
        "delay_range_seconds": [2.0, 5.0],
        "max_reply_length": 200,
        "max_incoming_length": 500,
        "rules": [],
        "skip_patterns": [],
        "ignore_links": True,
    },
    # Private-message (IM) auto-reply: separate opt-in, own rules, own limits. Reuses the
    # autoreply.history / autoreply.input selectors (IM windows have the same two web panes).
    "autoreply_im": {
        "enabled": False,
        "dry_run": True,
        "window_title_regex": "",
        "max_windows": 5,
        "log_kinds": ["im"],
        "only_nicknames": [],
        "rules": [],
        "skip_patterns": [],
        "ignore_links": True,
        "prefix": "[auto] ",
        "answer_first_message": True,
        "per_sender_cooldown_seconds": 3600,
        "max_per_sender_per_day": 3,
        "global_min_gap_seconds": 20,
        "max_per_hour": 10,
        "delay_range_seconds": [2.0, 5.0],
        "max_reply_length": 200,
        "max_incoming_length": 500,
    },
    # Room music DJ: chat commands (!request/!queue/!current/!skip/!help) with a
    # persistent queue. Needs autoreply.enabled for chat plumbing. Audio backends:
    # "chat" announces only; "local" also plays .wav files on this machine
    # (route them into Camfrog with a virtual cable / stereo mix for the room
    # to hear; MP3 needs converting to WAV first).
    "dj": {
        "enabled": False,
        "prefix": "!",
        "queue_file": "dj_queue.json",
        "max_per_user": 3,
        "music_dir": "music",
        "audio_backend": "chat",
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
        status = result.setdefault("status", {})
        if _unverified_status_test_submit_enabled():
            # Keep the explicit local test route consistent across parent and worker.
            status["execution_mode"] = "interactive"
            status["background_enter_target"] = "combo"
        elif status.get("execution_mode") != "best_effort_background":
            # Normal standalone workers remain fail-closed.
            status["execution_mode"] = "strict_background"
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
        config = deep_merge(DEFAULTS, json.load(f))
    status = config.get("status", {})
    try:
        interval = float(status.get("interval_seconds", RANDOM_INTERVAL_MIN))
        if math.isfinite(interval):
            status["interval_seconds"] = max(RANDOM_INTERVAL_MIN, interval)
    except (TypeError, ValueError, OverflowError):
        pass
    marquee = status.get("marquee", {})
    try:
        step = float(marquee.get("step_seconds", MARQUEE_STEP_MIN))
        if math.isfinite(step):
            marquee["step_seconds"] = max(MARQUEE_STEP_MIN, step)
    except (TypeError, ValueError, OverflowError):
        pass
    return config


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
        sels += [
            ("autoreply.history", cfg["autoreply"]["history"]),
            ("autoreply.input", cfg["autoreply"]["input"]),
        ]
    out = []
    for i, (na, a) in enumerate(sels):
        for nb, b in sels[i + 1 :]:
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
        to_check += [
            ("autoreply.history", ar["history"]),
            ("autoreply.input", ar["input"]),
        ]
    for name, sel in to_check:
        if isinstance(sel, dict) and not any(
            sel.get(k) not in (None, "") for k in SEL_KEYS
        ):
            errs.append(t("e_selempty", k=name))
    for na, nb in selector_clashes(cfg):
        (errs if not cfg["dry_run"] else warns).append(
            t("e_selclash" if not cfg["dry_run"] else "w_selclash", a=na, b=nb)
        )
    if ar.get("log_kind", "room") not in ("room", "im", "any"):
        errs.append("autoreply.log_kind must be room, im or any")
    if str(cfg["language"]).lower() not in ("auto", "th", "en"):
        errs.append(t("e_lang"))
    try:
        re.compile(cfg["window_title_regex"])
    except re.error as e:
        errs.append(t("e_regex", e=e))
    if cfg["active_hours"]["enabled"]:
        try:
            parse_hm(cfg["active_hours"]["start"])
            parse_hm(cfg["active_hours"]["end"])
        except Exception:
            errs.append(t("e_hours"))
    if st["enabled"]:
        if not st["messages"]:
            errs.append(t("e_msgs"))
        if st["interval_seconds"] < RANDOM_INTERVAL_MIN:
            errs.append(t("e_interval"))
        if st["language_mode"] not in ("th", "en", "both", "alternate"):
            errs.append(t("e_mode"))
        if not isinstance(st["edit"], dict):
            errs.append(t("e_sel", k="status.edit"))
        if st.get("background_enter_target", "edit") not in ("edit", "combo"):
            errs.append("status.background_enter_target must be edit or combo")
        if st.get("execution_mode", "interactive") not in (
            "interactive",
            "strict_background",
            "best_effort_background",
        ):
            errs.append(
                "status.execution_mode must be interactive, strict_background, "
                "or best_effort_background"
            )
        mq, hs = st["marquee"], st["history"]
        try:
            inf = mq.get("infinite_loop", False)
            scroll = mq.get("scroll", False)
            if (
                mq["enabled"]
                and scroll
                and not (
                    8 <= mq["width"] <= 80
                    and mq["width"] <= st["max_length"]
                    and 1 <= mq["stride"] <= mq["width"]
                    and mq["step_seconds"] >= MARQUEE_STEP_MIN
                    and (inf or (1 <= mq["cycles"] <= 5))
                    and 5 <= mq["max_frames"] <= 300
                )
            ):
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
                parse_hm(sc["start"])
                parse_hm(sc["end"])
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
    if dj.get("audio_backend", "chat") not in ("chat", "local"):
        errs.append('dj.audio_backend must be "chat" or "local"')
    for key in ("announce_now", "announce_queued"):
        if not isinstance(dj.get(key), str) or not dj[key]:
            errs.append(f"dj.{key} must be a non-empty string")
    if not dj.get("queue_file"):
        errs.append("dj.queue_file must be set")


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
        if title and title.startswith(
            (
                "ZChat Status Changer",
                "ZChat Random Status",
                "ZChat Marquee Status",
            )
        ):
            return True
    except Exception:
        pass
    return False


def get_status_window(cfg, desktop_factory):
    """Use fast Win32 title enumeration, then attach UIA only to Camfrog candidates."""
    # Status rotation is allowed to target a minimized or explicitly hidden
    # Camfrog window. The exact configured Edit selector is still required
    # before accepting a candidate, so unrelated hidden windows are rejected.
    windows = desktop_factory(backend="win32").windows(
        title_re=cfg["window_title_regex"], visible_only=False
    )
    windows = [win for win in windows if not is_own_gui_window(win)]
    windows = [win for win in windows if _window_process_id(win) != os.getpid()]
    if not windows:
        raise RuntimeError(t("no_window"))
    desktop = desktop_factory(backend="uia")
    selector = cfg.get("status", {}).get("edit", {})
    matches = []
    for native_window in windows:
        try:
            window = desktop.window(handle=native_window.handle).wrapper_object()
            if selector:
                find(window, selector)
            matches.append((native_window, window))
        except Exception:
            continue

    if matches:
        # Broad title filters may include other Camfrog clients or auxiliary
        # windows. First identify windows that actually own the configured
        # status control, then fail closed only if that target remains
        # ambiguous across processes.
        _require_single_window_process([native for native, _window in matches])
        return matches[0][1]

    # Keep setup/detection useful when the saved selector no longer matches,
    # but only when the title filter still identifies one verified process.
    _require_single_window_process(windows)

    # Preserve the old setup behavior: let detection inspect the first Camfrog
    # candidate and report that the saved selector may need replacing.
    log.warning(
        "Status edit control not found in any matching window; using first window"
    )
    return desktop.window(handle=windows[0].handle).wrapper_object()


def _window_process_id(window):
    """Return a positive process ID for a UIA top-level window, or None."""
    try:
        pid = int(window.process_id())
        return pid if pid > 0 else None
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def _require_single_window_process(windows):
    """Fail closed when a title filter spans more than one/unverifiable process.

    Every candidate must resolve to a positive, identical process ID. A single
    window with an unverifiable (missing or invalid) PID is rejected rather than
    accepted silently, so automation never targets an arbitrary Camfrog account.
    """
    pids = [_window_process_id(window) for window in windows]
    if any(pid is None for pid in pids) or len(set(pids)) != 1:
        raise RuntimeError(
            "Multiple or unverifiable Camfrog processes match the configured window filter; "
            "narrow window_title_regex or close extra clients."
        )


def get_window(cfg: dict):
    from pywinauto import Desktop  # lazy: `check` works without pywinauto

    if APP_PROFILE == "status":
        return get_status_window(cfg, Desktop)

    wins = Desktop(backend="uia").windows(
        title_re=cfg["window_title_regex"], visible_only=True
    )
    wins = [w for w in wins if not is_own_gui_window(w)]
    wins = [w for w in wins if _window_process_id(w) != os.getpid()]
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
    log.warning(
        "Status edit control not found in any matching window; using first window"
    )
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
            flush()
            nick, parts, want_child = None, [], False
        elif ty == "Hyperlink":
            flush()
            parts = []
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
            out.append(
                {
                    "type": ty,
                    "name": "" if ty == "Image" else str(_ctrl_attr(c, "name")),
                }
            )
    return out[-tail:] if tail else out


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
        return "\n".join(parse_web_log(web_items(ctrl, tail)))
    return read_text(ctrl)


def _wm_settext(ctrl, text):
    """Last resort for real HWND edit/richedit controls (Camfrog's input is RICHEDIT50W)."""
    WM_SETTEXT = 0x000C
    u = ctypes.windll.user32
    u.SendMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
    ]
    u.SendMessageW(ctrl.handle, WM_SETTEXT, None, text)


def write_text(ctrl, text: str) -> bool:
    for fn in (
        lambda t_: ctrl.set_edit_text(t_),
        lambda t_: ctrl.iface_value.SetValue(t_),
        lambda t_: _wm_settext(ctrl, t_),
    ):
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
    k.GlobalAlloc.restype, k.GlobalAlloc.argtypes = (
        ctypes.c_void_p,
        [ctypes.c_uint, ctypes.c_size_t],
    )
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


def commit_web(
    win, ctrl, text: str, require_fg: bool = True, restore: bool = False
) -> bool:
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
        keyboard.send_keys(
            "^v" if pasted else _escape_keys(text), pause=0.01, with_spaces=True
        )
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
            raise LookupError(
                "Status Edit has no CComboBoxTS parent; cannot try combo Enter"
            )
        handle = getattr(parent, "handle", 0)
    else:
        handle = getattr(ctrl, "handle", 0)
        if not handle and hasattr(ctrl, "element_info"):
            handle = getattr(ctrl.element_info, "handle", 0)
        if not handle:
            raise LookupError(
                "Status Edit has no HWND; refusing keyboard-input fallback"
            )
    if not handle:
        raise LookupError("Status combo has no HWND; cannot try combo Enter")

    u = ctypes.windll.user32
    try:
        u.PostMessageW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        u.PostMessageW.restype = ctypes.c_int
    except (AttributeError, TypeError):
        pass

    # Match the tested probe route: scan code 0x1c and key-transition bits.
    if not u.PostMessageW(handle, 0x0100, 0x0D, 0x001C0001):
        raise RuntimeError("Could not post Enter key-down to status control")
    if not u.PostMessageW(handle, 0x0101, 0x0D, 0xC01C0001):
        raise RuntimeError("Could not post Enter key-up to status control")
    if os.name == "nt":
        time.sleep(0.02)


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
        try:
            kernel32 = getattr(getattr(ctypes, "windll", None), "kernel32", None)
            if kernel32 and hasattr(kernel32, "GetTickCount"):
                return kernel32.GetTickCount() & 0xFFFFFFFF
        except Exception:
            pass
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    @staticmethod
    def _win_last_input():
        if os.name != "nt":
            return None
        try:
            user32 = getattr(getattr(ctypes, "windll", None), "user32", None)
            if not user32 or not hasattr(user32, "GetLastInputInfo"):
                return None

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            if not user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            return info.dwTime
        except Exception:
            return None

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
        """Wait for idle input; refuse unsafe injection when Windows input is unknown."""
        if self.idle_ms <= 0:
            return True
        deadline = self._clock() + self.max_wait
        noted = False
        while True:
            idle = self.user_idle_ms()
            if idle is None:
                if os.name == "nt":
                    log.error("Cannot verify Windows user activity; refusing desktop input injection")
                    return False
                return True
            if idle >= self.idle_ms:
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


def _commit_impl(
    win,
    ctrl,
    text: str,
    dry: bool,
    require_fg: bool = True,
    apply_btn=None,
    restore: bool = False,
    background_enter_target: str = "edit",
    background_if_not_foreground: bool = False,
    execution_mode: str = "interactive",
) -> bool:
    """Write and submit status text, requiring an actual Edit read-back before Enter.

    When execution_mode is "strict_background", delegates to commit_background() which
    uses camfrog_status_engine.BackgroundStatusExecutor and dispatches targeted window
    messages without global input injection, clipboard mutation, or sound-registry
    changes. Camfrog may still activate a window while handling submission; the strict
    executor rejects results when its foreground checks cannot confirm isolation.
    """
    if execution_mode == "strict_background":
        return commit_background(text, dry=dry)
    if dry:
        log.info(t("dry_send", text=shown(text)))
        return True
    if execution_mode == "best_effort_background":
        log.error(
            "BEST_EFFORT_BACKGROUND_DISABLED: Camfrog combo-post can take "
            "desktop focus and interrupt keyboard input; refusing status send."
        )
        return False
    if is_web(ctrl):
        return commit_web(win, ctrl, text, require_fg, restore)
    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    window_is_foreground = foreground == getattr(win, "handle", None)
    if apply_btn is None and not window_is_foreground and (
        background_if_not_foreground
        or not require_fg
        or background_enter_target == "combo"
    ):
        log.error(
            "Refusing targeted background status submit: Camfrog can activate "
            "and take desktop focus."
        )
        return False
    # A status update should not raise Camfrog over the user's current window.
    # If Camfrog is not foreground, write to its verified Edit and submit via
    # its verified CComboBoxTS parent. Explicit combo mode uses the same path
    # even while Camfrog is foreground (useful on builds that beep on Edit Enter).
    unverified_test_submit = _unverified_status_test_submit_enabled()
    best_effort = execution_mode == "best_effort_background"
    background_submit = apply_btn is None and (
        best_effort
        or (unverified_test_submit and background_if_not_foreground)
        or background_enter_target == "combo"
        or not require_fg
        or (background_if_not_foreground and not window_is_foreground)
    )
    submit_target = "combo" if best_effort else background_enter_target
    # Always submit through the verified combo parent. Posting Enter directly
    # to Camfrog's child Edit can produce a Windows ding on supported builds.
    # Combo submission has independently observed foreground activation, so it
    # remains experimental and does not qualify for strict-background mode.
    if background_submit and submit_target == "edit":
        submit_target = "combo"
    needs_foreground = require_fg and apply_btn is None and not background_submit
    prev = foreground if restore and needs_foreground else 0
    if needs_foreground and not foreground_ok(win):
        log.error(t("not_fg"))
        return False
    if needs_foreground:
        ctrl.set_focus()
        if user32.GetForegroundWindow() != win.handle:
            log.error(t("not_fg"))
            return False
    if not write_text(ctrl, text):
        if unverified_test_submit:
            log.error("STATUS_SUBMIT=FAILED stage=edit_write")
        log.error(t("mismatch"))
        return False

    if apply_btn is not None:
        press_button(apply_btn)
    else:
        if not background_submit:
            if (
                user32.GetForegroundWindow() != win.handle
            ):  # focus stolen: do not submit
                log.error(t("not_fg"))
                return False
            # Use a foreground key event so Camfrog's parent combo handles
            # Enter normally. Posting raw key messages to the child Edit can
            # trigger a Windows ding on every marquee frame on some builds.
            ctrl.type_keys("{ENTER}")
        else:
            try:
                _background_enter(ctrl, submit_target)
            except Exception as exc:
                if unverified_test_submit:
                    log.error(
                        "STATUS_SUBMIT=DISPATCH_FAILED route=%s error=%s",
                        f"{submit_target}-post",
                        type(exc).__name__,
                    )
                raise
            if unverified_test_submit or best_effort:
                log.warning(
                    "STATUS_SUBMIT=DISPATCHED_UNVERIFIED route=%s; "
                    "best-effort combo-post may activate or flash Camfrog; "
                    "verify in another Camfrog account's friend list",
                    f"{submit_target}-post",
                )
    if (
        prev
        and prev != win.handle
        and user32.IsWindow(prev)
        and user32.GetForegroundWindow() != prev
    ):
        time.sleep(0.15)
        user32.SetForegroundWindow(
            prev
        )  # best effort: give focus back to what you were doing
    return True


def commit_background(text: str, dry: bool = False, pid: Optional[int] = None) -> bool:
    """Attempt a strict background status change; the current adapter fails closed.

    Uses camfrog_status_engine.BackgroundStatusExecutor which:
    - Discovers and verifies the Camfrog process independently (PID + creation time +
      Authenticode signature).
    - Refuses before writing because submit isolation has not been verified.

    Returns True only when the client already confirms the requested text; otherwise
    returns False. No external provider result is inferred from local UI state.
    """
    if dry:
        log.info(t("dry_send", text=shown(text)))
        return True

    try:
        from camfrog_status_engine import (
            BackgroundStatusExecutor,
            EmergencyStopScope,
            ExecutionMode,
            StatusResultState,
        )
    except ImportError as exc:
        log.error("camfrog_status_engine not available: %s", exc)
        return False

    executor = BackgroundStatusExecutor(
        default_mode=ExecutionMode.STRICT_BACKGROUND,
        emergency_scope=EmergencyStopScope.FEATURE,
        emergency_resource_key="camfrog.status-changer",
    )
    try:
        evidence = executor.execute_status_change(
            status_text=text,
            pid=pid,
            execution_mode=ExecutionMode.STRICT_BACKGROUND,
        )
    except Exception as exc:
        # Fail closed: ambiguous identity, emergency stop, or any other
        # execution-boundary failure must never propagate into GUI workers
        # (StopIteration in particular must not silently end a loop).
        log.error(t("bg_status_failed", state="EXECUTION_ERROR", code=str(exc)[:120]))
        return False

    if evidence.result_state == StatusResultState.CLIENT_STATE_CONFIRMED.value:
        log.info(t("bg_status_client_confirmed", text=shown(text)))
        return True
    log.error(
        t("bg_status_failed", state=evidence.result_state, code=evidence.error_code)
    )
    return False


RESET_BURST = (
    25  # more fresh chat lines than this in one poll = history reset, not live chat
)
LINK_RE = re.compile(r"(https?://|www\.)", re.I)


def expand(text: str, nick: str = "", own: str = "") -> str:
    """Replace {sender} {me} {time} {date}. Plain replace (never str.format)."""
    now = dt.datetime.now()
    for k, v in (
        ("{sender}", nick),
        ("{me}", own),
        ("{time}", now.strftime("%H:%M")),
        ("{date}", now.strftime("%Y-%m-%d")),
    ):
        text = text.replace(k, v)
    return text


def compile_rules(ar: dict) -> list[dict]:
    return [
        {
            "idx": i + 1,
            "rx": re.compile(r["pattern"], re.I),
            "spec": r["reply"],
            "lang": r.get("lang"),
            "mention": bool(r.get("mention")),
            "cooldown": r.get("cooldown_seconds", 0),
            "last": -1e9,
        }
        for i, r in enumerate(ar["rules"])
        if r.get("enabled", True)
    ]


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
    path.parent.mkdir(
        parents=True, exist_ok=True
    )  # the config subfolder may not exist yet
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(5):  # AV/indexers can briefly lock the target
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2 * (i + 1))
    os.replace(tmp, path)


LEAD_VOWELS = set(
    "\u0e40\u0e41\u0e42\u0e43\u0e44"
)  # เ แ โ ใ ไ  (written before the consonant)


def clusters(text: str) -> list[str]:
    """Split into display clusters: combining marks (Thai vowels/tone marks, emoji
    modifiers) stay with their base; Thai leading vowels stay with the next consonant."""
    out, prefix = [], ""
    for ch in text:
        if ch in LEAD_VOWELS:
            prefix += ch
            continue
        attach = (
            unicodedata.category(ch) in ("Mn", "Mc", "Me")
            or ch in "\u200d\ufe0f\ufe0e"
            or (out and out[-1].endswith("\u200d"))
        )
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


def marquee_frames(
    text: str,
    width: int,
    stride: int = 2,
    separator: str = "   \u2022   ",
    cycles: int = 1,
    max_frames: int = 80,
    infinite_loop: bool = False,
) -> list[str]:
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
            self.items = [
                {
                    "text": i["text"],
                    "lang": detect_lang(i["text"]),
                    "count": int(i.get("count", 0)),
                    "last": float(i.get("last", 0)),
                }
                for i in raw
                if isinstance(i, dict) and str(i.get("text", "")).strip()
            ]
        except Exception:
            self.items = []

    def save(self):
        try:
            atomic_write(
                self.path, json.dumps(self.items, ensure_ascii=False, indent=2)
            )
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
        while (
            len(self.items) > self.max_items
        ):  # drop least used, then oldest; never the newcomer
            self.items.remove(
                min(
                    (i for i in self.items if i is not new),
                    key=lambda i: (i["count"], i["last"]),
                )
            )
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
        pool = [
            i
            for i in self.items
            if i["text"] != exclude and (not lang or i["lang"] == lang)
        ]
        if not pool:  # no item in that language: fall back to any language
            pool = [i for i in self.items if i["text"] != exclude]
        if not pool:
            return None
        if mode == "random":
            return random.choice(pool)["text"]
        if mode == "most_used":
            return max(pool, key=lambda i: (i["count"], -i["last"]))["text"]
        return min(pool, key=lambda i: i["last"])[
            "text"
        ]  # rotate: least recently used first


def safe_reply(template, nick, limit, own=""):
    text = CTRL_CHARS.sub(" ", expand(template, nick, own)).strip()
    if not text or text.startswith("/"):
        return None  # never emit room commands from the auto-replier
    return clip(text, limit)


def new_lines(prev, cur):
    for k in range(min(len(prev), len(cur)), -1, -1):
        if prev[len(prev) - k :] == cur[:k]:
            return cur[k:]
    return cur


# ---------- process control (background mode) ----------
def pid_path(cfg):
    # Each standalone status worker writes its PID/STOP into its own DATA_DIR.
    return DATA_DIR / cfg["safety"]["pid_file"]


def read_pid(cfg):
    try:
        return int(pid_path(cfg).read_text().strip())
    except Exception:
        return None


def _process_query_api():
    """Return kernel32 process-query APIs with pointer-safe Windows types."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
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
                handle, 0, buffer, ctypes.byref(size)
            )
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
            expected.add(ntpath.normcase(ntpath.join(ntpath.dirname(current), sibling)))
    return candidate in expected


def running_pid(cfg):
    pid = read_pid(cfg)
    return pid if pid_alive(pid) and pid_is_ours(pid) else None


def worker_start_gate_path(cfg):
    return pid_path(cfg).with_suffix(pid_path(cfg).suffix + ".start")


def wait_for_worker_start_gate():
    """Keep a launched worker inert until its parent verifies registration."""
    gate_value = os.environ.get("ZCFATO_WORKER_START_GATE")
    if not gate_value:
        return True
    gate = Path(gate_value)
    deadline = time.monotonic() + WORKER_START_GATE_TIMEOUT_SECONDS
    while not gate.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not gate.exists():
        log.error("Worker start authorization was not received; exiting before status work.")
        return False
    try:
        gate.unlink()
    except OSError:
        log.exception("Could not remove worker start authorization file")
    return True


def _authorize_worker_start(cfg, emit):
    gate = worker_start_gate_path(cfg)
    temporary = gate.with_name(f"{gate.name}.{time.time_ns()}.tmp")
    try:
        temporary.write_text("start\n", encoding="ascii")
        temporary.replace(gate)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        emit(f"Worker PID registered but start authorization failed: {exc}")
        return False
    return True


def _wait_for_worker_registration(cfg, process, log_path, emit):
    """Wait for the registered worker PID, including a onefile launcher handoff."""
    deadline = time.monotonic() + WORKER_START_TIMEOUT_SECONDS
    launcher_exit_at = None
    while time.monotonic() < deadline:
        if running_pid(cfg) is not None:
            if _authorize_worker_start(cfg, emit):
                return True
            break
        if process.poll() is not None:
            if launcher_exit_at is None:
                launcher_exit_at = time.monotonic()
            elif time.monotonic() - launcher_exit_at >= WORKER_LAUNCHER_EXIT_GRACE_SECONDS:
                break
        time.sleep(0.1)

    if running_pid(cfg) is not None and _authorize_worker_start(cfg, emit):
        return True
    cleanup_error = None
    try:
        worker_start_gate_path(cfg).unlink(missing_ok=True)
        if os.name == "nt":
            subprocess.run(
                [system_taskkill_path(), "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        cleanup_error = exc

    if cleanup_error is not None:
        emit(
            "Worker PID registration failed and its child could not be "
            f"stopped or verified (PID {process.pid}): {cleanup_error}"
        )
    if launcher_exit_at is not None:
        emit(t("bg_failed", code=process.returncode, log=log_path))
    else:
        emit(
            f"Worker process did not register its PID within "
            f"{WORKER_START_TIMEOUT_SECONDS} seconds; PID {process.pid}; "
            f"see log: {log_path}"
        )
    return False


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
        env.pop("_MEIPASS2", None)  # older PyInstaller
    return env


def stats_path(cfg):
    return DATA_DIR / cfg["stats"]["file"]


def cmd_state(cfg):
    pid = running_pid(cfg)
    running = bool(pid)
    print(t("state_running", pid=pid) if running else t("state_stopped"))
    try:
        d = json.loads(stats_path(cfg).read_text(encoding="utf-8"))
        print(t("stats_hdr"))
        print(
            t(
                "stats_line",
                started=d.get("started", "?"),
                st=d.get("statuses", 0),
                rp=d.get("replies", 0),
                fl=d.get("failures", 0),
            )
        )
    except Exception:
        pass
    return 0 if running else 1


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = {
    "room": "CamfrogRoomControl",
    "im_reply": "CamfrogIMAutoReply",
    "status": "CamfrogStatusChanger",
    "music": "CamfrogMusicDJ",
}.get(APP_PROFILE, "CamfrogAuto")


PROFILE_ASSETS = {
    "room": "room-control.exe",
    "im_reply": "im-autoreply.exe",
    "status": "status-changer.exe",
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

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as k:
        if enable:
            cmd = subprocess.list2cmdline(
                child_cmd(args) + ["start"]
            )  # quotes paths with spaces
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
class DJError(ValueError):
    """A refused DJ request with a user-facing reason."""


class DJQueue:
    """Persistent song-request queue: {current: {title, user} | None, queue: [...] }."""

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
            self.current = {
                "title": str(cur["title"]).strip(),
                "user": str(cur.get("user", "")),
            }
        items = raw.get("queue")
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and str(it.get("title", "")).strip():
                    self.queue.append(
                        {
                            "title": str(it["title"]).strip(),
                            "user": str(it.get("user", "")),
                        }
                    )

    def save(self):
        try:
            atomic_write(
                self.path,
                json.dumps(
                    {"current": self.current, "queue": self.queue},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        except Exception as e:  # queue must never break the bot
            log.debug("dj save failed: %s", e)

    def add(self, title, user, max_per_user=3):
        title, user = str(title).strip(), str(user).strip()
        if not title:
            raise DJError("give a song name: !request <song>")
        mine = [q for q in self.queue if q["user"].lower() == user.lower()]
        if len(mine) >= max(1, max_per_user):
            raise DJError(f"{user} already has {len(mine)} songs queued")
        if any(q["title"].lower() == title.lower() for q in self.queue):
            raise DJError(f'"{title}" is already queued')
        self.queue.append({"title": title, "user": user})
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


def dj_find_song(music_dir, title):
    """Match a request against .wav files in music_dir. Returns Path or None."""
    want = str(title).strip().lower()
    if not want:
        return None
    try:
        wavs = sorted(Path(music_dir).glob("*.wav"))
    except OSError:
        return None
    for wav in wavs:
        if want in wav.stem.lower():
            return wav
    return None


def dj_wav_duration(path):
    """Seconds of a WAV file, or None when unreadable."""
    try:
        import wave

        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate else None
    except Exception:
        return None


def dj_play(path):
    """Play a WAV file async on Windows (winsound). Returns True when started."""
    if os.name != "nt":
        return False
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except Exception as e:
        log.debug("dj play failed: %s", e)
        return False


def dj_stop():
    try:
        if os.name == "nt":
            import winsound

            winsound.PlaySound(None, 0)
    except Exception:
        pass


class Runner:
    def __init__(self, cfg, cfg_path=None):
        self.cfg_path = Path(cfg_path) if cfg_path else None
        self._mtime = (
            self.cfg_path.stat().st_mtime
            if self.cfg_path and self.cfg_path.exists()
            else 0
        )
        self._next_reload = 0.0
        self._next_resolve_retry = 0.0
        self.marq, self.lang_i, self.msg_i, self.current_text = None, 0, {}, None
        self.next_attach = 0.0
        self.im_wins, self.im_next_attach, self.im_hint_at = {}, 0.0, 0.0
        # Resolve targets: stay None until _do_resolve() succeeds, so the
        # retry loop degrades to warnings instead of AttributeError.
        self.win = self.status_edit = self.apply_btn = None
        self.history = self.input = self.chat_win = None
        self.apply_cfg(cfg)
        self.sender_last, self.sent = {}, []
        self.im_sender_last, self.im_day, self.im_sent, self.im_next_ok = (
            {},
            {},
            [],
            0.0,
        )
        self.next_reply_ok = 0.0
        self.status_i = 0
        self.dj_prev = []
        self.dj_started = 0.0
        self.dj_length = 0.0
        now = time.monotonic()
        if cfg["status"]["set_on_start"]:
            status = cfg["status"]
            marquee = status["marquee"]
            switch_delay = (
                max(MARQUEE_STEP_MIN, float(marquee["step_seconds"]))
                if marquee.get("enabled") and not status.get("random", False)
                else float(status["interval_seconds"])
            )
            self.last_status = now - switch_delay + STATUS_STARTUP_DELAY_SECONDS
        else:
            self.last_status = now
        self.prev = []
        self.stats = {
            "started": dt.datetime.now().isoformat(timespec="seconds"),
            "statuses": 0,
            "marquee_frames": 0,
            "replies": 0,
            "im_replies": 0,
            "failures": 0,
            "by_rule": {},
        }
        self._next_stats = 0.0

    def apply_cfg(self, cfg):
        cfg = apply_app_profile(cfg)
        normalize_marquee_loop(cfg)
        self.cfg = cfg
        self.dry = cfg["dry_run"]
        self.stop_file = BASE / cfg["safety"]["stop_file"]
        ar = cfg["autoreply"]
        old = {
            (r["idx"], r["rx"].pattern): r["last"] for r in getattr(self, "rules", [])
        }
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
        old_im = {
            (r["idx"], r["rx"].pattern): r["last"]
            for r in getattr(self, "im_rules", [])
        }
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
                    for txt in (
                        [it]
                        if isinstance(it, str)
                        else [it.get("th", ""), it.get("en", "")]
                    ):
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
            if (
                not new["dry_run"]
                and new["autoreply"]["enabled"]
                and not new["autoreply"]["own_nickname"].strip()
            ):
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
        try:
            self.resolve()
        except Exception as e:
            # A config reload can coincide with Camfrog closing, restarting,
            # or becoming ambiguous. Keep the worker alive, but never keep
            # using an HWND/control from the previous attachment.
            self.win = self.status_edit = self.apply_btn = None
            self.marq = None
            log.warning("Config reloaded but Camfrog re-attach failed: %s", e)
            return
        log.info(t("reloaded"))

    def write_stats(self, now, force=False):
        sc = self.cfg["stats"]
        if not sc["enabled"] or (not force and now < self._next_stats):
            return
        self._next_stats = now + sc["write_seconds"]
        try:
            atomic_write(
                BASE / sc["file"], json.dumps(self.stats, ensure_ascii=False, indent=2)
            )
        except Exception as e:  # stats must never break the bot
            log.debug("stats write failed: %s", e)

    def send(self, ctrl, text, btn=None, win=None):
        s = self.cfg["safety"]
        target = (
            self.cfg["status"].get("background_enter_target", "edit")
            if ctrl is getattr(self, "status_edit", None) and win is None
            else "edit"
        )
        return commit(
            win or self.win,
            ctrl,
            text,
            self.dry,
            s["require_foreground"],
            btn,
            s["restore_previous_window"],
            target,
            background_if_not_foreground=(
                ctrl is getattr(self, "status_edit", None) and win is None
            ),
            execution_mode=self.cfg["status"].get("execution_mode", "interactive"),
        )

    def resolve(self):
        try:
            self._do_resolve()
        except LookupError as e:
            if not self.cfg_path:
                raise
            log.warning(
                f"UI element missing ({e}). Auto-detecting and updating config..."
            )
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
                log.error(
                    "Auto-detect failed to find missing elements. Retrying on next tick."
                )
                self.status_edit = None
                self.apply_btn = None
                self.win = None

    def _do_resolve(self):
        cfg = self.cfg
        st, ar = cfg["status"], cfg["autoreply"]
        # Invalidate any prior handles before discovery. If discovery fails,
        # callers must not accidentally submit through a stale HWND.
        self.win = self.status_edit = self.apply_btn = None
        self.win = get_window(cfg)
        self.status_edit, self.apply_btn = None, None
        self.status_edit = find(self.win, st["edit"]) if st["enabled"] else None
        self.apply_btn = (
            find(self.win, st["apply_button"])
            if st["enabled"] and st["apply_button"]
            else None
        )
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
        self.history, self.input, self.chat_win = (
            find(w, ar["history"]),
            find(w, ar["input"]),
            w,
        )
        self.prev = read_chat(self.history, ar["history_tail"]).splitlines()
        log.info(t("room_attached", title=shown(w.window_text())))

    def im_summary(self):
        im = _im(self.cfg)
        if not im["enabled"]:
            log.info(t("im_off"))
            return
        mode = "dry-run" if self.im_dry else "LIVE"
        log.info(
            t(
                "im_on",
                mode=mode,
                n=len([n for n in im["only_nicknames"] if str(n).strip()]),
                r=len(im["rules"]),
                k="+".join(im["log_kinds"]),
            )
        )

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
                if (
                    now < self.im_next_ok
                    or len(self.im_sent) >= im["max_per_hour"]
                    or len(day) >= im["max_per_sender_per_day"]
                    or now - self.im_sender_last.get(lk, -1e9)
                    < im["per_sender_cooldown_seconds"]
                    or now - d["rule"]["last"] < d["rule"]["cooldown"]
                ):
                    continue
                time.sleep(random.uniform(*im["delay_range_seconds"]))
                if not commit(
                    st["win"],
                    st["inp"],
                    d["reply"],
                    self.im_dry,
                    s["require_foreground"],
                    None,
                    s["restore_previous_window"],
                ):
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
            self.im_day = {
                k: v for k, v in self.im_day.items() if any(now - x < 86400 for x in v)
            }
            # Prune sender last-seen older than the per-sender cooldown (no longer blocks new messages)
            self.im_sender_last = {
                k: v
                for k, v in self.im_sender_last.items()
                if now - v < im["per_sender_cooldown_seconds"]
            }

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

    def choose_status(self, sequence_index=None, sequence_pool=None):
        """Pick a status, optionally pinned to its Marquee slot index."""
        st = self.cfg["status"]
        if sequence_index is not None:
            pool = sequence_pool if sequence_pool is not None else self.status_pool()
            if not pool:
                return None
            # Marquee is a slot sequence. Pick directly from the captured pool
            # so history or language filters cannot redirect slot N.
            item = pool[sequence_index % len(pool)]
            cycle = st["language_cycle"]
            if cycle:
                desired = cycle[self.lang_i % len(cycle)]
                self.lang_i += 1
                if isinstance(item, dict) and item.get(desired):
                    return item[desired]
                if isinstance(item, str) and detect_lang(item) == desired:
                    return item
            return pick_status(item, st["language_mode"], sequence_index)
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

    def _finish_status(self, active, displayed):
        """Complete one status and advance the Marquee slot cursor exactly once."""
        self.marq = None
        slot_index = active.get("slot_index")
        if slot_index is None:
            self.status_i += 1
        else:
            self.status_i = slot_index + 1
        self.stats["statuses"] += 1
        self.current_text = active["raw"]
        if slot_index is None:
            log.info(t("status_set", text=shown(displayed)))
        else:
            log.info(
                t(
                    "status_slot_set",
                    slot=slot_index + 1,
                    total=active["slot_count"],
                    text=shown(displayed),
                )
            )

    def build_frames(self, text):
        st, mq = self.cfg["status"], self.cfg["status"]["marquee"]
        limit = st["max_length"]
        final = clip(text, limit)
        if not mq["enabled"] or not mq.get("scroll", False):
            return [final]  # per-line rotation: one whole line per tick
        # GUI Loop cycles through status slots. Preserve frame looping only for
        # legacy configs that have not opted into slot looping.
        frame_loop = bool(
            mq.get("infinite_loop", False) and not mq.get("loop_slots", False)
        )
        frames = [
            clip(f, limit)
            for f in marquee_frames(
                text,
                mq["width"],
                mq["stride"],
                mq["separator"],
                mq["cycles"],
                mq["max_frames"],
                frame_loop,
            )
        ]
        if not frame_loop and (not frames or frames[-1] != final):
            frames.append(final)  # finish this status before advancing to the next slot
        return frames

    def do_status(self, now):
        st = self.cfg["status"]
        if not self.status_edit:
            if st["enabled"] and now >= getattr(self, "_next_status_warn", 0):
                log.warning(t("no_ctrl", spec={"section": "status.edit"}, n=0))
                self._next_status_warn = now + 60
            return
        mq = st["marquee"]
        sequence = bool(mq.get("enabled") and not st.get("random", False))
        active = self.marq
        if (
            active is not None
            and active.get("loop_frames")
            and now >= active["deadline"]
        ):
            self._finish_status(active, active["frames"][-1])
        if self.marq is None:
            pool = None
            if sequence:
                pool = self.status_pool()
                if not pool:
                    return
                if self.status_i >= len(pool):
                    if mq.get("loop_slots", False):
                        self.status_i = 0
                    else:
                        return
            switch_delay = (
                max(MARQUEE_STEP_MIN, float(mq["step_seconds"]))
                if sequence
                else st["interval_seconds"]
            )
            if now - self.last_status < switch_delay:
                return
            slot_index = self.status_i if sequence else None
            raw = (
                self.choose_status(slot_index, pool)
                if sequence
                else self.choose_status()
            )
            if raw is None:
                return
            text = expand(raw, own=self.cfg["autoreply"]["own_nickname"])
            if not text.strip():
                log.warning(t("status_blank"))
                self.last_status = now
                return
            frames = self.build_frames(text)
            loop_frames = bool(
                mq.get("infinite_loop")
                and not mq.get("loop_slots")
                and mq.get("scroll")
                and len(frames) > 1
            )
            self.marq = {
                "raw": raw,
                "frames": frames,
                "i": 0,
                "next": now,
                "loop_frames": loop_frames,
                "deadline": now + st["interval_seconds"],
                "history_recorded": False,
            }
            if sequence:
                self.marq["slot_index"] = slot_index
                self.marq["slot_count"] = len(pool)
            if loop_frames:
                self.last_status = now
        m = self.marq
        if now < m["next"]:
            return
        frame = m["frames"][m["i"]]
        if not self.send(self.status_edit, frame, self.apply_btn):
            self.marq = None
            retry_delay = max(st["retry_seconds"], st["interval_seconds"])
            switch_delay = (
                max(MARQUEE_STEP_MIN, float(mq["step_seconds"]))
                if sequence
                else st["interval_seconds"]
            )
            self.last_status = now + retry_delay - switch_delay
            raise RuntimeError(t("status_fail"))
        if (
            m["i"] == 0
            and not m["history_recorded"]
            and st["history"]["enabled"]
            and st["history"]["record"]
        ):
            self.hist.record(m["raw"])
            m["history_recorded"] = True
        if len(m["frames"]) > 1:
            self.stats["marquee_frames"] += 1
        m["i"] += 1
        if m["loop_frames"]:
            if m["i"] >= len(m["frames"]):
                m["i"] = 0
            m["next"] = time.monotonic() + max(
                MARQUEE_STEP_MIN, float(st["marquee"]["step_seconds"])
            )
            log.debug(t("status_set", text=shown(frame)))
            return
        if m["i"] >= len(m["frames"]):
            self._finish_status(m, frame)
            # Marquee slot changes are paced by Step, regardless of Scroll.
            # Random retains its separate Every interval.
            self.last_status = now
        else:
            # Schedule from completion of the UI send, not the tick start. UIA
            # writes can be slow; using `now` here could make delayed sends run
            # back-to-back and destabilize the ticker.
            m["next"] = time.monotonic() + max(
                MARQUEE_STEP_MIN, float(st["marquee"]["step_seconds"])
            )
            log.debug(t("status_set", text=shown(frame)))

    def do_chat(self, can_reply):
        ar = self.cfg["autoreply"]
        dj = self.cfg.get("dj") or DEFAULTS["dj"]
        dj_pre = dj.get("prefix", "!") if dj["enabled"] else ""
        cur = read_chat(self.history, ar["history_tail"]).splitlines()
        fresh, self.prev = new_lines(self.prev, cur), cur
        if not can_reply:
            return  # consume backlog so it isn't answered later
        if (
            len(fresh) > RESET_BURST
        ):  # room switch / reload dumped old lines: never answer them
            log.warning(t("burst_skip", n=len(fresh)))
            return
        if len(self.sender_last) > 500:
            now = time.monotonic()
            self.sender_last = {
                k: v
                for k, v in self.sender_last.items()
                if now - v < ar["per_sender_cooldown_seconds"]
            }
        for line in fresh:
            m = LINE_RE.match(line.strip())
            if not m:
                continue  # continuation / system line: never act on it
            nick, msg = m["nick"].strip(), m["msg"][: ar["max_incoming_length"]]
            if dj_pre and msg.startswith(dj_pre):
                continue  # DJ commands belong to do_dj, never to room rules
            lk, mlang = nick.lower(), detect_lang(m["msg"])
            if (
                not NICK_RE.match(nick)
                or lk == self.own
                or lk in self.ignore
                or (self.only and lk not in self.only)
            ):
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
            reply = (
                safe_reply(template, nick, ar["max_reply_length"], ar["own_nickname"])
                if template
                else None
            )
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
        if cmd in ("request", "req", "song"):
            title = arg
            if dj.get("audio_backend", "chat") == "local":
                song = dj_find_song(BASE / dj.get("music_dir", "music"), arg)
                if song is None:
                    raise DJError(f'not found as .wav in music/: "{arg}"')
                title = song.stem
            if (
                queue.current is not None
                and queue.current["title"].lower() == title.lower()
            ):
                raise DJError(f'"{title}" is playing now')
            pos = queue.add(title, nick, dj.get("max_per_user", 3))
            if queue.current is None:
                queue.current = queue.queue.pop(0)
                self.dj_start_playback(queue, dj)
                return dj["announce_now"].format(
                    title=queue.current["title"], user=queue.current["user"]
                )
            return dj["announce_queued"].format(pos=pos, title=title)
        if cmd == "queue":
            if not queue.queue:
                return "[dj] queue is empty"
            lines = [
                f"{i}. {q['title']} ({q['user']})"
                for i, q in enumerate(queue.queue[:10], 1)
            ]
            return "[dj] queue: " + " | ".join(lines)
        if cmd in ("current", "np"):
            if queue.current is None:
                return "[dj] nothing is playing"
            return "[dj] current: {0} ({1})".format(
                queue.current["title"], queue.current["user"]
            )
        if cmd == "skip":
            nxt = queue.skip(nick, owner)
            self.dj_start_playback(queue, dj)
            if nxt is None:
                return "[dj] skipped, queue is empty"
            return dj["announce_now"].format(title=nxt["title"], user=nxt["user"])
        if cmd == "help":
            return "[dj] commands: !request <song> !queue !current !skip"
        return None

    def dj_start_playback(self, queue, dj):
        """Begin local audio for the current song (or stop when empty)."""
        self.dj_started, self.dj_length = 0.0, 0.0
        if queue.current is None or self.dry:
            dj_stop()
            return
        if dj.get("audio_backend", "chat") != "local":
            return
        song = dj_find_song(BASE / dj.get("music_dir", "music"), queue.current["title"])
        if song is None:
            return
        if dj_play(song):
            self.dj_started = time.monotonic()
            self.dj_length = dj_wav_duration(song) or 0.0

    def dj_auto_advance(self, queue, dj):
        """Move to the next song when the WAV finished. Returns True if changed."""
        if queue.current is None or not self.dj_started or not self.dj_length:
            if (
                queue.current is not None
                and not self.dj_started
                and not self.dry
                and dj.get("audio_backend", "chat") == "local"
            ):
                self.dj_start_playback(queue, dj)  # resume after restart
                return True
            return False
        if time.monotonic() - self.dj_started < self.dj_length:
            return False
        nxt = queue.advance()
        self.dj_start_playback(queue, dj)
        if nxt is None:
            self.say_now("[dj] queue finished")
        else:
            self.say_now(
                dj["announce_now"].format(title=nxt["title"], user=nxt["user"])
            )
        return True

    def say_now(self, text):
        if not self.send(self.input, text, None, self.chat_win):
            raise RuntimeError(t("reply_fail"))
        log.info(t("replied", nick="dj", text=shown(text)))

    def do_dj(self, active):
        dj = self.cfg.get("dj") or DEFAULTS["dj"]
        if not (
            dj["enabled"]
            and active
            and self.history is not None
            and self.input is not None
        ):
            return
        ar = self.cfg["autoreply"]
        cur = read_chat(self.history, ar["history_tail"]).splitlines()
        fresh, self.dj_prev = new_lines(getattr(self, "dj_prev", []), cur), cur
        queue = DJQueue(BASE / dj["queue_file"])
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
            cmd, _, arg = msg[len(pre) :].strip().partition(" ")
            try:
                reply = self.dj_command(
                    queue, dj, cmd.lower(), arg.strip(), nick, owner
                )
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
        log.info(
            "STATUS_WORKER_MODE execution_mode=%s unverified_test_mode=%s",
            self.cfg["status"].get("execution_mode", "interactive"),
            _unverified_status_test_submit_enabled(),
        )
        self.im_summary()
        fails = 0
        try:
            while not self.stop_file.exists():
                clean_worker_log_at_noon()
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
                    if self._wait_for_stop(min(30, 2**fails)):
                        continue
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
                time.sleep(wait)
            log.info(t("stopped"))
            return 0
        finally:
            self.write_stats(time.monotonic(), force=True)

    def _wait_for_stop(self, seconds):
        """Wait for retry backoff, checking the stop marker at short intervals."""
        deadline = time.monotonic() + max(0.0, seconds)
        while not self.stop_file.exists():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.1, remaining))
        return True


def _strict_status_start_blocked(cfg, emit=print):
    """Fail closed before a status worker starts when live submit is unverified."""
    if cfg.get("dry_run", True) or not cfg.get("status", {}).get("enabled", True):
        return False
    if cfg.get("status", {}).get("execution_mode") == "best_effort_background":
        emit(
            "BEST_EFFORT_BACKGROUND_DISABLED: Camfrog combo-post can take "
            "desktop focus and interrupt keyboard input. Status worker refused."
        )
        return True
    if _unverified_status_test_submit_enabled():
        emit(
            "UNVERIFIED TEST MODE: status submissions use combo-post to avoid "
            "the Edit control ding; Camfrog may activate. Verify the result in "
            "another Camfrog account's friend list."
        )
        return False
    try:
        from camfrog_status_engine import strict_background_submit_supported

        supported = strict_background_submit_supported()
    except (ImportError, AttributeError):
        supported = False
    if supported:
        return False
    emit(t(
        "bg_status_failed",
        state="BACKGROUND_UNSUPPORTED",
        code="STRICT_SUBMIT_UNVERIFIED",
    ))
    return True


def run_guarded(cfg, cfg_path=None):
    """Hold local status-write authority for this worker's full lifecycle."""
    cfg = apply_app_profile(cfg)
    if _strict_status_start_blocked(cfg):
        return 2
    existing = running_pid(cfg)
    if existing and existing != os.getpid():
        print(t("already_running", pid=existing))
        return 3
    try:
        from camfrog_status_engine import StatusResourceLock

        writer_lock = StatusResourceLock(
            resource_key="camfrog_status_writer", timeout_seconds=0.5
        )
        if not writer_lock.acquire():
            raise TimeoutError("status writer is already owned")
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        log.error("Status writer ownership unavailable: %s", exc)
        print("Another status worker owns the local Camfrog status writer.")
        return 3
    try:
        pf = pid_path(cfg)
        pf.write_text(str(os.getpid()))
        try:
            if not wait_for_worker_start_gate():
                return 4
            return Runner(cfg, cfg_path).run()
        finally:
            if read_pid(cfg) == os.getpid():
                pf.unlink(missing_ok=True)
    finally:
        writer_lock.release()


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
    sample = deep_merge(
        DEFAULTS,
        {
            "status": {
                "messages": [{"th": "สวัสดีครับ ยินดีต้อนรับ", "en": "Welcome everyone"}]
            },
            "autoreply": {
                "rules": [
                    {
                        "pattern": "สวัสดี|หวัดดี|\\bhello\\b|\\bhi\\b",
                        "reply": {
                            "th": ["สวัสดีครับคุณ {sender}"],
                            "en": ["Hi {sender}, welcome!"],
                        },
                        "cooldown_seconds": 60,
                    }
                ]
            },
        },
    )
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
    if (
        not NICK_RE.match(nick)
        or lk == own
        or lk in ignore
        or (only and lk not in only)
    ):
        out["skip"] = "sender"
        return out
    msg = msg[: ar["max_incoming_length"]]
    out["skip"] = {"link": "link", "pattern": "pattern"}.get(
        is_skipped(msg, skips, ar["ignore_links"])
    )
    if out["skip"]:
        return out
    rule = match_rule(rules, msg, mlang, own)
    out["rule"] = rule
    if rule is not None:
        tpl = pick_reply(rule["spec"], mlang)
        out["reply"] = (
            safe_reply(tpl, nick, ar["max_reply_length"], ar["own_nickname"])
            if tpl
            else None
        )
    return out


def im_view(cfg):
    """cfg whose `autoreply` section carries the IM rules/limits, so the room-tested matching code
    (evaluate_message, compile_rules, ...) is reused unchanged for private chats."""
    ar, im = cfg["autoreply"], _im(cfg)
    return {
        "autoreply": {
            **ar,
            "rules": im["rules"],
            "skip_patterns": im["skip_patterns"],
            "ignore_links": im["ignore_links"],
            "only_nicknames": im["only_nicknames"],
            "max_reply_length": im["max_reply_length"],
            "max_incoming_length": im["max_incoming_length"],
        }
    }


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
            print(
                "  "
                + t(
                    "tr_match",
                    i=r["rule"]["idx"],
                    reply=r["reply"] if r["reply"] else t("tr_blocked"),
                )
            )
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
    n = sum(
        hist.add(line)
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
    )
    hist.save()
    print(t("hist_imported", n=n))
    return 0


def cmd_marquee(cfg, a):
    mq = cfg["status"]["marquee"]
    width = a.width or mq["width"]
    stride = a.stride or mq["stride"]
    inf = a.infinite_loop or mq.get("infinite_loop", False)
    frames = marquee_frames(
        a.text,
        width,
        stride,
        mq["separator"],
        a.cycles or mq["cycles"],
        a.max_frames or mq["max_frames"],
        inf,
    )
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
        name = (
            "" if ctype in STRUCT_SKIP else str(_ctrl_attr(c, "name"))[:60]
        )  # no nicknames/chat text
        f.write(
            f"{'  ' * depth}{_ctrl_attr(c, 'class_name')} | {ctype} | "
            f"{name!r} | id={_ctrl_attr(c, 'automation_id')!r} | {rect}\n"
        )
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
            f.write(
                f"=== window: {w.window_text()[:60]!r} class={w.class_name()} ===\n"
            )
            dump_controls(w, f)
    print(t("discover_done", p=out))
    return 0


# ---------- Camfrog 8.x control detection ----------
# Static analysis of Camfrog Video Chat 8.5.0.51219 (see docs/CAMFROG-8.5-FINDINGS.md): the input is a
# WTL RICHEDIT50W, the custom status is a combo box (inner Edit), the room log is a CEF Document.
STRUCT_SKIP = {
    "Text",
    "Hyperlink",
    "Image",
    "ListItem",
    "DataItem",
    "TreeItem",
}  # may hold chat text


def describe_controls(win):
    """Live UIA tree -> plain dicts (no chat text: Text-like rows keep no name)."""
    rows = []
    for c in win.descendants():
        try:
            r = c.rectangle()
            rows.append(
                {
                    "class_name": _ctrl_attr(c, "class_name"),
                    "control_type": _ctrl_attr(c, "control_type"),
                    "name": str(_ctrl_attr(c, "name"))[:40],
                    "auto_id": str(_ctrl_attr(c, "automation_id")),
                    "visible": bool(c.is_visible()),
                    "enabled": bool(c.is_enabled()),
                    "left": r.left,
                    "top": r.top,
                    "w": r.width(),
                    "h": r.height(),
                    "parent_class": str(
                        getattr(
                            getattr(c.element_info, "parent", None), "class_name", ""
                        )
                        or ""
                    ),
                }
            )
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
    live = [
        r
        for r in rows
        if r.get("visible") and r.get("enabled") and r["w"] > 0 and r["h"] > 0
    ]
    out = {}
    hosts = [r for r in live if r["class_name"] == "AtlAxWinLic140"]

    def kind(
        h,
    ):  # the IE pane sharing the host's rectangle names the page: wb-log-room-data / wb-edit-data
        for r in rows:
            if r["class_name"] == "Internet Explorer_Server" and (
                r["left"],
                r["top"],
                r["w"],
                r["h"],
            ) == (h["left"], h["top"], h["w"], h["h"]):
                return r["name"]
        return ""

    logs = [h for h in hosts if "wb-log-room-data" in kind(h)]
    edits_ = [h for h in hosts if "wb-edit-data" in kind(h)]
    if logs and edits_:
        out["autoreply.history"] = (
            _sel(rows, max(logs, key=lambda r: r["w"] * r["h"])),
            "room chat log (web pane wb-log-room-data)",
        )
        out["autoreply.input"] = (
            _sel(rows, max(edits_, key=lambda r: r["top"])),
            "room chat input (web pane wb-edit-data)",
        )
        rich = []
    else:
        rich = [r for r in live if r["class_name"].upper().startswith("RICHEDIT")]
    inp = max(rich, key=lambda r: (r["top"], r["w"]), default=None) if rich else None
    if inp:
        out["autoreply.input"] = (_sel(rows, inp), "bottom-most RichEdit = chat input")
    docs = (
        [r for r in live if r["control_type"] == "Document"]
        if not out.get("autoreply.history")
        else []
    )
    if out.get("autoreply.history"):
        pass
    elif docs:
        d = max(docs, key=lambda r: r["w"] * r["h"])
        out["autoreply.history"] = (
            {"control_type": "Document", "index": _nth(rows, d, "control_type")},
            "largest Chromium Document = room log",
        )
    else:
        rest = [r for r in rich if r is not inp]
        if rest:
            h = max(rest, key=lambda r: r["w"] * r["h"])
            out["autoreply.history"] = (
                _sel(rows, h),
                "largest other RichEdit (no Document found)",
            )
    edits = [
        r
        for r in live
        if r["class_name"].lower() == "edit" and "combo" in r["parent_class"].lower()
    ]
    if edits:
        e = min(edits, key=lambda r: (r["top"], r["left"]))
        out["status.edit"] = (_sel(rows, e), "top-most combo-box Edit = custom status")
    else:
        combos = [r for r in live if "combobox" in r["class_name"].lower()]
        if combos:
            c = min(combos, key=lambda r: (r["top"], r["left"]))
            out["status.edit"] = (
                _sel(rows, c),
                "top-most ComboBox (no inner Edit exposed)",
            )
        else:
            edits_any = [r for r in live if r["class_name"].lower() == "edit"]
            if edits_any:
                e2 = min(edits_any, key=lambda r: (r["top"], r["left"]))
                out["status.edit"] = (
                    _sel(rows, e2),
                    "top-most Edit (broad fallback, verify manually)",
                )
    return out


def write_detect_report(rows, path):
    with Path(path).open("w", encoding="utf-8") as f:
        f.write(
            "class_name | control_type | name(<=40) | auto_id | visible | left,top,w,h | parent_class\n"
        )
        for r in rows:
            if r["control_type"] in STRUCT_SKIP:
                continue
            f.write(
                f"{r['class_name']} | {r['control_type']} | {r['name']} | {r['auto_id']} | "
                f"{int(r['visible'])} | {r['left']},{r['top']},{r['w']},{r['h']} | {r['parent_class']}\n"
            )


def cmd_detect(cfg, cfg_path=None, apply=False, as_json=False):
    win = get_window(cfg)
    if APP_PROFILE == "status":
        # Status Changer only owns the status control. Do not walk chat room and
        # IM trees that can contain thousands of UIA nodes and are irrelevant here.
        per_window = [describe_controls(win)]
    else:
        per_window = [describe_controls(win)] + [
            describe_controls(w) for w in process_windows(win) if w.handle != win.handle
        ]
    rows = [r for rs in per_window for r in rs]
    found = {}
    for rs in per_window:  # selectors are per window (find() indexes inside one window)
        for k, v in detect_camfrog(rs).items():
            found.setdefault(k, v)
    if APP_PROFILE == "status":
        found = {key: value for key, value in found.items() if key == "status.edit"}
    rep = BASE / "detect_report.txt"
    write_detect_report(rows, rep)
    if as_json:
        print("PROPOSAL " + json.dumps({k: v[0] for k, v in found.items()}))
    keys = (
        ("status.edit",)
        if APP_PROFILE == "status"
        else ("status.edit", "autoreply.history", "autoreply.input")
    )
    for key in keys:
        if key in found:
            print(
                t("det_found", k=key, sel=json.dumps(found[key][0]), why=found[key][1])
            )
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
        problems.append(
            "autoreply_im.dry_run is true: nothing is sent (dry-run only logs)"
        )
    if not in_active_hours(cfg):
        problems.append("outside active_hours: the bot is idle")
    print(
        f"  enabled={im['enabled']}  friends={len(friends)}  rules={len(im['rules'])}  "
        f"log_kinds={im['log_kinds']}  dry_run(global/IM)={cfg['dry_run']}/{im['dry_run']}"
    )
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
            print(
                f"  [other] {title!r}: no chat input/history panes found with the autoreply selectors"
            )
            continue
        kind = log_kind(hist) if is_web(hist) else ""
        if kind in im["log_kinds"]:
            n_im += 1
            verdict = f"{kind.upper()} window: WILL be tracked"
        elif kind == "mtim":
            verdict = 'MTIM window: ignored. If this is your private chat, add "mtim" to autoreply_im.log_kinds (unverified)'
        elif kind == "room":
            verdict = "room window: never used for IM"
        else:
            verdict = (
                "chat panes found but the page kind is unknown (UIA names not exposed?)"
            )
        print(f"  [{kind or '?'}] {title!r}: {verdict}")
    if not n_im:
        print(
            "  PROBLEM: no private-chat window would be tracked. Open a private chat (not minimized to the tray) and retry."
        )
    return 0 if (n_im and not problems) else 1


def cmd_status(cfg, text):
    cfg = apply_app_profile(cfg)
    st, sf = cfg["status"], cfg["safety"]
    text = clip(expand(text, own=cfg["autoreply"]["own_nickname"]), st["max_length"])
    if st.get("execution_mode", "interactive") == "strict_background":
        # Strict path discovers and verifies process/controls itself;
        # never resolve legacy visible controls first.
        ok = commit_background(text, dry=cfg["dry_run"])
        print(t("ok") if ok else t("failed"))
        return 0 if ok else 1
    win = get_window(cfg)
    ctrl = find(win, st["edit"])
    btn = find(win, st["apply_button"]) if st["apply_button"] else None
    ok = commit(
        win,
        ctrl,
        text,
        cfg["dry_run"],
        sf["require_foreground"],
        btn,
        sf["restore_previous_window"],
        st.get("background_enter_target", "edit"),
        background_if_not_foreground=True,
        execution_mode=st.get("execution_mode", "interactive"),
    )
    print(t("ok") if ok else t("failed"))
    return 0 if ok else 1


LANG_LOCKED = False


def setup_logging(cfg):
    global LOG_TEXT
    lc = cfg["log"]
    LOG_TEXT = lc["log_message_text"]
    handlers = [logging.StreamHandler()]
    if lc["file"]:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                BASE / lc["file"],
                maxBytes=lc["max_bytes"],
                backupCount=lc["backups"],
                encoding="utf-8",
            )
        )
    logging.basicConfig(
        level=getattr(logging, lc["level"].upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


# Public command names used by GUI launchers when forwarding CLI arguments.
CLI_COMMANDS = {
    "check",
    "discover",
    "windows",
    "run",
    "start",
    "stop",
    "state",
    "autostart-on",
    "autostart-off",
    "chat-probe",
    "im-probe",
    "detect",
    "status",
    "init",
    "marquee",
    "history",
    "history-add",
    "history-import",
    "test-rules",
}

PROFILE_COMMANDS = {
    "room": {
        "check",
        "discover",
        "windows",
        "run",
        "start",
        "stop",
        "state",
        "autostart-on",
        "autostart-off",
        "chat-probe",
        "detect",
        "init",
        "test-rules",
        "update",
    },
    "im_reply": {
        "check",
        "discover",
        "windows",
        "run",
        "start",
        "stop",
        "state",
        "autostart-on",
        "autostart-off",
        "im-probe",
        "detect",
        "init",
        "test-rules",
        "update",
    },
    "status": {
        "check",
        "discover",
        "windows",
        "run",
        "start",
        "stop",
        "state",
        "status",
        "marquee",
        "history",
        "history-add",
        "history-import",
        "detect",
        "init",
        "update",
    },
    "music": {
        "check",
        "discover",
        "windows",
        "run",
        "start",
        "stop",
        "state",
        "autostart-on",
        "autostart-off",
        "chat-probe",
        "detect",
        "init",
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
    common.add_argument(
        "--lang", choices=["auto", "th", "en"], default=argparse.SUPPRESS
    )
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=None)
    p.add_argument("--lang", choices=["auto", "th", "en"], default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in (
        "check",
        "discover",
        "windows",
        "run",
        "start",
        "stop",
        "state",
        "autostart-on",
        "autostart-off",
    ):
        sub.add_parser(name, parents=[common])
    up = sub.add_parser(
        "update",
        parents=[common],
        help="self-update this executable from the GitHub release",
    )
    up.add_argument("asset", nargs="?", default=None)
    sub.add_parser("chat-probe", parents=[common])
    sub.add_parser("im-probe", parents=[common])
    dp = sub.add_parser("detect", parents=[common])
    dp.add_argument(
        "--apply", action="store_true", help="write detected selectors into config.json"
    )
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
    mqp.add_argument(
        "--infinite-loop",
        action="store_true",
        help="loop continuously without settling",
    )
    sub.add_parser("history", parents=[common])
    ha = sub.add_parser("history-add", parents=[common])
    ha.add_argument("text")
    hi = sub.add_parser("history-import", parents=[common])
    hi.add_argument("file")
    tr = sub.add_parser("test-rules", parents=[common])
    tr.add_argument("text", nargs="?")
    tr.add_argument("--sender", default="TestUser")
    tr.add_argument("--file")
    tr.add_argument(
        "--im", action="store_true", help="test with the autoreply_im rules and limits"
    )
    return p


def cli_main(argv=None, force_strict_background=False):
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
        if (
            force_strict_background
            and APP_PROFILE == "status"
            and cfg.get("status", {}).get("execution_mode")
            != "best_effort_background"
            and not _unverified_status_test_submit_enabled()
        ):
            # Preserve the explicit best-effort choice made in the Status
            # Changer UI. The worker still emits the activation warning and
            # uses the combo-post route; only implicit/legacy modes are forced
            # back to strict when launched as a hidden process.
            cfg.setdefault("status", {})["execution_mode"] = "strict_background"
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
    except (
        RuntimeError,
        LookupError,
    ) as e:  # expected setup problems: message, no traceback
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
# GUI (from camfrog_status_gui.py), combined Random + Marquee modes.
# ============================================================


class SingleInstanceGuard:
    """Prevent multiple GUI instances using a Windows named mutex.

    On non-Windows platforms, always allows the instance to run.
    If another instance exists, restores it instead of showing an error.
    Use allow_multiple=True to bypass the check entirely.
    """

    ERROR_ALREADY_EXISTS = 183
    WM_SHOWME = 0x8000 + 42  # Custom message to restore window

    def __init__(self, name: Optional[str] = None, allow_multiple: bool = False):
        self.mode = "both"
        suffix = "" if self.mode == "both" else f"-{self.mode}"
        self.name = name or f"CamfrogStatusChanger{suffix}"
        self.allow_multiple = allow_multiple
        self.handle = None
        self.kernel32 = None
        self.user32 = None

    def acquire(self) -> bool:
        if os.name != "nt" or self.allow_multiple:
            if self.allow_multiple:
                print("[SingleInstanceGuard] --allow-multiple: skipping mutex check")
            return True
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_wchar_p,
        )
        self.kernel32.CreateMutexW.restype = ctypes.c_void_p
        self.kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        self.kernel32.CloseHandle.restype = ctypes.c_int
        ctypes.set_last_error(0)
        mutex_name = f"Local\\{self.name}"
        self.handle = self.kernel32.CreateMutexW(None, False, mutex_name)
        if not self.handle:
            raise OSError(
                ctypes.get_last_error(), f"Could not create mutex {mutex_name}"
            )
        if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS:
            print(
                f"[SingleInstanceGuard] Mutex {mutex_name} already exists - another instance running"
            )
            # Another instance exists - try to restore it
            self._restore_existing_instance()
            return False
        print(f"[SingleInstanceGuard] Created mutex {mutex_name}")
        return True

    def _restore_existing_instance(self):
        """Find the existing window and send a restore message."""
        if os.name != "nt":
            return
        try:
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            # Find window by class name (TkTopLevel) and title containing our app name
            # The first instance registers a window class we can find
            self.user32.EnumWindows.argtypes = (
                ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p),
                ctypes.c_void_p,
            )
            self.user32.EnumWindows.restype = ctypes.c_int
            self.user32.GetWindowTextW.argtypes = (
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_int,
            )
            self.user32.GetWindowTextW.restype = ctypes.c_int
            self.user32.GetClassNameW.argtypes = (
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_int,
            )
            self.user32.GetClassNameW.restype = ctypes.c_int
            self.user32.PostMessageW.argtypes = (
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )
            self.user32.PostMessageW.restype = ctypes.c_int

            found = []

            def enum_callback(hwnd, lparam):
                try:
                    length = self.user32.GetWindowTextW(
                        hwnd, ctypes.create_unicode_buffer(256), 256
                    )
                    if length > 0:
                        title = ctypes.create_unicode_buffer(256)
                        self.user32.GetWindowTextW(hwnd, title, 256)
                        expected = (
                            "ZChat Status Changer"
                            if self.mode == "both"
                            else f"ZChat {self.mode.title()} Status"
                        )
                        if expected in title.value:
                            found.append(hwnd)
                            # Found it - send restore message
                            self.user32.PostMessageW(hwnd, self.WM_SHOWME, 0, 0)
                            return False  # Stop enumeration
                except Exception:
                    pass
                return True  # Continue enumeration

            callback = ctypes.WINFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p
            )(enum_callback)
            self.user32.EnumWindows(callback, 0)
            if found:
                print(
                    f"[SingleInstanceGuard] Found existing window(s): {found} - sent restore message"
                )
            else:
                print(
                    "[SingleInstanceGuard] Mutex exists but no matching window found (stale mutex?)"
                )
        except Exception as e:
            print(f"[SingleInstanceGuard] Error restoring instance: {e}")
            pass  # Best effort

    @staticmethod
    def force_cleanup(name: Optional[str] = None) -> bool:
        """Forcefully release a stale mutex by opening and closing it.
        Returns True if a mutex was found and closed."""
        if os.name != "nt":
            return False
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenMutexW.argtypes = (
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_wchar_p,
            )
            kernel32.OpenMutexW.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel32.CloseHandle.restype = ctypes.c_int
            mode = "both"
            suffix = "" if mode == "both" else f"-{mode}"
            mutex_name = f"Local\\{name or 'CamfrogStatusChanger' + suffix}"
            handle = kernel32.OpenMutexW(
                0x1F0001, False, mutex_name
            )  # MUTEX_ALL_ACCESS
            if handle:
                kernel32.CloseHandle(handle)
                print(f"[SingleInstanceGuard] Force-cleaned stale mutex: {mutex_name}")
                return True
            return False
        except Exception as e:
            print(f"[SingleInstanceGuard] Force cleanup failed: {e}")
            return False

    def release(self):
        if self.handle and self.kernel32:
            self.kernel32.CloseHandle(self.handle)
        self.handle = None


DEFAULTS = {
    "language": "auto",
    "reload_config": True,
    "stats": {"enabled": True, "file": "stats.json", "write_seconds": 60},
    "window_title_regex": ".*Camfrog.*",
    "dry_run": True,
    "poll_seconds": 1.5,
    "log": {
        "level": "INFO",
        "file": "camfrog_status_changer.log",
        "max_bytes": 1000000,
        "backups": 3,
        "log_message_text": True,
    },
    "active_hours": {"enabled": False, "start": "09:00", "end": "23:30"},
    "safety": {
        "stop_file": "camfrog_status_changer.STOP",
        "pid_file": "camfrog_status_changer.pid",
        "require_foreground": True,
        "restore_previous_window": True,
        "max_consecutive_failures": 5,
    },
    "status": {
        "enabled": True,
        "execution_mode": "strict_background",
        "edit": {"class_name": "Edit", "auto_id": "1001", "index": 0},
        "apply_button": None,
        "background_enter_target": "edit",  # experimental "combo" targets the parent CComboBoxTS
        "interval_seconds": 600,
        "set_on_start": True,
        "random": False,
        "language_mode": "both",
        "retry_seconds": 30,
        "max_length": 120,
        "messages": [],
        "schedules": [],
        "language_cycle": [],
        "marquee": {
            "enabled": False,
            "scroll": False,
            "width": 28,
            "stride": 2,
            "step_seconds": 0.5,
            "separator": "   \u2022   ",
            "cycles": 1,
            "max_frames": 80,
            "infinite_loop": False,
            "loop_slots": False,
        },
        "history": {
            "enabled": True,
            "file": "camfrog_status_changer_history.json",
            "max_items": 50,
            "record": True,
            "use_as_source": False,
            "seed_from_messages": True,
            "mode": "rotate",
        },
    },
    "autoreply": {
        "enabled": True,
        "own_nickname": "",
        "window_title_regex": "",
        "log_kind": "room",
        "history_tail": 150,
        "history": {"class_name": "AtlAxWinLic140", "auto_id": "1002", "index": 0},
        "input": {"class_name": "AtlAxWinLic140", "auto_id": "1002", "index": 1},
        "ignore_nicknames": [],
        "only_nicknames": [],
        "per_sender_cooldown_seconds": 300,
        "global_min_gap_seconds": 20,
        "max_per_hour": 30,
        "delay_range_seconds": [2.0, 5.0],
        "max_reply_length": 200,
        "max_incoming_length": 500,
        "rules": [],
        "skip_patterns": [],
        "ignore_links": True,
    },
    # Private-message (IM) auto-reply: separate opt-in, own rules, own limits. Reuses the
    # autoreply.history / autoreply.input selectors (IM windows have the same two web panes).
    "autoreply_im": {
        "enabled": False,
        "dry_run": True,
        "window_title_regex": "",
        "max_windows": 5,
        "log_kinds": ["im"],
        "only_nicknames": [],
        "rules": [],
        "skip_patterns": [],
        "ignore_links": True,
        "prefix": "[auto] ",
        "answer_first_message": True,
        "per_sender_cooldown_seconds": 3600,
        "max_per_sender_per_day": 3,
        "global_min_gap_seconds": 20,
        "max_per_hour": 10,
        "delay_range_seconds": [2.0, 5.0],
        "max_reply_length": 200,
        "max_incoming_length": 500,
    },
    # Room music DJ: needs autoreply.enabled for chat plumbing (see CONFIG docs).
    "dj": {
        "enabled": False,
        "prefix": "!",
        "queue_file": "dj_queue.json",
        "max_per_user": 3,
        "music_dir": "music",
        "audio_backend": "chat",
        "announce_now": "[dj] Now playing: {title} (requested by {user})",
        "announce_queued": "[dj] Queued #{pos}: {title}",
    },
}


STATUS_MODE = "both"

# Keep the combined status app visually aligned with the Web Status GUI.
STATUS_CHANGER_THEME = {
    "background": "#f4f1e8",
    "surface": "#fffdfa",
    "surface_high": "#ebe5d9",
    "entry": "#ffffff",
    "border": "#d5c8ac",
    "accent": "#8b672b",
    "accent_hover": "#76541f",
    "gold": "#8b672b",
    "gold_hover": "#76541f",
    "teal": "#d6e9e8",
    "button_text": "#283648",
    "button_active": "#d5c8ac",
    "button_pressed": "#c2b392",
    "text": "#283648",
    "muted": "#626d7a",
    "danger": "#9f3740",
    "success": "#28744c",
    "error": "#a3303a",
    "log_text": "#2c3b4d",
}

# Where this app keeps its own config, status pool, logs, and worker PID/STOP.
# The combined app keeps its worker state together and status pools separate.
DATA_DIR = (
    Path(os.environ["ZCFATO_DATA_DIR"])
    if os.environ.get("ZCFATO_DATA_DIR")
    else BASE / "status-data"
)

# ---------- Original Status GUI ----------


ACTIVE_MODE_FILE = "camfrog_status_changer.mode"
MODE_SETTINGS_FILE = "status-mode-settings.json"
MARQUEE_STEP_MIN = 0.3
MARQUEE_STRIDE_DEFAULT = 2
RANDOM_INTERVAL_MIN = 10
RANDOM_INTERVAL_DEFAULT = 600
RANDOM_INTERVAL_MAX = 86400
STATUS_SLOTS = 10
CONTROL_DISCOVERY_TIMEOUT_SECONDS = 30


def legacy_mode_data_dir(mode):
    """Find an existing standalone app data folder so its status pool is reused."""
    if mode not in ("random", "marquee"):
        raise ValueError("mode must be random or marquee")
    candidates = (
        BASE / f"{mode}-data",
        BASE.parent / f"status-{mode}" / f"{mode}-data",
    )
    return next((path for path in candidates if path.is_dir()), None)


def mode_pool_path(mode):
    if mode not in ("random", "marquee"):
        raise ValueError("mode must be random or marquee")
    return DATA_DIR / f"{mode}.db"


def legacy_mode_config(mode):
    legacy = legacy_mode_data_dir(mode)
    if legacy is None:
        return None
    config_path = legacy / "camfrog-status-config.json"
    if not config_path.is_file():
        return None
    try:
        return load_cfg(config_path)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def initial_combined_config():
    """Merge mode-specific legacy settings when creating the combined config."""
    random_config = legacy_mode_config("random")
    marquee_config = legacy_mode_config("marquee")
    result = deep_merge(DEFAULTS, random_config or marquee_config or {})
    if marquee_config:
        result["status"]["marquee"] = copy.deepcopy(
            marquee_config["status"].get("marquee", DEFAULTS["status"]["marquee"])
        )
        result["status"]["background_enter_target"] = marquee_config["status"].get(
            "background_enter_target",
            result["status"].get("background_enter_target", "edit"),
        )
        result["status"]["execution_mode"] = marquee_config["status"].get(
            "execution_mode", result["status"].get("execution_mode", "interactive")
        )
    # The single worker identity belongs to the combined app, regardless of which
    # legacy config supplied the shared Camfrog selectors.
    result["status"]["random"] = False
    result["status"]["marquee"]["enabled"] = False
    result["status"]["enabled"] = False
    result["dry_run"] = True
    return result


def load_mode_intervals(legacy_configs=None, data_dir=None):
    """Load separate Random/Marquee intervals, preferring combined-app settings."""
    legacy_configs = legacy_configs or {}
    values = {}
    try:
        settings_path = Path(data_dir or DATA_DIR) / MODE_SETTINGS_FILE
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            values = saved
    except (OSError, ValueError):
        pass
    result = {}
    for mode in ("random", "marquee"):
        fallback = (
            (legacy_configs.get(mode) or {})
            .get("status", {})
            .get("interval_seconds", DEFAULTS["status"]["interval_seconds"])
        )
        raw = values.get(mode, fallback)
        try:
            result[mode] = parse_interval_seconds(raw)
        except (TypeError, ValueError):
            result[mode] = DEFAULTS["status"]["interval_seconds"]
    return result


def status_mode_config_paths(config_path):
    """Return independent config paths for the combined app's two status modes."""
    source = Path(config_path).resolve()
    if source.name == "camfrog-status-config.json":
        return {
            mode: source.parent / f"{mode}-config.json"
            for mode in ("random", "marquee")
        }
    return {
        mode: source.with_name(f"{source.stem}-{mode}.json")
        for mode in ("random", "marquee")
    }


def initialize_status_mode_configs(config_path):
    """Create missing per-mode configs, copying legacy settings without deleting them."""
    source_path = Path(config_path).resolve()
    paths = status_mode_config_paths(source_path)
    shared = load_cfg(source_path) if source_path.is_file() else None
    legacy = {mode: legacy_mode_config(mode) for mode in ("random", "marquee")}
    old_intervals = load_mode_intervals(legacy, source_path.parent)
    settings_path = source_path.parent / MODE_SETTINGS_FILE
    if shared and not settings_path.is_file() and not legacy.get("random"):
        try:
            old_intervals["random"] = parse_interval_seconds(
                shared["status"]["interval_seconds"]
            )
        except (KeyError, TypeError, ValueError):
            pass

    for mode, path in paths.items():
        if path.is_file():
            continue
        mode_legacy = legacy.get(mode)
        seed = shared or mode_legacy or initial_combined_config()
        config = deep_merge(DEFAULTS, seed)
        if mode_legacy:
            # A pre-existing standalone app may already have mode-specific
            # messages and Marquee controls. Keep those while the old combined
            # file remains the source for shared Camfrog selectors/settings.
            legacy_status = mode_legacy.get("status", {})
            for key, value in legacy_status.items():
                if key in {"edit", "apply_button", "enabled", "random"}:
                    continue
                if mode == "random" and key == "marquee":
                    continue
                if mode == "marquee" and key == "interval_seconds":
                    continue
                config["status"][key] = copy.deepcopy(value)
        if mode == "random":
            config["status"]["interval_seconds"] = old_intervals["random"]
        config["status"]["random"] = mode == "random"
        config["status"]["marquee"]["enabled"] = mode == "marquee"
        config["status"]["enabled"] = False
        write_config(path, config)
    return paths


def parse_interval_seconds(raw):
    """Switch time in seconds. Preserve fractions and clamp to the allowed window.

    Raises ValueError on non-numeric input (including nan/inf)."""
    try:
        value = float(str(raw).strip())
        if not math.isfinite(value):
            raise ValueError("interval must be finite")
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid interval: {raw!r}") from exc
    value = min(RANDOM_INTERVAL_MAX, max(RANDOM_INTERVAL_MIN, value))
    return int(value) if value.is_integer() else value


def configure_status_mode(
    config, mode, messages, interval_seconds, marquee_options=None
):
    """Apply only the settings owned by the selected status workflow."""
    if mode not in ("random", "marquee"):
        raise ValueError("mode must be random or marquee")

    status = config["status"]
    marquee = status["marquee"]
    status["interval_seconds"] = parse_interval_seconds(interval_seconds)
    status["messages"] = messages
    status["random"] = mode == "random"
    marquee["enabled"] = mode == "marquee"

    # Marquee controls are independent from Random. An unfinished or invalid
    # marquee edit must not prevent saving or starting the Random workflow.
    if mode == "marquee":
        options = marquee_options or {}
        try:
            step = float(str(options["step"]).strip())
            stride = float(str(options["stride"]).strip())
            if not math.isfinite(step) or not math.isfinite(stride):
                raise ValueError("marquee values must be finite")
            marquee["step_seconds"] = max(MARQUEE_STEP_MIN, step)
            marquee["stride"] = max(1, int(stride))
            if "loop_slots" in options:
                marquee["loop_slots"] = bool(options["loop_slots"])
                marquee["infinite_loop"] = False
            else:
                # Backward compatibility for callers using the old checkbox
                # option name. New GUI saves use loop_slots explicitly.
                legacy_loop = bool(options["infinite_loop"])
                marquee["loop_slots"] = legacy_loop
                marquee["infinite_loop"] = legacy_loop
            marquee["scroll"] = bool(options["scroll"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid marquee speed values") from exc

    return config


def read_status_pool(path, seed_messages=None):
    """Read one ten-slot pool, seeding a new database from legacy settings once."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    was_missing = not path.exists()
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS status_slots "
                "(slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND 10), message TEXT NOT NULL)"
            )
            if was_missing and seed_messages is not None:
                values = [
                    message_to_text(message) for message in seed_messages[:STATUS_SLOTS]
                ]
                values.extend([""] * (STATUS_SLOTS - len(values)))
                connection.executemany(
                    "INSERT INTO status_slots(slot, message) VALUES(?, ?)",
                    enumerate(values, start=1),
                )
            rows = dict(connection.execute("SELECT slot, message FROM status_slots"))
    return [str(rows.get(slot, "")) for slot in range(1, STATUS_SLOTS + 1)]


def write_status_pool(path, values):
    """Persist all ten slots in a mode-specific SQLite database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    values = [str(value) for value in values[:STATUS_SLOTS]]
    values.extend([""] * (STATUS_SLOTS - len(values)))
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS status_slots "
                "(slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND 10), message TEXT NOT NULL)"
            )
            connection.execute("DELETE FROM status_slots")
            connection.executemany(
                "INSERT INTO status_slots(slot, message) VALUES(?, ?)",
                enumerate(values, start=1),
            )


def migrate_status_pools():
    """Copy legacy standalone pools into this app's private data folder once."""
    paths = {mode: DATA_DIR / f"{mode}.db" for mode in ("random", "marquee")}
    for mode, target in paths.items():
        if target.is_file():
            continue
        legacy_dir = legacy_mode_data_dir(mode)
        source = legacy_dir / f"{mode}.db" if legacy_dir is not None else None
        if source is None or not source.is_file():
            continue
        try:
            # Read-only query; do not alter a database still owned by the old app.
            with closing(sqlite3.connect(source)) as connection:
                rows = dict(
                    connection.execute("SELECT slot, message FROM status_slots")
                )
            values = [str(rows.get(slot, "")) for slot in range(1, STATUS_SLOTS + 1)]
            write_status_pool(target, values)
        except (OSError, sqlite3.Error) as exc:
            logging.warning(
                "Could not import legacy %s status pool: %s", mode, type(exc).__name__
            )
    return paths


def text_to_message(value):
    value = value.strip()
    if not value:
        return None
    if "||" in value:
        th, en = (part.strip() for part in value.split("||", 1))
        if th and en:
            return {"th": th, "en": en}
        return th or en or None
    return value


def message_to_text(value):
    if isinstance(value, dict):
        return f"{value.get('th', '')} || {value.get('en', '')}"
    return str(value or "")


def messages_from_slots(values):
    """Return the configured, non-empty messages represented by the ten GUI slots."""
    result = []
    for value in values:
        message = text_to_message(value)
        if message is not None:
            result.append(message)
    return result


def normalize_marquee_loop(config):
    """Interpret the legacy Marquee Loop flag as looping through status slots."""
    marquee = config.get("status", {}).get("marquee", {})
    if not marquee.get("enabled"):
        return config
    if marquee.get("loop_slots") or marquee.get("infinite_loop"):
        marquee["loop_slots"] = True
        marquee["infinite_loop"] = False
    return config


def runtime_config(config, mode="random"):
    """Build worker settings for one mode while sharing one worker identity."""
    if mode not in ("random", "marquee"):
        raise ValueError("mode must be random or marquee")
    runtime = copy.deepcopy(config)
    status = runtime.setdefault("status", {})
    # Normal workers fail closed in strict mode. An explicit local test flag
    # opts into a combo-parent route that remains externally unverified and may
    # activate Camfrog.
    if _unverified_status_test_submit_enabled():
        status["execution_mode"] = "interactive"
        status["background_enter_target"] = "combo"
    elif status.get("execution_mode") == "best_effort_background":
        status["background_enter_target"] = "combo"
    else:
        status["execution_mode"] = "strict_background"
    status["random"] = mode == "random"
    status.setdefault("marquee", {})["enabled"] = mode == "marquee"
    if mode == "marquee":
        normalize_marquee_loop(runtime)
    runtime["autoreply"]["enabled"] = False
    runtime.setdefault("autoreply_im", {})["enabled"] = False
    runtime["stats"]["file"] = f"{mode}-status-stats.json"
    runtime["log"]["file"] = f"{mode}-status-worker.log"
    status.setdefault("history", {})["file"] = f"{mode}-status-history.json"
    safety = runtime.setdefault("safety", {})
    safety["pid_file"] = "camfrog_status_changer.pid"
    safety["stop_file"] = "camfrog_status_changer.STOP"
    return runtime


def status_config_for_live_send(config, live_send):
    """Return a copy whose dry-run setting matches the explicit UI live-send toggle."""
    result = copy.deepcopy(config)
    result["dry_run"] = not bool(live_send)
    return result


def write_config(path, config):
    atomic_write(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")


_ACTIVITY_LOG_LOCK = threading.Lock()

STATUS_LOG_FILES = (
    ("status_activity.log", None),
    ("random-status-worker.log", "random"),
    ("marquee-status-worker.log", "marquee"),
    ("camfrog_status_changer.log", None),
)


def _local_now(now=None):
    value = now if now is not None else dt.datetime.now().astimezone()
    return value.astimezone() if value.tzinfo is None else value


def _log_clean_marker(path):
    path = Path(path)
    return path.with_name(f".{path.name}.clean-date")


def _log_cleanup_due(path, now):
    if now.time().replace(tzinfo=None) < dt.time(12, 0):
        return False
    try:
        last_clean = _log_clean_marker(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        last_clean = ""
    return last_clean != now.date().isoformat()


def _clear_log_file(path, now, backup_count=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8"):
        pass
    for index in range(1, max(0, int(backup_count)) + 1):
        Path(f"{path}.{index}").unlink(missing_ok=True)
    atomic_write(_log_clean_marker(path), now.date().isoformat() + "\n")


def clean_status_logs_at_noon(data_dir=None, now=None, active_modes=()):
    """Clear the combined Status Changer logs once per local day at or after noon."""
    data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
    local_now = _local_now(now)
    active_modes = {mode for mode in active_modes if mode in ("random", "marquee")}
    cleaned = []
    with _ACTIVITY_LOG_LOCK:
        for filename, mode in STATUS_LOG_FILES:
            if mode in active_modes:
                continue
            path = data_dir / filename
            if not _log_cleanup_due(path, local_now):
                continue
            try:
                _clear_log_file(path, local_now, backup_count=3)
            except OSError:
                continue
            cleaned.append(filename)
    return tuple(cleaned)


def clean_worker_log_at_noon(now=None, handlers=None):
    """Reset this worker's open rotating log safely once local noon has passed."""
    local_now = _local_now(now)
    if local_now.time().replace(tzinfo=None) < dt.time(12, 0):
        return ()
    handlers = logging.getLogger().handlers if handlers is None else handlers
    worker_logs = {
        "random-status-worker.log",
        "marquee-status-worker.log",
        "camfrog_status_changer.log",
    }
    cleaned = []
    for handler in tuple(handlers):
        if not isinstance(handler, logging.handlers.RotatingFileHandler):
            continue
        path = Path(handler.baseFilename)
        if path.name not in worker_logs:
            continue
        handler.acquire()
        try:
            if not _log_cleanup_due(path, local_now):
                continue
            try:
                if handler.stream is not None:
                    handler.flush()
                    handler.stream.close()
                    handler.stream = None
                _clear_log_file(path, local_now, backup_count=handler.backupCount)
                cleaned.append(path.name)
            except OSError:
                pass
            finally:
                if handler.stream is None:
                    try:
                        handler.stream = handler._open()
                    except OSError:
                        pass
        finally:
            handler.release()
    return tuple(cleaned)


def write_activity_log(mode, message):
    """Append a credential-free GUI event to the combined app activity log."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        safe_mode = mode if mode in ("random", "marquee") else "app"
        safe_message = " ".join(
            str(message).replace("\r", " ").replace("\n", " ").split()
        )
        line = f"{stamp} | {safe_mode} | {safe_message}\n"
        with _ACTIVITY_LOG_LOCK:
            with (DATA_DIR / "status_activity.log").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(line)
    except OSError:
        logging.exception("Could not append Status Changer activity log")


def active_worker_mode():
    try:
        value = (DATA_DIR / ACTIVE_MODE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if value in ("random", "marquee") else None


def apply_status_text(config_path, text):
    """Apply one status with the saved safety settings, discovering controls if needed."""
    config = apply_app_profile(load_cfg(config_path))
    try:
        return cmd_status(config, text)
    except (LookupError, RuntimeError):
        try:
            detected = cmd_detect(config, config_path, apply=True)
        except RuntimeError as exc:
            raise RuntimeError(
                t(
                    "Cannot find Camfrog window. Is Camfrog running?",
                    "ไม่พบหน้าต่าง Camfrog เปิดแล้วหรือยัง",
                )
            ) from exc
        if detected == 0:
            return cmd_status(apply_app_profile(load_cfg(config_path)), text)
        raise RuntimeError(
            t(
                "Could not find status controls. Open a chat room in Camfrog and retry.",
                "ไม่พบ control ของ status เปิดห้องแชทใน Camfrog แล้วลองใหม่",
            )
        )


class StatusOperationCancelled(Exception):
    """Raised when Stop cancels an in-progress one-shot status animation."""


def _raise_if_status_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise StatusOperationCancelled("status update cancelled")


def _commit_frames(config, frames, cancel_event=None):
    """Send prebuilt frames one by one, stopping between frames on request."""
    config = apply_app_profile(config)
    st, sf = config["status"], config["safety"]
    mq = st["marquee"]
    strict = st.get("execution_mode", "interactive") == "strict_background"
    _raise_if_status_cancelled(cancel_event)
    if strict:
        # Strict path discovers and verifies process/controls itself per
        # frame; never resolve legacy visible controls first.
        win = ctrl = btn = None
    else:
        win = get_window(config)
        ctrl = find(win, st["edit"])
        btn = find(win, st["apply_button"]) if st["apply_button"] else None
    try:
        step = max(MARQUEE_STEP_MIN, float(mq.get("step_seconds", MARQUEE_STEP_MIN)))
    except (TypeError, ValueError):
        step = MARQUEE_STEP_MIN
    for i, frame in enumerate(frames):
        _raise_if_status_cancelled(cancel_event)
        if strict:
            ok = commit_background(frame, dry=config["dry_run"])
        else:
            ok = commit(
                win,
                ctrl,
                frame,
                config["dry_run"],
                sf["require_foreground"],
                btn,
                sf["restore_previous_window"],
                st.get("background_enter_target", "edit"),
                background_if_not_foreground=True,
                execution_mode=st.get("execution_mode", "interactive"),
            )
        if not ok:
            print(t("failed"))
            return 1
        # A frame already being submitted cannot be rolled back, but Stop must
        # prevent every later frame from being queued to Camfrog.
        _raise_if_status_cancelled(cancel_event)
        if i < len(frames) - 1:
            if cancel_event is not None:
                if cancel_event.wait(step):
                    raise StatusOperationCancelled("status update cancelled")
            else:
                time.sleep(step)
    print(t("ok"))
    return 0


def apply_status_scrolling(config_path, text, cancel_event=None):
    """Apply one status, scrolling it immediately when scroll is enabled.

    Static configs fall back to the single-shot path (with control discovery).
    A supplied event lets the GUI Stop button cancel the frame sequence.
    """
    _raise_if_status_cancelled(cancel_event)
    config = load_cfg(config_path)
    frames = Runner(config).build_frames(
        expand(text, own=config["autoreply"]["own_nickname"])
    )
    if len(frames) <= 1:
        _raise_if_status_cancelled(cancel_event)
        return apply_status_text(config_path, text)
    try:
        return _commit_frames(config, frames, cancel_event=cancel_event)
    except (LookupError, RuntimeError):
        _raise_if_status_cancelled(cancel_event)
        try:
            detected = cmd_detect(config, config_path, apply=True)
        except RuntimeError as exc:
            raise RuntimeError(
                t(
                    "Cannot find Camfrog window. Is Camfrog running?",
                    "ไม่พบหน้าต่าง Camfrog เปิดแล้วหรือยัง",
                )
            ) from exc
        if detected == 0:
            _raise_if_status_cancelled(cancel_event)
            return _commit_frames(
                load_cfg(config_path), frames, cancel_event=cancel_event
            )
        raise RuntimeError(
            t(
                "Could not find status controls. Open a chat room in Camfrog and retry.",
                "ไม่พบ control ของ status เปิดห้องแชทใน Camfrog แล้วลองใหม่",
            )
        )


def cmd_start(cfg, args):
    if _strict_status_start_blocked(cfg):
        return 2
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    registered = read_pid(cfg)
    if registered and pid_alive(registered):
        print(f"Existing worker PID {registered} is alive but could not be verified; refusing another start.")
        return 1
    pid_path(cfg).unlink(missing_ok=True)
    start_gate = worker_start_gate_path(cfg)
    start_gate.unlink(missing_ok=True)
    logf = BASE / cfg["log"]["file"]
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    env = child_env()
    env["ZCFATO_WORKER_START_GATE"] = str(start_gate)
    p = subprocess.Popen(
        child_cmd(args) + ["run"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
        cwd=str(BASE),
        env=env,
    )
    if not _wait_for_worker_registration(cfg, p, logf, print):
        return 1
    print(t("bg_started", pid=read_pid(cfg), log=logf))
    return 0


def worker_cmd(args):
    """Relaunch this standalone app in its hidden worker mode."""
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        entrypoint = Path(__file__).with_name("status_changer_entry.py")
        command = [
            str(pythonw if pythonw.exists() else python),
            str(entrypoint.resolve()),
        ]
    command.append("--worker")
    cfg_arg = []
    if getattr(args, "config", None):
        cfg_arg += ["--config", str(Path(args.config).resolve())]
    if getattr(args, "lang", None):
        cfg_arg += ["--lang", args.lang]
    return command + cfg_arg


def run_control_discovery(
    config_path, apply=True, timeout=CONTROL_DISCOVERY_TIMEOUT_SECONDS
):
    """Run UI Automation discovery out of process so a hung UIA call cannot pin the GUI busy."""
    args = SimpleNamespace(config=str(Path(config_path).resolve()), lang=None)
    command = worker_cmd(args)

    # A console Python child keeps CLI errors available to its caller while
    # CREATE_NO_WINDOW prevents a console from flashing beside the GUI.
    if not getattr(sys, "frozen", False):
        console_python = Path(command[0]).with_name("python.exe")
        if console_python.is_file():
            command[0] = str(console_python)
    command.extend(("detect", "--apply") if apply else ("detect",))
    creationflags = (0x08000000 | 0x00000200) if os.name == "nt" else 0
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(BASE),
            env=child_env(),
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        seconds = int(timeout) if float(timeout).is_integer() else timeout
        raise RuntimeError(t("discover_timeout", seconds=seconds)) from exc
    if result.returncode != 0:
        raise RuntimeError(t("discover_failed", code=result.returncode))
    return result.returncode


def cmd_start_worker(cfg, args, report=None):
    """Start this app's hidden status worker in the background."""
    emit = report if report is not None else print
    if _strict_status_start_blocked(cfg, emit=emit):
        return 2
    pid = running_pid(cfg)
    if pid:
        emit(t("already_running", pid=pid))
        return 1
    if os.name != "nt":
        emit(t("win_only"))
        return 2
    if not cfg["log"]["file"]:
        emit(t("bg_needs_log"))
        return 2
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    registered = read_pid(cfg)
    if registered and pid_alive(registered):
        emit(f"Existing worker PID {registered} is alive but could not be verified; refusing another start.")
        return 1
    pid_path(cfg).unlink(missing_ok=True)
    start_gate = worker_start_gate_path(cfg)
    start_gate.unlink(missing_ok=True)
    logf = DATA_DIR / cfg["log"]["file"]
    flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    env = child_env()
    env["ZCFATO_DATA_DIR"] = str(DATA_DIR)
    env["ZCFATO_STATUS_MODE"] = STATUS_MODE
    env["ZCFATO_WORKER_START_GATE"] = str(start_gate)
    p = subprocess.Popen(
        worker_cmd(args) + ["run"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
        cwd=str(DATA_DIR),
        env=env,
    )
    if not _wait_for_worker_registration(cfg, p, logf, emit):
        return 1
    emit(t("bg_started", pid=read_pid(cfg), log=logf))
    return 0


def cmd_stop(cfg):
    pid = running_pid(cfg)
    stop_file = DATA_DIR / cfg["safety"]["stop_file"]
    if not pid:
        print(t("not_running"))
        pid_path(cfg).unlink(missing_ok=True)
        (DATA_DIR / ACTIVE_MODE_FILE).unlink(missing_ok=True)
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
            subprocess.run(
                [system_taskkill_path(), "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
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
    (DATA_DIR / ACTIVE_MODE_FILE).unlink(missing_ok=True)
    return 0


STRUCT_SKIP = {"Text", "Hyperlink", "Image", "ListItem", "DataItem", "TreeItem"}

# ---------- Inlined ClipboardController ----------


_WIDGET_CLASSES = {
    "Entry",
    "TEntry",
    "TCombobox",
    "Spinbox",
    "TSpinbox",
    "Text",
    "Listbox",
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
        for label, action in (
            ("Cut|ตัด", "cut"),
            ("Copy|คัดลอก", "copy"),
            ("Paste|วาง", "paste"),
        ):
            self.menu.add_command(
                label=self.translate(label),
                command=lambda op=action: self.perform(self.target, op),
            )
        self.menu.add_separator()
        self.menu.add_command(
            label=self.translate("Select all|เลือกทั้งหมด"),
            command=lambda: self.select_all(self.target),
        )

    def refresh_menu(self):
        labels = (
            "Cut|\u0e15\u0e31\u0e14",
            "Copy|\u0e04\u0e31\u0e14\u0e25\u0e2d\u0e01",
            "Paste|\u0e27\u0e32\u0e07",
            "Select all|\u0e40\u0e25\u0e37\u0e2d\u0e01\u0e17\u0e31\u0e49\u0e07\u0e2b\u0e21\u0e14",
        )
        for index, label in enumerate(labels):
            menu_index = index if index < 3 else 4
            self.menu.entryconfigure(menu_index, label=self.translate(label))

    def install(self, parent):
        """Bind editors recursively, including controls added after the first build."""
        for widget in parent.winfo_children():
            widget_class = _widget_class(widget)
            if (
                widget_class in _WIDGET_CLASSES
                and widget not in self._installed_widgets
            ):
                self._installed_widgets.add(widget)
                self.widgets.append(widget)
                if widget_class in {"Text", "Listbox"}:
                    try:
                        widget.configure(
                            background=(
                                STATUS_CHANGER_THEME["entry"]
                                if widget_class == "Text"
                                else STATUS_CHANGER_THEME["surface"]
                            ),
                            foreground=STATUS_CHANGER_THEME["text"],
                            selectbackground=STATUS_CHANGER_THEME["accent"],
                            selectforeground=STATUS_CHANGER_THEME["surface"],
                            insertbackground=STATUS_CHANGER_THEME["text"],
                            highlightbackground=STATUS_CHANGER_THEME["border"],
                            highlightcolor=STATUS_CHANGER_THEME["accent"],
                        )
                    except Exception:
                        pass
                for sequence, action in (
                    ("<Control-c>", "copy"),
                    ("<Control-x>", "cut"),
                    ("<Control-v>", "paste"),
                    ("<Control-Insert>", "copy"),
                    ("<Shift-Delete>", "cut"),
                    ("<Shift-Insert>", "paste"),
                ):
                    widget.bind(
                        sequence,
                        lambda event, op=action: self.handle(event, op),
                        add="+",
                    )
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
        key = next(
            (
                name
                for name, (chars, code, _action) in shortcuts.items()
                if keysym == name or char in chars or keycode == code
            ),
            None,
        )
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


def build_app():
    import tkinter as tk
    from tkinter import messagebox, ttk

    class StatusChanger:
        def __init__(
            self, config_path, *, parent=None, on_closed=None, on_close_failed=None
        ):
            self.embedded = parent is not None
            self.on_closed = on_closed
            self.on_close_failed = on_close_failed
            self._closed_callback_called = False
            self.config_paths = initialize_status_mode_configs(config_path)
            self.config_path = self.config_paths["random"]
            self.runtime_config_paths = {
                "random": DATA_DIR / "random-status-runtime.json",
                "marquee": DATA_DIR / "marquee-status-runtime.json",
            }
            self.root = parent if self.embedded else tk.Tk()
            self.root.configure(background=STATUS_CHANGER_THEME["background"])
            self.app_name = "Status Changer"
            if not self.embedded:
                title = f"ZChat {self.app_name}"
                if _unverified_status_test_submit_enabled():
                    title += " [UNVERIFIED TEST MODE]"
                self.root.title(title)
                self.root.geometry("520x700")
                self.root.minsize(480, 640)
                self.root.resizable(True, True)
                try:
                    self.root.iconbitmap(str(_application_icon_path()))
                except (tk.TclError, OSError):
                    pass
            self.busy = False
            self.close_requested = False
            self.stop_in_progress = False
            self.pending_stop_mode = None
            self.task_results = queue.Queue()
            self.stop_results = queue.Queue()
            self.task_poll_job = None
            self.state_job = None
            self.save_job = None
            self.mode = "random"
            self.loading_mode = False
            self.activity_refresh_job = None
            self.log_cleanup_job = None
            self.activity_snapshot = None
            self.entry_widgets = {"random": [], "marquee": []}
            try:
                self.config = load_cfg(self.config_path)
            except Exception as exc:
                messagebox.showerror(
                    f"ZChat {self.app_name}", str(exc), parent=self.root
                )
                if not self.embedded:
                    self.root.destroy()
                raise SystemExit(2)
            global LANG
            self.language_preference = self.config.get("language", "auto")
            LANG = resolve_lang(self.language_preference)
            self.pool_paths = migrate_status_pools()
            # Only Random has a wall-clock switch interval. Marquee advances by
            # its Frame setting, so it has no hidden Every control/value.
            self.intervals = {
                "random": tk.StringVar(
                    value=str(self.config["status"]["interval_seconds"])
                )
            }
            self.random_fields = [tk.StringVar(value="") for _ in range(STATUS_SLOTS)]
            self.marquee_fields = [tk.StringVar(value="") for _ in range(STATUS_SLOTS)]
            self.step = tk.StringVar(
                value=str(
                    max(
                        MARQUEE_STEP_MIN,
                        self.config["status"]["marquee"]["step_seconds"],
                    )
                )
            )
            self.stride = tk.StringVar(
                value=str(
                    self.config["status"]["marquee"].get(
                        "stride", MARQUEE_STRIDE_DEFAULT
                    )
                )
            )
            marquee = self.config["status"]["marquee"]
            self.loop_slots = tk.BooleanVar(
                value=bool(
                    marquee.get("loop_slots", False)
                    or marquee.get("infinite_loop", False)
                )
            )
            self.scroll_frames = tk.BooleanVar(
                value=bool(self.config["status"]["marquee"].get("scroll", False))
            )
            initial_submit_mode = self.config["status"].get("execution_mode", "strict_background")
            if initial_submit_mode not in ("strict_background", "best_effort_background"):
                initial_submit_mode = "strict_background"
            self.execution_mode = tk.StringVar(value=initial_submit_mode)
            self.live_send = tk.BooleanVar(value=not self.config.get("dry_run", True))
            try:
                self._load_fields()
            except (OSError, sqlite3.Error) as exc:
                messagebox.showerror(
                    f"ZChat {self.app_name}", str(exc), parent=self.root
                )
                if not self.embedded:
                    self.root.destroy()
                raise SystemExit(2)
            for variable in (
                self.random_fields
                + self.marquee_fields
                + list(self.intervals.values())
                + [self.step, self.stride, self.live_send, self.execution_mode]
            ):
                variable.trace_add("write", self.schedule_save)
            self._build()
            self.clean_logs_at_noon()
            self._log_activity("Application opened", "app")
            if _unverified_status_test_submit_enabled():
                self._log_activity(
                    "UNVERIFIED TEST MODE enabled: combo-post dispatch; "
                    "Camfrog may activate and friend-list PASS/FAIL must be "
                    "recorded separately",
                    "app",
                )
            self.clipboard_controller = ClipboardController(
                self.root,
                translate=self._translate_clipboard,
                on_error=self._clipboard_error,
            )
            self.clipboard_controller.install(self.root)
            self.root.bind("<F3>", lambda _e: self.set_worker_running(self.mode, True))
            self.root.bind("<F4>", lambda _e: self.set_worker_running(self.mode, False))
            if not self.embedded:
                self.root.protocol("WM_DELETE_WINDOW", self.close_app)
            self.task_poll_job = self.root.after(100, self.poll_task_results)
            self.state_job = self.root.after(500, self.refresh_state)
            self.activity_refresh_job = self.root.after(500, self.refresh_activity_log)
            if not self.embedded:
                self.root.after(1000, self._discover_controls_on_start)
                self.root.after(2500, self._auto_update_check)

            # A second launch restores the visible standalone window.
            if os.name == "nt" and not self.embedded:
                self._register_single_instance_handler()

        def _load_fields(self):
            for mode, fields in (
                ("random", self.random_fields),
                ("marquee", self.marquee_fields),
            ):
                mode_config = load_cfg(self.config_paths[mode])
                seed = mode_config["status"].get("messages", [])[:STATUS_SLOTS]
                values = read_status_pool(self.pool_paths[mode], seed_messages=seed)
                for var, message in zip(fields, values[:STATUS_SLOTS]):
                    var.set(message)

        def _fields_for_mode(self, mode=None):
            mode = mode or self.mode
            return self.random_fields if mode == "random" else self.marquee_fields

        def _load_mode_controls(self, mode):
            """Load that mode's independent settings into the shared controls."""
            self.loading_mode = True
            try:
                self.config_path = self.config_paths[mode]
                self.config = load_cfg(self.config_path)
                self.live_send.set(not self.config.get("dry_run", True))
                submit_mode = self.config["status"].get("execution_mode", "strict_background")
                self.execution_mode.set(
                    submit_mode if submit_mode in (
                        "strict_background", "best_effort_background"
                    ) else "strict_background"
                )
                status = self.config["status"]
                if mode == "random":
                    self.intervals["random"].set(str(status["interval_seconds"]))
                else:
                    marquee = status["marquee"]
                    self.step.set(str(max(MARQUEE_STEP_MIN, marquee["step_seconds"])))
                    self.stride.set(str(marquee.get("stride", MARQUEE_STRIDE_DEFAULT)))
                    self.loop_slots.set(
                        bool(
                            marquee.get("loop_slots", False)
                            or marquee.get("infinite_loop", False)
                        )
                    )
                    self.scroll_frames.set(bool(marquee.get("scroll", False)))
            finally:
                self.loading_mode = False

        def _tr(self, en, th):
            return th if LANG == "th" else en

        def _change_language(self, _event=None):
            global LANG
            LANG = "th" if self.language_box.get() == "TH" else "en"
            self.language_preference = LANG
            # Language is a shared UI preference; mirror only this field into
            # each mode file so reopening the app in either tab keeps the choice.
            try:
                for config_path in self.config_paths.values():
                    config = load_cfg(config_path)
                    config["language"] = self.language_preference
                    write_config(config_path, config)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logging.exception("Could not save Status Changer language preference")
                try:
                    self.note.configure(
                        text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                    )
                except (AttributeError, tk.TclError):
                    pass
            self._build()
            if hasattr(self, "clipboard_controller"):
                self.clipboard_controller.widgets.clear()
                self.clipboard_controller.target = None
                self.clipboard_controller.refresh_menu()
                self.clipboard_controller.install(self.root)
            self.schedule_save()

        def _translate_clipboard(self, text):
            en, separator, th = text.partition("||")
            if not separator:
                en, _, th = text.partition("|")
            th, en = th.strip(), en.strip()
            return th if LANG == "th" and th else en

        def _clipboard_error(self, exc):
            try:
                self.note.configure(
                    text=self._tr(f"Clipboard error: {exc}", f"คลิปบอร์ดผิดพลาด: {exc}")
                )
            except tk.TclError:
                pass

        def _build(self):
            root = self.root
            clipboard_menu = getattr(
                getattr(self, "clipboard_controller", None), "menu", None
            )
            for child in root.winfo_children():
                if child is not clipboard_menu:
                    child.destroy()
            self.entry_widgets = {"random": [], "marquee": []}
            style = ttk.Style(root)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            colors = STATUS_CHANGER_THEME
            root.configure(background=colors["background"])
            style.configure("TFrame", background=colors["background"])
            style.configure(
                "TLabel",
                background=colors["background"],
                foreground=colors["text"],
                font=("Segoe UI", 9),
            )
            style.configure(
                "TCheckbutton",
                background=colors["background"],
                foreground=colors["text"],
                font=("Segoe UI", 9),
            )
            style.map(
                "TCheckbutton",
                background=[("active", colors["background"])],
                foreground=[("disabled", colors["muted"])],
            )
            style.configure(
                "TRadiobutton",
                background=colors["background"],
                foreground=colors["text"],
            )
            style.configure(
                "TLabelframe",
                background=colors["background"],
                bordercolor=colors["border"],
                relief="solid",
                padding=8,
            )
            style.configure(
                "TLabelframe.Label",
                background=colors["background"],
                foreground=colors["accent"],
                font=("Segoe UI", 9, "bold"),
            )
            style.configure(
                "TButton",
                padding=(10, 6),
                background=colors["surface_high"],
                foreground=colors["button_text"],
                bordercolor=colors["border"],
                font=("Segoe UI", 9, "bold"),
                relief="flat",
            )
            style.map(
                "TButton",
                background=[
                    ("disabled", colors["surface"]),
                    ("pressed", colors["button_pressed"]),
                    ("active", colors["button_active"]),
                ],
                foreground=[("disabled", colors["muted"])],
            )
            style.configure(
                "Action.TButton", padding=(8, 7), font=("Segoe UI", 9, "bold")
            )
            style.configure(
                "Primary.TButton",
                padding=(12, 7),
                background=colors["accent"],
                foreground=colors["surface"],
                bordercolor=colors["accent"],
                font=("Segoe UI", 9, "bold"),
                relief="flat",
            )
            style.map(
                "Primary.TButton",
                background=[
                    ("disabled", colors["surface_high"]),
                    ("pressed", colors["accent"]),
                    ("active", colors["accent_hover"]),
                ],
                foreground=[("disabled", colors["muted"])],
            )
            style.configure(
                "Success.TButton",
                padding=(9, 7),
                background=colors["success"],
                foreground=colors["surface"],
                bordercolor=colors["success"],
                font=("Segoe UI", 9, "bold"),
                relief="flat",
            )
            style.map(
                "Success.TButton",
                background=[
                    ("disabled", colors["surface_high"]),
                    ("pressed", colors["success"]),
                    ("active", colors["success"]),
                ],
                foreground=[("disabled", colors["muted"])],
            )
            style.configure(
                "Danger.TButton",
                padding=(9, 7),
                background=colors["danger"],
                foreground=colors["surface"],
                bordercolor=colors["danger"],
                font=("Segoe UI", 9, "bold"),
                relief="flat",
            )
            style.map(
                "Danger.TButton",
                background=[
                    ("disabled", colors["surface_high"]),
                    ("pressed", colors["danger"]),
                    ("active", colors["danger"]),
                ],
                foreground=[("disabled", colors["muted"])],
            )
            style.configure(
                "TEntry",
                padding=(7, 6),
                fieldbackground=colors["entry"],
                foreground=colors["text"],
                bordercolor=colors["border"],
                lightcolor=colors["accent"],
                darkcolor=colors["border"],
            )
            style.configure(
                "TSpinbox",
                padding=(5, 5),
                fieldbackground=colors["entry"],
                foreground=colors["text"],
                bordercolor=colors["border"],
                arrowcolor=colors["accent"],
            )
            style.configure("TNotebook", background=colors["background"], borderwidth=0)
            style.configure(
                "TNotebook.Tab",
                padding=(14, 8),
                background=colors["surface_high"],
                foreground=colors["muted"],
                font=("Segoe UI", 9, "bold"),
            )
            style.map(
                "TNotebook.Tab",
                background=[("selected", colors["surface"])],
                foreground=[("selected", colors["accent"])],
            )
            style.configure(
                "Brand.TLabel",
                background=colors["background"],
                font=("Segoe UI", 10, "bold"),
                foreground=colors["accent"],
            )
            style.configure(
                "State.TLabel",
                background=colors["background"],
                foreground=colors["text"],
                font=("Segoe UI", 9, "bold"),
            )
            style.configure(
                "State.OK.TLabel",
                background=colors["background"],
                font=("Segoe UI", 9, "bold"),
                foreground=colors["success"],
            )
            style.configure(
                "State.STOP.TLabel",
                background=colors["background"],
                font=("Segoe UI", 9, "bold"),
                foreground=colors["danger"],
            )
            style.configure(
                "Hint.TLabel",
                background=colors["background"],
                font=("Segoe UI", 8),
                foreground=colors["muted"],
            )
            style.configure("Mode.TNotebook", tabmargins=(0, 5, 0, 0))
            style.configure(
                "Mode.TNotebook.Tab", padding=(12, 7), font=("Segoe UI", 9, "bold")
            )
            style.configure("Card.TFrame", background=colors["surface"], padding=(3, 3))
            style.configure(
                "Card.TLabel",
                background=colors["surface"],
                foreground=colors["text"],
                font=("Segoe UI", 9),
            )
            style.configure(
                "Index.TLabel",
                background=colors["surface"],
                foreground=colors["accent"],
                font=("Segoe UI", 8, "bold"),
            )
            style.configure("Entry.TEntry", padding=(7, 4))
            outer = ttk.Frame(root, padding=(12, 10, 12, 10))
            outer.pack(fill="both", expand=True)

            header = ttk.Frame(outer)
            header.pack(fill="x", pady=(0, 2))
            ttk.Label(header, text=self.app_name.upper(), style="Brand.TLabel").pack(
                side="left"
            )
            self.state_label = ttk.Label(header, text="", style="State.TLabel")
            self.state_label.pack(side="right")
            self.language_box = ttk.Combobox(
                header, values=("TH", "EN"), width=3, state="readonly"
            )
            self.language_box.set("TH" if LANG == "th" else "EN")
            self.language_box.pack(side="right", padx=(0, 9))
            self.language_box.bind("<<ComboboxSelected>>", self._change_language)
            hint = self._tr(
                "Random and Marquee status tools with a combined activity log.",
                "จัดการสถานะสุ่มและข้อความเลื่อน พร้อมบันทึกกิจกรรม",
            )
            ttk.Label(outer, text=hint, style="Hint.TLabel").pack(
                anchor="w", pady=(0, 6)
            )

            self.tabs = ttk.Notebook(outer, style="Mode.TNotebook")
            self.tabs.pack(fill="both", expand=True)
            self.pages = {}
            for name_en, name_th, mode in (
                ("Random", "สุ่ม", "random"),
                ("Marquee", "เลื่อน", "marquee"),
                ("Activity Log", "บันทึกกิจกรรม", "activity"),
            ):
                page = ttk.Frame(self.tabs, padding=8)
                self.tabs.add(page, text=self._tr(name_en, name_th))
                self.pages[mode] = page
                self._build_page(page, mode)
            self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
            if self.mode in self.pages:
                self.tabs.select(self.pages[self.mode])

            self.note = ttk.Label(
                outer,
                text="",
                font=("Segoe UI", 8),
                anchor="w",
                foreground=colors["muted"],
                wraplength=420,
            )
            self.note.pack(fill="x", pady=(7, 5))
            options = ttk.Frame(outer)
            options.pack(fill="x", pady=(0, 5))
            ttk.Checkbutton(
                options,
                text=self._tr("Send statuses live to Camfrog", "ส่งสถานะจริงไป Camfrog"),
                variable=self.live_send,
                command=self.schedule_save,
            ).pack(side="left")
            ttk.Checkbutton(
                options,
                text=self._tr("Submit mode", "โหมดส่งสถานะ"),
                style="Hint.TLabel",
            ).pack(side="left", padx=(8, 3))
            ttk.Combobox(
                options, textvariable=self.execution_mode, state="readonly", width=24,
                values=("strict_background", "best_effort_background"),
            ).pack(side="left")
            self.execution_mode.trace_add("write", self.schedule_save)
            ttk.Label(
                outer,
                text=self._tr(
                    "Best-effort uses combo-post; Camfrog may flash or take focus. Strict currently refuses.",
                    "Best-effort ใช้ combo-post; Camfrog อาจกระพริบหรือแย่งโฟกัส ส่วน Strict ยังปฏิเสธการส่ง",
                ),
                style="Hint.TLabel", wraplength=420,
            ).pack(fill="x", pady=(0, 5))
            self._build_actions(outer)
            ttk.Label(
                outer,
                text=f"ZCHAT {self.app_name.upper()}",
                style="Brand.TLabel",
                anchor="center",
            ).pack(fill="x")

        def _build_page(self, page, mode):
            if mode == "activity":
                toolbar = ttk.Frame(page)
                toolbar.pack(fill="x", pady=(0, 5))
                ttk.Label(
                    toolbar,
                    text=self._tr(
                        "GUI actions and worker logs appear here. Sent status text is included when log_message_text is enabled. Logs clear daily at 12:00 local time; if the app is closed, cleanup runs at next launch.",
                        "บันทึก GUI และ worker จะแสดงที่นี่ โดยข้อความสถานะที่ส่งจะอยู่ใน log เมื่อเปิด log_message_text ระบบล้าง log ทุกวันเวลา 12:00 ตามเวลาท้องถิ่น หากปิดแอปไว้จะล้างเมื่อเปิดครั้งถัดไป",
                    ),
                    style="Hint.TLabel",
                ).pack(side="left", fill="x", expand=True)
                ttk.Button(
                    toolbar,
                    text=self._tr("Open log folder", "เปิดโฟลเดอร์ log"),
                    command=self.open_log_folder,
                ).pack(side="right")
                frame = ttk.Frame(page)
                frame.pack(fill="both", expand=True)
                self.activity_text = tk.Text(
                    frame,
                    wrap="none",
                    state="disabled",
                    height=24,
                    background=STATUS_CHANGER_THEME["entry"],
                    foreground=STATUS_CHANGER_THEME["log_text"],
                    insertbackground=STATUS_CHANGER_THEME["accent"],
                    selectbackground=STATUS_CHANGER_THEME["border"],
                    relief="flat",
                    padx=9,
                    pady=8,
                    font=("Consolas", 9),
                    undo=False,
                )
                scroll_y = ttk.Scrollbar(
                    frame, orient="vertical", command=self.activity_text.yview
                )
                scroll_x = ttk.Scrollbar(
                    frame, orient="horizontal", command=self.activity_text.xview
                )
                self.activity_text.configure(
                    yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set
                )
                self.activity_text.grid(row=0, column=0, sticky="nsew")
                scroll_y.grid(row=0, column=1, sticky="ns")
                scroll_x.grid(row=1, column=0, sticky="ew")
                frame.rowconfigure(0, weight=1)
                frame.columnconfigure(0, weight=1)
                self.activity_snapshot = None
                return

            title = (
                self._tr("Random status pool", "ชุดข้อความสุ่ม")
                if mode == "random"
                else self._tr("Marquee status pool", "ชุดข้อความเลื่อน")
            )
            hint = (
                self._tr(
                    "Pick one of these entries on each switch.",
                    "สุ่มเลือกหนึ่งข้อความเมื่อถึงรอบเปลี่ยน",
                )
                if mode == "random"
                else self._tr(
                    "Marquee settings and messages stay separate from Random.",
                    "ตั้งค่าความเร็วและข้อความแยกจากโหมดสุ่ม",
                )
            )
            ttk.Label(page, text=title, font=("Segoe UI", 9, "bold")).pack(
                anchor="w", pady=(0, 3)
            )
            ttk.Label(page, text=hint, style="Hint.TLabel").pack(
                anchor="w", pady=(0, 6)
            )

            timing = ttk.Frame(page, style="Card.TFrame", padding=(7, 6))
            timing.pack(fill="x", pady=(0, 4))
            colors = STATUS_CHANGER_THEME
            if mode == "random":
                ttk.Label(
                    timing, text=self._tr("Every", "เปลี่ยนทุก"), style="Card.TLabel"
                ).pack(side="left", padx=(2, 0))
                ttk.Spinbox(
                    timing,
                    textvariable=self.intervals["random"],
                    from_=RANDOM_INTERVAL_MIN,
                    to=RANDOM_INTERVAL_MAX,
                    increment=1,
                    width=6,
                ).pack(side="left", padx=(5, 3))
                ttk.Label(timing, text="s", style="Card.TLabel").pack(
                    side="left", padx=(0, 7)
                )
                ttk.Label(
                    timing,
                    text=self._tr("status interval", "ช่วงเปลี่ยน"),
                    style="Card.TLabel",
                    foreground=colors["muted"],
                ).pack(side="left")
            else:
                ttk.Label(
                    timing, text=self._tr("Frame", "จังหวะ"), style="Card.TLabel"
                ).pack(side="left", padx=(2, 0))
                ttk.Spinbox(
                    timing,
                    textvariable=self.step,
                    from_=MARQUEE_STEP_MIN,
                    to=3600,
                    increment=0.1,
                    width=5,
                ).pack(side="left", padx=(4, 3))
                ttk.Label(timing, text="s", style="Card.TLabel").pack(side="left")
                ttk.Label(
                    timing, text=self._tr("Stride", "ก้าว"), style="Card.TLabel"
                ).pack(side="left", padx=(8, 2))
                ttk.Spinbox(
                    timing,
                    textvariable=self.stride,
                    from_=1,
                    to=10,
                    increment=1,
                    width=3,
                ).pack(side="left")
                ttk.Checkbutton(
                    timing,
                    text=self._tr("Loop slots", "วนช่อง 1–10"),
                    variable=self.loop_slots,
                    command=self.schedule_save,
                ).pack(side="left", padx=(8, 0))
                ttk.Checkbutton(
                    timing,
                    text=self._tr("Scroll", "เลื่อน"),
                    variable=self.scroll_frames,
                    command=self.schedule_save,
                ).pack(side="left", padx=(8, 0))

            entries = ttk.Frame(page, style="Card.TFrame", padding=4)
            entries.pack(fill="both", expand=True, pady=(4, 0))
            for index, var in enumerate(self._fields_for_mode(mode)):
                ttk.Label(
                    entries, text=f"{index + 1:02d}", width=3, style="Index.TLabel"
                ).grid(row=index, column=0, sticky="w", pady=1, padx=(4, 8))
                entry = ttk.Entry(entries, textvariable=var, style="Entry.TEntry")
                entry.grid(row=index, column=1, sticky="ew", pady=1, padx=(0, 4))
                self.entry_widgets[mode].append((entry, var))
            entries.columnconfigure(1, weight=1)

        def _build_actions(self, parent):
            """Two-column, colour-coded controls shared by Random and Marquee."""
            actions = ttk.Frame(parent)
            actions.pack(fill="x", pady=(2, 8))
            for col in range(4):
                actions.columnconfigure(col, weight=1, uniform="action")
            for index, (title_en, title_th, command, button_style) in enumerate(
                (
                    (
                        "1. Discovery",
                        "1. ค้นหา Control",
                        self.discover,
                        "Action.TButton",
                    ),
                    ("2. Save Data", "2. บันทึกข้อมูล", self.save_data, "Success.TButton"),
                    (
                        "3. Start",
                        "3. เริ่ม",
                        lambda: self.set_worker_running(self.mode, True),
                        "Primary.TButton",
                    ),
                    (
                        "4. Stop",
                        "4. หยุด",
                        lambda: self.set_worker_running(self.mode, False),
                        "Danger.TButton",
                    ),
                )
            ):
                button = ttk.Button(
                    actions,
                    text=self._tr(title_en, title_th),
                    command=command,
                    style=button_style,
                    takefocus=True,
                )
                button.grid(
                    row=0,
                    column=index,
                    padx=(0, 4) if index < 3 else (0, 0),
                    pady=(0, 4),
                    sticky="ew",
                )
            if not self.embedded:
                ttk.Button(
                    parent,
                    text=self._tr("Check for updates", "ตรวจอัปเดต"),
                    command=self.check_update,
                ).pack(fill="x", pady=(0, 5))
            ttk.Label(
                parent,
                text=self._tr(
                    (
                        "Closing ZChat stops this feature's worker."
                        if self.embedded
                        else "Close / X stops this app's worker before exiting."
                    ),
                    (
                        "เมื่อปิด ZChat จะหยุด worker ของฟีเจอร์นี้ก่อนออก"
                        if self.embedded
                        else "ปิดหน้าต่างด้วย X เพื่อหยุด worker ของแอปก่อนออก"
                    ),
                ),
                style="Hint.TLabel",
            ).pack(anchor="w", pady=(0, 5))

        def _tab_changed(self, _event=None):
            try:
                selected = str(self.tabs.select())
                selected_mode = next(
                    mode for mode, page in self.pages.items() if str(page) == selected
                )
            except (StopIteration, tk.TclError):
                return
            if selected_mode not in ("random", "marquee") or selected_mode == self.mode:
                return
            old_mode = self.mode
            try:
                if self.save_job is not None:
                    try:
                        self.root.after_cancel(self.save_job)
                    except tk.TclError:
                        pass
                    self.save_job = None
                self._save_pools()
                if self.save_settings(mode=old_mode) is None:
                    self.tabs.select(self.pages[old_mode])
                    return
                self.mode = selected_mode
                self._load_mode_controls(selected_mode)
            except (OSError, sqlite3.Error, ValueError, KeyError, tk.TclError) as exc:
                logging.exception("Could not switch status mode config")
                self.mode = old_mode
                try:
                    self._load_mode_controls(old_mode)
                    self.tabs.select(self.pages[old_mode])
                    self.note.configure(
                        text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                    )
                except (OSError, ValueError, KeyError, tk.TclError):
                    pass

        def _log_activity(self, message, mode=None):
            write_activity_log(mode or self.mode, message)
            self.activity_snapshot = None

        def refresh_activity_log(self):
            if not self.root.winfo_exists():
                return
            widget = getattr(self, "activity_text", None)
            if widget is not None:
                entries = []
                sources = [(DATA_DIR / "status_activity.log", "GUI")]
                sources.extend(
                    (DATA_DIR / f"{mode}-status-worker.log", f"{mode.title()} worker")
                    for mode in ("random", "marquee")
                )
                for path, source in sources:
                    try:
                        with path.open("rb") as stream:
                            stream.seek(max(0, path.stat().st_size - 500_000))
                            raw_lines = (
                                stream.read()
                                .decode("utf-8", errors="replace")
                                .splitlines()
                            )
                    except OSError:
                        continue
                    for line in raw_lines[-1500:]:
                        stamp = line[:19]
                        entries.append((stamp, f"[{source}] {line}"))
                entries.sort(key=lambda item: item[0])
                text = "\n".join(line for _stamp, line in entries[-3000:])
                if text != self.activity_snapshot:
                    widget.configure(state="normal")
                    widget.delete("1.0", "end")
                    widget.insert(
                        "end",
                        text
                        or self._tr("No activity recorded yet.", "ยังไม่มีบันทึกกิจกรรม"),
                    )
                    widget.configure(state="disabled")
                    widget.see("end")
                    self.activity_snapshot = text
            try:
                self.activity_refresh_job = self.root.after(
                    1000, self.refresh_activity_log
                )
            except tk.TclError:
                self.activity_refresh_job = None

        def clean_logs_at_noon(self):
            if not self.root.winfo_exists():
                return
            active_modes = set()
            mode = active_worker_mode()
            if mode:
                try:
                    config = load_cfg(self.config_paths[mode])
                    if running_pid(runtime_config(config, mode)):
                        active_modes.add(mode)
                except (OSError, ValueError, KeyError):
                    pass
            if clean_status_logs_at_noon(DATA_DIR, active_modes=active_modes):
                self.activity_snapshot = None
            now = dt.datetime.now().astimezone()
            next_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
            if next_noon <= now:
                next_noon += dt.timedelta(days=1)
            delay_ms = max(1000, int((next_noon - now).total_seconds() * 1000))
            try:
                self.log_cleanup_job = self.root.after(
                    delay_ms, self.clean_logs_at_noon
                )
            except tk.TclError:
                self.log_cleanup_job = None

        def open_log_folder(self):
            try:
                if os.name == "nt":
                    os.startfile(str(DATA_DIR))
                else:
                    self.note.configure(
                        text=self._tr(
                            f"Logs are stored in {DATA_DIR}", f"ไฟล์ log อยู่ที่ {DATA_DIR}"
                        )
                    )
            except OSError as exc:
                self.note.configure(
                    text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                )

        def check_update(self):
            self.note.configure(
                text=self._tr("Checking for updates...", "กำลังตรวจอัปเดต..."),
                foreground=STATUS_CHANGER_THEME["muted"],
            )

            def work():
                try:
                    state, message = self_update("status-changer.exe")
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                ) as exc:
                    logging.warning(
                        "Manual update check unavailable: %s", type(exc).__name__
                    )
                    state, message = (
                        "failed",
                        "Update check unavailable (network or metadata error).",
                    )
                try:
                    self.root.after(0, lambda: self._update_done(state, message))
                except RuntimeError:
                    pass  # Window closed before the worker finished

            threading.Thread(target=work, daemon=True).start()

        def _update_done(self, state, message):
            self.note.configure(
                text=message,
                foreground=(
                    STATUS_CHANGER_THEME["error"]
                    if state == "failed"
                    else STATUS_CHANGER_THEME["success"]
                ),
            )
            if state == "ready" and not self.embedded:
                self.root.after(1200, self.root.destroy)

        def _auto_update_check(self):
            def work():
                try:
                    found = check_for_update("status-changer.exe")
                    if found:
                        self.root.after(0, self.check_update)
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                ) as exc:
                    # GitHub 403/rate limits, offline mode and invalid metadata
                    # must not crash an unattended GUI background thread.
                    logging.warning(
                        "Automatic update check unavailable: %s", type(exc).__name__
                    )

            threading.Thread(target=work, daemon=True).start()

        def schedule_save(self, *_args):
            if self.loading_mode:
                return
            if self.save_job is not None:
                try:
                    self.root.after_cancel(self.save_job)
                except tk.TclError:
                    pass
            self.save_job = self.root.after(450, self.save_settings)

        def collect_config(self, enabled=None, mode=None):
            mode = mode if mode in ("random", "marquee") else self.mode
            self._save_pools()
            config = status_config_for_live_send(
                load_cfg(self.config_path), self.live_send.get()
            )
            config["language"] = self.language_preference
            st = config["status"]
            st["execution_mode"] = self.execution_mode.get()
            st["background_enter_target"] = (
                "combo" if st["execution_mode"] == "best_effort_background" else "edit"
            )
            if mode == "random":
                try:
                    interval = parse_interval_seconds(self.intervals["random"].get())
                except ValueError as exc:
                    raise ValueError(
                        self._tr(
                            "Enter a valid switch interval.",
                            "กรอกช่วงเวลาเปลี่ยนสถานะให้ถูกต้อง",
                        )
                    ) from exc
            else:
                # The engine uses marquee.step_seconds to pace slot changes.
                # Keep the legacy schema field valid without an Every control.
                interval = MARQUEE_STEP_MIN
            marquee_options = None
            if mode == "marquee":
                marquee_options = {
                    "step": self.step.get(),
                    "stride": self.stride.get(),
                    "loop_slots": self.loop_slots.get(),
                    "scroll": self.scroll_frames.get(),
                }
            try:
                configure_status_mode(
                    config,
                    mode,
                    messages_from_slots(
                        var.get() for var in self._fields_for_mode(mode)
                    ),
                    interval,
                    marquee_options,
                )
            except ValueError as exc:
                raise ValueError(
                    self._tr(
                        "Enter valid marquee speed values.",
                        "กรอกค่าความเร็วข้อความเลื่อนให้ถูกต้อง",
                    )
                ) from exc
            if enabled:
                # "Start" means rotate a status now, not after one interval.
                st["set_on_start"] = True
            if enabled is not None:
                st["enabled"] = enabled
            else:
                active = active_worker_mode()
                st["enabled"] = bool(
                    active == mode and running_pid(runtime_config(config, mode))
                )
            if st["enabled"] and not st["messages"]:
                raise ValueError(
                    self._tr("Enter at least one status.", "กรอกสถานะอย่างน้อยหนึ่งข้อความ")
                )
            errors, _warnings = validate(config)
            if errors:
                raise ValueError("\n".join(errors))
            return config

        def save_settings(self, enabled=None, mode=None, notify=False):
            self.save_job = None
            mode = mode if mode in ("random", "marquee") else self.mode
            try:
                config = self.collect_config(enabled=enabled, mode=mode)
                write_config(self.config_path, config)
                runtime = runtime_config(config, mode)
                write_config(self.runtime_config_paths[mode], runtime)
                self.config = config
                self._log_activity("Status lines saved", mode)
                if notify:
                    self.note.configure(
                        text=self._tr(
                            "Saved to standalone status config",
                            "บันทึกการตั้งค่า Status Changer แล้ว",
                        )
                    )
                return config
            except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
                self.note.configure(
                    text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                )
                return None

        def _save_pools(self):
            modes = ("random", "marquee")
            for mode in modes:
                write_status_pool(
                    self.pool_paths[mode],
                    [var.get() for var in self._fields_for_mode(mode)],
                )

        def _save_discovery_inputs(self):
            """Persist both status modes before discovery touches either config."""
            if self.save_job is not None:
                try:
                    self.root.after_cancel(self.save_job)
                except tk.TclError:
                    pass
                self.save_job = None

            if self.save_settings() is None:
                return False

            try:
                for mode in ("random", "marquee"):
                    config_path = self.config_paths[mode]
                    config = load_cfg(config_path)
                    config["status"]["messages"] = messages_from_slots(
                        var.get() for var in self._fields_for_mode(mode)
                    )
                    errors, _warnings = validate(config)
                    if errors:
                        raise ValueError("\n".join(errors))
                    write_config(config_path, config)
                    write_config(
                        self.runtime_config_paths[mode], runtime_config(config, mode)
                    )
                    if mode != self.mode:
                        self._log_activity("Status lines saved", mode)
                return True
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
                self.note.configure(
                    text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                )
                return False

        def save_data(self):
            """Persist current status pools and settings without contacting Camfrog."""
            if self.busy or self.stop_in_progress:
                return
            if self.save_job is not None:
                try:
                    self.root.after_cancel(self.save_job)
                except tk.TclError:
                    pass
                self.save_job = None
            config = self.save_settings()
            if config is None:
                return
            self.note.configure(
                text=self._tr("Status data saved.", "บันทึกข้อมูลสถานะแล้ว"),
                foreground=STATUS_CHANGER_THEME["success"],
            )

        def discover(self):
            """Scan the running Camfrog window for status/room controls and write the
            detected selectors into this app's config (apply=True)."""
            if self.busy:
                return
            # Flush pending debounced saves and both mode pools before discovery.
            if not self._save_discovery_inputs():
                return

            def do():
                rc = run_control_discovery(self.config_path)
                if rc == 0:
                    self.config = load_cfg(self.config_path)
                return rc

            self.run_task(
                do,
                self._tr("Controls discovered", "ดึง control แล้ว"),
                action=self._tr("Discover controls", "ค้นหา control"),
            )

        def _discover_controls_on_start(self):
            """Save current settings, then discover controls for both status modes."""
            if self.embedded or self.close_requested or self.busy:
                return
            if not self._save_discovery_inputs():
                return

            def do():
                failures = []
                for mode in ("random", "marquee"):
                    try:
                        run_control_discovery(self.config_paths[mode])
                    except Exception as exc:
                        failures.append(f"{mode}: {type(exc).__name__}: {exc}")
                if failures:
                    raise RuntimeError("Control discovery failed (" + "; ".join(failures) + ")")
                self.config = load_cfg(self.config_path)
                return 0

            self.run_task(
                do,
                self._tr("Controls discovered", "ดึง control แล้ว"),
                action=self._tr("Discover controls", "ค้นหา control"),
            )

        def set_worker_running(self, mode, should_run):
            """Start or stop the single worker for the explicitly selected mode."""
            if mode not in ("random", "marquee"):
                self.note.configure(
                    text=self._tr(
                        "Select a status mode first.", "กรุณาเลือกโหมดสถานะก่อน"
                    ),
                    foreground=STATUS_CHANGER_THEME["error"],
                )
                return
            if not should_run:
                self.mode = mode
                if self.stop_in_progress:
                    self.note.configure(
                        text=self._tr(
                            "A stop request is already running.",
                            "กำลังดำเนินการหยุด worker อยู่แล้ว",
                        ),
                        foreground=STATUS_CHANGER_THEME["muted"],
                    )
                    return
                # Stop is an emergency/operator action: do not validate or save
                # unsaved form data first. Invalid input must never block Stop.
                try:
                    config = load_cfg(self.config_path)
                    runtime = runtime_config(config, mode)
                    worker_pid = running_pid(runtime)
                except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
                    self.note.configure(
                        text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                    )
                    return
                if worker_pid:
                    self._start_stop_task(
                        runtime,
                        self._tr("Status worker stopped.", "หยุดตัวเปลี่ยนสถานะแล้ว"),
                        action=self._tr("Stop worker", "หยุด worker"),
                    )
                elif self.busy:
                    # A Start task may be between spawning the worker and
                    # recording its PID. Recheck as soon as that task returns.
                    self.pending_stop_mode = mode
                    self.note.configure(
                        text=self._tr(
                            "Stop queued until the current task finishes.",
                            "รับคำสั่งหยุดแล้ว จะตรวจอีกครั้งเมื่องานปัจจุบันเสร็จ",
                        ),
                        foreground=STATUS_CHANGER_THEME["muted"],
                    )
                else:
                    self.note.configure(
                        text=self._tr(
                            "Status rotation is not running.", "ไม่ได้เปิดการเปลี่ยนสถานะอยู่"
                        ),
                        foreground=STATUS_CHANGER_THEME["muted"],
                    )
                return

            if self.busy or self.stop_in_progress:
                return
            self.mode = mode

            config = self.save_settings(enabled=True, mode=mode)
            if config is None:
                return
            runtime = runtime_config(config, mode)
            if running_pid(runtime):
                active = active_worker_mode() or self._tr("another mode", "อีกโหมด")
                self.note.configure(
                    text=self._tr(
                        f"Status worker is already running ({active}). Stop it before switching modes.",
                        f"worker กำลังทำงานโหมด {active} อยู่ ให้หยุดก่อนเปลี่ยนโหมด",
                    ),
                    foreground=STATUS_CHANGER_THEME["success"],
                )
                return
            self._start_worker(config, mode)

        def _start_worker(self, config, mode):
            runtime = runtime_config(config, mode)
            runtime_path = self.runtime_config_paths[mode]
            args = SimpleNamespace(config=str(runtime_path), lang=None)

            def start():
                messages = []
                rc = cmd_start_worker(runtime, args, report=messages.append)
                if rc != 0:
                    detail = "\n".join(messages).strip()
                    raise RuntimeError(
                        detail
                        or self._tr(
                            "Could not start the status worker.",
                            "เริ่ม worker สถานะไม่ได้",
                        )
                    )
                if rc == 0:
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        if running_pid(runtime):
                            (DATA_DIR / ACTIVE_MODE_FILE).write_text(
                                mode, encoding="utf-8"
                            )
                            return rc
                        time.sleep(0.1)
                    raise RuntimeError(
                        self._tr(
                            "Worker started but its process could not be verified.",
                            "เริ่ม worker แล้วแต่ตรวจสอบโปรเซสไม่ได้",
                        )
                    )
                return rc

            if config["dry_run"]:
                done_text = self._tr(
                    "Status worker started in dry-run mode; no statuses will be sent.",
                    "เริ่ม worker ในโหมดทดลองแล้ว จะยังไม่ส่งสถานะ",
                )
            else:
                done_text = self._tr("Status worker started.", "เริ่มตัวเปลี่ยนสถานะแล้ว")
            self.run_task(
                start,
                done_text,
                action=self._tr(f"Start {mode} worker", f"เริ่ม worker โหมด{mode}"),
            )

        def _stop_worker(self, runtime):
            return cmd_stop(runtime)

        def run_task(self, function, done_text, on_done=None, action=None):
            if self.busy or self.stop_in_progress:
                return False
            self.busy = True
            self.note.configure(
                text=self._tr("Working…", "กำลังทำงาน…"),
                foreground=STATUS_CHANGER_THEME["muted"],
            )
            task_mode = self.mode
            if action:
                self._log_activity(f"{action} started", task_mode)

            def worker():
                try:
                    result = function()
                    text = (
                        done_text
                        if result in (None, 0)
                        else self._tr("Command failed.", "คำสั่งไม่สำเร็จ")
                    )
                    ok = result in (None, 0)
                    if action:
                        self._log_activity(
                            f"{action} {'succeeded' if ok else 'failed'}", task_mode
                        )
                except StatusOperationCancelled:
                    text = self._tr("Status update stopped.", "หยุดการเปลี่ยนสถานะแล้ว")
                    ok = True
                    if action:
                        self._log_activity(
                            self._tr("Apply status cancelled", "ยกเลิกการใช้สถานะ"),
                            task_mode,
                        )
                except Exception as exc:
                    text, ok = str(exc), False
                    if action:
                        self._log_activity(
                            f"{action} failed | {type(exc).__name__}: {exc}", task_mode
                        )
                self.task_results.put((text, ok, on_done))

            try:
                threading.Thread(
                    target=worker, daemon=True, name="camfrog-status-task"
                ).start()
                return True
            except RuntimeError as exc:
                self.busy = False
                self.note.configure(
                    text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                )
                return False

        def _start_stop_task(self, runtime, done_text, on_done=None, action=None):
            """Stop the worker independently of unrelated GUI background tasks."""
            if self.stop_in_progress:
                return False
            self.stop_in_progress = True
            self.note.configure(
                text=self._tr("Stopping worker…", "กำลังหยุด worker…"),
                foreground=STATUS_CHANGER_THEME["muted"],
            )
            task_mode = self.mode
            if action:
                self._log_activity(f"{action} started", task_mode)

            def worker():
                try:
                    result = self._stop_worker(runtime)
                    text = (
                        done_text
                        if result in (None, 0)
                        else self._tr("Command failed.", "คำสั่งไม่สำเร็จ")
                    )
                    ok = result in (None, 0)
                    if action:
                        self._log_activity(
                            f"{action} {'succeeded' if ok else 'failed'}", task_mode
                        )
                except Exception as exc:
                    text, ok = str(exc), False
                    if action:
                        self._log_activity(
                            f"{action} failed | {type(exc).__name__}: {exc}", task_mode
                        )
                self.stop_results.put((text, ok, on_done))

            try:
                threading.Thread(
                    target=worker, daemon=True, name="camfrog-status-stop"
                ).start()
                return True
            except RuntimeError as exc:
                self.stop_in_progress = False
                self.note.configure(
                    text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                )
                return False

        def poll_task_results(self):
            try:
                while True:
                    text, ok, on_done = self.task_results.get_nowait()
                    self.finish_task(text, ok, on_done)
            except queue.Empty:
                pass
            except Exception as exc:
                logging.exception("Could not process Status Changer task result")
                if self.root.winfo_exists():
                    self.note.configure(
                        text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                    )
            try:
                while True:
                    text, ok, on_done = self.stop_results.get_nowait()
                    self.finish_stop_task(text, ok, on_done)
            except queue.Empty:
                pass
            except Exception as exc:
                logging.exception("Could not process Status Changer stop result")
                if self.root.winfo_exists():
                    self.note.configure(
                        text=str(exc), foreground=STATUS_CHANGER_THEME["error"]
                    )
            try:
                self.task_poll_job = self.root.after(100, self.poll_task_results)
            except tk.TclError:
                self.task_poll_job = None

        def finish_task(self, text, ok, on_done=None):
            self.busy = False
            self.note.configure(
                text=text,
                foreground=(
                    STATUS_CHANGER_THEME["success"]
                    if ok
                    else STATUS_CHANGER_THEME["error"]
                ),
            )
            self.refresh_state()
            close_after_task = self.close_requested
            self.close_requested = False
            if on_done:
                on_done(ok)
            if close_after_task:
                self.pending_stop_mode = None
                try:
                    if self.root.winfo_exists():
                        self.close_app()
                except tk.TclError:
                    pass
            elif self.pending_stop_mode is not None:
                pending_mode = self.pending_stop_mode
                self.pending_stop_mode = None
                self.set_worker_running(pending_mode, False)

        def finish_stop_task(self, text, ok, on_done=None):
            self.stop_in_progress = False
            self.note.configure(
                text=text,
                foreground=(
                    STATUS_CHANGER_THEME["success"]
                    if ok
                    else STATUS_CHANGER_THEME["error"]
                ),
            )
            self.refresh_state()
            close_after_stop = self.close_requested
            self.close_requested = False
            if on_done:
                on_done(ok)
            if close_after_stop and ok:
                try:
                    if self.root.winfo_exists():
                        self.close_app()
                except tk.TclError:
                    pass

        def _restore_window(self):
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            except tk.TclError:
                pass

        def close_app(self):
            """Stop the standalone worker and exit; never leave the UI in the tray."""
            if self.stop_in_progress:
                self.close_requested = True
                self.note.configure(
                    text=self._tr(
                        "Waiting for the worker to stop before exit…",
                        "กำลังรอให้ worker หยุดก่อนปิดโปรแกรม…",
                    ),
                    foreground=STATUS_CHANGER_THEME["muted"],
                )
                return
            if self.busy:
                self.close_requested = True
                self.note.configure(
                    text=self._tr(
                        "Finishing the current task before exit…",
                        "รอให้งานปัจจุบันเสร็จแล้วปิดโปรแกรม…",
                    ),
                    foreground=STATUS_CHANGER_THEME["muted"],
                )
                return
            try:
                config = load_cfg(self.config_path)
                runtime = runtime_config(config, self.mode)
                if running_pid(runtime):
                    self.note.configure(
                        text=self._tr(
                            "Stopping the status worker before exit…",
                            "กำลังหยุดตัวเปลี่ยนสถานะก่อนออก…",
                        ),
                        foreground=STATUS_CHANGER_THEME["muted"],
                    )
                    started = self._start_stop_task(
                        runtime,
                        self._tr("Status worker stopped.", "หยุดตัวเปลี่ยนสถานะแล้ว"),
                        on_done=self._close_after_worker_stop,
                        action=self._tr("Stop worker before exit", "หยุด worker ก่อนออก"),
                    )
                    if not started and self.embedded and callable(self.on_close_failed):
                        self.on_close_failed()
                    return
            except Exception as exc:
                logging.exception("Could not prepare a clean Status Changer exit")
                messagebox.showerror(
                    f"ZChat {self.app_name}",
                    self._tr(
                        f"Could not verify worker shutdown: {exc}",
                        f"ตรวจสอบการหยุด worker ไม่สำเร็จ: {exc}",
                    ),
                    parent=self.root,
                )
                if self.embedded and callable(self.on_close_failed):
                    self.on_close_failed()
                return
            self._finalize_close()

        def _close_after_worker_stop(self, ok):
            if ok:
                self._finalize_close()
            else:
                self.note.configure(
                    text=self._tr(
                        "Worker did not confirm shutdown. The window stays open so you can retry.",
                        "worker ยังไม่ยืนยันว่าหยุดแล้ว หน้าต่างยังเปิดไว้ให้ลองใหม่",
                    ),
                    foreground=STATUS_CHANGER_THEME["error"],
                )
                if self.embedded and callable(self.on_close_failed):
                    self.on_close_failed()

        def _finalize_close(self):
            if self._closed_callback_called:
                return
            if self.save_job is not None:
                try:
                    self.root.after_cancel(self.save_job)
                except tk.TclError:
                    pass
                self.save_job = None
            self.save_settings(enabled=False, mode=self.mode)
            for job in (
                self.state_job,
                self.task_poll_job,
                self.activity_refresh_job,
                self.log_cleanup_job,
            ):
                if job is not None:
                    try:
                        self.root.after_cancel(job)
                    except tk.TclError:
                        pass
            if self.embedded:
                self._closed_callback_called = True
                if callable(self.on_closed):
                    self.on_closed()
            else:
                try:
                    self.root.quit()
                except tk.TclError:
                    pass
                try:
                    self.root.destroy()
                except tk.TclError:
                    pass

        def _register_single_instance_handler(self):
            """Subclass the Tk root window proc to handle WM_SHOWME from second instance."""
            if os.name != "nt":
                return
            try:
                import ctypes

                self.user32 = ctypes.WinDLL("user32", use_last_error=True)
                self.user32.SetWindowLongPtrW.argtypes = (
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.c_void_p,
                )
                self.user32.SetWindowLongPtrW.restype = ctypes.c_void_p
                self.user32.CallWindowProcW.argtypes = (
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                )
                self.user32.CallWindowProcW.restype = ctypes.c_void_p

                self.root.update_idletasks()
                hwnd = self.root.winfo_id()

                def new_wndproc(hwnd, msg, wparam, lparam):
                    if msg == SingleInstanceGuard.WM_SHOWME:
                        self.root.after(0, self._restore_window)
                        return 0
                    return self.user32.CallWindowProcW(
                        self.old_wndproc, hwnd, msg, wparam, lparam
                    )

                self.new_wndproc = ctypes.WINFUNCTYPE(
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                )(new_wndproc)

                GWLP_WNDPROC = -4
                self.old_wndproc = self.user32.SetWindowLongPtrW(
                    hwnd, GWLP_WNDPROC, self.new_wndproc
                )
            except Exception:
                pass

        def refresh_state(self):
            if not self.root.winfo_exists():
                return
            try:
                config = load_cfg(self.config_path)
                pid = running_pid(runtime_config(config, self.mode))
                running = bool(pid)
                active = active_worker_mode() if running else None
                label = (
                    (
                        self._tr("RUNNING", "ทำงาน")
                        + (f" · {active.title()}" if active else "")
                    )
                    if running
                    else self._tr("STOPPED", "หยุด")
                )
                self.state_label.configure(
                    text=label,
                    style=("State.OK.TLabel" if running else "State.STOP.TLabel"),
                )
            except Exception:
                pass
            self.state_job = self.root.after(1200, self.refresh_state)

        def mainloop(self):
            self.root.mainloop()

    return StatusChanger


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)

    if "--worker" in argv:
        argv.remove("--worker")
        return cli_main(argv, force_strict_background=True)

    # Handle --cleanup-mutex before single-instance guard
    if "--cleanup-mutex" in argv:
        if argv != ["--cleanup-mutex"]:
            print("--cleanup-mutex cannot be combined with other arguments")
            return 2
        return 0 if SingleInstanceGuard.force_cleanup() else 1

    parser = argparse.ArgumentParser(description="ZChat Status Changer")
    parser.add_argument("--config", help="use an alternate app-local config")
    parser.add_argument(
        "--allow-multiple", action="store_true", help="allow a second GUI instance"
    )
    try:
        args = parser.parse_args(argv)
        if args.config is not None and not args.config.strip():
            parser.error("argument --config: expected a path")
    except SystemExit as exc:
        return exc.code

    config_path = DATA_DIR / "camfrog-status-config.json"
    if args.config:
        candidate = Path(args.config)
        if not candidate.is_absolute():
            candidate = DATA_DIR / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(DATA_DIR.resolve())
        except ValueError:
            print(
                "Status Changer config must stay inside this executable's private data folder."
            )
            return 2
        config_path = candidate

    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    # Single-instance guard: a second launch restores the existing window
    # (posted as WM_SHOWME in acquire()) and exits silently.
    # Use --allow-multiple to bypass this check.
    instance = SingleInstanceGuard(allow_multiple=args.allow_multiple)
    if not instance.acquire():
        return 0

    try:
        try:
            app = build_app()(config_path)
        except SystemExit as exc:
            return exc.code
        except Exception as exc:
            print(f"Status Changer failed: {exc}")
            return 1
        app.mainloop()
        return 0
    finally:
        instance.release()


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
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
    try:
        parsed = urllib.parse.urlsplit(str(url))
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid GitHub release URL") from exc
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() not in allowed_hosts
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
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
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "camfrog-auto/{0}".format(APP_VERSION),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("unexpected GitHub release response shape")
    tag = str(data.get("tag_name") or "").lstrip("v")
    if not tag:
        return None
    assets = {}
    entries = data.get("assets", [])
    if not isinstance(entries, list):
        raise ValueError("invalid GitHub release assets")
    for asset in entries:
        if not isinstance(asset, dict):
            raise ValueError("invalid GitHub release asset entry")
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
        sums_url = next(
            (
                value
                for name, value in sorted(assets.items())
                if name.endswith("SHA256SUMS.txt")
            ),
            "",
        )
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
        url, headers={"User-Agent": "camfrog-auto/{0}".format(APP_VERSION)}
    )
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
        sums_url, headers={"User-Agent": "camfrog-auto/{0}".format(APP_VERSION)}
    )
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
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,123}\.exe", asset_name, flags=re.IGNORECASE
    ):
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
        'del "%~f0"\r\n'.format(staged=staged, exe=exe),
        encoding="utf-8",
    )
    command = ["cmd", "/c", str(script)]
    spawn = {"cwd": str(exe.parent)}
    if os.name == "nt":  # CREATE_NO_WINDOW: no console flash during the swap
        spawn["creationflags"] = 0x08000000
    subprocess.Popen(command, close_fds=True, **spawn)
    return True, "update is staged; it will replace {0} after the app exits.".format(
        asset_name
    )
