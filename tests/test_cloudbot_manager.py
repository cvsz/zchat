"""Stand-alone CloudBot-Manager.exe app wiring (content-level regression tests)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_entry_sets_mode_and_launches_shared_gui_main():
    entry = (ROOT / "src" / "features" / "cloudbot_manager_entry.py").read_text(
        encoding="utf-8")
    assert 'os.environ["ZCHAT_BOT_MANAGER_ONLY"] = "1"' in entry
    assert entry.index('ZCHAT_BOT_MANAGER_ONLY') < entry.index("from web_status_gui")
    assert "from web_status_gui import main" in entry
    assert "raise SystemExit(main())" in entry


def test_gui_bot_only_mode_constants_and_window_title():
    gui = (ROOT / "src" / "features" / "web_status_gui.py").read_text(
        encoding="utf-8")
    assert 'BOT_MANAGER_ONLY = os.environ.get("ZCHAT_BOT_MANAGER_ONLY", "").strip() == "1"' in gui
    assert 'if not BOT_MANAGER_ONLY:' in gui
    assert 'self.nb.hide(tab)' in gui
    assert '"ZeaZDev CloudBot Manager"' in gui
    assert '"CloudBot-Manager.exe" if BOT_MANAGER_ONLY else "web-status.exe"' in gui


def test_build_and_release_wire_the_standalone_cloudbot_manager():
    build = (ROOT / "build.bat").read_text(encoding="utf-8")
    assert "--distpath dist\\cloudbot-manager --workpath build\\cloudbot-manager" in build
    assert "--name CloudBot-Manager %ICONARG%" in build
    assert "src\\features\\cloudbot_manager_entry.py" in build
    assert 'if not exist "dist\\cloudbot-manager\\CloudBot-Manager.exe" goto :fail' in build
    assert 'echo dist\\cloudbot-manager\\CloudBot-Manager.exe' in build
    assert 'im-autoreply auto-dj web-status cloudbot-manager name-color-changer' in build
    assert 'if exist "dist\\cloudbot-manager\\CloudBot-Manager.exe" del /q "dist\\cloudbot-manager\\CloudBot-Manager.exe"' in build

    full = (ROOT / "full-build.bat").read_text(encoding="utf-8").lower()
    assert "dist\\cloudbot-manager\\cloudbot-manager.exe" in full

    package = (ROOT / "tools" / "package.py").read_text(encoding="utf-8")
    assert '"cloudbot-manager/CloudBot-Manager.exe"' in package

    release = (ROOT / "tools" / "release.py").read_text(encoding="utf-8")
    assert '"CloudBot-Manager.exe": DIST / "cloudbot-manager" / "CloudBot-Manager.exe",' in release


def test_windows_runtime_recognizes_the_standalone_title():
    auto = (ROOT / "src" / "camfrog" / "camfrog_auto.py").read_text(
        encoding="utf-8")
    runtime = (ROOT / "src" / "camfrog" / "camfrog_windows_runtime.py").read_text(
        encoding="utf-8")
    assert '"ZeaZDev CloudBot Manager",' in auto
    assert '"ZeaZDev CloudBot Manager",' in runtime
