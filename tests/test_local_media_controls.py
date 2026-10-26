"""Local Web Status integrations for AutoDJ and vMix."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_status_gui as wsg  # noqa: E402


def test_music_dj_control_request_uses_protocol_and_cleans_up(tmp_path):
    executable = tmp_path / "AutoDJ.exe"

    def simulate_worker(_delay):
        request_path = next((tmp_path / "dj-control").glob("*.request.json"))
        request = json.loads(request_path.read_text(encoding="utf-8"))
        response = {
            "protocol": wsg.DJ_CONTROL_PROTOCOL,
            "request_id": request["request_id"],
            "ok": True,
            "current": {"title": "Song", "user": "Ann"},
            "queue": [],
        }
        (tmp_path / "dj-control" /
         (request["request_id"] + ".response.json")).write_text(
            json.dumps(response), encoding="utf-8")

    result = wsg.music_dj_control_request(
        executable, "status", timeout=1, sleep=simulate_worker)

    assert result["current"] == {"title": "Song", "user": "Ann"}
    assert list((tmp_path / "dj-control").iterdir()) == []


def test_music_dj_control_request_sends_bounded_voice_text(tmp_path):
    executable = tmp_path / "AutoDJ.exe"
    seen = {}

    def simulate_worker(_delay):
        request_path = next((tmp_path / "dj-control").glob("*.request.json"))
        request = json.loads(request_path.read_text(encoding="utf-8"))
        seen.update(request)
        response = {
            "protocol": wsg.DJ_CONTROL_PROTOCOL,
            "request_id": request["request_id"],
            "ok": True,
            "current": None,
            "queue": [],
        }
        (tmp_path / "dj-control" /
         (request["request_id"] + ".response.json")).write_text(
            json.dumps(response), encoding="utf-8")

    result = wsg.music_dj_control_request(
        executable, "speak", timeout=1, sleep=simulate_worker, text="สวัสดีครับ")

    assert result["ok"] is True
    assert seen["action"] == "speak"
    assert seen["text"] == "สวัสดีครับ"
    assert list((tmp_path / "dj-control").iterdir()) == []
    with pytest.raises(ValueError, match="240 printable"):
        wsg.music_dj_control_request(executable, "speak", text="x" * 241)


def test_music_dj_voice_summary_includes_requester_time_and_next_song():
    summary = wsg.music_dj_speech_summary({
        "current": {"title": "Current song", "user": "Ann"},
        "queue": [{"title": "Next song", "user": "Bob"}],
        "updated_at": 1000,
        "playback": {"elapsed_seconds": 75, "duration_seconds": 180},
    }, now=1000)
    assert "Current song" in summary
    assert "requested by Ann" in summary
    assert "playback 1:15 of 3:00" in summary
    assert "Next song, requested by Bob" in summary
    assert len(summary) <= 240
    with pytest.raises(ValueError, match="no current song"):
        wsg.music_dj_speech_summary({"current": None})


def test_music_dj_control_refuses_unknown_actions_and_wrong_executables(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        wsg.music_dj_control_request(tmp_path / "AutoDJ.exe", "restart")
    with pytest.raises(ValueError, match="packaged AutoDJ.exe"):
        wsg.music_dj_control_request(tmp_path / "other.exe", "status")


def test_music_dj_state_requires_a_fresh_valid_snapshot(tmp_path, monkeypatch):
    executable = tmp_path / "AutoDJ.exe"
    state_dir = tmp_path / "dj-control"
    state_dir.mkdir()
    state = {
        "protocol": wsg.DJ_CONTROL_PROTOCOL,
        "running": True,
        "current": None,
        "queue": [],
        "updated_at": time.time(),
    }
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    assert wsg.music_dj_worker_state(executable) == state
    state["updated_at"] -= 30
    (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert wsg.music_dj_worker_state(executable) is None


@pytest.mark.parametrize("address", [
    "http://127.0.0.1:8088",
    "http://localhost:8088/api/",
    "https://[::1]:8443/",
])
def test_vmix_api_origin_accepts_only_local_origins(address):
    assert wsg.normalize_vmix_api_url(address).endswith("/api/")


@pytest.mark.parametrize("address", [
    "http://192.168.1.20:8088",
    "http://vmix.example:8088",
    "http://user:password@127.0.0.1:8088",
    "http://127.0.0.1:8088/?Function=CutDirect",
])
def test_vmix_api_origin_rejects_remote_hosts_and_embedded_parameters(address):
    with pytest.raises(ValueError):
        wsg.normalize_vmix_api_url(address)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self.payload


def test_fetch_vmix_inputs_parses_only_valid_input_records(monkeypatch):
    response = (b'<vmix><inputs>'
                b'<input key="00000000-0000-0000-0000-000000000001" '
                b'number="1" title="Camera 1" type="VideoCapture" />'
                b'<input key="bad" number="x" title="bad" />'
                b'</inputs></vmix>')
    requested = []

    class _Opener:
        def open(self, request, timeout):
            requested.append((request.full_url, timeout))
            return _Response(response)

    monkeypatch.setattr(wsg.urllib.request, "build_opener",
                        lambda *_handlers: _Opener())
    inputs = wsg.fetch_vmix_inputs("http://127.0.0.1:8088")

    assert inputs == [{
        "key": "00000000-0000-0000-0000-000000000001",
        "number": 1,
        "title": "Camera 1",
        "type": "VideoCapture",
    }]
    assert requested[0][0] == "http://127.0.0.1:8088/api/"


def test_fetch_vmix_inputs_rejects_dtd_and_entities(monkeypatch):
    response = (
        b'<!DOCTYPE vmix [<!ENTITY data SYSTEM "file:///etc/passwd">]>'
        b"<vmix><inputs>&data;</inputs></vmix>"
    )
    monkeypatch.setattr(wsg, "vmix_api_request", lambda *args, **kwargs: response)

    with pytest.raises(RuntimeError, match="vMix returned invalid XML"):
        wsg.fetch_vmix_inputs("http://127.0.0.1:8088")
