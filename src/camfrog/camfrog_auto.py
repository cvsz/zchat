"""Camfrog auto status + auto reply  |  ตั้ง status + ตอบแชทอัตโนมัติ (Windows, UIA)

Commands / คำสั่ง:
  check            validate config / ตรวจ config (ไม่ต้องเปิด Camfrog)
  windows          list visible windows (find your window_title_regex) / แสดงรายการหน้าต่างที่เห็น
  discover         dump control tree -> controls.txt / ดึงรายการ control
  status "text"    set status once / ตั้ง status ครั้งเดียว
  marquee "text"   preview scrolling-status frames offline / ดูตัวอย่างเฟรมสถานะเลื่อน
  history          list status history / ดูประวัติ status
  history-add "t"  add a status to history / เพิ่ม status เข้าประวัติ
  history-import F import statuses (one per line) / นำเข้า status จากไฟล์
  init             write a starter config.json / สร้าง config.json เริ่มต้น
  test-rules       test replies offline (no Camfrog) / ทดสอบกฎตอบแชทแบบออฟไลน์
  run              run in this window / รันในหน้าต่างนี้
  start            run in BACKGROUND (hidden) / รันเบื้องหลัง
  stop             stop background instance / หยุดเบื้องหลัง
  state            show running state / ดูสถานะ
  autostart-on     start background at Windows login / เปิดอัตโนมัติตอนเข้า Windows
  autostart-off    remove autostart / ยกเลิก
Options (before or after the command): --config PATH  --lang auto|th|en

NOTE: untested against a live Camfrog build. Set selectors in config.json via `discover`.
"""
import argparse
import copy
import ctypes
import datetime as dt
import hashlib
import json
import logging
import logging.handlers
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

APP_PROFILE = os.environ.get("CAMFROG_APP_PROFILE", "full").lower()
APP_PROFILES = {"full", "room", "im_reply", "status", "music"}

# Standalone executables can pin runtime state to their own directory. Only the
# status profile may consume ZCFATO_DATA_DIR; it must not redirect other apps.
if APP_PROFILE == "status" and os.environ.get("ZCFATO_DATA_DIR"):
    BASE = Path(os.environ["ZCFATO_DATA_DIR"])
elif os.environ.get("CAMFROG_APP_DATA_DIR"):
    BASE = Path(os.environ["CAMFROG_APP_DATA_DIR"])

