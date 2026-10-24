"""Ensure process-tree shutdown cannot resolve taskkill from a writable PATH entry."""

import ast
import os
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIRS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
}


def _raise_walk_error(error: OSError) -> None:
    raise error


def iter_python_sources(root: Path = ROOT) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(
        root, topdown=True, onerror=_raise_walk_error
    ):
        dirnames[:] = sorted(name for name in dirnames if name not in GENERATED_DIRS)
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield Path(dirpath) / filename


def test_source_scan_prunes_generated_directories(tmp_path):
    source_file = tmp_path / "source.py"
    source_file.write_text("pass\n", encoding="utf-8")
    for directory in GENERATED_DIRS:
        generated_dir = tmp_path / directory
        generated_dir.mkdir()
        (generated_dir / "ignored.py").write_text("pass\n", encoding="utf-8")

    assert list(iter_python_sources(tmp_path)) == [source_file]


def test_taskkill_invocations_use_windows_system_directory_helper():
    taskkill_calls = []
    bare_calls = []
    for source_path in iter_python_sources():
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "run" or not node.args or not isinstance(node.args[0], ast.List):
                continue
            argv = node.args[0].elts
            if not argv:
                continue
            first = argv[0]
            if isinstance(first, ast.Constant) and first.value in {"taskkill", "taskkill.exe"}:
                bare_calls.append(f"{source_path.relative_to(ROOT)}:{node.lineno}")
            elif (
                isinstance(first, ast.Call)
                and isinstance(first.func, ast.Name)
                and first.func.id == "system_taskkill_path"
            ):
                taskkill_calls.append(source_path)

    assert not bare_calls, f"taskkill must use its absolute Windows system path: {bare_calls}"
    assert len(taskkill_calls) >= 12
