"""Shared GUI palette and clipboard behavior across shipped apps."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GUI_FILES = (
    "src/camfrog/camfrog_gui.py",
    "src/features/camfrog_private_chat_gui.py",
    "src/features/camfrog_status_gui.py",
    "src/features/camfrog_music_gui.py",
    "src/features/chat_im_private_gui.py",
    "src/features/room_control_gui.py",
    "src/features/im_autoreply_gui.py",
    "src/features/status_random_gui.py",
    "src/features/status_marquee_gui.py",
    "src/features/status_changer_gui.py",
    "src/features/web_status_gui.py",
    "line/line_status_gui.py",
)

CLIPBOARD_CONTROLLERS = (
    ("clipboard_support", "ClipboardController"),
    ("camfrog_gui", "ClipboardController"),
    ("camfrog_private_chat_gui", "ClipboardController"),
    ("camfrog_status_gui", "ClipboardController"),
    ("camfrog_music_gui", "ClipboardController"),
    ("chat_im_private_gui", "PrivateChatClipboardController"),
    ("room_control_gui", "ClipboardController"),
    ("im_autoreply_gui", "ClipboardController"),
    ("status_random_gui", "ClipboardController"),
    ("status_marquee_gui", "ClipboardController"),
    ("status_changer_gui", "ClipboardController"),
    ("name_changer_gui", "ClipboardController"),
    ("name_color_changer_gui", "ClipboardController"),
    ("icon_changer_gui", "ClipboardController"),
)


class FakeMenu:
    def __init__(self, *_args, **_kwargs):
        self.labels = []

    def add_command(self, **kwargs):
        self.labels.append(kwargs.get("label", ""))

    def add_separator(self):
        self.labels.append(None)

    def entryconfigure(self, index, **kwargs):
        while len(self.labels) <= index:
            self.labels.append(None)
        self.labels[index] = kwargs.get("label", self.labels[index])


class FakeEntry:
    def __init__(self):
        self.value = "abcdef"
        self.selection = (1, 4)
        self.cursor = 0
        self.bindings = {}
        self.bind_count = {}

    def winfo_class(self):
        return "TEntry"

    def winfo_children(self):
        return []

    def bind(self, sequence, callback, add=None):
        self.bind_count[sequence] = self.bind_count.get(sequence, 0) + 1
        self.bindings[sequence] = callback

    def cget(self, name):
        assert name == "state"
        return "normal"

    def selection_present(self):
        return self.selection is not None

    def selection_range(self, start, end):
        end = len(self.value) if end == "end" else int(end)
        self.selection = (int(start), end)

    def icursor(self, position):
        self.cursor = len(self.value) if position == "end" else int(position)

    def focus_set(self):
        pass

    def get(self):
        return self.value

    def index(self, name):
        if name == "sel.first":
            return self.selection[0]
        if name == "sel.last":
            return self.selection[1]
        if name == "insert":
            return self.cursor
        raise AssertionError(name)

    def delete(self, _start, _end):
        start, end = self.selection
        self.value = self.value[:start] + self.value[end:]
        self.cursor = start
        self.selection = None

    def insert(self, _index, value):
        self.value = self.value[: self.cursor] + value + self.value[self.cursor :]
        self.cursor += len(value)


class FakeRoot:
    def __init__(self, *widgets):
        self.children = list(widgets)
        self.clipboard = ""

    def winfo_children(self):
        return list(self.children)

    def clipboard_clear(self):
        self.clipboard = ""

    def clipboard_append(self, value):
        self.clipboard = value

    def clipboard_get(self):
        return self.clipboard

    def update_idletasks(self):
        pass


def _load_controller(module_name, attribute):
    if module_name == "line_clipboard_support":
        spec = importlib.util.spec_from_file_location(
            module_name, ROOT / "line" / "clipboard_support.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_name)
    return module, getattr(module, attribute)


@pytest.mark.parametrize("module_name,attribute", CLIPBOARD_CONTROLLERS)
def test_clipboard_shortcuts_work_with_non_latin_keysyms(
    module_name, attribute, monkeypatch
):
    import tkinter

    module, controller_type = _load_controller(module_name, attribute)
    monkeypatch.setattr(tkinter, "Menu", FakeMenu)
    owner = importlib.import_module(controller_type.__module__)
    if hasattr(owner, "set_clipboard_text"):
        monkeypatch.setattr(
            owner,
            "set_clipboard_text",
            lambda root, value: root.clipboard_append(value),
        )
    if hasattr(owner, "get_clipboard_text"):
        monkeypatch.setattr(
            owner, "get_clipboard_text", lambda root: root.clipboard_get()
        )

    entry = FakeEntry()
    root = FakeRoot(entry)
    controller = controller_type(root)
    controller.install(root)

    def key(shortcut, keycode):
        return controller.handle_control_key(
            SimpleNamespace(widget=entry, keysym="??", char="ษ", keycode=keycode)
        )

    assert key("x", 88) == "break"
    assert root.clipboard == "bcd"
    assert entry.value == "aef"

    assert key("a", 65) == "break"
    assert entry.selection == (0, len(entry.value))

    assert key("c", 67) == "break"
    assert root.clipboard == "aef"

    root.clipboard = "paste works"
    assert key("v", 86) == "break"
    assert entry.value == "paste works"


def test_all_controller_context_menus_display_one_language(monkeypatch):
    import tkinter

    _, controller_type = _load_controller("clipboard_support", "ClipboardController")
    monkeypatch.setattr(tkinter, "Menu", FakeMenu)
    controller = controller_type(
        FakeRoot(),
        translate=lambda text: text.partition("|")[1] and text.partition("|")[2],
    )
    assert controller.menu.labels == ["ตัด", "คัดลอก", "วาง", None, "เลือกทั้งหมด"]


def test_web_status_clipboard_menu_has_localized_single_language_labels():
    import web_status_gui

    required = {
        "clipboard_cut",
        "clipboard_copy",
        "clipboard_paste",
        "clipboard_select_all",
    }
    assert required <= web_status_gui.WEB_STATUS_LANGUAGE_TEXT["en"].keys()
    assert required <= web_status_gui.WEB_STATUS_LANGUAGE_TEXT["th"].keys()
    assert all(
        "|" not in web_status_gui.WEB_STATUS_LANGUAGE_TEXT["th"][key]
        for key in required
    )


def test_web_status_ctrl_x_works_with_unknown_keysym(monkeypatch):
    import tkinter
    import web_status_gui

    monkeypatch.setattr(tkinter, "Menu", FakeMenu)
    copied = []
    monkeypatch.setattr(
        web_status_gui,
        "_write_clipboard_text",
        lambda _root, value: copied.append(value),
    )
    entry = FakeEntry()
    root = FakeRoot(entry)
    controller = web_status_gui.WebStatusClipboardController(
        root, translate=lambda key: web_status_gui.WEB_STATUS_LANGUAGE_TEXT["th"][key]
    )
    controller.install(root)
    assert controller.menu.labels == ["ตัด", "คัดลอก", "วาง", None, "เลือกทั้งหมด"]

    result = controller.handle_control_key(
        SimpleNamespace(widget=entry, keysym="??", char="ษ", keycode=88)
    )

    assert result == "break"
    assert copied == ["bcd"]
    assert entry.value == "aef"


def test_all_gui_files_use_web_status_light_palette():
    old_palette = (
        "#edf2f5",
        "#233548",
        "#087b83",
        "#d9e8f0",
        "#f4f7f7",
        "#a52834",
        "#138a55",
        "#1769c2",
        "#006d78",
        "#d72d32",
    )
    for relative in GUI_FILES:
        source = (
            (ROOT / relative).read_text(encoding="utf-8").lower()
            if not relative.startswith("src/")
            else (ROOT / relative).read_text(encoding="utf-8").lower()
        )
        assert "#f4f1e8" in source, relative
        assert "#8b672b" in source, relative
        for color in old_palette:
            assert color not in source, f"{relative} still contains {color}"


def test_all_gui_clipboard_controllers_bind_layout_independent_keys(monkeypatch):
    import tkinter

    monkeypatch.setattr(tkinter, "Menu", FakeMenu)
    modules = CLIPBOARD_CONTROLLERS + (
        ("line_clipboard_support", "ClipboardController"),
    )
    for module_name, attribute in modules:
        _module, controller_type = _load_controller(module_name, attribute)
        entry = FakeEntry()
        controller = controller_type(FakeRoot(entry))
        controller.install(controller.root)
        assert "<Control-KeyPress>" in entry.bindings, module_name
        assert (
            controller.handle_control_key(
                SimpleNamespace(widget=entry, keysym="??", char="ษ", keycode=65)
            )
            == "break"
        ), module_name