# Nickname regex: Unicode word chars + space . - (Camfrog allows Thai in nicknames).
# LINE_RE nick capture is permissive; validation happens via NICK_RE.
NICK_RE = re.compile(r"^[\w][\w .\-]{0,31}$", re.UNICODE)
LINE_RE = re.compile(r"^(?P<nick>[^:\r\n]{1,32}):\s(?P<msg>.+)$")
CTRL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
log = logging.getLogger("camfrog_auto")
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
    "another_instance": ("another worker instance is already running", "มี worker อีก instance กำลังทำงานอยู่แล้ว"),
    "bg_started": ("started in background (PID {pid}). Log: {log}",
                   "เริ่มทำงานเบื้องหลังแล้ว (PID {pid}) ล็อก: {log}"),
    "bg_failed": ("background process exited (code {code}). See log: {log}",
                  "โปรเซสเบื้องหลังจบการทำงาน (code {code}) ดูล็อก: {log}"),
    "bg_start_timeout": ("worker did not become ready within {seconds} seconds. See log: {log}",
                         "worker ไม่พร้อมภายใน {seconds} วินาที ดูล็อก: {log}"),
    "bg_needs_log": ("background mode needs log.file set in config",
                     "โหมดเบื้องหลังต้องตั้ง log.file ใน config"),
    "win_only": ("this command is Windows-only", "คำสั่งนี้ใช้ได้เฉพาะ Windows"),
    "not_running": ("not running", "ไม่ได้ทำงานอยู่"),
    "stopping": ("stopping PID {pid} ...", "กำลังหยุด PID {pid} ..."),
    "stopped_ok": ("stopped", "หยุดแล้ว"),
    "killed": ("did not stop in time; terminated", "หยุดไม่ทันเวลา จึงบังคับปิด"),
    "stop_failed": ("could not confirm process exit (PID {pid}); STOP and PID files were kept so you can retry",
                    "ยืนยันไม่ได้ว่าโปรเซสหยุดแล้ว (PID {pid}) จึงเก็บไฟล์ STOP/PID ไว้ให้ลองใหม่"),
    "stop_failed_unknown": ("worker did not release its mutex; STOP and PID files were kept so you can retry",
                             "worker ยังไม่ปล่อย mutex จึงเก็บไฟล์ STOP/PID ไว้ให้ลองใหม่"),
    "state_running": ("RUNNING (PID {pid})", "กำลังทำงาน (PID {pid})"),
    "state_running_unknown": ("RUNNING (worker active; PID unavailable)", "กำลังทำงาน (พบ worker แต่ไม่ทราบ PID)"),
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
    "im_replied": ("IM reply echo recorded in local chat for {nick}: {text} (recipient delivery not confirmed)",
                   "พบข้อความตอบกลับในประวัติแชตฝั่งเครื่องสำหรับ {nick}: {text} (ยังไม่ยืนยันว่าถึงผู้รับ)"),
    "im_echo_visible": ("IM reply row was scrolled into the local chat viewport for {nick}; recipient delivery is not confirmed",
                        "เลื่อนบรรทัดตอบกลับให้อยู่ในพื้นที่แชตฝั่งเครื่องของ {nick} แล้ว แต่ยังไม่ยืนยันว่าถึงผู้รับ"),
    "im_echo_not_visible": ("IM reply is in local history but could not be placed in the chat viewport for {nick}; recipient delivery is not confirmed",
                            "พบข้อความตอบในประวัติ แต่เลื่อนไปยังพื้นที่แชตของ {nick} ไม่สำเร็จ และยังไม่ยืนยันว่าถึงผู้รับ"),
    "im_previewed": ("IM reply preview for {nick} (dry-run; not sent)", "ดูตัวอย่างคำตอบ IM ของ {nick} (dry-run; ยังไม่ได้ส่ง)"),
    "im_filtered": ("IM from {nick} ignored: {reason}", "ข้าม IM จาก {nick}: {reason}"),
    "im_no_rule": ("IM from {nick} ignored: no reply rule matched", "ข้าม IM จาก {nick}: ไม่มีกฎตอบที่ตรงกัน"),
    "im_unconfirmed": ("IM reply to {nick} was not confirmed in chat history", "ไม่พบข้อความตอบกลับของ {nick} ในประวัติแชต จึงยืนยันการส่งไม่ได้"),
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
        "global_min_gap_seconds": 20, "max_per_hour": 10, "delay_range_seconds": [0.5, 1.0],
        "max_reply_length": 200, "max_incoming_length": 500,
    },
    # Room music DJ: chat commands (!request/!queue/!current/!skip/!help) with a
    # persistent queue. Needs autoreply.enabled for chat plumbing. Audio backends:
    # "chat" announces only; "local" also plays .wav files on this machine
    # (route them into Camfrog with a virtual cable / stereo mix for the room
    # to hear; MP3 needs converting to WAV first).
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
try:
    from camfrog_windows_runtime import (
        WindowsCamfrogProcessDiscovery,
        WindowsCamfrogWindowClassifier,
        attach_to_camfrog_client,
        evaluate_client_runtime_state,
        ProcessIdentity,
        ClassifiedWindow,
        ClientRuntimeState,
        WindowRole,
    )
except ImportError:
    WindowsCamfrogProcessDiscovery = None  # type: ignore
    WindowsCamfrogWindowClassifier = None  # type: ignore
    attach_to_camfrog_client = None  # type: ignore
    evaluate_client_runtime_state = None  # type: ignore
    ProcessIdentity = None  # type: ignore
    ClassifiedWindow = None  # type: ignore
    ClientRuntimeState = None  # type: ignore
    WindowRole = None  # type: ignore


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
                "Camfrog Status Changer", "Camfrog Random Status", "Camfrog Marquee Status",
                "Camfrog Music DJ", "Camfrog Auto Reply", "Camfrog Presence", "Room Control", "ZChat")):
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
    wins = Desktop(backend="uia").windows(
        title_re=cfg["window_title_regex"], visible_only=True)
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
    log.warning("Status edit control not found in any matching window; using first window")
    return wins[0]


