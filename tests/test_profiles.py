"""Feature-boundary and app-local config tests; never touch Camfrog windows."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camfrog_auto as ca  # noqa: E402
import camfrog_gui as gui  # noqa: E402
import camfrog_private_chat_gui as private_chat  # noqa: E402
import camfrog_status_gui as status_gui  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_app_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "BASE", tmp_path)
    monkeypatch.setattr(private_chat, "CONFIG_PATH", tmp_path / "private-chat-config.json")


@pytest.mark.parametrize("profile,disabled", [
    ("room", ("status", "autoreply_im")),
    ("im_reply", ("status", "autoreply")),
    ("status", ("autoreply", "autoreply_im")),
])
def test_profiles_disable_other_features_without_mutating_source(profile, disabled, monkeypatch):
    source = copy.deepcopy(ca.DEFAULTS)
    for name in ("status", "autoreply", "autoreply_im"):
        source[name]["enabled"] = True

    monkeypatch.setattr(ca, "APP_PROFILE", profile)
    runtime = ca.apply_app_profile(source)
    for name in disabled:
        assert runtime[name]["enabled"] is False
    assert all(source[name]["enabled"] is True
               for name in ("status", "autoreply", "autoreply_im"))

    if profile in ("room", "im_reply"):
        monkeypatch.setattr(gui, "APP_PROFILE", profile)
        gui_runtime = gui.apply_app_profile(source)
        assert gui_runtime["status"]["enabled"] is False
        disabled_in_gui = "autoreply_im" if profile == "room" else "autoreply"
        assert gui_runtime[disabled_in_gui]["enabled"] is False


@pytest.mark.parametrize("profile,command", [
    ("room", ["status", "message"]),
    ("room", ["im-probe"]),
    ("room", ["test-rules", "message", "--im"]),
    ("im_reply", ["status", "message"]),
    ("im_reply", ["chat-probe"]),
    ("im_reply", ["test-rules", "message"]),
    ("status", ["chat-probe"]),
    ("status", ["im-probe"]),
])
def test_cli_profile_rejects_commands_outside_feature(profile, command, monkeypatch, capsys):
    monkeypatch.setattr(ca, "APP_PROFILE", profile)
    assert ca.main(command) == 2
    output = capsys.readouterr().out
    assert "not available" in output or "cannot" in output or "requires" in output


def test_cli_profile_rejection_precedes_config_load(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ca, "APP_PROFILE", "room")
    config = tmp_path / "missing.json"
    assert ca.main(["status", "blocked", "--config", str(config)]) == 2
    assert not config.exists()
    assert "not available" in capsys.readouterr().out


def test_unknown_profile_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(ca, "APP_PROFILE", "unknown")
    assert ca.main(["check"]) == 2
    assert "Unknown Camfrog app profile" in capsys.readouterr().out


def test_private_chat_config_is_local_valid_and_feature_disabled(tmp_path):
    config_path = tmp_path / "private-chat-config.json"
    config = private_chat.load_private_config(config_path)
    assert config_path.exists()
    assert config["status"]["enabled"] is False
    assert config["autoreply"]["enabled"] is False
    assert config["autoreply_im"]["enabled"] is False
    assert ca.validate(config)[0] == []


def test_private_chat_config_rejects_paths_outside_app_folder(tmp_path):
    outside = tmp_path.parent / "outside-private-config.json"
    with pytest.raises(ValueError, match="must stay beside"):
        private_chat.load_private_config(outside)
    assert not outside.exists()


def test_gui_rejects_missing_config_argument_and_option_value_is_not_a_command(capsys):
    assert gui.main(["--config"]) == 2
    assert "argument --config" in capsys.readouterr().err
    assert gui.has_cli_command(["--config", "status"]) is False
    assert gui.has_cli_command(["im-probe"]) is True
    assert gui.has_cli_command(["im-probe"]) is True


def test_status_gui_rejects_missing_config_before_mutex_or_window(capsys):
    assert status_gui.main(["--config"]) == 2
    assert "argument --config" in capsys.readouterr().err


def test_private_chat_gui_rejects_missing_config_without_opening_tk(capsys):
    assert private_chat.main(["--config"]) == 2
    assert "argument --config" in capsys.readouterr().err
