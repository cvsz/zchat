"""Guard against update-check failures leaking from Windows GUI threads."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["status_random_gui.py", "status_marquee_gui.py"])
def test_auto_update_worker_handles_network_error(name):
    source = (ROOT / "src" / "features" / name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_auto_update_check")
    worker = next(node for node in ast.walk(method)
                  if isinstance(node, ast.FunctionDef) and node.name == "work")
    assert any(isinstance(node, ast.Try) and any(
        isinstance(handler.type, ast.Tuple) and any(
            isinstance(item, ast.Name) and item.id == "OSError"
            for item in handler.type.elts)
        for handler in node.handlers) for node in worker.body), (
        "Background update check must catch HTTPError (an OSError subclass)")



@pytest.mark.parametrize("module_name", ["status_random_gui", "status_marquee_gui"])
@pytest.mark.parametrize("payload", [b"[]", b'{"tag_name":"v1.2.3","assets":[null]}',
                                     b'{"tag_name":"v1.2.3","assets":{}}'])
def test_release_metadata_rejects_invalid_json_shapes(module_name, payload, monkeypatch):
    module = __import__(module_name)

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return payload

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(ValueError):
        module.github_latest_release()


@pytest.mark.parametrize("module_name", ["status_random_gui", "status_marquee_gui"])
def test_manual_update_worker_handles_rate_limit(module_name, monkeypatch):
    """Manual update's SECOND network request must not raise in the daemon worker."""
    import urllib.error
    import queue
    module = __import__(module_name)
    result = queue.Queue()

    class FakeRoot:
        def after(self, _delay, callback):
            callback()

    class FakeLabel:
        def configure(self, **kwargs):
            pass

    class FakeWindow:
        root = FakeRoot()
        note = FakeLabel()
        _tr = staticmethod(lambda en, th: en)
        _update_done = lambda self, state, message: result.put((state, message))

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.github.com/", 403,
                                     "rate limit exceeded", {}, None)
    monkeypatch.setattr(module, "self_update", fail)
    class SynchronousThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(module.threading, "Thread", SynchronousThread)
    src = (ROOT / "src" / "features" / (module_name + ".py")).read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "check_update")
    namespace = {}
    # Isolate this unit-test target by compiling its AST from repository source.
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<isolated-method>", "exec"),  # nosec B102
         {"threading": module.threading, "self_update": module.self_update,
          "logging": module.logging}, namespace)
    namespace["check_update"](FakeWindow())
    state, message = result.get_nowait()
    assert state == "failed"
    assert "unavailable" in message