def process_windows(main) -> list:
    """All visible top-level windows of the same process as `main` (Camfrog opens each chat room,
    IM and the buddy list as separate windows)."""
    from pywinauto import Desktop
    return Desktop(backend="uia").windows(process=main.process_id(), visible_only=True)


def get_verified_camfrog_process(pid: Optional[int] = None):
    """Return a verified Camfrog client ProcessIdentity or None."""
    if WindowsCamfrogProcessDiscovery is None:
        return None
    try:
        return WindowsCamfrogProcessDiscovery().get_verified_process(pid=pid)
    except Exception:
        return None


def get_client_runtime_state(pid: Optional[int] = None):
    """Evaluate evidence-separated runtime states for verified Camfrog client."""
    if attach_to_camfrog_client is None:
        return None
    try:
        att = attach_to_camfrog_client(pid=pid)
        return att.get_runtime_state()
    except Exception:
        return None


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


def reveal_chat_message(ctrl, nickname, message):
    """Scroll a matching local chat echo into the embedded history viewport."""
    try:
        import pythoncom
        import win32con
        import win32gui
        from win32com.client import Dispatch

        servers = ctrl.descendants(class_name="Internet Explorer_Server")
        if not servers:
            return False
        msg = win32gui.RegisterWindowMessage("WM_HTML_GETOBJECT")
        sent, result = win32gui.SendMessageTimeout(
            servers[0].handle, msg, 0, 0, win32con.SMTO_ABORTIFHUNG, 1000
        )
        if not sent or not result:
            return False
        document = Dispatch(pythoncom.ObjectFromLresult(result, pythoncom.IID_IDispatch, 0))
        expected_nick = str(nickname).strip().casefold()
        expected_message = " ".join(str(message).split()).casefold()
        matches = []
        elements = document.all
        for index in range(int(elements.length)):
            nickname_node = elements.item(index)
            if "nickname-field" not in str(nickname_node.className or "").lower().split():
                continue
            row = nickname_node.parentElement
            if "msg-user" not in str(row.className or "").lower().split():
                continue
            sender = str(nickname_node.innerText or "").strip().casefold()
            row_message = ""
            for child_index in range(int(row.children.length)):
                child = row.children.item(child_index)
                if "msgid" in str(child.className or "").lower().split():
                    row_message = " ".join(str(child.innerText or "").split()).casefold()
                    break
            if sender == expected_nick and row_message == expected_message:
                matches.append(row)
        if not matches:
            return False

        # Prefer the newest occurrence when the same automatic reply was sent before.
        row = matches[-1]
        row.scrollIntoView(False)
        rect = row.getBoundingClientRect()
        root = document.documentElement
        style = row.currentStyle
        width, height = int(root.clientWidth), int(root.clientHeight)
        return bool(
            int(row.offsetWidth) > 0 and int(row.offsetHeight) > 0
            and str(style.display or "").lower() != "none"
            and str(style.visibility or "").lower() not in {"hidden", "collapse"}
            and float(rect.left) >= 0 and float(rect.top) >= 0
            and float(rect.right) <= width and float(rect.bottom) <= height
        )
    except Exception as exc:
        log.debug("Could not reveal local IM echo: %s", type(exc).__name__)
        return False


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


