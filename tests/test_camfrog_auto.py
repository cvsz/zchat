"""Offline tests: no Camfrog / pywinauto needed (UI is never touched)."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camfrog_auto as c  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    x = c.load_cfg(ROOT / "config.example.json")
    x["dry_run"] = (
        True  # tests must not depend on the user's live settings in the shipped config
    )
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
    monkeypatch.setattr(c, "BASE", tmp_path)  # never write history/stats into the repo
    c.LANG = "en"
    yield
    c.LANG = "en"


def test_example_config_valid(cfg):
    errs, _ = c.validate(cfg)
    assert errs == []


@pytest.mark.parametrize(
    "argv",
    [
        ["check", "--config", str(ROOT / "config.example.json")],
        ["--config", str(ROOT / "config.example.json"), "check"],
        ["--lang", "th", "check", "--config", str(ROOT / "config.example.json")],
    ],
)
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
    assert c.in_window("22:00", "02:00", t(23, 0)) and c.in_window(
        "22:00", "02:00", t(1, 0)
    )
    assert not c.in_window("22:00", "02:00", t(12, 0))
    assert c.in_window("09:00", "17:00", t(9, 0)) and not c.in_window(
        "09:00", "17:00", t(18, 0)
    )


def test_rules_mention_lang_and_skips(cfg):
    ar = cfg["autoreply"]
    rules, skips = c.compile_rules(ar), c.compile_skips(ar)
    assert c.match_rule(rules, "hello there", "en", "me")["idx"] == 2  # greeting rule
    assert (
        c.match_rule(rules, "hey @me you there?", "en", "me")["idx"] == 1
    )  # mention rule first
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
    cfg["status"]["schedules"] = [
        {"start": "00:00", "end": "23:59", "messages": ["sched"]}
    ]
    assert r.status_pool() == ["sched"]
    cfg["status"]["schedules"] = []
    assert r.status_pool() == cfg["status"]["messages"]


def test_pid_helpers():
    assert (
        c.pid_alive(os.getpid()) and not c.pid_alive(None) and not c.pid_alive(99999999)
    )
    if os.name == "nt":
        assert c.pid_image(os.getpid())
        created = c.pid_creation_time(os.getpid())
        assert created is not None
        assert c.pid_is_ours(os.getpid(), expected_creation_time=created)


def test_single_instance_and_stop(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    pf = tmp_path / cfg["safety"]["pid_file"]
    pf.write_text(str(os.getppid()))  # another live process
    monkeypatch.setattr(c, "acquire_run_mutex", lambda: False)
    assert c.run_guarded(cfg) == 3
    pf.write_text("99999999")  # stale pid
    assert c.cmd_stop(cfg) == 0 and not pf.exists()
    assert c.cmd_state(cfg) == 1


def test_im_failed_send_remains_pending_but_unconfirmed_send_is_consumed(
    cfg, monkeypatch
):
    cfg["dry_run"] = False
    cfg["autoreply"]["own_nickname"] = "Seaza"
    cfg["autoreply_im"].update(
        enabled=True,
        dry_run=False,
        only_nicknames=["zdevz"],
        prefix="[auto] ",
        delay_range_seconds=[0, 0],
        rules=[
            {
                "pattern": "hello",
                "reply": "Hi {sender}",
                "lang": "en",
                "cooldown_seconds": 0,
            }
        ],
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
    assert state["prev"] == [
        "zdevz: hello"
    ]  # avoid a duplicate if delivery was ambiguous


def test_confirmed_im_echo_is_revealed_in_local_history(cfg, monkeypatch):
    revealed = []
    history = object()
    monkeypatch.setattr(c, "read_chat", lambda *_args: "Seaza: Hi zdevz")
    monkeypatch.setattr(
        c,
        "reveal_chat_message",
        lambda history, nickname, message: (
            revealed.append((history, nickname, message)) or True
        ),
    )

    assert c.confirm_im_reply_echo(history, [], "Seaza", "Hi zdevz", timeout=0)
    assert revealed == [(history, "Seaza", "Hi zdevz")]


def test_stop_confirms_process_exit_after_taskkill(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    pid_file = tmp_path / cfg["safety"]["pid_file"]
    stop_file = tmp_path / cfg["safety"]["stop_file"]
    pid_file.write_text(
        json.dumps({"pid": 4242, "creation_time": 123}), encoding="utf-8"
    )
    alive = {"value": True}
    monkeypatch.setattr(c, "running_pid", lambda _cfg: 4242)
    monkeypatch.setattr(c, "pid_alive", lambda _pid: alive["value"])
    monkeypatch.setattr(
        c,
        "pid_is_ours",
        lambda _pid, image=None, expected_creation_time=None: (
            expected_creation_time == 123
        ),
    )
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
    pid_file.write_text(
        json.dumps({"pid": 4242, "creation_time": 123}), encoding="utf-8"
    )
    monkeypatch.setattr(c, "running_pid", lambda _cfg: 4242)
    monkeypatch.setattr(c, "pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        c,
        "pid_is_ours",
        lambda _pid, image=None, expected_creation_time=None: (
            expected_creation_time == 123
        ),
    )
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
    assert c.cmd_state(cfg) == 1  # prints stats, process not running


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
    assert c.main(["init", "--config", str(p)]) == 1  # refuses to overwrite
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
    assert (
        c.main(
            [
                "test-rules",
                "--file",
                str(f),
                "--config",
                str(ROOT / "config.example.json"),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "alice" in out and "ignored line" in out


# ---------- marquee ----------
THAI = "สวัสดีครับ ยินดีต้อนรับทุกคนเข้าห้องนะครับ ว่างคุยได้ ทักได้เลย เก่งมากๆ"


def test_clusters_keep_thai_marks_and_leading_vowels_together():
    cl = c.clusters("ก้าวเกี่ยว")
    assert cl == ["ก้", "า", "ว", "เกี่", "ย", "ว"]  # marks and leading เ stay attached
    for x in cl:  # no cluster starts with a combining mark
        assert c.unicodedata.category(x[0]) not in ("Mn", "Mc", "Me")
    assert c.clusters("ab") == ["a", "b"]
    assert c.clusters("เ") == ["เ"]  # dangling leading vowel kept


@pytest.mark.parametrize("width,stride", [(8, 1), (12, 3), (28, 2), (80, 5)])
def test_marquee_frames_never_blank_or_broken(width, stride):
    for text in (
        THAI,
        "Welcome everyone to the room, free to chat so say hi anytime",
        "ก" * 100,
    ):
        frames = c.marquee_frames(text, width, stride, max_frames=300)
        assert frames and all(f.strip() for f in frames)
        for f in frames:
            assert len(c.clusters(f)) <= width
            assert c.unicodedata.category(f[0]) not in (
                "Mn",
                "Mc",
                "Me",
            )  # no orphan mark


def test_marquee_short_text_is_static_and_blank_is_empty():
    assert c.marquee_frames("hello", 28) == ["hello"]
    assert c.marquee_frames("   ", 28) == []


def test_marquee_frame_cap_and_cycle_wraps():
    frames = c.marquee_frames("x" * 50, 10, 1, "  |  ", cycles=5, max_frames=20)
    assert len(frames) <= 20


def test_marquee_infinite_loop_ignores_cycles():
    # infinite_loop=True ignores cycles, uses max_frames only
    frames = c.marquee_frames(
        "abcdefghijklmnopqrstuvwxyz",
        10,
        1,
        "  |  ",
        cycles=100,
        max_frames=15,
        infinite_loop=True,
    )
    assert len(frames) == 15
    # Without infinite_loop, cycles would be used
    frames2 = c.marquee_frames(
        "abcdefghijklmnopqrstuvwxyz",
        10,
        1,
        "  |  ",
        cycles=2,
        max_frames=15,
        infinite_loop=False,
    )
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
    assert h.pick("rotate", "th") == "สวัสดี 2"  # round-robin
    assert h.pick("rotate", "en", exclude="hello 1") == "hello 2"
    assert h.pick("most_used", "th") == "สวัสดี 1"
    assert h.pick("random", "en") in ("hello 1", "hello 2")
    assert h.pick("rotate", "th", exclude="สวัสดี 1") == "สวัสดี 2"
    only_en = c.StatusHistory(tmp_path / "e.json")
    only_en.add("only english")
    assert only_en.pick("rotate", "th") == "only english"  # falls back to any language
    for i in range(10):
        h.add(f"extra {i}")
    assert len(h.items) == 5
    h2 = c.StatusHistory(tmp_path / "h.json")
    h2.load()
    assert [i["count"] for i in h2.items if i["text"] == "สวัสดี 1"] == [1]  # persisted
    assert c.StatusHistory(tmp_path / "missing.json").items == []


def test_history_persistence_roundtrip(tmp_path):
    h = c.StatusHistory(tmp_path / "h.json")
    h.record("สวัสดี")
    h2 = c.StatusHistory(tmp_path / "h.json")
    h2.load()
    assert (
        h2.items[0]["text"] == "สวัสดี"
        and h2.items[0]["count"] == 1
        and h2.items[0]["lang"] == "th"
    )


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
    cfg["status"]["messages"] = [
        {"th": "ไทย 1", "en": "EN 1"},
        {"th": "ไทย 2", "en": "EN 2"},
    ]
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
    cfg["status"]["marquee"].update(
        enabled=True, scroll=True, width=16, stride=4, step_seconds=5, max_frames=12
    )
    r = c.Runner(cfg)
    sent = []
    r.status_edit, r.apply_btn = object(), None
    monkeypatch.setattr(
        c.Runner, "send", lambda self, ctrl, text, btn=None: sent.append(text) or True
    )
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
    r.do_status(5001.0)  # < step_seconds later: no second frame
    assert len(sent) == 1
    # history recorded the raw template
    assert r.hist.find(THAI) is not None


def test_do_status_infinite_loop_repeats_frames_until_status_interval(cfg, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(c.time, "monotonic", lambda: clock["now"])
    cfg["status"]["history"]["enabled"] = False
    cfg["status"]["history"]["use_as_source"] = False
    cfg["status"]["language_cycle"] = []
    cfg["status"]["messages"] = [
        "AAA status that is long enough",
        "BBB next status long enough",
    ]
    cfg["status"]["interval_seconds"] = 3.0
    cfg["status"]["marquee"].update(
        enabled=True,
        scroll=True,
        width=10,
        stride=2,
        step_seconds=0.5,
        cycles=1,
        max_frames=5,
        infinite_loop=True,
    )
    r = c.Runner(cfg)
    r.status_edit, r.apply_btn = object(), None
    sent = []
    monkeypatch.setattr(
        c.Runner, "send", lambda self, ctrl, text, btn=None: sent.append(text) or True
    )
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
    cfg["status"]["marquee"].update(
        enabled=True, scroll=True, width=10, stride=2, step_seconds=0.5, max_frames=20
    )
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
    monkeypatch.setattr(
        c.Runner, "send", lambda self, ctrl, text, btn=None: sent.append(text) or True
    )
    r.last_status = -1e9
    r.do_status(10.0)
    assert sent == [] and r.marq is None


def test_validate_marquee_and_history_rules(cfg):
    cfg["status"]["marquee"].update(enabled=True, scroll=True, step_seconds=0.1)
    cfg["status"]["language_cycle"] = ["fr"]
    cfg["status"]["history"]["mode"] = "bogus"
    errs, _ = c.validate(cfg)
    assert len(errs) == 3, errs
    cfg2 = c.load_cfg(ROOT / "config.example.json")
    cfg2["status"]["enabled"] = True
    cfg2["status"]["history"]["use_as_source"] = True
    cfg2["status"]["schedules"] = [
        {"start": "00:00", "end": "01:00", "messages": ["x"]}
    ]
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

    own = SimpleNamespace(
        class_name=lambda: "TkTopLevel", handle=1, process_id=lambda: 99
    )
    real = SimpleNamespace(class_name=lambda: "#32770", handle=2, process_id=lambda: 42)
    fake_desktop = SimpleNamespace(windows=lambda **kw: [own, real])
    fake_pywinauto = SimpleNamespace(Desktop=lambda backend=None: fake_desktop)
    monkeypatch.setitem(_sys.modules, "pywinauto", fake_pywinauto)
    assert c.is_own_gui_window(own) is True
    assert c.is_own_gui_window(real) is False
    assert c.get_window({"window_title_regex": ".*Camfrog.*"}) is real

    fake_empty = SimpleNamespace(windows=lambda **kw: [own])
    monkeypatch.setitem(
        _sys.modules,
        "pywinauto",
        SimpleNamespace(Desktop=lambda backend=None: fake_empty),
    )
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

    monkeypatch.setattr(
        c,
        "get_window",
        lambda _cfg: (_ for _ in ()).throw(
            RuntimeError("Camfrog window not found (check window_title_regex)")
        ),
    )
    r = c.Runner(cfg)
    with pytest.raises(RuntimeError):
        r.resolve()
    now = time.monotonic()
    assert (
        r.status_edit is None and now >= r._next_resolve_retry
    )  # run() line: no AttributeError
    assert r.history is None  # autoreply branch: no AttributeError


def test_marquee_scroll_off_applies_whole_line_per_tick(cfg):
    r = c.Runner(cfg)
    long_text = (
        " status message that is definitely longer than twenty-eight clusters wide "
    )
    r.cfg["status"]["marquee"]["enabled"] = True
    r.cfg["status"]["marquee"]["scroll"] = False
    assert r.build_frames(long_text) == [
        c.clip(long_text, r.cfg["status"]["max_length"])
    ]


def test_marquee_scroll_on_splits_frames(cfg):
    r = c.Runner(cfg)
    mq = r.cfg["status"]["marquee"]
    mq.update(
        {
            "enabled": True,
            "scroll": True,
            "width": 10,
            "stride": 2,
            "separator": " ",
            "cycles": 1,
            "max_frames": 80,
            "infinite_loop": False,
        }
    )
    frames = r.build_frames(
        " status message that is definitely longer than ten clusters "
    )
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
        c.clip("a long status line " * 10, loaded["status"]["max_length"])
    ]
