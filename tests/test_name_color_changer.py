"""Tests for standalone name-color-changer (GUI + CLI)."""

import json
import pytest
from src.features import name_color_changer_gui as nccg

SANITIZED_SETTINGS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Camfrog - Settings</title></head>
<body>
<script>
var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';
var external_data = (function(){return {
    "type": "settings",
    "nickname_color": "#279a29",
    "settings": {
        "nickname": {
            "colors": [
                {"color": "#cd46ff", "id": 3555},
                {"color": "#279a29", "id": 1}
            ]
        }
    }
};})()||{};
</script>
</body>
</html>
"""


def test_standalone_color_alternator_dry_run_sends_nothing(capsys):
    ret = nccg.main(["--alternate-colors", "--login", "Seaza"])
    assert ret == nccg.EXIT_OK
    out, _ = capsys.readouterr()
    assert "[DRY-RUN]" in out
    assert "Color A: id 1" in out
    assert "Color B: id 3555" in out
    assert "Dry-run active; no requests sent" in out


def test_standalone_color_alternator_requires_confirm_for_live(monkeypatch):
    called = []
    monkeypatch.setattr(nccg, "fetch_settings", lambda *a: called.append("settings"))
    monkeypatch.setattr(nccg, "post_flair_color", lambda *a: called.append("post"))

    ret = nccg.main(["--alternate-colors", "--login", "Seaza"])
    assert ret == nccg.EXIT_OK
    assert called == []


def test_standalone_color_alternator_clamps_interval_to_minimum():
    assert nccg.parse_color_alternate_interval(5) == 10.0
    assert nccg.parse_color_alternate_interval(10) == 10.0
    assert nccg.parse_color_alternate_interval(45) == 45.0
    with pytest.raises(ValueError, match="invalid interval"):
        nccg.parse_color_alternate_interval("not-a-number")


def test_standalone_color_alternator_extract_external_data_balanced_brace():
    data = nccg.extract_external_data(SANITIZED_SETTINGS_HTML)
    assert data is not None
    assert data["type"] == "settings"
    assert data["nickname_color"] == "#279a29"
    assert len(data["settings"]["nickname"]["colors"]) == 2

    assert nccg.extract_external_data("<html>no external data</html>") is None
    assert nccg.extract_external_data("var external_data = (function(){return {broken;})()||{};") is None


def test_standalone_color_alternator_parse_settings_colors():
    data = nccg.extract_external_data(SANITIZED_SETTINGS_HTML)
    curr_hex, allowed = nccg.parse_settings_nickname_colors(data)
    assert curr_hex == "#279a29"
    assert allowed == {3555: "#cd46ff", 1: "#279a29"}

    with pytest.raises(ValueError):
        nccg.parse_settings_nickname_colors({"type": "profile"})
    with pytest.raises(ValueError):
        nccg.parse_settings_nickname_colors({"type": "settings", "settings": {}})
    with pytest.raises(ValueError):
        nccg.parse_settings_nickname_colors(
            {"type": "settings", "settings": {"nickname": {"colors": []}}}
        )


def test_standalone_color_alternator_refuses_unknown_color_id(monkeypatch):
    out_msgs = []
    monkeypatch.setattr(
        nccg,
        "verify_active_session",
        lambda *a: (True, {"nickname": "Seaza", "user_id": "1"}, "token", ""),
    )
    monkeypatch.setattr(nccg, "fetch_settings", lambda *a: (SANITIZED_SETTINGS_HTML, 200))

    ret = nccg.run_color_alternator(
        opener=object(),
        expected_login="Seaza",
        color_a=9999,
        color_b=3555,
        confirm=True,
        print_fn=out_msgs.append,
    )
    assert ret == nccg.EXIT_USAGE
    assert any("Refusing color A: id 9999 is not in allowed colors" in m for m in out_msgs)


def test_standalone_color_alternator_alternation_order_and_restore(monkeypatch):
    out_msgs = []
    calls = []
    current_color_state = {"hex": "#279a29"}

    def fake_verify(*args):
        return True, {"nickname": "Seaza", "user_id": "1"}, "token", ""

    def fake_fetch_settings(opener, timeout):
        html = SANITIZED_SETTINGS_HTML.replace(
            '"nickname_color": "#279a29"',
            f'"nickname_color": "{current_color_state["hex"]}"',
        )
        return html, 200

    def fake_post_flair(opener, color_id, csrf, timeout):
        calls.append(color_id)
        if color_id == 3555:
            current_color_state["hex"] = "#cd46ff"
        elif color_id == 1:
            current_color_state["hex"] = "#279a29"
        return json.dumps({"error": False, "response": "ok"}), 200, None

    monkeypatch.setattr(nccg, "verify_active_session", fake_verify)
    monkeypatch.setattr(nccg, "fetch_settings", fake_fetch_settings)
    monkeypatch.setattr(nccg, "post_flair_color", fake_post_flair)

    class DummyTime:
        def sleep(self, duration):
            pass

    ret = nccg.run_color_alternator(
        opener=object(),
        expected_login="Seaza",
        color_a=1,
        color_b=3555,
        confirm=True,
        max_cycles=2,
        time_module=DummyTime(),
        print_fn=out_msgs.append,
    )
    assert ret == nccg.EXIT_OK
    assert calls == [3555, 1, 1]
    assert any("Restoration succeeded: color changed to #279a29" in m for m in out_msgs)


def test_standalone_color_alternator_stops_immediately_on_429(monkeypatch):
    out_msgs = []
    monkeypatch.setattr(
        nccg,
        "verify_active_session",
        lambda *a: (True, {"nickname": "Seaza", "user_id": "1"}, "token", ""),
    )
    monkeypatch.setattr(nccg, "fetch_settings", lambda *a: (SANITIZED_SETTINGS_HTML, 200))

    def fake_post_429(opener, color_id, csrf, timeout):
        return "rate limit", 429, "60"

    monkeypatch.setattr(nccg, "post_flair_color", fake_post_429)

    ret = nccg.run_color_alternator(
        opener=object(),
        expected_login="Seaza",
        color_a=1,
        color_b=3555,
        confirm=True,
        max_cycles=4,
        print_fn=out_msgs.append,
    )
    assert ret == nccg.EXIT_BLOCKED
    assert any("HTTP 429 rate limited" in m for m in out_msgs)
    assert any("Retry-After: 60" in m for m in out_msgs)
    assert any("STOPPING IMMEDIATELY" in m for m in out_msgs)


def test_standalone_color_alternator_stops_immediately_on_error_true(monkeypatch):
    out_msgs = []
    monkeypatch.setattr(
        nccg,
        "verify_active_session",
        lambda *a: (True, {"nickname": "Seaza", "user_id": "1"}, "token", ""),
    )
    monkeypatch.setattr(nccg, "fetch_settings", lambda *a: (SANITIZED_SETTINGS_HTML, 200))
    monkeypatch.setattr(
        nccg,
        "post_flair_color",
        lambda *a: (json.dumps({"error": True, "response": "color expired"}), 200, None),
    )

    ret = nccg.run_color_alternator(
        opener=object(),
        expected_login="Seaza",
        color_a=1,
        color_b=3555,
        confirm=True,
        max_cycles=4,
        print_fn=out_msgs.append,
    )
    assert ret == nccg.EXIT_BLOCKED
    assert any("server returned error: color expired" in m for m in out_msgs)
    assert any("STOPPING IMMEDIATELY" in m for m in out_msgs)


def test_standalone_color_alternator_redacts_credentials_and_personal_data():
    sample_key = "sk_" + "live_" + "TEST" + "EXAMPLE" + "00000000000000000000"
    raw = (
        "Sending csrf=90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0 with "
        f"PHPSESSID=session_secret_123, email john.doe@example.com, and "
        f"stripe_key={sample_key}"
    )
    redacted = nccg.redact_sensitive(raw, secrets=["session_secret_123"])
    assert "90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0" not in redacted
    assert "session_secret_123" not in redacted
    assert "john.doe@example.com" not in redacted
    assert sample_key not in redacted
    assert "[email redacted]" in redacted
    assert "[stripe-key redacted]" in redacted
    assert "[redacted]" in redacted
