import json
import urllib.error
import pytest
from unittest.mock import MagicMock, patch

from src.camfrog.zgpu_client import ZGPUClient, ZGPUResponse


def test_zgpu_client_init():
    client = ZGPUClient(endpoint_url="http://localhost:9999/rpc", timeout=5.0, default_model="fast-llm")
    assert client.endpoint_url == "http://localhost:9999/rpc"
    assert client.timeout == 5.0
    assert client.default_model == "fast-llm"


def test_zgpu_call_rpc_success():
    client = ZGPUClient()
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"status": "ok", "gpu": "zGPU-1"}
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = client.call_rpc("system.health", {})
        assert res["result"]["status"] == "ok"
        assert res["result"]["gpu"] == "zGPU-1"


def test_zgpu_generate_success_dict():
    client = ZGPUClient()
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "text": "Hello from zGPU accelerated LLM!",
            "tokens_used": 42,
            "latency_ms": 12.5,
            "model": "zgpu-llama-3"
        }
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        resp = client.generate(prompt="Hi", model="zgpu-llama-3")
        assert resp.ok is True
        assert resp.text == "Hello from zGPU accelerated LLM!"
        assert resp.tokens_used == 42
        assert resp.latency_ms == 12.5
        assert resp.model == "zgpu-llama-3"


def test_zgpu_generate_success_str():
    client = ZGPUClient()
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": "Raw text response"
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        resp = client.generate(prompt="Hi")
        assert resp.ok is True
        assert resp.text == "Raw text response"


def test_zgpu_generate_rpc_error():
    client = ZGPUClient()
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32603, "message": "GPU OOM"}
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        resp = client.generate(prompt="Hi")
        assert resp.ok is False
        assert "GPU OOM" in resp.error


def test_zgpu_generate_network_error():
    client = ZGPUClient()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        resp = client.generate(prompt="Hi")
        assert resp.ok is False
        assert "Connection refused" in resp.error
        assert resp.latency_ms >= 0


def test_zgpu_health_check():
    client = ZGPUClient()
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "jsonrpc": "2.0",
        "id": 0,
        "result": {"healthy": True}
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        h = client.health_check()
        assert h["ok"] is True
        assert h["details"]["healthy"] is True

    with patch("urllib.request.urlopen", side_effect=Exception("Unreachable")):
        h_fail = client.health_check()
        assert h_fail["ok"] is False
        assert "Unreachable" in h_fail["error"]


from conftest import has_display


@pytest.mark.skipif(not has_display(), reason="needs tkinter + display")
def test_zgpu_tester_gui_headless(monkeypatch):
    import tkinter
    from src.features.zgpu_tester_gui import ZGPUTesterApp

    # Use hidden tk root
    root = tkinter.Tk()
    root.withdraw()
    app = ZGPUTesterApp(root)
    assert app.endpoint_var.get() == "http://127.0.0.1:8880/rpc"
    assert app.model_var.get() == "default"

    # Test client creation
    client = app._get_client()
    assert client.endpoint_url == "http://127.0.0.1:8880/rpc"

    root.destroy()