def _type_human_like(text: str, keyboard_module=None, min_jitter: float = 0.015, max_jitter: float = 0.055):
    """Simulate authentic human typing rhythm with randomized inter-keystroke jitter."""
    if keyboard_module is None:
        from pywinauto import keyboard as keyboard_module
    import random
    # Human reaction / thinking pause before starting to type
    time.sleep(random.uniform(0.12, 0.35))
    for char in text:
        escaped = "{%s}" % char if char in "{}()+^%~" else char
        keyboard_module.send_keys(escaped, pause=0.005, with_spaces=True)
        # Jitter delay per keystroke (simulates 50-70 WPM typing cadence)
        delay = random.uniform(min_jitter, max_jitter)
        # Occasional micro-pause at spaces or punctuation (natural cadence)
        if char in " .,!?\n":
            delay += random.uniform(0.04, 0.12)
        time.sleep(delay)


def commit_web(win, ctrl, text: str, require_fg: bool = True, restore: bool = False, human_like: bool = True) -> bool:
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
    pasted = False
    if not human_like:
        pasted = _clip_set(text)
    try:
        keyboard.send_keys("^a", pause=0.02)
        if human_like or not pasted:
            _type_human_like(text, keyboard)
        else:
            keyboard.send_keys("^v", pause=0.01, with_spaces=True)
        time.sleep(0.15)
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
    try:
        u.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_size_t, ctypes.c_ssize_t)
        u.PostMessageW.restype = ctypes.c_int
    except (AttributeError, TypeError):
        pass

    if not u.PostMessageW(handle, 0x0100, 0x0D, 0):
        raise RuntimeError("Could not post Enter key-down to status control")
    if not u.PostMessageW(handle, 0x0101, 0x0D, 0):
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
            # Always use "combo" target for background submit: posting Enter
            # directly to the child Edit control triggers a Windows ding on
            # Camfrog builds that don't handle Enter in the Edit — use the
            # verified CComboBoxTS parent instead.
            _background_enter(ctrl, "combo" if background_enter_target == "edit" else background_enter_target)
    if prev and prev != win.handle and user32.IsWindow(prev):
        time.sleep(0.15)
        user32.SetForegroundWindow(prev)  # best effort: give focus back to what you were doing
    return True


def commit_strict_background_status(text: str, dry: bool = False) -> bool:
    """Run a status mutation through the shared fail-closed background engine."""
    if dry:
        log.info(t("dry_send", text=shown(text)))
        return True
    try:
        from camfrog_status_engine import (
            BackgroundStatusExecutor,
            ExecutionMode,
            StatusResultState,
        )
        evidence = BackgroundStatusExecutor(
            default_mode=ExecutionMode.STRICT_BACKGROUND,
        ).execute_status_change(
            status_text=text,
            execution_mode=ExecutionMode.STRICT_BACKGROUND,
        )
    except Exception as exc:
        log.error("Strict background status failed: %s", str(exc)[:160])
        return False
    if evidence.result_state == StatusResultState.CLIENT_STATE_CONFIRMED.value:
        log.info("Camfrog client shows the status; external state remains unverified.")
        return True
    log.error("Strict background status failed (state=%s, code=%s).",
              evidence.result_state, evidence.error_code)
    return False


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


def confirm_im_reply_echo(history, previous_lines, own_nickname, reply_text,
                          history_tail=150, timeout=5.0):
    """Confirm a live IM send only after its exact outgoing line appears in the chat log."""
    expected_nick = str(own_nickname).strip().casefold()
    expected_text = " ".join(str(reply_text).split()).casefold()
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            current = read_chat(history, history_tail).splitlines()
        except Exception:
            current = []
        for line in new_lines(previous_lines, current):
            match = LINE_RE.match(line.strip())
            if not match:
                continue
            sender = match["nick"].strip().casefold()
            message = " ".join(match["msg"].split()).casefold()
            if sender == expected_nick and message == expected_text:
                if reveal_chat_message(history, own_nickname, reply_text):
                    log.info(t("im_echo_visible", nick=own_nickname))
                else:
                    log.warning(t("im_echo_not_visible", nick=own_nickname))
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.1, remaining))


# ---------- process control (background mode) ----------
def pid_path(cfg):
    return BASE / cfg["safety"]["pid_file"]


def read_pid(cfg):
    record = read_pid_record(cfg)
    return record["pid"] if record else None


