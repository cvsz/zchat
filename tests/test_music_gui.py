"""Music DJ GUI tests: self-containment, profile, CLI passthrough, smoke."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camfrog_music_gui as g  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_module_has_no_project_imports():
    """The DJ exe is built from this single file: no sibling modules allowed."""
    src = (ROOT / "src" / "features" / "camfrog_music_gui.py").read_text(
        encoding="utf-8"
    )
    for banned in (
        "import camfrog_auto",
        "import camfrog_gui",
        "import camfrog_status_gui",
        "import camfrog_private_chat_gui",
        "import web_status_gui",
        "from tools",
        "import tools",
        "import clipboard_support",
        "from clipboard_support",
    ):
        assert banned not in src, banned


def test_profile_is_music_and_disables_other_features():
    assert g.APP_PROFILE == "music"
    cfg = copy.deepcopy(g.DEFAULTS)
    cfg["status"]["enabled"] = True
    cfg["autoreply_im"]["enabled"] = True
    out = g.apply_app_profile(cfg)
    assert out["status"]["enabled"] is False
    assert out["autoreply_im"]["enabled"] is False
    assert out["autoreply"]["enabled"] is True


def test_profile_command_boundary():
    assert g.profile_command_error("music", SimpleNamespace(cmd="run")) is None
    assert g.profile_command_error("music", SimpleNamespace(cmd="marquee")) is not None


def test_cli_passthrough(capsys):
    assert g.main(["check", "--config", str(ROOT / "config.example.json")]) == 0
    assert "config OK" in capsys.readouterr().out


def test_cli_rejects_foreign_commands(capsys):
    rc = g.main(["marquee", "hello"])
    assert rc == 2
    assert "marquee" in capsys.readouterr().out


def test_dj_defaults_present_in_engine_copy():
    dj = g.DEFAULTS["dj"]
    assert dj["enabled"] is False
    assert dj["prefix"] == "!"
    assert dj["audio_backend"] == "chat"
    assert dj["queue_file"] == "dj_queue.json"


from conftest import has_display, tk_root  # noqa: E402


@pytest.mark.skipif(not has_display(), reason="needs tkinter + display")
def test_gui_smoke(tmp_path, monkeypatch):
    config = json.loads((ROOT / "config.example.json").read_text("utf-8"))
    config["autoreply"]["own_nickname"] = "DJBot"
    (tmp_path / "config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(g, "BASE", tmp_path)  # config must live beside BASE
    import tkinter

    root = tk_root()
    root.deiconify()
    monkeypatch.setattr(tkinter, "Tk", lambda: root)
    app = g.build_app()(tmp_path / "config.json")
    app.root.update()
    tabs = [app.nb.tab(i, "text") for i in range(len(app.nb.tabs()))]
    assert len(tabs) == 5  # Dashboard, Setup, Auto-reply, Music, Advanced
    cfg, errs = app.collect()
    assert not errs
    assert cfg["dj"]["prefix"] == "!"
    assert cfg["autoreply"]["own_nickname"] == "DJBot"
    assert hasattr(app, "dj_tree") and hasattr(app, "t_skips")
    assert not hasattr(app, "im_tree") and not hasattr(app, "htree")
    app.dj_refresh()
    app.dj_skip()  # nothing playing: user-facing error, no crash
    app.dj_clear()
    app.test_rule()
    assert "rule" in app.tout.cget("text")
    app.switch_lang()
    assert g.LANG == "th"
    app.switch_lang()
    assert g.LANG == "en"
    app.tick()  # the shared root is left alive for later smoke tests


@pytest.mark.skipif(not has_display(), reason="needs tkinter + display")
def test_music_folder_picker_sets_folder_used_by_chat_requests(tmp_path, monkeypatch):
    import tkinter
    from tkinter import filedialog

    config = json.loads((ROOT / "config.example.json").read_text("utf-8"))
    config["autoreply"]["own_nickname"] = "DJBot"
    config["dj"]["music_dir"] = "music"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(g, "BASE", tmp_path)
    root = tk_root()
    root.deiconify()
    monkeypatch.setattr(tkinter, "Tk", lambda: root)
    app = g.build_app()(config_path)
    app.root.update()

    selected = tmp_path / "picked-music"
    selected.mkdir()
    choices = [str(selected), ""]
    calls = []

    def askdirectory(**kwargs):
        calls.append(kwargs)
        return choices.pop(0)

    monkeypatch.setattr(filedialog, "askdirectory", askdirectory)
    app.btn_dj_browse_music.invoke()
    assert app.dj_music_dir_var.get() == str(selected)
    app.btn_dj_browse_music.invoke()  # cancel must preserve the selected folder
    assert app.dj_music_dir_var.get() == str(selected)
    assert calls[0]["mustexist"] is True
    assert Path(calls[0]["initialdir"]).is_dir()
    cfg, errors = app.collect()
    assert errors == []
    assert cfg["dj"]["music_dir"] == str(selected)
    app.root.destroy()


def test_config_must_stay_inside_base(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "BASE", tmp_path / "app")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "config.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    g.ConfigModel(tmp_path / "app" / "config.json")  # inside: fine
    with pytest.raises(ValueError):
        g.ConfigModel(outside)
