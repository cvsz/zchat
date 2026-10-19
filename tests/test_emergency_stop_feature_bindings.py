from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FEATURE_BINDINGS = {
    "camfrog_status_gui.py": "camfrog.status",
    "status_changer_gui.py": "camfrog.status-changer",
    "status_random_gui.py": "camfrog.status-random",
    "status_marquee_gui.py": "camfrog.status-marquee",
}


@pytest.mark.parametrize(("filename", "feature_id"), FEATURE_BINDINGS.items())
def test_status_gui_executor_uses_its_feature_emergency_stop_scope(
    filename, feature_id
):
    source = ROOT / "src" / "features" / filename
    tree = ast.parse(source.read_text(encoding="utf-8"))
    executor_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "BackgroundStatusExecutor"
    ]

    assert any(
        any(
            keyword.arg == "emergency_scope"
            and isinstance(keyword.value, ast.Attribute)
            and keyword.value.attr == "FEATURE"
            for keyword in call.keywords
        )
        and any(
            keyword.arg == "emergency_resource_key"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == feature_id
            for keyword in call.keywords
        )
        for call in executor_calls
    )
