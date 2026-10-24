"""Offline tests: no Camfrog / pywinauto needed (UI is never touched)."""
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camfrog_auto as c  # noqa: E402
import camfrog_status_engine as cse  # noqa: E402
from camfrog_status_engine import (  # noqa: E402
    BackgroundStatusExecutor,
    StatusControlAdapter,
    StatusResultState,
    validate_status_config,
)
from camfrog_windows_runtime import (  # noqa: E402
    ClassifiedWindow,
    ProcessIdentity,
    WindowRole,
)
from tools.evidence_validator import validate_bundle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    x = c.load_cfg(ROOT / "config.example.json")
    x["dry_run"] = True  # tests must not depend on the user's live settings in the shipped config
    x["status"]["enabled"] = True
    x["autoreply"]["enabled"] = True
    x["status"]["enabled"] = True
    x["autoreply"]["enabled"] = True
    x["autoreply"]["own_nickname"] = ""
    x["autoreply"]["rules"] = [
        {"pattern": "@me", "reply": "Hello {sender}", "mention": True},
        {"pattern": "hello", "reply": "Hi {sender}", "lang": "en"},
    ]
    x["autoreply"]["skip_patterns"] = ["free money"]
    return x


@pytest.fixture(autouse=True)
def _reset_lang(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)   # never write history/stats into the repo
    c.LANG = "en"
    yield
    c.LANG = "en"


def test_example_config_valid(cfg):
    errs, _ = c.validate(cfg)
    assert errs == []


@pytest.mark.parametrize("argv", [
    ["check", "--config", str(ROOT / "config.example.json")],
    ["--config", str(ROOT / "config.example.json"), "check"],
    ["--lang", "th", "check", "--config", str(ROOT / "config.example.json")],
])
def test_option_order(argv):
    assert c.main(argv) == 0


def test_i18n_and_detect():
    c.LANG = "th"
    assert "คอนฟิก" in c.t("cfg_ok")
    c.LANG = "en"
    assert c.t("cfg_ok") == "config OK"
    assert c.detect_lang("สวัสดี") == "th" and c.detect_lang("hello") == "en"
    assert c.detect_lang("hi สวัสดี") == "th"


def test_every_string_has_both_languages():
    for k, (en, th) in c.S.items():
        assert en and th, k


def test_pick_status_modes():
    it = {"th": "ไทย", "en": "EN"}
    assert c.pick_status(it, "th", 0) == "ไทย"
    assert c.pick_status(it, "en", 0) == "EN"
    assert c.pick_status(it, "both", 0) == "ไทย | EN"
    assert [c.pick_status(it, "alternate", i) for i in (0, 1)] == ["ไทย", "EN"]
    assert c.pick_status("plain", "both", 3) == "plain"
    assert c.pick_status({"en": "only"}, "th", 0) == "only"


def test_pick_reply_fallbacks():
    spec = {"th": ["ก"], "en": ["e"]}
    assert c.pick_reply(spec, "th") == "ก" and c.pick_reply(spec, "en") == "e"
    assert c.pick_reply({"en": "e"}, "th") == "e"
    assert c.pick_reply("x", "en") == "x" and c.pick_reply(["a"], "th") == "a"


def test_expand_and_safe_reply():
    out = c.expand("{sender}/{me}/{time}/{date}/{unknown}", "bob", "me")
    assert out.startswith("bob/me/") and out.endswith("/{unknown}")
    assert c.safe_reply("/ban {sender}", "b", 50) is None
    assert c.safe_reply("hi {sender}\nx", "bob", 50) == "hi bob x"
    assert c.safe_reply("x" * 500, "b", 10) == "x" * 10


def test_new_lines():
    assert c.new_lines(["a", "b"], ["a", "b", "c"]) == ["c"]
    assert c.new_lines(["a", "b", "c"], ["b", "c", "d"]) == ["d"]
    assert c.new_lines([], ["x"]) == ["x"]


def test_line_parsing_ignores_non_message_lines():
    assert c.LINE_RE.match("bob: hello")
    assert not c.LINE_RE.match("no colon here")
    assert not c.NICK_RE.match("/ban x")


def test_in_window_overnight():
    t = c.dt.time
    assert c.in_window("22:00", "02:00", t(23, 0)) and c.in_window("22:00", "02:00", t(1, 0))
    assert not c.in_window("22:00", "02:00", t(12, 0))
    assert c.in_window("09:00", "17:00", t(9, 0)) and not c.in_window("09:00", "17:00", t(18, 0))


def test_rules_mention_lang_and_skips(cfg):
    ar = cfg["autoreply"]
    rules, skips = c.compile_rules(ar), c.compile_skips(ar)
    assert c.match_rule(rules, "hello there", "en", "me")["idx"] == 2          # greeting rule
    assert c.match_rule(rules, "hey @me you there?", "en", "me")["idx"] == 1   # mention rule first
    assert c.match_rule(rules, "random words", "en", "me") is None
    assert c.is_skipped("see http://spam.example", skips, True) == "link"
    assert c.is_skipped("see http://spam.example", skips, False) is None
    assert c.is_skipped("free money now", skips, False) == "pattern"


def test_validate_catches_bad_config(cfg):
    cfg["language"] = "fr"
    cfg["status"]["language_mode"] = "zz"
    cfg["status"]["schedules"] = [{"start": "bad", "end": "10:00", "messages": ["x"]}]
    cfg["autoreply"]["skip_patterns"] = ["("]
    cfg["autoreply"]["rules"][0]["pattern"] = "("
    errs, _ = c.validate(cfg)
    assert len(errs) == 5, errs  # language, mode, schedule, skip pattern, rule regex
    c.LANG = "th"
    assert all(e for e in c.validate(cfg)[0])


def test_status_schedule_pool(cfg):
    r = c.Runner(cfg)
    cfg["status"]["schedules"] = [{"start": "00:00", "end": "23:59", "messages": ["sched"]}]
    assert r.status_pool() == ["sched"]
    cfg["status"]["schedules"] = []
    assert r.status_pool() == cfg["status"]["messages"]


def test_pid_helpers():
    assert c.pid_alive(os.getpid()) and not c.pid_alive(None) and not c.pid_alive(99999999)
    if os.name == "nt":
        assert c.pid_image(os.getpid())
        created = c.pid_creation_time(os.getpid())
        assert created is not None
        assert c.pid_is_ours(os.getpid(), expected_creation_time=created)


