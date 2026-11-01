"""Regression tests for compact, resizable Tk GUI startup geometry."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPACT_GUI_PATHS = (
    "src/features/status_changer_gui.py",
    "src/features/status_random_gui.py",
    "src/features/status_marquee_gui.py",
    "src/features/room_control_gui.py",
    "src/features/im_autoreply_gui.py",
    "src/features/camfrog_music_gui.py",
    "src/camfrog/camfrog_gui.py",
    "src/features/name_changer_gui.py",
    "src/features/name_color_changer_gui.py",
    "src/features/icon_changer_gui.py",
    "src/features/web_status_gui.py",
    "src/features/camfrog_private_chat_gui.py",
    "src/features/chat_im_private_gui.py",
    "src/features/zgpu_tester_gui.py",
    "line/line_status_gui.py",
    "zchat-client/src/zchat_client/ui.py",
)


def test_all_supported_gui_sources_use_compact_default_geometry():
    for relative_path in COMPACT_GUI_PATHS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert 'geometry("360x480")' in source, relative_path
        assert "minsize(320, 240)" in source, relative_path


def test_compact_geometry_keeps_tk_window_resizable_by_default():
    # Tk windows are resizable by default unless explicitly disabled. Guard
    # against adding a fixed-size setting to the compact GUI sources.
    forbidden = ("resizable(False, False)", "resizable(0, 0)")
    for relative_path in COMPACT_GUI_PATHS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert not any(setting in source for setting in forbidden), relative_path