def read_pid_record(cfg):
    try:
        raw = pid_path(cfg).read_text(encoding="utf-8").strip()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            raw_pid = data.get("pid")
            if isinstance(raw_pid, bool):
                return None
            pid = int(raw_pid)
            created = data.get("creation_time")
            if created is not None:
                if isinstance(created, bool):
                    return None
                created = int(created)
            if not 0 < pid <= 0xFFFFFFFF or (created is not None and created < 0):
                return None
            return {"pid": pid, "creation_time": created}
        pid = int(raw)
        return {"pid": pid, "creation_time": None} if 0 < pid <= 0xFFFFFFFF else None
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
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME))
    kernel32.GetProcessTimes.restype = wintypes.BOOL
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


def pid_creation_time(pid):
    """Return a process creation FILETIME to distinguish reused PIDs."""
    if os.name != "nt":
        return None
    try:
        pid = int(pid)
        if not 0 < pid <= 0xFFFFFFFF:
            return None
        kernel32, wintypes = _process_query_api()
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user))
            if not ok:
                return None
            return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def write_pid(cfg, pid):
    """Atomically register a worker PID with its process creation time."""
    path = pid_path(cfg)
    record = {"pid": int(pid), "creation_time": pid_creation_time(pid)}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(path)


def pid_is_ours(pid, image=None, expected_creation_time=None):
    """Trust a matching executable only when its PID creation time also matches."""
    if os.name != "nt":
        return False
    if expected_creation_time is None:
        return False
    if pid_creation_time(pid) != expected_creation_time:
        return False
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
    record = read_pid_record(cfg)
    if not record:
        return None
    pid = record["pid"]
    created = record["creation_time"]
    return pid if (created is not None and pid_alive(pid)
                   and pid_is_ours(pid, expected_creation_time=created)) else None


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
    if worker_mutex_active():
        print(t("another_instance"))
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
    timeout, deadline = 15.0, time.monotonic() + 15.0
    while True:
        active = running_pid(cfg)
        record = read_pid_record(cfg)
        child_registered = bool(
            record and record["pid"] == p.pid
            and record["creation_time"] is None and worker_mutex_active())
        if active == p.pid or child_registered:
            print(t("bg_started", pid=p.pid, log=logf))
            return 0
        if active:
            _terminate_startup_child(p)
            print(t("already_running", pid=active))
            return 1
        if p.poll() is not None:
            if worker_mutex_active():
                print(t("another_instance"))
                return 1
            print(t("bg_failed", code=p.returncode, log=logf))
            return 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_startup_child(p)
            print(t("bg_start_timeout", seconds=int(timeout), log=logf))
            return 1
        time.sleep(min(0.1, remaining))


