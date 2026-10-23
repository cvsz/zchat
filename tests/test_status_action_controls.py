"""Regression coverage for four status GUI controls (offline, no Camfrog needed)."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_combined_status_save_data_button_does_not_send_a_status():
    source = (ROOT / "src" / "features" / "status_changer_gui.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    actions = next(node for node in methods if node.name == "_build_actions")
    action_source = ast.get_source_segment(source, actions)
    assert '"2. Save Data"' in action_source
    assert "self.save_data" in action_source
    assert "self.apply_now" not in action_source

    save_data = next(node for node in methods if node.name == "save_data")
    save_source = ast.get_source_segment(source, save_data)
    assert "self.save_settings()" in save_source
    for sender in (
        "apply_status_scrolling",
        "apply_status_text",
        "cmd_status",
        "commit(",
        "run_task(",
    ):
        assert sender not in save_source
    assert not any(node.name == "apply_now" for node in methods)


@pytest.mark.parametrize("name", ["status_random_gui.py", "status_marquee_gui.py"])
def test_four_button_handlers_are_present_and_ordered(name):
    source = (ROOT / "src" / "features" / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    buttons = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_build_actions"
    )
    rendered = ast.get_source_segment(source, buttons)
    assert rendered.index("1. Discovery") < rendered.index("2. Apply")
    assert rendered.index("2. Apply") < rendered.index("3. Start")
    assert rendered.index("3. Start") < rendered.index("4. Stop")
    for handler in ("self.discover", "self.apply_now", "self.set_enabled"):
        assert handler in rendered


@pytest.mark.parametrize("name", ["status_random_gui.py", "status_marquee_gui.py"])
def test_stop_is_not_blocked_by_unsaved_invalid_form_data(name):
    source = (ROOT / "src" / "features" / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "set_enabled"
    )
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        for node in function.body
    )
    # STOP branch occurs before form validation/save and immediately returns.
    first_stop = next(
        node
        for node in function.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "enabled"
    )
    assert isinstance(first_stop.body[-1], ast.Return)
    assert "save_settings" not in ast.get_source_segment(source, first_stop)
    assert "cmd_stop" in source


@pytest.mark.parametrize("name", ["status_random_gui.py", "status_marquee_gui.py"])
def test_action_handlers_guard_busy_state(name):
    source = (ROOT / "src" / "features" / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for handler in ("apply_now", "discover"):
        f = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == handler
        )
        assert any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Attribute)
            and node.test.attr == "busy"
            for node in f.body[:3]
        ), handler
    set_enabled = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "set_enabled"
    )
    if name == "status_marquee_gui.py":
        # Stop is emergency control and must bypass busy; Start remains guarded.
        stop_branch = next(
            index
            for index, node in enumerate(set_enabled.body)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
        )
        start_guard = next(
            index
            for index, node in enumerate(set_enabled.body)
            if index > stop_branch
            and isinstance(node, ast.If)
            and any(
                isinstance(item, ast.Attribute) and item.attr == "busy"
                for item in ast.walk(node.test)
            )
        )
        assert start_guard > stop_branch
    else:
        assert any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Attribute)
            and node.test.attr == "busy"
            for node in set_enabled.body[:3]
        ), "set_enabled"


@pytest.mark.parametrize("name", ["status_random_gui.py", "status_marquee_gui.py"])
def test_action_grid_has_one_row_and_distinct_button_colours(name):
    source = (ROOT / "src" / "features" / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_build_actions"
    )
    body = ast.get_source_segment(source, method)
    assert "tk.Button(" in body
    assert "for col in range(4):" in body
    # One-row grid: all four buttons share row=0 and use their column index.
    assert "button.grid(" in body
    assert "row=0" in body
    assert "column=index" in body
    assert all(
        colour in body for colour in ("#ebe5d9", "#d6e9e8", "#8b672b", "#9f3740")
    )
    assert "command=command" in body
    assert "highlightthickness=2" in body
    assert 'highlightcolor="#283648"' in body
