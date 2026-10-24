"""Private-message (IM) auto-reply: validation, offline decisions, per-window runner logic (offline)."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camfrog_auto as c  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    x = c.load_cfg(ROOT / "config.example.json")
    x["dry_run"] = True
    x["autoreply"]["own_nickname"] = "Me"
    return x


@pytest.fixture
def im_cfg(cfg):
    cfg["autoreply"]["enabled"] = True
    cfg["autoreply_im"].update(enabled=True, only_nicknames=["Friend"], dry_run=False)
    cfg["autoreply_im"]["rules"] = [{"pattern": ".*", "reply": "away, back later"}]
    cfg["dry_run"] = False
    return cfg


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    monkeypatch.setattr(c.time, "sleep", lambda *_a: None)
    c.LANG = "en"


def errs_of(cfg):
    return c.validate(cfg)[0]


# ---------- validation ----------
def test_shipped_config_has_im_disabled_and_valid(cfg):
    assert cfg["autoreply_im"]["enabled"] is False
    assert cfg["autoreply_im"]["dry_run"] is True
    assert errs_of(cfg) == []


def test_enabled_needs_allowlist(im_cfg):
    im_cfg["autoreply_im"]["only_nicknames"] = []
    assert c.t("e_im_only") in errs_of(im_cfg)
    im_cfg["autoreply_im"]["only_nicknames"] = ["  "]
    assert c.t("e_im_only") in errs_of(im_cfg)


def test_enabled_needs_own_nickname_even_in_dry_run(im_cfg):
    im_cfg["dry_run"] = True
    im_cfg["autoreply"]["own_nickname"] = ""
    assert c.t("e_im_nick") in errs_of(im_cfg)


def test_room_log_kind_must_stay_room(im_cfg):
    for kind in ("any", "im"):
        im_cfg["autoreply"]["log_kind"] = kind
        assert c.t("e_im_kind") in errs_of(im_cfg)
    im_cfg["autoreply"]["log_kind"] = "room"
    assert c.t("e_im_kind") not in errs_of(im_cfg)


def test_room_log_kind_is_not_checked_when_im_disabled(cfg):
    cfg["autoreply"]["log_kind"] = "any"
    assert c.t("e_im_kind") not in errs_of(cfg)


@pytest.mark.parametrize("key,bad", [
    ("per_sender_cooldown_seconds", 10), ("max_per_sender_per_day", 0), ("max_per_hour", 0),
    ("max_per_hour", 1000), ("max_reply_length", 0), ("max_windows", 0), ("global_min_gap_seconds", -1),
])
def test_ranges(im_cfg, key, bad):
    im_cfg["autoreply_im"][key] = bad
    assert any(f"autoreply_im.{key}" in e for e in errs_of(im_cfg))


def test_prefix_cannot_be_a_command(im_cfg):
    im_cfg["autoreply_im"]["prefix"] = "/kick "
    assert c.t("e_im_prefix") in errs_of(im_cfg)


def test_empty_prefix_only_warns(im_cfg):
    im_cfg["autoreply_im"]["prefix"] = ""
    errs, warns = c.validate(im_cfg)
    assert errs == [] and c.t("w_im_noprefix") in warns


def test_bad_rule_and_skip_regex(im_cfg):
    im_cfg["autoreply_im"]["rules"] = [{"pattern": "(", "reply": "x"}, {"pattern": "a", "reply": ""}]
    im_cfg["autoreply_im"]["skip_patterns"] = ["["]
    errs = errs_of(im_cfg)
    assert any(e.startswith("autoreply_im.rules[0] invalid") for e in errs)
    assert any(e.startswith("autoreply_im.skip_patterns[0] invalid") for e in errs)
    assert c.t("e_reply", i=1) in errs


def test_delay_range(im_cfg):
    im_cfg["autoreply_im"]["delay_range_seconds"] = [5, 1]
    assert c.t("e_im_delay") in errs_of(im_cfg)


def test_im_selectors_are_checked_even_if_room_autoreply_off(im_cfg):
    im_cfg["autoreply"]["enabled"] = False
    im_cfg["autoreply"]["history"] = {}
    assert any("autoreply.history" in e for e in errs_of(im_cfg))


def test_no_rules_warns(im_cfg):
    im_cfg["autoreply_im"]["rules"] = []
    assert c.t("w_im_norules") in c.validate(im_cfg)[1]


# ---------- offline decision ----------
def test_unlisted_sender_never_answered(im_cfg):
    assert c.evaluate_im(im_cfg, "Stranger", "hi")["skip"] == "sender"


def test_empty_allowlist_answers_nobody(im_cfg):
    im_cfg["autoreply_im"]["only_nicknames"] = []
    assert c.evaluate_im(im_cfg, "Friend", "hi")["skip"] == "sender"


def test_allowlist_is_case_insensitive_and_reply_gets_prefix(im_cfg):
    r = c.evaluate_im(im_cfg, "friend", "hello")
    assert r["skip"] is None and r["reply"] == "[auto] away, back later"


def test_room_rules_do_not_leak_into_im(im_cfg):
    im_cfg["autoreply"]["rules"] = [{"pattern": "price", "reply": "ROOM RULE"}]
    im_cfg["autoreply_im"]["rules"] = [{"pattern": "zzz", "reply": "im only"}]
    assert c.evaluate_im(im_cfg, "Friend", "price?")["rule"] is None
    assert c.evaluate_im(im_cfg, "Friend", "zzz")["reply"] == "[auto] im only"


def test_other_bots_prefix_is_not_answered(im_cfg):
    assert c.evaluate_im(im_cfg, "Friend", "[auto] I'm away")["skip"] == "bot"


def test_links_and_skip_patterns(im_cfg):
    assert c.evaluate_im(im_cfg, "Friend", "see http://x.y")["skip"] == "link"
    im_cfg["autoreply_im"]["skip_patterns"] = ["secret"]
    assert c.evaluate_im(im_cfg, "Friend", "a secret")["skip"] == "pattern"


def test_reply_clipped_but_prefix_kept(im_cfg):
    im_cfg["autoreply_im"]["max_reply_length"] = 20
    im_cfg["autoreply_im"]["rules"] = [{"pattern": ".*", "reply": "x" * 100}]
    r = c.evaluate_im(im_cfg, "Friend", "hi")["reply"]
    assert r.startswith("[auto] ") and len(r) <= 20


def test_slash_template_is_blocked(im_cfg):
    im_cfg["autoreply_im"]["rules"] = [{"pattern": ".*", "reply": "/ignore {sender}"}]
    assert c.evaluate_im(im_cfg, "Friend", "hi")["reply"] is None


# ---------- runner (fakes, no Windows) ----------
class FakeWin:
    def __init__(self, h, title="Friend - Private chat"):
        self.handle, self.title = h, title

    def window_text(self):
        return self.title


class Harness:
    """Runner with a scripted IM log per window and a recording commit()."""
    def __init__(self, cfg, monkeypatch):
        self.logs, self.sent = {}, []
        self.cfg = cfg
        self.r = c.Runner(cfg)
        monkeypatch.setattr(c, "read_chat", lambda ctrl, tail=150: "\n".join(self.logs[ctrl]))
        monkeypatch.setattr(c, "commit", self.commit)
        monkeypatch.setattr(c, "reveal_chat_message", lambda *_args: False)

    def commit(self, win, ctrl, text, dry, *args, **kwargs):
        self.sent.append((win.handle, text, dry))
        if not dry:
            own = self.cfg["autoreply"]["own_nickname"]
            history = self.r.im_wins[win.handle]["hist"]
            self.logs[history].append(f"{own}: {text}")
        return True

    def open(self, h, lines, first=False):
        w = FakeWin(h)
        self.logs[f"hist{h}"] = list(lines)
        self.r.im_wins[h] = {"win": w, "hist": f"hist{h}", "inp": f"inp{h}", "prev": list(lines)}
        return w


@pytest.fixture
def hz(im_cfg, monkeypatch):
    return Harness(im_cfg, monkeypatch)


def test_answers_new_friend_line_once(hz):
    hz.open(1, ["Friend: old", "Me: older"])
    hz.r.do_im(True)
    assert hz.sent == []                       # backlog is never answered
    hz.logs["hist1"].append("Friend: hello")
    hz.r.do_im(True)
    assert hz.sent == [(1, "[auto] away, back later", False)]
    hz.logs["hist1"].append("Friend: you there?")
    hz.r.do_im(True)
    assert len(hz.sent) == 1                   # per-sender cooldown
    assert hz.r.stats["im_replies"] == 1 and hz.r.stats["by_rule"] == {"im:1": 1}


def test_own_lines_and_strangers_ignored(hz):
    hz.open(1, [])
    hz.logs["hist1"] += ["Me: hello", "Stranger: hi", "Friend: [auto] away"]
    hz.r.do_im(True)
    assert hz.sent == []


def test_daily_cap_per_sender(hz, monkeypatch):
    hz.r.cfg["autoreply_im"].update(per_sender_cooldown_seconds=60, max_per_sender_per_day=2,
                                    global_min_gap_seconds=0)
    hz.open(1, [])
    clock = [1000.0]
    monkeypatch.setattr(c.time, "monotonic", lambda: clock[0])
    for i in range(4):
        hz.logs["hist1"].append(f"Friend: m{i}")
        hz.r.do_im(True)
        clock[0] += 120                        # past the per-sender cooldown, inside the day
    assert len(hz.sent) == 2


def test_hourly_cap_across_windows(hz):
    hz.r.cfg["autoreply_im"]["max_per_hour"] = 1
    hz.r.cfg["autoreply_im"]["global_min_gap_seconds"] = 0
    hz.r.cfg["autoreply_im"]["only_nicknames"] = ["Friend", "Pal"]
    hz.open(1, [])
    hz.open(2, [])
    hz.logs["hist1"].append("Friend: a")
    hz.logs["hist2"].append("Pal: b")
    hz.r.do_im(True)
    assert len(hz.sent) == 1


def test_each_window_answers_in_its_own_window(hz):
    hz.r.cfg["autoreply_im"]["only_nicknames"] = ["Friend", "Pal"]
    hz.r.cfg["autoreply_im"]["global_min_gap_seconds"] = 0
    hz.open(1, [])
    hz.open(2, [])
    hz.logs["hist2"].append("Pal: b")
    hz.logs["hist1"].append("Friend: a")
    hz.r.do_im(True)
    assert sorted(h for h, _t, _d in hz.sent) == [1, 2]


def test_inactive_hours_consume_backlog(hz):
    hz.open(1, [])
    hz.logs["hist1"].append("Friend: hi")
    hz.r.do_im(False)
    hz.r.do_im(True)
    assert hz.sent == []


def test_burst_is_not_answered(hz):
    hz.open(1, [])
    hz.logs["hist1"] += [f"Friend: m{i}" for i in range(c.RESET_BURST + 1)]
    hz.r.do_im(True)
    assert hz.sent == []


def test_closed_window_is_dropped_not_a_failure(hz, monkeypatch):
    hz.open(1, [])
    def boom(ctrl, tail=150):
        raise OSError("gone")
    monkeypatch.setattr(c, "read_chat", boom)
    hz.r.do_im(True)
    assert hz.r.im_wins == {}


def test_failed_send_raises_so_the_loop_counts_it(hz, monkeypatch):
    hz.open(1, [])
    monkeypatch.setattr(c, "commit", lambda *a, **k: False)
    hz.logs["hist1"].append("Friend: hi")
    with pytest.raises(RuntimeError):
        hz.r.do_im(True)


def test_im_dry_run_is_independent_and_global_dry_wins(im_cfg, monkeypatch):
    im_cfg["autoreply_im"]["dry_run"] = True
    h = Harness(im_cfg, monkeypatch)
    h.open(1, [])
    h.logs["hist1"].append("Friend: hi")
    h.r.do_im(True)
    assert h.sent[0][2] is True                # IM dry although room/global is live
    im_cfg["autoreply_im"]["dry_run"] = False
    im_cfg["dry_run"] = True
    assert c.Runner(im_cfg).im_dry is True      # global dry_run wins


def test_hot_reload_keeps_im_rule_cooldowns(hz, im_cfg):
    hz.r.cfg["autoreply_im"]["rules"] = [{"pattern": ".*", "reply": "x", "cooldown_seconds": 999}]
    hz.r.apply_cfg(hz.r.cfg)
    hz.r.im_rules[0]["last"] = 123.0
    hz.r.apply_cfg(copy.deepcopy(hz.r.cfg))
    assert hz.r.im_rules[0]["last"] == 123.0


def test_run_refuses_without_own_nickname(im_cfg):
    im_cfg["autoreply"]["own_nickname"] = ""
    im_cfg["dry_run"] = True
    assert c.Runner(im_cfg).run() == 2


# ---------- window discovery / attach ----------
class FakePane:
    def __init__(self, kind, web=True):
        self.kind, self.web = kind, web


@pytest.fixture
def desktop(monkeypatch):
    wins = {}
    monkeypatch.setattr(c, "process_windows", lambda main: [w for w, _k in wins.values()])
    monkeypatch.setattr(c, "find", lambda w, spec: FakePane(wins[w.handle][1]))
    monkeypatch.setattr(c, "is_web", lambda ctrl: ctrl.web)
    monkeypatch.setattr(c, "log_kind", lambda ctrl: ctrl.kind)
    return wins


def test_only_im_windows_are_returned(im_cfg, desktop):
    main = FakeWin(0)
    for h, kind in ((0, "room"), (1, "room"), (2, "im"), (3, "mtim"), (4, ""), (5, "im")):
        desktop[h] = (FakeWin(h), kind)
    got = c.get_im_windows(im_cfg, main)
    assert [w.handle for w, _h, _i in got] == [2, 5]


def test_max_windows_limit(im_cfg, desktop):
    for h in range(1, 9):
        desktop[h] = (FakeWin(h), "im")
    assert len(c.get_im_windows(im_cfg, FakeWin(0), 3)) == 3


def test_title_regex_filters(im_cfg, desktop):
    im_cfg["autoreply_im"]["window_title_regex"] = "Friend"
    desktop[1] = (FakeWin(1, "Friend - Private chat"), "im")
    desktop[2] = (FakeWin(2, "Other - Private chat"), "im")
    assert [w.handle for w, _h, _i in c.get_im_windows(im_cfg, FakeWin(0))] == [1]


def attach_with(hz, monkeypatch, desktop, lines, force):
    desktop[7] = (FakeWin(7), "im")
    hz.r.win = FakeWin(0)
    hz.logs["hist"] = list(lines)
    monkeypatch.setattr(c, "find", lambda w, spec: "hist" if spec is hz.r.cfg["autoreply"]["history"] else "inp")
    monkeypatch.setattr(c, "is_web", lambda ctrl: True)
    monkeypatch.setattr(c, "log_kind", lambda ctrl: "im")
    hz.r.attach_im(0.0, force=force)
    return hz.r.im_wins[7]


def test_windows_open_at_start_are_baselined_silently(hz, monkeypatch, desktop):
    st = attach_with(hz, monkeypatch, desktop, ["Friend: hi"], force=True)
    assert st["prev"] == ["Friend: hi"]
    hz.r.do_im(True)
    assert hz.sent == []


def test_newly_opened_window_with_friends_first_message_is_answered(hz, monkeypatch, desktop):
    attach_with(hz, monkeypatch, desktop, ["Friend: hi"], force=False)
    hz.r.do_im(True)
    assert len(hz.sent) == 1


def test_first_message_option_can_be_turned_off(hz, monkeypatch, desktop):
    hz.r.cfg["autoreply_im"]["answer_first_message"] = False
    attach_with(hz, monkeypatch, desktop, ["Friend: hi"], force=False)
    hz.r.do_im(True)
    assert hz.sent == []


@pytest.mark.parametrize("lines", [
    ["Friend: a", "Friend: b"],              # reloaded history, not an opening message
    ["Stranger: hi"],                         # not on the allowlist
    ["Me: hi"],                               # our own line
    [],
])
def test_new_window_with_history_or_stranger_is_not_answered(hz, monkeypatch, desktop, lines):
    attach_with(hz, monkeypatch, desktop, lines, force=False)
    hz.r.do_im(True)
    assert hz.sent == []


# ---------- CLI ----------
def test_test_rules_im_flag(im_cfg, capsys, tmp_path):
    import argparse
    a = argparse.Namespace(text="hello", sender="Friend", file=None, im=True)
    assert c.cmd_test_rules(im_cfg, a) == 0
    assert "[auto] away, back later" in capsys.readouterr().out
    a.sender = "Stranger"
    c.cmd_test_rules(im_cfg, a)
    assert "SKIPPED" in capsys.readouterr().out


# ---------- v2.17: log_kinds, im-probe, hints ----------
def test_log_kinds_validation(im_cfg):
    for bad in ([], ["room"], "im", ["im", "x"]):
        im_cfg["autoreply_im"]["log_kinds"] = bad
        assert c.t("e_im_kinds") in errs_of(im_cfg)
    im_cfg["autoreply_im"]["log_kinds"] = ["im", "mtim"]
    errs, warns = c.validate(im_cfg)
    assert c.t("e_im_kinds") not in errs and c.t("w_im_mtim") in warns


def test_mtim_window_ignored_by_default_but_tracked_when_allowed(im_cfg, desktop):
    desktop[1] = (FakeWin(1), "mtim")
    assert c.get_im_windows(im_cfg, FakeWin(0)) == []
    im_cfg["autoreply_im"]["log_kinds"] = ["im", "mtim"]
    assert [w.handle for w, _h, _i in c.get_im_windows(im_cfg, FakeWin(0))] == [1]


def test_room_autoreply_still_ignores_mtim(im_cfg, desktop):
    desktop[1] = (FakeWin(1), "mtim")
    assert c.get_im_windows(im_cfg, FakeWin(0)) == []


def test_probe_reports_config_problems_and_window_kinds(cfg, desktop, monkeypatch, capsys):
    cfg["autoreply_im"]["enabled"] = False
    cfg["autoreply_im"]["only_nicknames"] = []
    monkeypatch.setattr(c, "get_window", lambda cfg: FakeWin(0, "Camfrog Video Chat"))
    desktop[0] = (FakeWin(0, "Camfrog Video Chat"), "")
    desktop[1] = (FakeWin(1, "Room: Video Chat Room"), "room")
    desktop[2] = (FakeWin(2, "Friend"), "mtim")
    assert c.cmd_im_probe(cfg) == 1
    out = capsys.readouterr().out
    assert "autoreply_im.enabled is false" in out and "only_nicknames is empty" in out
    assert "[room]" in out and "MTIM window: ignored" in out and "no private-chat window would be tracked" in out


def test_probe_ok_when_ready(im_cfg, desktop, monkeypatch, capsys):
    im_cfg["dry_run"] = False
    im_cfg["autoreply_im"]["dry_run"] = False
    monkeypatch.setattr(c, "get_window", lambda cfg: FakeWin(0))
    desktop[0] = (FakeWin(0), "")
    desktop[3] = (FakeWin(3, "Friend"), "im")
    assert c.cmd_im_probe(im_cfg) == 0
    out = capsys.readouterr().out
    assert "config looks ready" in out and "WILL be tracked" in out


def test_startup_summary_and_no_window_hint(hz, caplog, desktop, monkeypatch):
    import logging
    caplog.set_level(logging.INFO, logger="camfrog_auto")
    hz.r.im_summary()
    assert "IM auto-reply ON: LIVE, friends=1, rules=1, kinds=im" in caplog.text
    hz.r.win = FakeWin(0)
    hz.r.attach_im(500.0, force=False)
    assert "no private-chat window found" in caplog.text
    caplog.clear()
    hz.r.im_next_attach = 0
    hz.r.attach_im(501.0, force=False)       # hint is rate-limited
    assert "no private-chat window found" not in caplog.text


def test_non_allowlisted_sender_is_logged_by_nick_only(hz, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="camfrog_auto")
    hz.open(1, [])
    hz.logs["hist1"].append("Stranger: secret text")
    hz.r.do_im(True)
    assert "Stranger" in caplog.text and "secret text" not in caplog.text and hz.sent == []
