"""Tests for standalone name-changer (GUI + CLI with Thai font/Unicode support)."""

import pytest
from src.features import name_changer_gui as ncg


def test_name_validation_allows_thai_and_english():
    assert ncg.validate_name("ผู้ใช้ camfrog") == "ผู้ใช้ camfrog"
    assert ncg.validate_name("Camfrog DJ") == "Camfrog DJ"
    assert ncg.validate_name("  สายคามฟร็อก  ") == "สายคามฟร็อก"
    with pytest.raises(ValueError, match="cannot be empty"):
        ncg.validate_name("   ")


def test_name_manager_cycling():
    thai_names = ["คนึงนิจ", "สายคามฟร็อก", "มังกรทอง"]
    manager = ncg.NameManager(thai_names)
    assert manager.current_name() == "คนึงนิจ"
    assert manager.switch_next() == "สายคามฟร็อก"
    assert manager.switch_next() == "มังกรทอง"
    assert manager.switch_next() == "คนึงนิจ"
    assert manager.switch_prev() == "มังกรทอง"


def test_name_manager_direct_switch():
    manager = ncg.NameManager(["คนึงนิจ", "มังกรทอง"])
    assert manager.switch_to("ดีเจเสียงใส") == "ดีเจเสียงใส"
    assert manager.current_name() == "ดีเจเสียงใส"
    assert "ดีเจเสียงใส" in manager.names


def test_name_manager_empty_pool_rejected():
    manager = ncg.NameManager(["ชื่อแรก"])
    with pytest.raises(ValueError, match="Must provide at least one valid name"):
        manager.set_names(["   ", ""])


def test_cli_set_name_thai(capsys):
    ret = ncg.main(["--set-name", "ผู้ใช้ไทยแท้"])
    assert ret == ncg.EXIT_OK
    out, _ = capsys.readouterr()
    assert "Name set to: ผู้ใช้ไทยแท้" in out


def test_cli_cycle_name(capsys):
    ret = ncg.main(["--names", "ชื่อหนึ่ง", "ชื่อสอง", "--cycle"])
    assert ret == ncg.EXIT_OK
    out, _ = capsys.readouterr()
    assert "Switched to next name: ชื่อสอง" in out


def test_name_changer_clipboard_controller(monkeypatch):
    from types import SimpleNamespace
    import tkinter
    from tests.test_gui_theme_clipboard import FakeEntry, FakeMenu, FakeRoot

    monkeypatch.setattr(tkinter, "Menu", FakeMenu)
    monkeypatch.setattr(
        ncg, "set_clipboard_text", lambda root, value: root.clipboard_append(value)
    )
    monkeypatch.setattr(ncg, "get_clipboard_text", lambda root: root.clipboard_get())
    entry = FakeEntry()
    root = FakeRoot(entry)
    controller = ncg.ClipboardController(root)
    controller.install(root)

    assert "<Control-KeyPress>" in entry.bindings
    assert "<Control-c>" in entry.bindings
    assert "<Control-v>" in entry.bindings
    assert "<Control-x>" in entry.bindings
    assert "<Control-a>" in entry.bindings

    # Test Ctrl+X
    assert (
        controller.handle_control_key(
            SimpleNamespace(widget=entry, keysym="??", char="ษ", keycode=88)
        )
        == "break"
    )
    assert root.clipboard == "bcd"
    assert entry.value == "aef"

    # Test Ctrl+A then Ctrl+V
    assert (
        controller.handle_control_key(
            SimpleNamespace(widget=entry, keysym="??", char="ฟ", keycode=65)
        )
        == "break"
    )
    assert entry.selection == (0, len(entry.value))

    root.clipboard = "ทดสอบไทย"
    assert (
        controller.handle_control_key(
            SimpleNamespace(widget=entry, keysym="??", char="อ", keycode=86)
        )
        == "break"
    )
    assert entry.value == "ทดสอบไทย"