def _terminate_startup_child(process):
    """Stop only the child just spawned when it failed to become the registered worker."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            log.error("new worker child did not exit after startup cancellation")
    except OSError as exc:
        log.warning("could not stop unregistered startup child: %s", type(exc).__name__)


def cmd_stop(cfg):
    pid = running_pid(cfg)
    record = read_pid_record(cfg)
    stop_file = BASE / cfg["safety"]["stop_file"]
    if not worker_mutex_active():
        print(t("not_running"))
        pid_path(cfg).unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)
        return 0
    if pid:
        print(t("stopping", pid=pid))
    stop_file.write_text("stop")
    for _ in range(200):  # up to 20 s for cooperative shutdown
        if not worker_mutex_active():
            break
        time.sleep(0.1)
    forced = False
    created = record.get("creation_time") if record else None
    if (worker_mutex_active() and pid and created is not None
            and pid_alive(pid)
            and pid_is_ours(pid, expected_creation_time=created)):
        try:
            subprocess.run([system_taskkill_path(), "/PID", str(pid), "/F"],
                           capture_output=True, check=False, timeout=10)
            forced = True
        except (OSError, subprocess.TimeoutExpired):
            log.exception("taskkill failed for PID %s", pid)
        for _ in range(30):  # confirm worker released its mutex
            if not worker_mutex_active():
                break
            time.sleep(0.1)
    if worker_mutex_active():
        if pid:
            print(t("stop_failed", pid=pid))
        else:
            print(t("stop_failed_unknown"))
        return 1  # retain STOP/PID files so another stop attempt can finish cleanup
    print(t("killed") if forced else t("stopped_ok"))
    stop_file.unlink(missing_ok=True)
    pid_path(cfg).unlink(missing_ok=True)
    return 0


def stats_path(cfg):
    return BASE / cfg["stats"]["file"]


def cmd_state(cfg):
    pid = running_pid(cfg)
    running = bool(pid) or worker_mutex_active()
    if pid:
        print(t("state_running", pid=pid))
    elif running:
        print(t("state_running_unknown"))
    else:
        print(t("state_stopped"))
    try:
        d = json.loads(stats_path(cfg).read_text(encoding="utf-8"))
        print(t("stats_hdr"))
        replies = int(d.get("replies") or 0) + int(d.get("im_replies") or 0)
        print(t("stats_line", started=d.get("started", "?"), st=d.get("statuses", 0),
                rp=replies, fl=d.get("failures", 0)))
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
            self.current = {"title": str(cur["title"]).strip(), "user": str(cur.get("user", ""))}
        items = raw.get("queue")
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and str(it.get("title", "")).strip():
                    self.queue.append({"title": str(it["title"]).strip(),
                                       "user": str(it.get("user", ""))})

    def save(self):
        try:
            atomic_write(self.path, json.dumps(
                {"current": self.current, "queue": self.queue},
                ensure_ascii=False, indent=2) + "\n")
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
        self._mtime = self.cfg_path.stat().st_mtime if self.cfg_path and self.cfg_path.exists() else 0
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
        is_status = ctrl is getattr(self, "status_edit", None) and win is None
        if (is_status and self.cfg.get("status", {}).get("execution_mode")
                == "strict_background"):
            return commit_strict_background_status(text, self.dry)
        target = (self.cfg["status"].get("background_enter_target", "edit")
                  if is_status else "edit")
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
            fresh = new_lines(st["prev"], cur)
            overlap = len(cur) - len(fresh)
            if not can_reply:
                st["prev"] = cur
                continue  # consume backlog so it isn't answered later
            if len(fresh) > RESET_BURST:
                log.warning(t("burst_skip", n=len(fresh)))
                st["prev"] = cur
                continue

            for index, line in enumerate(fresh):
                def mark_consumed(
                    state=st, current_lines=cur, line_overlap=overlap, line_index=index
                ):
                    state["prev"] = current_lines[:line_overlap + line_index + 1]

                m = LINE_RE.match(line.strip())
                if not m:
                    mark_consumed()
                    continue
                nick, msg = m["nick"].strip(), m["msg"][: im["max_incoming_length"]]
                lk = nick.lower()
                if not NICK_RE.match(nick) or lk == self.own or lk in self.ignore:
                    mark_consumed()
                    continue
                d = evaluate_im(self.cfg, nick, msg, self.im_rules, self.im_skips)
                if d["skip"] == "sender":
                    log.info(t("im_skip_nick", nick=nick))
                    mark_consumed()
                    continue
                if d["skip"]:
                    log.info(t("im_filtered", nick=nick, reason=t("why_" + d["skip"])))
                    mark_consumed()
                    continue
                if d["rule"] is None or d["reply"] is None:
                    log.info(t("im_no_rule", nick=nick))
                    mark_consumed()
                    continue
                now = time.monotonic()
                self.im_sent = [x for x in self.im_sent if now - x < 3600]
                day = [x for x in self.im_day.get(lk, []) if now - x < 86400]
                self.im_day[lk] = day
                if (now < self.im_next_ok or len(self.im_sent) >= im["max_per_hour"]
                        or len(day) >= im["max_per_sender_per_day"]
                        or now - self.im_sender_last.get(lk, -1e9) < im["per_sender_cooldown_seconds"]
                        or now - d["rule"]["last"] < d["rule"]["cooldown"]):
                    mark_consumed()
                    continue
                time.sleep(random.uniform(*im["delay_range_seconds"]))
                try:
                    before_send = read_chat(st["hist"], ar["history_tail"]).splitlines()
                except Exception as e:
                    raise RuntimeError("could not snapshot IM history before sending") from e
                if not commit(st["win"], st["inp"], d["reply"], self.im_dry,
                              s["require_foreground"], None, s["restore_previous_window"]):
                    raise RuntimeError(t("reply_fail"))
                if self.im_dry:
                    mark_consumed()
                    log.info(t("im_previewed", nick=nick))
                    continue
                if not confirm_im_reply_echo(
                        st["hist"], before_send, self.own, d["reply"], ar["history_tail"]):
                    # The send may have reached Camfrog even if its log pane did not
                    # refresh. Consume it to avoid an unsafe duplicate on the next tick.
                    mark_consumed()
                    raise RuntimeError(t("im_unconfirmed", nick=nick))
                mark_consumed()
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
        active = self.marq
        if active is not None and active.get("loop") and now >= active["deadline"]:
            self.marq = None
            self.status_i += 1
            self.stats["statuses"] += 1
            self.current_text = active["raw"]
            log.info(t("status_set", text=shown(active["frames"][-1])))
        if self.marq is None:
            if now - self.last_status < st["interval_seconds"]:
                return
            raw = self.choose_status()
            text = expand(raw, own=self.cfg["autoreply"]["own_nickname"])
            if not text.strip():
                log.warning(t("status_blank"))
                self.last_status = now
                return
            frames = self.build_frames(text)
            mq = st["marquee"]
            loop = bool(mq.get("infinite_loop") and mq.get("scroll")
                        and len(frames) > 1)
            self.marq = {"raw": raw, "frames": frames, "i": 0, "next": now,
                         "loop": loop, "deadline": now + st["interval_seconds"],
                         "history_recorded": False}
            if loop:
                self.last_status = now
        m = self.marq
        if now < m["next"]:
            return
        frame = m["frames"][m["i"]]
        if not self.send(self.status_edit, frame, self.apply_btn):
            self.marq = None
            self.last_status = now - st["interval_seconds"] + st["retry_seconds"]
            raise RuntimeError(t("status_fail"))
        if (m["i"] == 0 and not m["history_recorded"]
                and st["history"]["enabled"] and st["history"]["record"]):
            self.hist.record(m["raw"])
            m["history_recorded"] = True
        if len(m["frames"]) > 1:
            self.stats["marquee_frames"] += 1
        m["i"] += 1
        if m["loop"]:
            if m["i"] >= len(m["frames"]):
                m["i"] = 0
            m["next"] = time.monotonic() + max(
                0.3, float(st["marquee"]["step_seconds"]))
            log.debug(t("status_set", text=shown(frame)))
            return
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
        if cmd in ("play", "request", "req", "song"):
            title = arg
            if not title:
                raise DJError("please specify a song title to play: !play <song name>")
            if dj.get("audio_backend", "chat") == "local":
                song = dj_find_song(BASE / dj.get("music_dir", "music"), arg)
                if song is None:
                    # Extended fallback: search in music directory with looser match
                    raise DJError(f'not found as .wav in music/: "{arg}"')
                title = song.stem
            if queue.current is not None \
                    and queue.current["title"].lower() == title.lower():
                raise DJError(f'"{title}" is playing now')
            pos = queue.add(title, nick, dj.get("max_per_user", 3))
            if queue.current is None:
                queue.current = queue.queue.pop(0)
                self.dj_start_playback(queue, dj)
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
            return "[dj] current: {0} ({1})".format(queue.current["title"],
                                                   queue.current["user"])
        if cmd == "skip":
            nxt = queue.skip(nick, owner)
            self.dj_start_playback(queue, dj)
            if nxt is None:
                return "[dj] skipped, queue is empty"
            return dj["announce_now"].format(title=nxt["title"], user=nxt["user"])
        if cmd == "help":
            return "[dj] commands: !play <song> !request <song> !queue !current !skip"
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
            if queue.current is not None and not self.dj_started \
                    and not self.dry and dj.get("audio_backend", "chat") == "local":
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
            self.say_now(dj["announce_now"].format(title=nxt["title"], user=nxt["user"]))
        return True

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
                time.sleep(wait)
            log.info(t("stopped"))
            return 0
        finally:
            self.write_stats(time.monotonic(), force=True)


def worker_mutex_name():
    profile = re.sub(r"[^A-Za-z0-9_.-]", "_", str(APP_PROFILE))
    data_dir = os.path.normcase(os.path.abspath(os.fspath(BASE))).casefold()
    digest = hashlib.sha256(data_dir.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]
    return f"Local\\CamfrogWorker_{profile}_{digest}"


def worker_mutex_active():
    """Check the worker's named mutex without creating or acquiring it."""
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_mutex = kernel32.OpenMutexW
        open_mutex.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        open_mutex.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = open_mutex(0x00100000, False, worker_mutex_name())  # SYNCHRONIZE
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except (OSError, AttributeError, TypeError):
        return False