def test_single_instance_and_stop(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    pf = tmp_path / cfg["safety"]["pid_file"]
    pf.write_text(str(os.getppid()))                 # another live process
    monkeypatch.setattr(c, "acquire_run_mutex", lambda: False)
    assert c.run_guarded(cfg) == 3
    pf.write_text("99999999")                        # stale pid
    assert c.cmd_stop(cfg) == 0 and not pf.exists()
    assert c.cmd_state(cfg) == 1


def test_im_failed_send_remains_pending_but_unconfirmed_send_is_consumed(cfg, monkeypatch):
    cfg["dry_run"] = False
    cfg["autoreply"]["own_nickname"] = "Seaza"
    cfg["autoreply_im"].update(
        enabled=True, dry_run=False, only_nicknames=["zdevz"], prefix="[auto] ",
        delay_range_seconds=[0, 0],
        rules=[{"pattern": "hello", "reply": "Hi {sender}", "lang": "en",
                "cooldown_seconds": 0}],
    )
    r = c.Runner(cfg)
    state = {"hist": object(), "win": object(), "inp": object(), "prev": []}
    r.im_wins = {1: state}
    monkeypatch.setattr(c, "read_chat", lambda *_args: "zdevz: hello")
    monkeypatch.setattr(c.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr(c.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(c, "commit", lambda *_args: False)

    with pytest.raises(RuntimeError, match="reply commit failed"):
        r.do_im(True)
    assert state["prev"] == []  # retry this message on the next worker tick

    monkeypatch.setattr(c, "commit", lambda *_args: True)
    monkeypatch.setattr(c, "confirm_im_reply_echo", lambda *_args: False)
    with pytest.raises(RuntimeError, match="not confirmed"):
        r.do_im(True)
    assert state["prev"] == ["zdevz: hello"]  # avoid a duplicate if delivery was ambiguous


def test_confirmed_im_echo_is_revealed_in_local_history(cfg, monkeypatch):
    revealed = []
    history = object()
    monkeypatch.setattr(c, "read_chat", lambda *_args: "Seaza: Hi zdevz")
    monkeypatch.setattr(
        c, "reveal_chat_message",
        lambda history, nickname, message: revealed.append((history, nickname, message)) or True,
    )

    assert c.confirm_im_reply_echo(history, [], "Seaza", "Hi zdevz", timeout=0)
    assert revealed == [(history, "Seaza", "Hi zdevz")]


def test_stop_confirms_process_exit_after_taskkill(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    pid_file = tmp_path / cfg["safety"]["pid_file"]
    stop_file = tmp_path / cfg["safety"]["stop_file"]
    pid_file.write_text(json.dumps({"pid": 4242, "creation_time": 123}), encoding="utf-8")
    alive = {"value": True}
    monkeypatch.setattr(c, "running_pid", lambda _cfg: 4242)
    monkeypatch.setattr(c, "pid_alive", lambda _pid: alive["value"])
    monkeypatch.setattr(c, "pid_is_ours", lambda _pid, image=None, expected_creation_time=None:
                        expected_creation_time == 123)
    monkeypatch.setattr(c, "worker_mutex_active", lambda: alive["value"])
    monkeypatch.setattr(c.time, "sleep", lambda _seconds: None)

    def taskkill(*_args, **_kwargs):
        alive["value"] = False

    monkeypatch.setattr(c.subprocess, "run", taskkill)
    assert c.cmd_stop(cfg) == 0
    assert not pid_file.exists() and not stop_file.exists()


def test_stop_keeps_pid_and_stop_files_if_process_survives(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    pid_file = tmp_path / cfg["safety"]["pid_file"]
    stop_file = tmp_path / cfg["safety"]["stop_file"]
    pid_file.write_text(json.dumps({"pid": 4242, "creation_time": 123}), encoding="utf-8")
    monkeypatch.setattr(c, "running_pid", lambda _cfg: 4242)
    monkeypatch.setattr(c, "pid_alive", lambda _pid: True)
    monkeypatch.setattr(c, "pid_is_ours", lambda _pid, image=None, expected_creation_time=None:
                        expected_creation_time == 123)
    monkeypatch.setattr(c, "worker_mutex_active", lambda: True)
    monkeypatch.setattr(c.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(c.subprocess, "run", lambda *_args, **_kwargs: None)
    assert c.cmd_stop(cfg) == 1
    assert pid_file.exists() and stop_file.exists()


def test_stats_written_atomically(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    r = c.Runner(cfg)
    r.stats["replies"] = 3
    r.write_stats(0.0, force=True)
    assert json.loads((tmp_path / "stats.json").read_text())["replies"] == 3
    assert not list(tmp_path.glob("*.tmp"))
    assert c.cmd_state(cfg) == 1   # prints stats, process not running


def test_hot_reload_accepts_valid_and_rejects_invalid(cfg, tmp_path, monkeypatch):
    original_pattern = cfg["autoreply"]["rules"][0]["pattern"]
    monkeypatch.setattr(c, "BASE", tmp_path)
    monkeypatch.setattr(c.Runner, "resolve", lambda self: None)  # no UI in tests
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    r = c.Runner(cfg, path)
    # valid edit
    data = json.loads(path.read_text(encoding="utf-8"))
    data["autoreply"]["max_per_hour"] = 7
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    r.maybe_reload(100.0)
    assert r.cfg["autoreply"]["max_per_hour"] == 7
    # invalid edit (bad regex) is rejected, old config kept
    data["autoreply"]["rules"][0]["pattern"] = "("
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
    r.maybe_reload(200.0)
    assert r.cfg["autoreply"]["max_per_hour"] == 7
    assert r.cfg["autoreply"]["rules"][0]["pattern"] == original_pattern
    # going live without own_nickname via reload is rejected
    data["autoreply"]["rules"][0]["pattern"] = ".*"
    data["dry_run"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 15))
    r.maybe_reload(300.0)
    assert r.cfg["dry_run"] is True


def test_run_refuses_live_without_nickname(cfg):
    cfg["dry_run"] = False
    assert c.Runner(cfg).run() == 2


def test_init_command(tmp_path, capsys):
    p = tmp_path / "config.json"
    assert c.main(["init", "--config", str(p)]) == 0
    assert c.main(["check", "--config", str(p)]) == 0
    assert c.main(["init", "--config", str(p)]) == 1          # refuses to overwrite
    assert c.main(["init", "--config", str(p), "--force"]) == 0


def test_test_rules_command(tmp_path, capsys):
    config = c.load_cfg(ROOT / "config.example.json")
    config["autoreply"]["rules"] = [
        {"pattern": "hello", "reply": "Hello {sender}", "lang": "en"},
        {"pattern": "สวัสดี", "reply": "สวัสดี {sender}"},
    ]
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    cfgp = str(cfg_file)
    assert c.main(["test-rules", "สวัสดีครับ", "--sender", "Bob", "--config", cfgp]) == 0
    out = capsys.readouterr().out
    assert "rule #2" in out and "Bob" in out
    c.main(["test-rules", "check http://x.example", "--config", cfgp])
    assert "SKIPPED" in capsys.readouterr().out
    c.main(["--lang", "th", "test-rules", "zzz qqq", "--config", cfgp])
    assert "ไม่มีกฎที่ตรง" in capsys.readouterr().out


def test_test_rules_file(tmp_path, capsys):
    f = tmp_path / "chat.txt"
    f.write_text("alice: hello\nnot a message line\n/ban x: hi\n", encoding="utf-8")
    assert c.main(["test-rules", "--file", str(f), "--config", str(ROOT / "config.example.json")]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "ignored line" in out


# ---------- marquee ----------
THAI = "สวัสดีครับ ยินดีต้อนรับทุกคนเข้าห้องนะครับ ว่างคุยได้ ทักได้เลย เก่งมากๆ"


def test_clusters_keep_thai_marks_and_leading_vowels_together():
    cl = c.clusters("ก้าวเกี่ยว")
    assert cl == ["ก้", "า", "ว", "เกี่", "ย", "ว"]        # marks and leading เ stay attached
    for x in cl:   # no cluster starts with a combining mark
        assert c.unicodedata.category(x[0]) not in ("Mn", "Mc", "Me")
    assert c.clusters("ab") == ["a", "b"]
    assert c.clusters("เ") == ["เ"]                       # dangling leading vowel kept


@pytest.mark.parametrize("width,stride", [(8, 1), (12, 3), (28, 2), (80, 5)])
def test_marquee_frames_never_blank_or_broken(width, stride):
    for text in (THAI, "Welcome everyone to the room, free to chat so say hi anytime", "ก" * 100):
        frames = c.marquee_frames(text, width, stride, max_frames=300)
        assert frames and all(f.strip() for f in frames)
        for f in frames:
            assert len(c.clusters(f)) <= width
            assert c.unicodedata.category(f[0]) not in ("Mn", "Mc", "Me")   # no orphan mark


def test_marquee_short_text_is_static_and_blank_is_empty():
    assert c.marquee_frames("hello", 28) == ["hello"]
    assert c.marquee_frames("   ", 28) == []


def test_marquee_frame_cap_and_cycle_wraps():
    frames = c.marquee_frames("x" * 50, 10, 1, "  |  ", cycles=5, max_frames=20)
    assert len(frames) <= 20


def test_marquee_infinite_loop_ignores_cycles():
    # infinite_loop=True ignores cycles, uses max_frames only
    frames = c.marquee_frames("abcdefghijklmnopqrstuvwxyz", 10, 1, "  |  ", cycles=100, max_frames=15, infinite_loop=True)
    assert len(frames) == 15
    # Without infinite_loop, cycles would be used
    frames2 = c.marquee_frames("abcdefghijklmnopqrstuvwxyz", 10, 1, "  |  ", cycles=2, max_frames=15, infinite_loop=False)
    assert len(frames2) <= 15
    assert len(frames2) != 100  # not 100 * something


# ---------- history ----------
def test_history_modes_language_and_trim(tmp_path):
    h = c.StatusHistory(tmp_path / "h.json", max_items=5)
    for txt in ("สวัสดี 1", "hello 1", "สวัสดี 2", "hello 2"):
        assert h.add(txt)
    assert not h.add("hello 1") and not h.add("   ")
    assert h.pick("rotate", "th") == "สวัสดี 1"
    h.record("สวัสดี 1")
    assert h.pick("rotate", "th") == "สวัสดี 2"                 # round-robin
    assert h.pick("rotate", "en", exclude="hello 1") == "hello 2"
    assert h.pick("most_used", "th") == "สวัสดี 1"
    assert h.pick("random", "en") in ("hello 1", "hello 2")
    assert h.pick("rotate", "th", exclude="สวัสดี 1") == "สวัสดี 2"
    only_en = c.StatusHistory(tmp_path / "e.json")
    only_en.add("only english")
    assert only_en.pick("rotate", "th") == "only english"       # falls back to any language
    for i in range(10):
        h.add(f"extra {i}")
    assert len(h.items) == 5
    h2 = c.StatusHistory(tmp_path / "h.json")
    h2.load()
    assert [i["count"] for i in h2.items if i["text"] == "สวัสดี 1"] == [1]   # persisted
    assert c.StatusHistory(tmp_path / "missing.json").items == []


def test_history_persistence_roundtrip(tmp_path):
    h = c.StatusHistory(tmp_path / "h.json")
    h.record("สวัสดี")
    h2 = c.StatusHistory(tmp_path / "h.json")
    h2.load()
    assert h2.items[0]["text"] == "สวัสดี" and h2.items[0]["count"] == 1 and h2.items[0]["lang"] == "th"


def test_language_cycle_alternates_th_en_from_history(cfg):
    cfg["status"]["history"]["use_as_source"] = True
    cfg["status"]["language_cycle"] = ["th", "en"]
    r = c.Runner(cfg)
    langs = []
    for _ in range(6):
        raw = r.choose_status()
        langs.append(c.detect_lang(raw))
        r.hist.record(raw)
        r.current_text = raw
    assert langs == ["th", "en"] * 3, langs


def test_language_cycle_from_messages_without_history(cfg):
    cfg["status"]["history"]["use_as_source"] = False
    cfg["status"]["language_cycle"] = ["th", "en"]
    cfg["status"]["messages"] = [{"th": "ไทย 1", "en": "EN 1"}, {"th": "ไทย 2", "en": "EN 2"}]
    r = c.Runner(cfg)
    picked = [r.choose_status() for _ in range(4)]
    assert picked == ["ไทย 1", "EN 1", "ไทย 2", "EN 2"]


# ---------- runner: marquee state machine ----------
def test_do_status_runs_marquee_then_settles_on_full_text(cfg, monkeypatch):
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    clock = {"now": 1000.0}
    monkeypatch.setattr(c.time, "monotonic", lambda: clock["now"])
    cfg["status"]["history"]["use_as_source"] = False
    cfg["status"]["language_cycle"] = []
    cfg["status"]["language_mode"] = "th"
    cfg["status"]["messages"] = [{"th": THAI, "en": "x"}]
    cfg["status"]["max_length"] = 120
    cfg["status"]["marquee"].update(enabled=True, scroll=True, width=16, stride=4, step_seconds=5, max_frames=12)
    r = c.Runner(cfg)
    sent = []
    r.status_edit, r.apply_btn = object(), None
    monkeypatch.setattr(c.Runner, "send", lambda self, ctrl, text, btn=None: sent.append(text) or True)
    now = 1000.0
    r.last_status = -1e9
    for _ in range(40):
        r.do_status(now)
        now += 5
        clock["now"] = now
    assert len(sent) > 3 and sent[-1] == THAI[:120]
    assert all(x.strip() for x in sent)
    assert r.stats["statuses"] >= 1 and r.stats["marquee_frames"] >= len(sent) - 1
    # respects the step spacing: nothing is sent between steps
    sent.clear()
    r.last_status, r.marq = -1e9, None
    clock["now"] = 5000.0
    r.do_status(5000.0)
    clock["now"] = 5001.0
    r.do_status(5001.0)      # < step_seconds later: no second frame
    assert len(sent) == 1
    # history recorded the raw template
    assert r.hist.find(THAI) is not None


def test_do_status_infinite_loop_repeats_frames_until_status_interval(cfg, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(c.time, "monotonic", lambda: clock["now"])
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["history"]["use_as_source"] = False
    cfg["status"]["language_cycle"] = []
    cfg["status"]["messages"] = ["AAA status that is long enough", "BBB next status long enough"]
    cfg["status"]["interval_seconds"] = 3.0
    cfg["status"]["marquee"].update(
        enabled=True, scroll=True, width=10, stride=2, step_seconds=0.5,
        cycles=1, max_frames=5, infinite_loop=True)
    r = c.Runner(cfg)
    r.status_edit, r.apply_btn = object(), None
    sent = []
    monkeypatch.setattr(c.Runner, "send",
                        lambda self, ctrl, text, btn=None: sent.append(text) or True)
    r.last_status = -1e9

    for instant in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        clock["now"] = instant
        r.do_status(instant)

    assert len(sent) == 7
    assert sent[0] == sent[5]
    assert sent[6] != sent[0]
    assert r.status_i == 1
    assert r.marq is not None


def test_marquee_waits_after_slow_ui_update(cfg, monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(c.time, "monotonic", lambda: clock["now"])
    cfg["status"]["history"]["use_as_source"] = False
    cfg["status"]["language_cycle"] = []
    cfg["status"]["language_mode"] = "en"
    cfg["status"]["messages"] = ["A long status message that must scroll"]
    cfg["status"]["max_length"] = 120
    cfg["status"]["marquee"].update(enabled=True, scroll=True, width=10, stride=2,
                                     step_seconds=0.5, max_frames=20)
    r = c.Runner(cfg)
    r.status_edit, r.apply_btn = object(), None

    def slow_send(_self, _ctrl, _text, btn=None):
        clock["now"] += 2.0  # emulate a slow UIA update
        return True

    monkeypatch.setattr(c.Runner, "send", slow_send)
    r.last_status = -1e9
    r.do_status(100.0)
    assert r.marq["next"] == 102.5
    r.do_status(102.4)
    assert r.marq["i"] == 1
    clock["now"] = 102.5
    r.do_status(102.5)
    assert r.marq["i"] == 2


def test_do_status_failure_drops_marquee_and_raises(cfg, monkeypatch):
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    r = c.Runner(cfg)
    r.status_edit, r.apply_btn = object(), None
    monkeypatch.setattr(c.Runner, "send", lambda self, ctrl, text, btn=None: False)
    r.last_status = -1e9
    with pytest.raises(RuntimeError):
        r.do_status(10.0)
    assert r.marq is None


def test_blank_status_is_skipped_not_sent(cfg, monkeypatch):
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    cfg["status"]["history"]["use_as_source"] = False
    cfg["status"]["language_cycle"] = []
    cfg["status"]["messages"] = ["   "]
    r = c.Runner(cfg)
    sent = []
    r.status_edit, r.apply_btn = object(), None
    monkeypatch.setattr(c.Runner, "send", lambda self, ctrl, text, btn=None: sent.append(text) or True)
    r.last_status = -1e9
    r.do_status(10.0)
    assert sent == [] and r.marq is None




# ---------- strict background fallback safety (PHASE 1) ----------

def _strict_runner(cfg, monkeypatch, fallback_authorized=False):
    """Runner with status_edit bound and strict_background mode."""
    cfg["status"]["execution_mode"] = "strict_background"
    if fallback_authorized:
        cfg["status"]["allow_authorized_background_fallback"] = True
    r = c.Runner(cfg)
    r.status_edit = object()
    return r


def test_send_strict_background_success_returns_true_no_fallback(cfg, monkeypatch):
    """commit_strict_background_status success -> send True, commit never called."""
    r = _strict_runner(cfg, monkeypatch)
    called = []
    monkeypatch.setattr(c, "commit_strict_background_status",
                        lambda text, dry: (called.append(text) or True))
    monkeypatch.setattr(c, "commit",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("commit must not be called on strict success")))
    assert r.send(r.status_edit, "Available") is True
    assert called == ["Available"]


def test_send_strict_background_refused_no_fallback_returns_false(cfg, monkeypatch):
    """commit_strict_background_status False + no authorized fallback -> send False, commit never called."""
    r = _strict_runner(cfg, monkeypatch)
    monkeypatch.setattr(c, "commit_strict_background_status", lambda text, dry: False)
    monkeypatch.setattr(c, "commit",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("commit must not be called without authorized fallback")))
    assert r.send(r.status_edit, "Available") is False


def test_send_strict_background_refused_authorized_fallback_calls_commit(cfg, monkeypatch):
    """Authorized fallback -> commit called with require_fg=False."""
    r = _strict_runner(cfg, monkeypatch, fallback_authorized=True)
    commit_args = {}
    monkeypatch.setattr(c, "commit_strict_background_status", lambda text, dry: False)
    def fake_commit(win, ctrl, text, dry, require_fg, btn, restore, target):
        commit_args.update(require_fg=require_fg, target=target, text=text)
        return True
    monkeypatch.setattr(c, "commit", fake_commit)
    assert r.send(r.status_edit, "Available") is True
    assert commit_args["require_fg"] is False
    assert commit_args["text"] == "Available"


def test_send_strict_background_authorized_fallback_but_commit_fails(cfg, monkeypatch):
    """Authorized fallback but commit returns False -> send False (no silent success)."""
    r = _strict_runner(cfg, monkeypatch, fallback_authorized=True)
    monkeypatch.setattr(c, "commit_strict_background_status", lambda text, dry: False)
    monkeypatch.setattr(c, "commit", lambda *a, **k: False)
    assert r.send(r.status_edit, "Available") is False


def test_send_non_strict_mode_uses_normal_commit(cfg, monkeypatch):
    """Non-strict mode -> normal commit path, commit_strict_background_status never called."""
    r = c.Runner(cfg)
    r.status_edit = object()
    strict_called = []
    monkeypatch.setattr(c, "commit_strict_background_status",
                        lambda text, dry: strict_called.append(text) or True)
    monkeypatch.setattr(c, "commit", lambda *a, **k: True)
    assert r.send(r.status_edit, "Available") is True
    assert strict_called == []


def test_validate_rejects_non_boolean_allow_authorized_background_fallback(cfg):
    cfg["status"]["execution_mode"] = "strict_background"
    cfg["status"]["allow_authorized_background_fallback"] = "yes"
    errs, _ = c.validate(cfg)
    assert any("allow_authorized_background_fallback" in e for e in errs)


def test_cmd_status_strict_background_refused_no_fallback_returns_1(cfg, monkeypatch):
    """cmd_status strict_background refused + no fallback -> return 1."""
    cfg["status"]["execution_mode"] = "strict_background"
    monkeypatch.setattr(c, "commit_strict_background_status", lambda text, dry: False)
    assert c.cmd_status(cfg, "Available") == 1


def test_cmd_status_strict_background_authorized_fallback_returns_0(cfg, monkeypatch):
    """cmd_status strict_background refused + authorized fallback -> commit called, return 0."""
    cfg["status"]["execution_mode"] = "strict_background"
    cfg["status"]["allow_authorized_background_fallback"] = True
    monkeypatch.setattr(c, "commit_strict_background_status", lambda text, dry: False)
    commit_args = {}
    def fake_commit(win, ctrl, text, dry, require_fg, btn, restore, target):
        commit_args.update(require_fg=require_fg)
        return True
    monkeypatch.setattr(c, "commit", fake_commit)
    monkeypatch.setattr(c, "get_window", lambda cfg: object())
    monkeypatch.setattr(c, "find", lambda win, sel: object())
    assert c.cmd_status(cfg, "Available") == 0
    assert commit_args["require_fg"] is False


def test_cmd_status_strict_background_authorized_fallback_but_commit_fails(cfg, monkeypatch):
    """cmd_status authorized fallback but commit fails -> return 1."""
    cfg["status"]["execution_mode"] = "strict_background"
    cfg["status"]["allow_authorized_background_fallback"] = True
    monkeypatch.setattr(c, "commit_strict_background_status", lambda text, dry: False)
    monkeypatch.setattr(c, "commit", lambda *a, **k: False)
    monkeypatch.setattr(c, "get_window", lambda cfg: object())
    monkeypatch.setattr(c, "find", lambda win, sel: object())
    assert c.cmd_status(cfg, "Available") == 1


def test_do_status_send_false_drops_marquee_and_raises(cfg, monkeypatch):
    """do_status when send returns False raises RuntimeError and clears marquee (no duplicate on retry)."""
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    r = c.Runner(cfg)
    r.status_edit, r.apply_btn = object(), None
    calls = []
    monkeypatch.setattr(c.Runner, "send",
                        lambda self, ctrl, text, btn=None: (calls.append(text) or False))
    r.last_status = -1e9
    with pytest.raises(RuntimeError, match="status commit failed"):
        r.do_status(10.0)
    assert r.marq is None
    assert len(calls) == 1  # exactly one dispatch attempt, no silent retry


def test_send_status_edit_none_is_not_status_path(cfg, monkeypatch):
    """send with ctrl != status_edit or win set -> normal commit, not strict path."""
    r = c.Runner(cfg)
    r.win = object()
    monkeypatch.setattr(c, "commit_strict_background_status",
                        lambda text, dry: (_ for _ in ()).throw(AssertionError("strict path must not be reached")))
    monkeypatch.setattr(c, "commit", lambda *a, **k: True)
    assert r.send(object(), "hello", win=r.win) is True


def test_defaults_allow_authorized_background_fallback_is_false():
    assert c.DEFAULTS["status"]["allow_authorized_background_fallback"] is False


def test_validate_status_engine_contract_defaults():
    valid, errors, normalized = validate_status_config({})
    assert valid is True
    assert errors == []
    assert normalized["status"]["execution_mode"] == "strict_background"
    assert normalized["safety"]["allow_clipboard_mutation"] is False


def test_emergency_stop_refuses_new_status_work(tmp_path, monkeypatch):
    """Emergency stop gate: new status work is refused while stopped."""
    from camfrog_emergency_stop import EmergencyStopRegistry, EmergencyStopScope
    monkeypatch.setenv("ZCHAT_EMERGENCY_STOP_DB", str(tmp_path / "es.sqlite3"))
    registry = EmergencyStopRegistry()
    registry._reset_for_testing()
    try:
        registry.activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-test")
        ev = BackgroundStatusExecutor().execute_status_change("Available")
        assert ev.result_state == "FAILED"
        assert ev.error_code == "EMERGENCY_STOP"
    finally:
        registry._reset_for_testing()


def test_no_duplicate_submission_when_status_already_applied(cfg, monkeypatch):
    """Duplicate status text is skipped via client read-back fast path (no duplicate mutation)."""
    same = "Currently In Meeting"
    proc = ProcessIdentity(pid=5555, create_time=100.0,
                           exe_path="C:\\Camfrog\\Camfrog Video Chat.exe",
                           publisher="Camfrog LLC", signature_valid=True, version="8.5.0")
    main_win = ClassifiedWindow(hwnd=9999, role=WindowRole.MAIN_DIRECTORY,
                                title="Seaza (Online)", pid=5555, is_visible=True,
                                signatures=["dir"])
    adapter = MagicMock(spec=StatusControlAdapter)
    adapter.read_applied_status.return_value = same
    adapter.controls_are_current.return_value = True
    adapter.supports_strict_background_submit.return_value = True
    adapter.read_edit_text.return_value = same
    resolver = MagicMock()
    resolver.resolve_status_controls.return_value = (proc, main_win, adapter)
    monkeypatch.setattr(cse, "_foreground_window_handle", lambda: 7001)
    executor = BackgroundStatusExecutor(resolver=resolver)
    executor._target_identity_is_current = MagicMock(return_value=True)
    ev = executor.execute_status_change(same, pid=5555)
    assert ev.result_state == StatusResultState.CLIENT_STATE_CONFIRMED.value
    assert ev.verification_source == "client_combo_state_already_active"
    assert not adapter.write_text_no_focus.called
    assert not adapter.post_background_enter.called


def test_stale_window_handle_fails_closed(cfg, monkeypatch):
    """Stale/dangling window handle -> LookupError from resolve, no targeting of wrong window."""
    monkeypatch.setattr(c, "get_window", lambda cfg: (_ for _ in ()).throw(
        LookupError("control not found: {'section': 'status.edit'}")))
    r = c.Runner(cfg)
    with pytest.raises(LookupError):
        r.resolve()
    assert r.status_edit is None
    assert r.win is None


def test_multiple_camfrog_processes_raises_rather_than_guessing(monkeypatch):
    """Multiple verified Camfrog processes -> RuntimeError, never picks wrong target."""
    from camfrog_windows_runtime import WindowsCamfrogProcessDiscovery
    discovery = WindowsCamfrogProcessDiscovery()
    monkeypatch.setattr(discovery, "discover_processes", lambda: [
        SimpleNamespace(pid=1111, create_time=1.0, exe_path="a", publisher="Camfrog LLC",
                        signature_valid=True, version="8.5.0"),
        SimpleNamespace(pid=2222, create_time=2.0, exe_path="b", publisher="Camfrog LLC",
                        signature_valid=True, version="8.5.0"),
    ])
    with pytest.raises(RuntimeError, match="Multiple verified Camfrog processes"):
        discovery.get_verified_process()


def test_f4_during_dispatch_refuses(tmp_path, monkeypatch):
    """F4 / emergency stop during dispatch -> work refused mid-flight."""
    from camfrog_emergency_stop import EmergencyStopRegistry, EmergencyStopScope
    monkeypatch.setenv("ZCHAT_EMERGENCY_STOP_DB", str(tmp_path / "es2.sqlite3"))
    registry = EmergencyStopRegistry()
    registry._reset_for_testing()
    try:
        registry.activate(EmergencyStopScope.GLOBAL, "OPERATOR_HALT", "op-f4")
        ev = BackgroundStatusExecutor().execute_status_change("Available")
        assert ev.error_code == "EMERGENCY_STOP"
    finally:
        registry._reset_for_testing()


def test_repeated_failures_do_not_escalate_or_duplicate(cfg, monkeypatch):
    """Repeated send() failures under strict_background never silently escalate to fallback."""
    r = _strict_runner(cfg, monkeypatch)
    monkeypatch.setattr(c, "commit_strict_background_status", lambda text, dry: False)
    monkeypatch.setattr(c, "commit",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("commit must never be called without authorized fallback")))
    for _ in range(5):
        assert r.send(r.status_edit, "Available") is False


def test_unknown_target_process_fails_closed(monkeypatch):
    """Unknown/unverified target process -> ProcessLookupError, no fallback to guess."""
    from camfrog_windows_runtime import WindowsCamfrogProcessDiscovery
    discovery = WindowsCamfrogProcessDiscovery()
    monkeypatch.setattr(discovery, "discover_processes", list)
    with pytest.raises(ProcessLookupError):
        discovery.get_verified_process()


def test_com_ui_a_exceptions_do_not_crash_runner(cfg, monkeypatch):
    """COM/UIA exceptions during discovery are caught and reported, never silently fall back."""
    monkeypatch.setattr(c, "get_window", lambda cfg: (_ for _ in ()).throw(
        RuntimeError("COM error: invalid class string")))
    r = c.Runner(cfg)
    with pytest.raises(RuntimeError, match="COM error"):
        r.resolve()


def test_config_with_execution_mode_best_effort_background(cfg):
    """best_effort_background is an accepted mode (validated, not rejected)."""
    cfg["status"]["execution_mode"] = "best_effort_background"
    errs, _ = c.validate(cfg)
    assert not any("execution_mode" in e for e in errs)


def test_config_with_execution_mode_interactive(cfg):
    """interactive is an accepted mode."""
    cfg["status"]["execution_mode"] = "interactive"
    errs, _ = c.validate(cfg)
    assert not any("execution_mode" in e for e in errs)


def test_config_with_invalid_execution_mode_rejected(cfg):
    cfg["status"]["execution_mode"] = "invalid_stealing"
    errs, _ = c.validate(cfg)
    assert any("execution_mode" in e for e in errs)


def test_do_status_re_resolves_immediately_when_control_lost(cfg, monkeypatch):
    """do_status re-attempts resolve() immediately when status_edit is None (fail-closed recovery)."""
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    r = c.Runner(cfg)
    r.status_edit = None
    r.win = None
    resolve_calls = []

    def fake_resolve(self):
        resolve_calls.append(True)
        if len(resolve_calls) == 1:
            raise LookupError("still missing")
        self.status_edit = object()

    monkeypatch.setattr(c.Runner, "resolve", fake_resolve)
    r.do_status(100.0)
    assert r.status_edit is None
    assert len(resolve_calls) == 1
    r.do_status(101.0)
    assert r.status_edit is not None
    assert len(resolve_calls) == 2


def test_allow_authorized_background_fallback_requires_explicit_opt_in(cfg, monkeypatch):
    """Even with execution_mode=strict_background, fallback is off unless explicitly True."""
    cfg["status"]["execution_mode"] = "strict_background"
    r = _strict_runner(cfg, monkeypatch, fallback_authorized=False)
    monkeypatch.setattr(c, "commit_strict_background_status", lambda text, dry: False)
    monkeypatch.setattr(c, "commit",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no silent fallback")))
    assert r.send(r.status_edit, "Available") is False


def test_authorized_fallback_not_enabled_by_default_in_config(cfg):
    """config.example.json: allow_authorized_background_fallback is false by default."""
    loaded = c.load_cfg(ROOT / "config.example.json")
    assert loaded["status"].get("allow_authorized_background_fallback") is False


# ---------- PHASE 4: evidence validator security ----------

def _make_evidence_bundle(tmp_path, run_id, nickname="Seaza", command_id="CMD-1",
                          account_b_ref="acct-b", transition=True, external_effect=False):
    """Helper to build a minimal valid evidence bundle."""
    out = tmp_path / run_id
    out.mkdir()
    samples = []
    h1 = "aaaa"
    h2 = "bbbb" if transition else "aaaa"
    for i in range(1, 3):
        samples.append({
            "run_id": run_id,
            "sample_index": i,
            "observed_at": (datetime.now(UTC) + timedelta(seconds=i)).isoformat(),
            "nickname": nickname,
            "command_id": command_id,
            "account_b_reference": account_b_ref,
            "state": "WEB_PROFILE_MATCH" if transition else "UNVERIFIED",
            "first_observed_text_sha256": h1 if i == 1 else ("cccc" if transition else "aaaa"),
            "last_observed_text_sha256": h2 if i == 1 else ("dddd" if transition else "aaaa"),
        })
    (out / f"{run_id}.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in samples), encoding="utf-8")
    summary = {
        "run_id": run_id,
        "account_a_nickname": nickname,
        "command_id": command_id,
        "account_b_reference": account_b_ref,
        "stage_chain": {
            "COMMAND_ACCEPTED": "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL",
            "WORKER_EXECUTED": "EXTERNAL_NOT_OBSERVED_BY_THIS_TOOL",
            "WEB_PROFILE_OBSERVED": "WEB_PROFILE_MATCH" if transition else "UNVERIFIED",
            "EXTERNAL_EFFECT_VERIFIED": external_effect,
        },
        "sample_log": f"{run_id}.jsonl",
        "screenshot_files": [],
        "transition_detected": transition,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    entries = []
    for f in sorted(out.iterdir()):
        if f.is_file() and f.name != "manifest.json":
            data = f.read_bytes()
            entries.append({"file": f.name, "sha256": hashlib.sha256(data).hexdigest(),
                            "size_bytes": len(data)})
    (out / "manifest.json").write_text(
        json.dumps({"run_id": run_id,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "files": entries}), encoding="utf-8")
    return out


def test_cross_run_command_id_mismatch_is_rejected(tmp_path):
    """Cross-run command ID mismatch: observer command_id must match Windows request_id."""
    out = _make_evidence_bundle(tmp_path, "RUN-XCMD", command_id="CMD-WRONG")
    verdict = validate_bundle(out, expected_command_id="CMD-EXPECTED")
    assert verdict["status"] == "FAIL"
    assert any("command_id" in e for e in verdict["errors"])


def test_account_identity_mismatch_rejected(tmp_path):
    """Account A nickname mismatch: observer bundle must match authorized Account A."""
    out = _make_evidence_bundle(tmp_path, "RUN-IDENT", nickname="Impostor")
    verdict = validate_bundle(out, expected_nickname="Seaza")
    assert verdict["status"] == "FAIL"
    assert any("nickname" in e for e in verdict["errors"])


def test_fabricated_external_effect_never_auto_assigned(tmp_path):
    """EXTERNAL_EFFECT_VERIFIED must never be true from the observer tool; validator rejects it."""
    out = _make_evidence_bundle(tmp_path, "RUN-EFFECT", external_effect=True)
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("EXTERNAL_EFFECT_VERIFIED must be false" in e for e in verdict["errors"])


def test_manifest_path_traversal_rejected(tmp_path):
    """Manifest entries with path traversal (../) are rejected."""
    out = _make_evidence_bundle(tmp_path, "RUN-PT")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({"file": "../etc/passwd", "sha256": "deadbeef", "size_bytes": 0})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"


def test_duplicate_manifest_entries_rejected(tmp_path):
    """Duplicate manifest entries are rejected."""
    out = _make_evidence_bundle(tmp_path, "RUN-DUP")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(dict(manifest["files"][0]))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"


def test_missing_file_not_in_manifest_rejected(tmp_path):
    """File present on disk but missing from manifest is rejected."""
    out = _make_evidence_bundle(tmp_path, "RUN-MISS")
    (out / "extra.txt").write_text("unlisted file")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("missing from manifest" in e for e in verdict["errors"])


def test_invalid_sha256_format_rejected(tmp_path):
    """Invalid SHA-256 format in manifest is rejected (hash mismatch)."""
    out = _make_evidence_bundle(tmp_path, "RUN-SHA")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "not-a-valid-sha256"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("hash mismatch" in e for e in verdict["errors"])


def test_malformed_jsonl_rejected(tmp_path):
    """Malformed JSONL (unparseable lines) is rejected."""
    out = _make_evidence_bundle(tmp_path, "RUN-JSONL")
    jsonl_path = out / "RUN-JSONL.jsonl"
    jsonl_path.write_text('{"run_id": "RUN-JSONL"}\n{not valid json}\n', encoding="utf-8")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = jsonl_path.read_bytes()
    for entry in manifest["files"]:
        if entry["file"] == "RUN-JSONL.jsonl":
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size_bytes"] = len(data)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"
    assert any("invalid JSON" in e for e in verdict["errors"])


def test_interrupted_evidence_collection_missing_sample_log(tmp_path):
    """Interrupted collection: missing sample log file is rejected."""
    out = _make_evidence_bundle(tmp_path, "RUN-INT")
    (out / "RUN-INT.jsonl").unlink()
    verdict = validate_bundle(out)
    assert verdict["status"] == "FAIL"


def test_exit_codes_structured_json(tmp_path):
    """Validator exit codes: 0=PASS, structured JSON output."""
    from tools.evidence_validator import main
    out = _make_evidence_bundle(tmp_path, "RUN-EXIT")
    rc = main([str(out)])
    assert rc == 0


# ---------- PHASE 2: control discovery ----------

def test_resolve_status_controls_finds_combo_and_edit(monkeypatch):
    """CamfrogWindowResolver discovers CComboBoxTS (1436) + Edit (1001) pair under main window."""
    from camfrog_status_engine import CamfrogWindowResolver
    proc = ProcessIdentity(pid=5555, create_time=100.0,
                           exe_path="C:\\Camfrog\\Camfrog Video Chat.exe",
                           publisher="Camfrog LLC", signature_valid=True, version="8.5.0")
    main_win = ClassifiedWindow(hwnd=9999, role=WindowRole.MAIN_DIRECTORY,
                                title="Seaza (Online)", pid=5555, is_visible=True,
                                signatures=["CTreeViewTS+CComboBoxTS"])
    control_ids = {10: 1436, 11: 1001}
    classes = {10: "CComboBoxTS", 11: "Edit"}
    parent = 9999
    win32gui = SimpleNamespace(
        IsWindow=lambda hwnd: hwnd in control_ids,
        IsChild=lambda p, hwnd: p == parent and hwnd in control_ids,
        GetDlgCtrlID=lambda hwnd: control_ids[hwnd],
        GetClassName=lambda hwnd: classes[hwnd],
        GetParent=lambda hwnd: parent,
        EnumChildWindows=lambda hwnd, cb, _: [cb(ch, None) for ch in sorted(control_ids)],
    )
    win32process = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, 5555))
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(cse, "revalidate_window_identity", lambda win, ident: True)
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setitem(sys.modules, "win32process", win32process)
    resolver = CamfrogWindowResolver()
    resolver.discovery.get_verified_process = lambda pid=None: proc
    resolver.classifier.classify_process_windows = lambda identity, visible_only=False: [main_win]
    p, w, a = resolver.resolve_status_controls(pid=5555)
    assert p.pid == 5555
    assert w.role == WindowRole.MAIN_DIRECTORY
    assert a.combo_hwnd == 10
    assert a.edit_hwnd == 11


def test_resolve_status_controls_rejects_missing_controls(monkeypatch):
    """No CComboBoxTS/Edit pair -> LookupError, never guesses a wrong control."""
    from camfrog_status_engine import CamfrogWindowResolver
    proc = ProcessIdentity(pid=5555, create_time=100.0,
                           exe_path="C:\\Camfrog\\Camfrog Video Chat.exe",
                           publisher="Camfrog LLC", signature_valid=True, version="8.5.0")
    main_win = ClassifiedWindow(hwnd=9999, role=WindowRole.MAIN_DIRECTORY,
                                title="Seaza (Online)", pid=5555, is_visible=True,
                                signatures=["CTreeViewTS+CComboBoxTS"])
    win32gui = SimpleNamespace(
        IsWindow=lambda hwnd: False,
        IsChild=lambda p, hwnd: False,
        GetDlgCtrlID=lambda hwnd: 0,
        GetClassName=lambda hwnd: "",
        GetParent=lambda hwnd: 0,
    )
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(cse, "revalidate_window_identity", lambda win, ident: True)
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    resolver = CamfrogWindowResolver()
    resolver.discovery.get_verified_process = lambda pid=None: proc
    resolver.classifier.classify_process_windows = lambda identity, visible_only=False: [main_win]
    with pytest.raises(LookupError, match="Status controls not found"):
        resolver.resolve_status_controls(pid=5555)


def test_control_ids_take_precedence_over_class_names(monkeypatch):
    """Control IDs (1436/1001) take precedence over class names to avoid binding wrong edit."""
    from camfrog_status_engine import CamfrogWindowResolver
    proc = ProcessIdentity(pid=5555, create_time=100.0,
                           exe_path="C:\\Camfrog\\Camfrog Video Chat.exe",
                           publisher="Camfrog LLC", signature_valid=True, version="8.5.0")
    main_win = ClassifiedWindow(hwnd=9999, role=WindowRole.MAIN_DIRECTORY,
                                title="Seaza (Online)", pid=5555, is_visible=True,
                                signatures=["CTreeViewTS+CComboBoxTS"])
    control_ids = {10: 1436, 11: 1001, 12: 9999}
    classes = {10: "CComboBoxTS", 11: "Edit", 12: "Edit"}
    parent = 9999
    win32gui = SimpleNamespace(
        IsWindow=lambda hwnd: hwnd in control_ids,
        IsChild=lambda p, hwnd: p == parent and hwnd in control_ids,
        GetDlgCtrlID=lambda hwnd: control_ids[hwnd],
        GetClassName=lambda hwnd: classes[hwnd],
        GetParent=lambda hwnd: 10 if hwnd == 11 else parent,
        EnumChildWindows=lambda hwnd, cb, _: [cb(ch, None) for ch in sorted(control_ids)],
    )
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setattr(cse, "revalidate_window_identity", lambda win, ident: True)
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    resolver = CamfrogWindowResolver()
    resolver.discovery.get_verified_process = lambda pid=None: proc
    resolver.classifier.classify_process_windows = lambda identity, visible_only=False: [main_win]
    _p, _w, a = resolver.resolve_status_controls(pid=5555)
    assert a.edit_hwnd == 11  # ID 1001 wins over class-only match


def test_hidden_and_stale_controls_not_targeted(monkeypatch):
    """Hidden or stale controls fail controls_are_current check."""
    adapter = StatusControlAdapter(combo_hwnd=10, edit_hwnd=11, expected_pid=55, parent_hwnd=99)
    control_ids = {10: 1436, 11: 1001}
    classes = {10: "CComboBoxTS", 11: "Edit"}
    win32gui = SimpleNamespace(
        IsWindow=lambda hwnd: hwnd == 10,  # edit (11) no longer a window -> stale
        IsChild=lambda p, hwnd: False,
        GetDlgCtrlID=lambda hwnd: control_ids[hwnd],
        GetClassName=lambda hwnd: classes[hwnd],
    )
    win32process = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, 55))
    _psutil = SimpleNamespace(
        pid_exists=lambda pid: True,
        Process=lambda pid: SimpleNamespace(
            is_running=lambda: True,
            create_time=lambda: 100.0,
            exe=lambda: "C:\\\\Camfrog\\\\Camfrog Video Chat.exe",
        ),
        NoSuchProcess=Exception,
        AccessDenied=Exception,
        ZombieProcess=Exception,
    )
    monkeypatch.setitem(sys.modules, "psutil", _psutil)
    monkeypatch.setattr(cse.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setitem(sys.modules, "win32process", win32process)
    assert adapter.controls_are_current() is False


# End of PHASE 1+2 tests


def test_validate_marquee_and_history_rules(cfg):
    cfg["status"]["marquee"].update(enabled=True, scroll=True, step_seconds=0.1)
    cfg["status"]["language_cycle"] = ["fr"]
    cfg["status"]["history"]["mode"] = "bogus"
    errs, _ = c.validate(cfg)
    assert len(errs) == 3, errs
    cfg2 = c.load_cfg(ROOT / "config.example.json")
    cfg2["status"]["enabled"] = True
    cfg2["status"]["history"]["use_as_source"] = True
    cfg2["status"]["schedules"] = [{"start": "00:00", "end": "01:00", "messages": ["x"]}]
    _, warns = c.validate(cfg2)
    assert any("schedules" in w for w in warns)


# ---------- CLI ----------
def test_marquee_and_history_cli(tmp_path, capsys):
    cfgp = str(ROOT / "config.example.json")
    assert c.main(["marquee", THAI, "--width", "16", "--config", cfgp]) == 0
    out = capsys.readouterr().out
    assert "frame(s)" in out and "  1 |" in out
    c.main(["--lang", "th", "marquee", "สั้น", "--config", cfgp])
    assert "ไม่เลื่อน" in capsys.readouterr().out
    assert c.main(["history-add", "สวัสดีครับ", "--config", cfgp]) == 0
    assert c.main(["history-add", "สวัสดีครับ", "--config", cfgp]) == 1
    f = tmp_path / "in.txt"
    f.write_text("hello one\n\nสวัสดี สอง\nhello one\n", encoding="utf-8")
    assert c.main(["history-import", str(f), "--config", cfgp]) == 0
    assert "imported 2 new" in capsys.readouterr().out
    assert c.main(["history", "--config", cfgp]) == 0
    out = capsys.readouterr().out
    assert "[th]" in out and "[en]" in out


# ---------- window selection ----------
def test_get_window_skips_own_tk_gui(monkeypatch):
    """wins[0] may be our own GUI when Enable is clicked (it is topmost)."""
    import sys as _sys
    from types import SimpleNamespace

    own = SimpleNamespace(class_name=lambda: "TkTopLevel", handle=1, process_id=lambda: 99)
    real = SimpleNamespace(class_name=lambda: "#32770", handle=2, process_id=lambda: 42)
    fake_desktop = SimpleNamespace(windows=lambda **kw: [own, real])
    fake_pywinauto = SimpleNamespace(Desktop=lambda backend=None: fake_desktop)
    monkeypatch.setitem(_sys.modules, "pywinauto", fake_pywinauto)
    assert c.is_own_gui_window(own) is True
    assert c.is_own_gui_window(real) is False
    assert c.get_window({"window_title_regex": ".*Camfrog.*"}) is real

    fake_empty = SimpleNamespace(windows=lambda **kw: [own])
    monkeypatch.setitem(_sys.modules, "pywinauto",
                        SimpleNamespace(Desktop=lambda backend=None: fake_empty))
    with pytest.raises(RuntimeError):
        c.get_window({"window_title_regex": ".*Camfrog.*"})




def test_window_selection_rejects_multiple_processes():
    from types import SimpleNamespace

    windows = [
        SimpleNamespace(process_id=lambda: 101),
        SimpleNamespace(process_id=lambda: 202),
    ]
    with pytest.raises(RuntimeError, match="processes match"):
        c._require_single_window_process(windows)


def test_window_selection_allows_multiple_windows_from_one_process():
    from types import SimpleNamespace

    windows = [
        SimpleNamespace(process_id=lambda: 101),
        SimpleNamespace(process_id=lambda: 101),
    ]
    c._require_single_window_process(windows)


def test_window_selection_rejects_unverifiable_process_in_multi_window_set():
    from types import SimpleNamespace

    windows = [
        SimpleNamespace(process_id=lambda: 101),
        SimpleNamespace(),
    ]
    with pytest.raises(RuntimeError, match="unverifiable"):
        c._require_single_window_process(windows)


def test_window_selection_rejects_single_unverifiable_window():
    """P2: even one candidate must resolve to a positive, verified PID."""
    from types import SimpleNamespace

    windows = [SimpleNamespace()]  # process_id() missing -> unverifiable
    with pytest.raises(RuntimeError, match="unverifiable"):
        c._require_single_window_process(windows)


def test_window_selection_allows_single_verified_window():
    from types import SimpleNamespace

    windows = [SimpleNamespace(process_id=lambda: 101)]
    c._require_single_window_process(windows)

def test_is_own_gui_window_matches_split_status_app_titles_when_class_lookup_fails():
    from types import SimpleNamespace

    for title in ("Camfrog Random Status", "Camfrog Marquee Status"):
        own = SimpleNamespace(
            class_name=lambda: (_ for _ in ()).throw(OSError()),
            window_text=lambda title=title: title,
        )
        assert c.is_own_gui_window(own) is True


def test_runner_retry_attrs_initialized(cfg):
    """A failed initial resolve must leave a retryable runner, not AttributeError."""
    r = c.Runner(cfg)
    assert r.status_edit is None
    assert r.apply_btn is None
    assert r.win is None
    assert r.history is None
    assert r.input is None
    assert r.chat_win is None
    assert r._next_resolve_retry == 0.0


def test_runner_failed_resolve_stays_retryable(cfg, monkeypatch):
    """Mirror the field log: window missing -> resolve raises -> loop lines must not crash."""
    import time

    monkeypatch.setattr(c, "get_window", lambda _cfg: (_ for _ in ()).throw(
        RuntimeError("Camfrog window not found (check window_title_regex)")))
    r = c.Runner(cfg)
    with pytest.raises(RuntimeError):
        r.resolve()
    now = time.monotonic()
    assert r.status_edit is None and now >= r._next_resolve_retry  # run() line: no AttributeError
    assert r.history is None  # autoreply branch: no AttributeError


def test_marquee_scroll_off_applies_whole_line_per_tick(cfg):
    r = c.Runner(cfg)
    long_text = " status message that is definitely longer than twenty-eight clusters wide "
    r.cfg["status"]["marquee"]["enabled"] = True
    r.cfg["status"]["marquee"]["scroll"] = False
    assert r.build_frames(long_text) == [c.clip(long_text, r.cfg["status"]["max_length"])]


def test_marquee_scroll_on_splits_frames(cfg):
    r = c.Runner(cfg)
    mq = r.cfg["status"]["marquee"]
    mq.update({"enabled": True, "scroll": True, "width": 10, "stride": 2,
               "separator": " ", "cycles": 1, "max_frames": 80, "infinite_loop": False})
    frames = r.build_frames(" status message that is definitely longer than ten clusters ")
    assert len(frames) > 1


def test_marquee_defaults_to_per_line(cfg):
    assert c.DEFAULTS["status"]["marquee"]["scroll"] is False


def test_marquee_shape_skipped_when_scroll_off(cfg):
    cfg["status"]["enabled"] = True
    cfg["status"]["messages"] = ["hello world, this is long enough"]
    cfg["status"]["marquee"]["enabled"] = True
    cfg["status"]["marquee"]["scroll"] = False
    cfg["status"]["marquee"]["width"] = 3  # garbage, but unused without scrolling
    errs, _ = c.validate(cfg)
    assert not [e for e in errs if "marquee" in e]


def test_marquee_shape_checked_when_scroll_on(cfg):
    cfg["status"]["enabled"] = True
    cfg["status"]["messages"] = ["hello world, this is long enough"]
    cfg["status"]["marquee"]["enabled"] = True
    cfg["status"]["marquee"]["scroll"] = True
    cfg["status"]["marquee"]["width"] = 3
    errs, _ = c.validate(cfg)
    assert any("marquee" in e for e in errs)


def test_old_config_without_scroll_defaults_to_per_line(cfg, tmp_path):
    import json

    slim = {"status": {"messages": ["hello world, this is long enough"]}}
    path = tmp_path / "old.json"
    path.write_text(json.dumps(slim), encoding="utf-8")
    loaded = c.load_cfg(path)
    assert loaded["status"]["marquee"]["scroll"] is False
    assert c.Runner(loaded).build_frames("a long status line " * 10) == [
        c.clip("a long status line " * 10, loaded["status"]["max_length"])]
