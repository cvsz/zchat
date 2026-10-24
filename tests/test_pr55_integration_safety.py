"""Regression checks for PR #55 features that must never fabricate success."""

import copy

import pytest


def test_f3_f4_delegate_to_validated_worker_lifecycle(monkeypatch, tmp_path):
    import camfrog_auto as app

    calls = []
    monkeypatch.setattr(
        app, "cmd_start",
        lambda cfg, args: calls.append(("start", cfg, args)) or 0,
    )
    monkeypatch.setattr(
        app, "cmd_stop",
        lambda cfg: calls.append(("stop", cfg)) or 0,
    )
    config = copy.deepcopy(app.DEFAULTS)
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    assert app.toggle_worker(config, "start", config_path) == "worker started"
    assert app.toggle_worker(config, "stop") == "worker stopped"
    assert calls[0][0] == "start"
    assert calls[0][2].config == str(config_path.resolve())
    assert calls[1] == ("stop", config)


@pytest.mark.parametrize("action,method", [
    ("start", "cmd_start"),
    ("stop", "cmd_stop"),
])
def test_worker_hotkeys_do_not_report_failed_operations_as_success(
    action, method, monkeypatch, tmp_path
):
    import camfrog_auto as app

    monkeypatch.setattr(app, method, lambda *_args: 1)
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    assert app.toggle_worker(
        copy.deepcopy(app.DEFAULTS), action, config_path
    ).endswith("failed")


def test_worker_start_without_verified_config_path_fails_closed(monkeypatch):
    import camfrog_auto as app

    monkeypatch.setattr(
        app, "cmd_start",
        lambda *_args: pytest.fail("worker cannot start without its config path"),
    )
    assert "failed" in app.toggle_worker(copy.deepcopy(app.DEFAULTS), "start")


@pytest.mark.parametrize("function,enable", [
    ("name_changer_apply", "name_changer_toggle"),
    ("color_cycle_apply", "color_cycle_toggle"),
])
def test_unverified_profile_update_does_not_touch_status_or_report_success(
    function, enable, monkeypatch
):
    import camfrog_auto as app

    def forbidden(*_args, **_kwargs):
        pytest.fail("unverified profile update must never write to status")

    monkeypatch.setattr(app, "get_window", forbidden)
    monkeypatch.setattr(app, "write_text", forbidden)
    config = copy.deepcopy(app.DEFAULTS)
    getattr(app, enable)(config, True)

    result = getattr(app, function)(config)
    assert "UNVERIFIED" in result
    assert "successfully" not in result