def worker_is_running(cfg):
    return bool(running_pid(cfg)) or worker_mutex_active()


def acquire_run_mutex():
    """Create a per-profile, per-data-directory Windows mutex for worker runs."""
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    ctypes.set_last_error(0)
    handle = create_mutex(None, 0, worker_mutex_name())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        log.warning("worker start refused: per-app mutex already exists")
        return False
    log.info("worker mutex acquired (pid=%s)", os.getpid())
    return handle


def release_run_mutex(handle):
    if handle is None or handle is False or os.name != "nt":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    if not kernel32.CloseHandle(handle):
        log.debug("Could not close worker mutex handle: %s", ctypes.get_last_error())


def run_guarded(cfg, cfg_path=None):
    """Single-instance guard + PID file around Runner.run()."""
    mutex = acquire_run_mutex()
    if mutex is False:
        existing = running_pid(cfg)
        print(t("already_running", pid=existing) if existing else t("another_instance"))
        return 3
    pf = pid_path(cfg)
    wrote_pid = False
    try:
        existing = running_pid(cfg)
        if existing and existing != os.getpid():
            print(t("already_running", pid=existing))
            return 3
        write_pid(cfg, os.getpid())
        wrote_pid = True
        return Runner(cfg, cfg_path).run()
    finally:
        try:
            if wrote_pid and read_pid(cfg) == os.getpid():
                pf.unlink(missing_ok=True)
        finally:
            release_run_mutex(mutex)


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
    if cfg.get("status", {}).get("execution_mode") == "strict_background":
        ok = commit_strict_background_status(text, cfg["dry_run"])
        print(t("ok") if ok else t("failed"))
        return 0 if ok else 1
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
    p.add_argument("--daemon", action="store_true", help="run as headless background daemon (Linux/WSL2/Windows Server)")
    return p


def main(argv=None, force_strict_background=False):
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
    if force_strict_background:
        cfg.setdefault("status", {})["execution_mode"] = "strict_background"
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
    if (force_strict_background and a.cmd in ("run", "start", "status")
            and not cfg.get("dry_run", True)
            and cfg.get("status", {}).get("enabled", True)):
        try:
            from camfrog_status_engine import strict_background_submit_supported
            supported = strict_background_submit_supported()
        except (ImportError, AttributeError):
            supported = False
        if not supported:
            print("background status failed (state=BACKGROUND_UNSUPPORTED, "
                  "code=STRICT_SUBMIT_UNVERIFIED)")
            return 2
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
    if getattr(a, "daemon", False) and os.name != "nt":
        # Fork to run in headless background daemon mode on POSIX / Linux / WSL2
        try:
            pid = os.fork()
            if pid > 0:
                print(f"[Daemon] Forked background worker process (PID {pid})")
                return 0
            os.setsid()
        except OSError as e:
            print(f"[Daemon] Fork failed: {e}")
            return 1
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


if __name__ == "__main__":
    sys.exit(main())
