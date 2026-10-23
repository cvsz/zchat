"""Tests for standalone icon-changer (GUI + CLI)."""

import json
import pytest
from src.features import icon_changer_gui as icg

SANITIZED_SETTINGS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Camfrog - Settings</title></head>
<body>
<script>
var csrf = '90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0';
var external_data = (function(){return {
    "type": "settings",
    "icon_id": 1,
    "nickname_color": "#279a29",
    "settings": {
        "nickname": {
            "colors": [
                {"color": "#cd46ff", "id": 3555},
                {"color": "#279a29", "id": 1}
            ]
        },
        "icon": {
            "icons": [
                {"id": 1, "name": "default"},
                {"id": 2, "name": "star"},
                {"id": 3, "name": "crown"},
                {"id": 4, "name": "diamond"}
            ]
        }
    }
};})()||{};
</script>
</body>
</html>
"""


def test_standalone_icon_alternator_dry_run_sends_nothing(capsys):
    ret = icg.main(["--alternate-icons", "--login", "Seaza"])
    assert ret == icg.EXIT_OK
    out, _ = capsys.readouterr()
    assert "[DRY-RUN]" in out
    assert "Icon A: id 1" in out
    assert "Icon B: id 2" in out
    assert "Dry-run active; no requests sent" in out


def test_standalone_icon_alternator_requires_confirm_for_live(monkeypatch):
    called = []
    monkeypatch.setattr(icg, "fetch_settings", lambda *a: called.append("settings"))
    monkeypatch.setattr(icg, "post_icon_change", lambda *a: called.append("post"))

    ret = icg.main(["--alternate-icons", "--login", "Seaza"])
    assert ret == icg.EXIT_OK
    assert called == []


def test_standalone_icon_alternator_clamps_interval_to_minimum():
    assert icg.parse_icon_alternate_interval(5) == 10.0
    assert icg.parse_icon_alternate_interval(10) == 10.0
    assert icg.parse_icon_alternate_interval(45) == 45.0
    with pytest.raises(ValueError, match="invalid interval"):
        icg.parse_icon_alternate_interval("not-a-number")


def test_standalone_icon_alternator_extract_external_data_balanced_brace():
    data = icg.extract_external_data(SANITIZED_SETTINGS_HTML)
    assert data is not None
    assert data["type"] == "settings"
    assert data["icon_id"] == 1
    assert len(data["settings"]["icon"]["icons"]) == 4

    assert icg.extract_external_data("<html>no external data</html>") is None
    assert (
        icg.extract_external_data(
            "var external_data = (function(){return {broken;})()||{};"
        )
        is None
    )


def test_standalone_icon_alternator_parse_settings_icons():
    data = icg.extract_external_data(SANITIZED_SETTINGS_HTML)
    curr_id, allowed = icg.parse_settings_icons(data)
    assert curr_id == 1
    assert allowed == {1: "default", 2: "star", 3: "crown", 4: "diamond"}

    with pytest.raises(ValueError):
        icg.parse_settings_icons({"type": "profile"})
    with pytest.raises(ValueError):
        icg.parse_settings_icons({"type": "settings", "settings": {}})
    with pytest.raises(ValueError):
        icg.parse_settings_icons(
            {"type": "settings", "settings": {"icon": {"icons": []}}}
        )


def test_standalone_icon_alternator_refuses_unknown_icon_id(monkeypatch):
    out_msgs = []
    monkeypatch.setattr(
        icg,
        "verify_active_session",
        lambda *a: (True, {"nickname": "Seaza", "user_id": "1"}, "token", ""),
    )
    monkeypatch.setattr(
        icg, "fetch_settings", lambda *a: (SANITIZED_SETTINGS_HTML, 200)
    )

    ret = icg.run_icon_alternator(
        opener=object(),
        expected_login="Seaza",
        icon_a=9999,
        icon_b=2,
        confirm=True,
        print_fn=out_msgs.append,
    )
    assert ret == icg.EXIT_USAGE
    assert any(
        "Refusing Icon A: id 9999 is not in allowed icons" in m for m in out_msgs
    )


def test_standalone_icon_alternator_alternation_order_and_restore(monkeypatch):
    out_msgs = []
    calls = []
    current_icon_state = {"id": 1}

    def fake_verify(*args):
        return True, {"nickname": "Seaza", "user_id": "1"}, "token", ""

    def fake_fetch_settings(opener, timeout):
        html = SANITIZED_SETTINGS_HTML.replace(
            '"icon_id": 1',
            f'"icon_id": {current_icon_state["id"]}',
        )
        return html, 200

    def fake_post_icon(opener, icon_id, csrf, timeout):
        calls.append(icon_id)
        current_icon_state["id"] = icon_id
        return json.dumps({"error": False, "response": "ok"}), 200, None

    monkeypatch.setattr(icg, "verify_active_session", fake_verify)
    monkeypatch.setattr(icg, "fetch_settings", fake_fetch_settings)
    monkeypatch.setattr(icg, "post_icon_change", fake_post_icon)

    class DummyTime:
        def sleep(self, duration):
            pass

    ret = icg.run_icon_alternator(
        opener=object(),
        expected_login="Seaza",
        icon_a=1,
        icon_b=2,
        confirm=True,
        max_cycles=2,
        time_module=DummyTime(),
        print_fn=out_msgs.append,
    )
    assert ret == icg.EXIT_OK
    # Starting at icon 1 (== icon_a), so first switch goes to B(2), then A(1), then restore to initial 1
    assert calls == [2, 1, 1]
    assert any("Restoration succeeded: icon changed to id 1" in m for m in out_msgs)


def test_standalone_icon_alternator_stops_immediately_on_429(monkeypatch):
    out_msgs = []
    monkeypatch.setattr(
        icg,
        "verify_active_session",
        lambda *a: (True, {"nickname": "Seaza", "user_id": "1"}, "token", ""),
    )
    monkeypatch.setattr(
        icg, "fetch_settings", lambda *a: (SANITIZED_SETTINGS_HTML, 200)
    )

    def fake_post_429(opener, icon_id, csrf, timeout):
        return "rate limit", 429, "60"

    monkeypatch.setattr(icg, "post_icon_change", fake_post_429)

    ret = icg.run_icon_alternator(
        opener=object(),
        expected_login="Seaza",
        icon_a=1,
        icon_b=2,
        confirm=True,
        max_cycles=4,
        print_fn=out_msgs.append,
    )
    assert ret == icg.EXIT_BLOCKED
    assert any("HTTP 429 rate limited" in m for m in out_msgs)
    assert any("Retry-After: 60" in m for m in out_msgs)
    assert any("STOPPING IMMEDIATELY" in m for m in out_msgs)


def test_standalone_icon_alternator_stops_immediately_on_error_true(monkeypatch):
    out_msgs = []
    monkeypatch.setattr(
        icg,
        "verify_active_session",
        lambda *a: (True, {"nickname": "Seaza", "user_id": "1"}, "token", ""),
    )
    monkeypatch.setattr(
        icg, "fetch_settings", lambda *a: (SANITIZED_SETTINGS_HTML, 200)
    )
    monkeypatch.setattr(
        icg,
        "post_icon_change",
        lambda *a: (json.dumps({"error": True, "response": "icon expired"}), 200, None),
    )

    ret = icg.run_icon_alternator(
        opener=object(),
        expected_login="Seaza",
        icon_a=1,
        icon_b=2,
        confirm=True,
        max_cycles=4,
        print_fn=out_msgs.append,
    )
    assert ret == icg.EXIT_BLOCKED
    assert any("server returned error: icon expired" in m for m in out_msgs)
    assert any("STOPPING IMMEDIATELY" in m for m in out_msgs)


def test_standalone_icon_alternator_redacts_credentials_and_personal_data():
    sample_key = "sk_" + "live_" + "TEST" + "EXAMPLE" + "00000000000000000000"
    raw = (
        "Sending csrf=90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0 with "
        f"PHPSESSID=session_secret_123, email john.doe@example.com, and "
        f"stripe_key={sample_key}"
    )
    redacted = icg.redact_sensitive(raw, secrets=["session_secret_123"])
    assert "90d230418b54c662c347d84f32f663e0a94fc5abd9d705a0" not in redacted
    assert "session_secret_123" not in redacted
    assert "john.doe@example.com" not in redacted
    assert sample_key not in redacted
    assert "[email redacted]" in redacted
    assert "[stripe-key redacted]" in redacted
    assert "[redacted]" in redacted


def test_icon_changer_clipboard_controller(monkeypatch):
    from types import SimpleNamespace
    import tkinter
    from tests.test_gui_theme_clipboard import FakeEntry, FakeMenu, FakeRoot

    monkeypatch.setattr(tkinter, "Menu", FakeMenu)
    monkeypatch.setattr(
        icg, "set_clipboard_text", lambda root, value: root.clipboard_append(value)
    )
    monkeypatch.setattr(icg, "get_clipboard_text", lambda root: root.clipboard_get())
    entry = FakeEntry()
    root = FakeRoot(entry)
    controller = icg.ClipboardController(root)
    controller.install(root)

    assert "<Control-KeyPress>" in entry.bindings
    assert "<Control-c>" in entry.bindings
    assert "<Control-v>" in entry.bindings
    assert "<Control-x>" in entry.bindings
    assert "<Control-a>" in entry.bindings

    assert (
        controller.handle_control_key(
            SimpleNamespace(widget=entry, keysym="??", char="ษ", keycode=88)
        )
        == "break"
    )
    assert root.clipboard == "bcd"
    assert entry.value == "aef"
