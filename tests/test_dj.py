"""Offline DJ tests: queue engine, commands, validation, music profile."""
import copy
import base64
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camfrog_music_gui as c  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    x = c.load_cfg(ROOT / "config.example.json")
    x["dry_run"] = True
    x["status"]["enabled"] = False
    x["autoreply"]["enabled"] = True
    x["autoreply"]["own_nickname"] = "DJBot"
    x["autoreply"]["rules"] = []
    x["dj"]["enabled"] = True
    return x


@pytest.fixture(autouse=True)
def _base(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "BASE", tmp_path)
    c.LANG = "en"
    yield
    c.LANG = "en"


def _dj_runner(cfg, monkeypatch, lines):
    r = c.Runner(cfg)
    r.history, r.input, r.chat_win = object(), object(), object()
    sent = []
    monkeypatch.setattr(r, "send", lambda ctrl, text, btn=None, win=None: sent.append(text) or True)
    monkeypatch.setattr(c, "read_chat", lambda hist, tail: "\n".join(lines))
    return r, sent


def test_dj_request_queues_and_persists(cfg, monkeypatch, tmp_path):
    r, sent = _dj_runner(cfg, monkeypatch, ["Ann: !request hello world"])
    r.do_dj(True)
    assert any("Now playing" in s and "hello world" in s for s in sent)
    raw = json.loads((tmp_path / "dj_queue.json").read_text(encoding="utf-8"))
    assert raw["current"] == {"title": "hello world", "user": "Ann"}
    r2, sent2 = _dj_runner(cfg, monkeypatch, ["Bob: !request second song"])
    r2.dj_prev = []
    r2.do_dj(True)
    assert any("Queued #1" in s for s in sent2)


def test_dj_first_request_starts_playing_immediately(cfg, monkeypatch):
    r, sent = _dj_runner(cfg, monkeypatch, ["Ann: !request song a"])
    r.do_dj(True)
    assert any("Now playing" in s for s in sent)


def test_dj_caps_and_duplicates(cfg, monkeypatch):
    r, sent = _dj_runner(cfg, monkeypatch, [
        "Ann: !request one", "Ann: !request two", "Ann: !request three",
        "Ann: !request four"])
    r.do_dj(True)
    assert len([s for s in sent if "Queued" in s or "Now playing" in s]) == 4
    r2, sent2 = _dj_runner(cfg, monkeypatch, ["Ann: !request five"])
    r2.dj_prev = []
    # same persisted queue file: reload state then exceed cap
    r2.do_dj(True)
    assert any("already has 3 songs" in s for s in sent2)
    r3, sent3 = _dj_runner(cfg, monkeypatch, ["Bob: !request ONE"])
    r3.dj_prev = []
    r3.do_dj(True)
    assert any("playing now" in s for s in sent3)
    r4, sent4 = _dj_runner(cfg, monkeypatch, ["Bob: !request"])
    r4.dj_prev = []
    r4.do_dj(True)
    assert any("give a song name" in s for s in sent4)


def test_dj_queue_current_help(cfg, monkeypatch):
    r, sent = _dj_runner(cfg, monkeypatch, ["Ann: !request song a"])
    r.do_dj(True)
    r2, sent2 = _dj_runner(cfg, monkeypatch, ["Bob: !queue", "Bob: !current", "Bob: !help"])
    r2.dj_prev = []
    r2.do_dj(True)
    assert any("queue is empty" in s for s in sent2)
    assert any("current: song a" in s for s in sent2)
    assert any("!request" in s for s in sent2)


def test_dj_skip_rules(cfg, monkeypatch):
    r, sent = _dj_runner(cfg, monkeypatch,
                         ["Ann: !request song a", "Bob: !request song b"])
    r.do_dj(True)
    stranger, stranger_sent = _dj_runner(cfg, monkeypatch, ["Zed: !skip"])
    stranger.dj_prev = []
    stranger.do_dj(True)
    assert any("only the requester" in s for s in stranger_sent)
    owner, owner_sent = _dj_runner(cfg, monkeypatch, ["DJBot: !skip"])
    owner.dj_prev = []
    owner.do_dj(True)  # own nick lines are never acted on
    assert owner_sent == []
    req, req_sent = _dj_runner(cfg, monkeypatch, ["Ann: !skip"])
    req.dj_prev = []
    req.do_dj(True)
    assert any("Now playing: song b" in s for s in req_sent)


def test_dj_ignores_plain_lines_and_unknown_commands(cfg, monkeypatch):
    r, sent = _dj_runner(cfg, monkeypatch, ["Ann: hello there", "Bob: !dance"])
    r.do_dj(True)
    assert sent == []


def test_dj_does_not_run_when_disabled_or_idle(cfg, monkeypatch):
    cfg["dj"]["enabled"] = False
    r, sent = _dj_runner(cfg, monkeypatch, ["Ann: !request x"])
    r.do_dj(True)
    assert sent == []
    cfg["dj"]["enabled"] = True
    r2, sent2 = _dj_runner(cfg, monkeypatch, ["Ann: !request x"])
    r2.do_dj(False)
    assert sent2 == []


def test_do_chat_yields_dj_lines_to_dj(cfg, monkeypatch):
    cfg["autoreply"]["rules"] = [{"pattern": "!request", "reply": "ROOM"}]
    r = c.Runner(cfg)
    r.history, r.input, r.chat_win = object(), object(), object()
    sent = []
    monkeypatch.setattr(r, "send", lambda ctrl, text, btn=None, win=None: sent.append(text) or True)
    monkeypatch.setattr(c, "read_chat", lambda hist, tail: "Ann: !request song a")
    r.do_chat(True)
    assert "ROOM" not in sent


def test_dj_local_backend_needs_mp3_file(cfg, monkeypatch, tmp_path):
    cfg["dj"]["audio_backend"] = "local"
    music = tmp_path / "music"
    music.mkdir()
    (music / "mysong.mp3").write_bytes(b"id3")
    cfg["dj"]["music_dir"] = str(music)
    r, sent = _dj_runner(cfg, monkeypatch, ["Ann: !request nosuch"])
    r.do_dj(True)
    assert any("no supported audio file found" in s for s in sent)
    r2, sent2 = _dj_runner(cfg, monkeypatch, ["Ann: !request mysong"])
    r2.dj_prev = []
    r2.do_dj(True)
    assert any("Now playing: mysong" in s for s in sent2)


def test_dj_audio_duration_reads_mci_length(tmp_path, monkeypatch):
    commands = []

    def fake_mci(command):
        commands.append(command)
        return "1250" if command.endswith("length") else ""

    monkeypatch.setattr(c, "_dj_windows_audio", lambda: True)
    monkeypatch.setattr(c, "_dj_mci_command", fake_mci)
    path = tmp_path / "My Song.mp3"

    assert c.dj_audio_duration(path) == 1.25
    assert any(command.startswith('open "') and " alias " in command
               and " type " not in command for command in commands)
    assert any("time format milliseconds" in command for command in commands)
    assert commands[-1].startswith("close ")


def test_dj_mci_reads_wav_duration_with_mocked_backend(tmp_path, monkeypatch):
    import wave

    song = tmp_path / "MCI audio test.wav"
    with wave.open(str(song), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 800)

    commands = []

    def fake_mci(command):
        commands.append(command)
        return "100" if command.endswith("length") else ""

    monkeypatch.setattr(c, "_dj_windows_audio", lambda: True)
    monkeypatch.setattr(c, "_dj_mci_command", fake_mci)

    duration = c.dj_audio_duration(song)
    assert duration == pytest.approx(0.1, abs=0.02)
    assert commands[0].startswith('open "') and str(song.resolve()) in commands[0]
    assert any("time format milliseconds" in command for command in commands)
    assert commands[-1].startswith("close ")


@pytest.mark.parametrize("extension", ["wav", "mp3", "flac", "mid"])
def test_dj_playback_uses_mci_auto_type_and_closes_previous_track(
        extension, tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(c, "_dj_windows_audio", lambda: True)
    monkeypatch.setattr(c, "_dj_mci_command", lambda command: commands.append(command) or "")
    path = tmp_path / "Music Folder" / f"My Song.{extension}"

    assert c.dj_play(path) is True
    assert any(command.startswith("close ") for command in commands)
    assert any(command.startswith('open "') and f"My Song.{extension}" in command
               and " alias " in command and " type " not in command
               for command in commands)
    assert any(command.startswith("play ") for command in commands)
    c.dj_stop()
    assert commands[-2].startswith("stop ")
    assert commands[-1].startswith("close ")


def test_dj_local_request_plays_matching_mp3(cfg, monkeypatch, tmp_path):
    cfg["dry_run"] = False
    cfg["dj"]["audio_backend"] = "local"
    music = tmp_path / "music"
    music.mkdir()
    song = music / "requested song.mp3"
    song.write_bytes(b"id3")
    cfg["dj"]["music_dir"] = str(music)
    played = []
    monkeypatch.setattr(c, "dj_audio_duration", lambda path: 42.0)
    monkeypatch.setattr(c, "dj_play", lambda path: played.append(path) or True)
    runner, sent = _dj_runner(cfg, monkeypatch, ["Ann: !request requested song"])

    runner.do_dj(True)

    assert played == [song]
    assert runner.dj_length == 42.0
    assert any("Now playing: requested song" in line for line in sent)


def test_dj_failed_initial_playback_does_not_block_later_request(cfg, monkeypatch, tmp_path):
    cfg["dry_run"] = False
    cfg["dj"]["audio_backend"] = "local"
    music = tmp_path / "music"
    music.mkdir()
    broken, good = music / "broken.mp3", music / "good.mp3"
    broken.write_bytes(b"broken")
    good.write_bytes(b"good")
    cfg["dj"]["music_dir"] = str(music)
    monkeypatch.setattr(c, "dj_find_song",
                        lambda _directory, title: music / (title + ".mp3"))
    played = []

    def play(path):
        played.append(path)
        return path == good

    monkeypatch.setattr(c, "dj_play", play)
    monkeypatch.setattr(c, "dj_audio_duration", lambda _path: 60.0)
    runner, sent = _dj_runner(cfg, monkeypatch, [
        "Ann: !request broken", "Bob: !request good"])

    runner.do_dj(True)

    saved = json.loads((tmp_path / "dj_queue.json").read_text(encoding="utf-8"))
    assert played == [broken, good]
    assert saved["current"] == {"title": "good", "user": "Bob"}
    assert saved["queue"] == []
    assert any("could not start local audio" in line for line in sent)
    assert any("Now playing: good" in line for line in sent)


def test_dj_auto_advance_discards_failed_queue_entry_and_tries_next(cfg, monkeypatch, tmp_path):
    cfg["dry_run"] = False
    cfg["dj"]["audio_backend"] = "local"
    music = tmp_path / "music"
    music.mkdir()
    broken, queued = music / "broken.mp3", music / "queued.mp3"
    broken.write_bytes(b"broken")
    queued.write_bytes(b"queued")
    cfg["dj"]["music_dir"] = str(music)
    monkeypatch.setattr(c, "dj_find_song",
                        lambda _directory, title: music / (title + ".mp3"))
    played = []

    def play(path):
        played.append(path)
        return path == queued

    monkeypatch.setattr(c, "dj_play", play)
    monkeypatch.setattr(c, "dj_audio_duration", lambda _path: 60.0)
    runner, sent = _dj_runner(cfg, monkeypatch, [])
    queue = c.DJQueue(tmp_path / "dj_queue.json")
    queue.queue = [
        {"title": "broken", "user": "Ann"},
        {"title": "queued", "user": "Bob"},
    ]

    assert runner.dj_auto_advance(queue, cfg["dj"]) is True
    assert queue.current is None
    assert runner.dj_auto_advance(queue, cfg["dj"]) is True

    assert played == [broken, queued]
    assert queue.current == {"title": "queued", "user": "Bob"}
    assert queue.queue == []
    assert any("could not start local audio" in line for line in sent)
    assert any("Now playing: queued" in line for line in sent)


def test_dj_finished_track_skips_unplayable_next_entry(cfg, monkeypatch, tmp_path):
    cfg["dry_run"] = False
    cfg["dj"]["audio_backend"] = "local"
    music = tmp_path / "music"
    music.mkdir()
    broken, queued = music / "broken.mp3", music / "queued.mp3"
    broken.write_bytes(b"broken")
    queued.write_bytes(b"queued")
    cfg["dj"]["music_dir"] = str(music)
    monkeypatch.setattr(c, "dj_find_song",
                        lambda _directory, title: music / (title + ".mp3"))
    played = []

    def play(path):
        played.append(path)
        return path == queued

    monkeypatch.setattr(c, "dj_play", play)
    monkeypatch.setattr(c, "dj_audio_duration", lambda _path: 60.0)
    runner, sent = _dj_runner(cfg, monkeypatch, [])
    runner.dj_started = time.monotonic() - 120
    runner.dj_length = 60.0
    queue = c.DJQueue(tmp_path / "dj_queue.json")
    queue.current = {"title": "finished", "user": "Ada"}
    queue.queue = [
        {"title": "broken", "user": "Ann"},
        {"title": "queued", "user": "Bob"},
    ]

    assert runner.dj_auto_advance(queue, cfg["dj"]) is True
    assert queue.current is None
    assert runner.dj_auto_advance(queue, cfg["dj"]) is True

    assert played == [broken, queued]
    assert queue.current == {"title": "queued", "user": "Bob"}
    assert queue.queue == []
    assert any("could not start local audio" in line for line in sent)
    assert any("Now playing: queued" in line for line in sent)


def test_dj_auto_advance_moves_on_after_duration(cfg, monkeypatch):
    r, sent = _dj_runner(cfg, monkeypatch, ["Ann: !request a", "Bob: !request b"])
    r.do_dj(True)
    assert r.dj_started == 0.0  # dry-run never arms audio timers
    r.dj_started = time.monotonic() - 60
    r.dj_length = 30.0
    r2, sent2 = _dj_runner(cfg, monkeypatch, [])
    # carry runtime state across instances like consecutive ticks would
    r2.dj_started, r2.dj_length, r2.dj_prev = r.dj_started, r.dj_length, []
    r2.do_dj(True)
    assert any("Now playing: b" in s for s in sent2)


def test_dj_auto_advance_uses_mci_mode_when_duration_is_unavailable(
        cfg, monkeypatch, tmp_path):
    cfg["dry_run"] = False
    cfg["dj"]["audio_backend"] = "local"
    runner, sent = _dj_runner(cfg, monkeypatch, [])
    queue = c.DJQueue(tmp_path / "queue.json")
    queue.current = {"title": "first", "user": "Ann"}
    queue.queue = [{"title": "next", "user": "Bob"}]
    runner.dj_started = time.monotonic() - 30
    runner.dj_length = 0.0
    runner.dj_start_playback = lambda *_args: True
    mode = {"value": "playing"}
    monkeypatch.setattr(c, "dj_audio_mode", lambda: mode["value"])

    assert runner.dj_auto_advance(queue, cfg["dj"]) is False
    assert queue.current["title"] == "first"

    mode["value"] = "stopped"
    assert runner.dj_auto_advance(queue, cfg["dj"]) is True
    assert queue.current["title"] == "next"
    assert any("Now playing: next" in line for line in sent)


def test_dj_find_song_matching(tmp_path):
    music = tmp_path / "m"
    music.mkdir()
    (music / "Hello World.MP3").write_bytes(b"id3")
    (music / "other.mp3").write_bytes(b"id3")
    assert c.dj_find_song(music, "hello").name == "Hello World.MP3"
    assert c.dj_find_song(music, "missing") is None
    assert c.dj_find_song(tmp_path / "nodir", "x") is None


def test_dj_find_song_matches_supported_audio_files(tmp_path):
    music = tmp_path / "audio"
    music.mkdir()
    (music / "not audio.txt").write_text("not audio", encoding="utf-8")

    for extension in c.DJ_AUDIO_EXTENSIONS:
        for old_file in music.glob("Hello World.*"):
            old_file.unlink()
        expected = music / f"Hello World{extension}"
        expected.write_bytes(b"audio")
        assert c.dj_find_song(music, "hello") == expected
    assert c.dj_find_song(music, "not audio") is None


def test_dj_local_play_failure_does_not_announce_now_playing(cfg, monkeypatch, tmp_path):
    cfg["dry_run"] = False
    cfg["dj"]["audio_backend"] = "local"
    music = tmp_path / "music"
    music.mkdir()
    (music / "silent failure.mp3").write_bytes(b"audio")
    cfg["dj"]["music_dir"] = str(music)
    monkeypatch.setattr(c, "dj_audio_duration", lambda _path: None)
    monkeypatch.setattr(c, "dj_play", lambda _path: False)
    runner, sent = _dj_runner(cfg, monkeypatch, ["Ann: !request silent failure"])

    runner.do_dj(True)

    assert any("could not start local audio" in line for line in sent)
    assert not any("Now playing" in line for line in sent)


def test_dj_validation(cfg):
    cfg["autoreply"]["enabled"] = False
    errs, _ = c.validate(cfg)
    assert any("autoreply.enabled" in e for e in errs)
    cfg["autoreply"]["enabled"] = True
    for bad in ({"prefix": ""}, {"prefix": "/x"}, {"max_per_user": 0},
                {"audio_backend": "mp3"}, {"announce_now": ""}, {"queue_file": ""}):
        bad_cfg = copy.deepcopy(cfg)
        bad_cfg["dj"].update(bad)
        assert c.validate(bad_cfg)[0], bad
    assert c.validate(cfg)[0] == []


def test_music_profile_disables_status_and_im(monkeypatch):
    monkeypatch.setattr(c, "APP_PROFILE", "music")
    out = c.apply_app_profile(copy.deepcopy(c.DEFAULTS))
    assert out["status"]["enabled"] is False
    assert out["autoreply_im"]["enabled"] is False
    assert out["autoreply"]["enabled"] is True
    assert "run" in c.PROFILE_COMMANDS["music"]
    assert "marquee" not in c.PROFILE_COMMANDS["music"]


def test_dj_queue_persist_roundtrip(tmp_path):
    q = c.DJQueue(tmp_path / "q.json")
    q.load()
    assert q.current is None and q.queue == []
    assert q.add("Song", "Ann") == 1
    q.save()
    q2 = c.DJQueue(tmp_path / "q.json")
    q2.load()
    assert q2.queue == [{"title": "Song", "user": "Ann"}]


def _write_dj_control_request(tmp_path, action, request_id, created_at, text=None):
    directory = c.dj_control_directory(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    path = c.dj_control_request_path(request_id, tmp_path)
    payload = {
        "protocol": c.DJ_CONTROL_PROTOCOL,
        "request_id": request_id,
        "action": action,
        "created_at": created_at,
    }
    if text is not None:
        payload["text"] = text
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, directory / (request_id + ".response.json")


def test_dj_control_skip_is_processed_by_owner_loop(cfg, tmp_path, monkeypatch):
    queue = c.DJQueue(tmp_path / "dj_queue.json")
    queue.current = {"title": "First", "user": "Ann"}
    queue.queue = [{"title": "Next", "user": "Bob"}]
    assert queue.save()
    stopped = []
    monkeypatch.setattr(c, "dj_stop", lambda: stopped.append(True))
    request, response = _write_dj_control_request(
        tmp_path, "skip", "a" * 32, time.time())

    assert c.process_dj_control_request(cfg, base=tmp_path) == 1

    saved = json.loads((tmp_path / "dj_queue.json").read_text(encoding="utf-8"))
    result = json.loads(response.read_text(encoding="utf-8"))
    assert saved["current"] == {"title": "Next", "user": "Bob"}
    assert saved["queue"] == []
    assert result["ok"] is True and result["current"] == saved["current"]
    assert stopped == [True]
    assert not request.exists()


def test_dj_control_clear_stops_audio_and_persists_empty_queue(cfg, tmp_path, monkeypatch):
    queue = c.DJQueue(tmp_path / "dj_queue.json")
    queue.current = {"title": "First", "user": "Ann"}
    queue.queue = [{"title": "Next", "user": "Bob"}]
    assert queue.save()
    stopped = []
    monkeypatch.setattr(c, "dj_stop", lambda: stopped.append(True))
    _, response = _write_dj_control_request(
        tmp_path, "clear", "b" * 32, time.time())

    c.process_dj_control_request(cfg, base=tmp_path)

    saved = json.loads((tmp_path / "dj_queue.json").read_text(encoding="utf-8"))
    result = json.loads(response.read_text(encoding="utf-8"))
    assert saved == {"current": None, "queue": []}
    assert result["ok"] is True
    assert stopped == [True]


def test_dj_control_speak_starts_bounded_local_voice(cfg, tmp_path, monkeypatch):
    spoken = []
    monkeypatch.setattr(c, "start_room_speech", lambda text: spoken.append(text))
    _, response = _write_dj_control_request(
        tmp_path, "speak", "e" * 32, time.time(), text="Now playing a song")
    assert c.process_dj_control_request(
        cfg, base=tmp_path, runner=SimpleNamespace()) == 1
    result = json.loads(response.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["action"] == "speak"
    assert spoken == ["Now playing a song"]

    _, bad_response = _write_dj_control_request(
        tmp_path, "speak", "f" * 32, time.time(), text="x" * 241)
    c.process_dj_control_request(cfg, base=tmp_path, runner=SimpleNamespace())
    bad_result = json.loads(bad_response.read_text(encoding="utf-8"))
    assert bad_result["ok"] is False
    assert "240 printable" in bad_result["error"]


def test_dj_control_skip_refuses_when_dj_is_disabled(cfg, tmp_path):
    cfg["dj"]["enabled"] = False
    queue = c.DJQueue(tmp_path / "dj_queue.json")
    queue.current = {"title": "First", "user": "Ann"}
    assert queue.save()
    _, response = _write_dj_control_request(
        tmp_path, "skip", "d" * 32, time.time())

    c.process_dj_control_request(cfg, base=tmp_path)

    result = json.loads(response.read_text(encoding="utf-8"))
    saved = json.loads((tmp_path / "dj_queue.json").read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "disabled" in result["error"]
    assert saved["current"] == {"title": "First", "user": "Ann"}


def test_dj_control_expired_request_is_rejected(cfg, tmp_path):
    _, response = _write_dj_control_request(
        tmp_path, "clear", "c" * 32,
        time.time() - c.DJ_CONTROL_REQUEST_TTL_SECONDS - 1)

    c.process_dj_control_request(cfg, base=tmp_path)

    result = json.loads(response.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert "expired" in result["error"]
    assert not (tmp_path / "dj_queue.json").exists()


def test_dj_control_state_is_bounded_and_rejects_queue_path_escape(cfg, tmp_path):
    cfg["dj"]["queue_file"] = "../outside.json"
    assert c.publish_dj_control_state(cfg, base=tmp_path) is None
    assert not (tmp_path.parent / "outside.json").exists()


def test_dj_control_state_publishes_playback_time_for_the_voice_summary(cfg, tmp_path):
    runner = SimpleNamespace(dj_started=time.monotonic() - 5, dj_length=180.0)
    payload = c.publish_dj_control_state(cfg, now=1000, base=tmp_path, runner=runner)
    assert payload["playback"]["elapsed_seconds"] >= 4
    assert payload["playback"]["duration_seconds"] == 180.0
    elapsed, duration = c.dj_playback_times(base=tmp_path, now=1000)
    assert elapsed >= 4
    assert duration == 180.0
    assert c.dj_playback_times(base=tmp_path, now=1011) == (None, None)


def test_youtube_search_url_is_fixed_origin_and_escaped():
    url = c.youtube_search_url("Thai song & camera")
    assert url.startswith("https://www.youtube.com/results?")
    assert "search_query=Thai+song+%26+camera" in url
    with pytest.raises(ValueError, match="Enter a YouTube"):
        c.youtube_search_url("  ")
    with pytest.raises(ValueError, match="printable"):
        c.youtube_search_url("hello\nworld")


def test_windows_speech_command_keeps_user_text_out_of_powershell_source():
    text = "Hello; Write-Output bad ไทย"
    command = c.windows_speech_powershell_command(text)
    script = base64.b64decode(command).decode("utf-16le")
    encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
    assert "System.Speech.Synthesis.SpeechSynthesizer" in script
    assert encoded_text in script
    assert text not in script
    with pytest.raises(ValueError, match="Enter the text"):
        c.windows_speech_powershell_command("  ")
    with pytest.raises(ValueError, match="240 printable"):
        c.windows_speech_powershell_command("x" * (c.ROOM_SPEECH_MAX_CHARS + 1))
    with pytest.raises(ValueError, match="240 printable"):
        c.windows_speech_powershell_command("line\nbreak")


def test_dj_status_includes_requester_time_and_next_song():
    summary = c.dj_status_summary(
        {"title": "Current song", "user": "Ann"},
        {"title": "Next song", "user": "Bob"}, elapsed=75, duration=180)
    assert summary == ("[dj] current: Current song; requested by Ann; time 01:15/03:00; "
                       "next: Next song (Bob)")
    spoken = c.dj_status_speech(
        {"title": "Current song", "user": "Ann"},
        {"title": "Next song", "user": "Bob"}, elapsed=75, duration=180)
    assert "Current song" in spoken
    assert "Ann" in spoken
    assert "01:15 of 03:00" in spoken
    assert "Next song" in spoken


def test_random_youtube_music_command_saves_a_youtube_search(cfg, monkeypatch):
    query = "Thai pop official music video"
    monkeypatch.setattr(c.random, "choice", lambda _choices: query)
    runner, sent = _dj_runner(cfg, monkeypatch, ["Ann: !randommusic"])

    runner.do_dj(True)

    url = c.youtube_search_url(query)
    history_path = Path(c.BASE) / c.YOUTUBE_RANDOM_MUSIC_FILE
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert sent == [f"[dj] Random YouTube music search (saved): {url}"]
    assert history["version"] == 1
    assert history["links"][-1]["query"] == query
    assert history["links"][-1]["url"] == url
    assert history["links"][-1]["created_at"].endswith("Z")


def test_random_youtube_music_history_is_bounded_and_refuses_corruption(tmp_path):
    query = c.YOUTUBE_RANDOM_MUSIC_QUERIES[0]
    url = c.youtube_search_url(query)
    for index in range(c.YOUTUBE_RANDOM_MUSIC_HISTORY_LIMIT + 5):
        c.save_random_youtube_music_link(query, url, base=tmp_path, now=index)
    path = tmp_path / c.YOUTUBE_RANDOM_MUSIC_FILE
    history = json.loads(path.read_text(encoding="utf-8"))
    assert len(history["links"]) == c.YOUTUBE_RANDOM_MUSIC_HISTORY_LIMIT
    assert history["links"][0]["created_at"] == "1970-01-01T00:00:05Z"

    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be read"):
        c.save_random_youtube_music_link(query, url, base=tmp_path)
    assert path.read_text(encoding="utf-8") == "{invalid"


def test_dj_skip_semantics():
    q = c.DJQueue(Path("nope.json"))
    with pytest.raises(c.DJError):
        q.skip("Ann", "owner")
    q.current = {"title": "Song", "user": "Ann"}
    q.queue = [{"title": "Next", "user": "Bob"}]
    with pytest.raises(c.DJError):
        q.skip("Zed", "owner")
    assert q.skip("ANN", "someone") == {"title": "Next", "user": "Bob"}
    assert q.skip("owner", "owner") is None
    assert q.current is None


@pytest.mark.parametrize("value,expected", [
    ("abcdefghijk", "abcdefghijk"),
    ("https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk"),
    ("https://youtu.be/abcdefghijk", "abcdefghijk"),
    ("https://youtube.com/shorts/abcdefghijk", "abcdefghijk"),
    ("https://youtube.com/embed/abcdefghijk", "abcdefghijk"),
])
def test_youtube_video_id_accepts_only_supported_video_forms(value, expected):
    assert c.youtube_video_id(value) == expected


@pytest.mark.parametrize("value", [
    "https://example.com/watch?v=abcdefghijk",
    "http://youtube.com/watch?v=abcdefghijk",
    "https://youtube.com.evil.test/watch?v=abcdefghijk",
    "https://user@youtube.com/watch?v=abcdefghijk",
    "https://youtube.com/watch?v=short",
    "not a video id",
])
def test_youtube_video_id_rejects_untrusted_or_invalid_forms(value):
    with pytest.raises(ValueError):
        c.youtube_video_id(value)


def test_youtube_player_bridge_checks_token_origin_revision_and_metadata(monkeypatch):
    import urllib.error
    import urllib.parse
    import urllib.request

    host = c.YouTubePlayerHost()
    monkeypatch.setattr(host, "_open_player_window", lambda _video: setattr(
        host, "_browser_launched", True))
    host.load_video("abcdefghijk")
    origin = host.origin
    token = host._token
    query = urllib.parse.urlencode({
        "token": token, "video": "abcdefghijk", "revision": host.revision})
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(origin + "/?video=abcdefghijk", timeout=2)
        assert denied.value.code == 403

        with urllib.request.urlopen(origin + "/?" + query, timeout=2) as response:
            page = response.read().decode("utf-8")
        assert response.status == 200
        assert "Music DJ YouTube Player" in page
        assert "const names={'-1':'ready'" in page

        payload = json.dumps({
            "kind": "playing", "error_code": 0, "video_id": "abcdefghijk",
            "revision": host.revision, "video_title": "Artist - Song",
        }).encode("utf-8")
        request = urllib.request.Request(
            origin + "/event?token=" + urllib.parse.quote(token), data=payload,
            headers={"Content-Type": "application/json", "Origin": origin},
            method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 204
        event = host.consume_events()[0]
        assert event["kind"] == "playing"
        assert event["video_title"] == "Artist - Song"

        old_revision = host.revision
        host.load_video("lmnopqrstuv")
        stale = json.dumps({
            "kind": "ended", "error_code": 0, "video_id": "abcdefghijk",
            "revision": old_revision, "video_title": "Old track",
        }).encode("utf-8")
        stale_request = urllib.request.Request(
            origin + "/event?token=" + urllib.parse.quote(token), data=stale,
            headers={"Content-Type": "application/json", "Origin": origin},
            method="POST")
        with pytest.raises(urllib.error.HTTPError) as conflict:
            urllib.request.urlopen(stale_request, timeout=2)
        assert conflict.value.code == 409
        assert host.consume_events() == []
    finally:
        host._closed_ack.set()
        host.close()


def test_youtube_queue_advances_only_for_current_player_events(cfg, monkeypatch, tmp_path):
    cfg["dry_run"] = False
    cfg["dj"]["audio_backend"] = "youtube"

    class FakePlayer:
        def __init__(self):
            self.revision = 0
            self.video_id = ""
            self.status = "loading"
            self.events = []

        def load_video(self, video_id):
            self.video_id = video_id
            self.revision += 1
            self.status = "loading"

        def consume_events(self):
            events, self.events = self.events, []
            return events

        def emit(self, kind, video_id=None, revision=None, title=""):
            self.events.append({
                "kind": kind,
                "video_id": video_id if video_id is not None else self.video_id,
                "revision": revision if revision is not None else self.revision,
                "video_title": title,
            })

        def stop_video(self):
            self.video_id = ""
            self.revision += 1
            self.status = "stopped"

        def close(self):
            self.status = "stopped"

    fake = FakePlayer()
    monkeypatch.setattr(c, "YouTubePlayerHost", lambda: fake)
    runner = c.Runner(cfg)
    runner.youtube_player = None
    sent = []
    monkeypatch.setattr(runner, "say_now", sent.append)
    queue = c.DJQueue(tmp_path / "dj_queue.json")
    first = "https://youtu.be/abcdefghijk"
    second = "https://www.youtube.com/watch?v=lmnopqrstuv"

    loading = runner.dj_command(queue, cfg["dj"], "request", first, "Ann", "djbot")
    assert "Loading YouTube video abcdefghijk" in loading
    assert queue.current == {
        "title": "abcdefghijk", "user": "Ann", "video_id": "abcdefghijk"}
    runner.dj_command(queue, cfg["dj"], "request", second, "Bob", "djbot")
    assert queue.queue[0]["video_id"] == "lmnopqrstuv"

    fake.emit("ready", title="Artist - First Song")
    fake.emit("playing", title="Artist - First Song")
    assert runner.dj_auto_advance(queue, cfg["dj"]) is True
    assert queue.current["title"] == "Artist - First Song"
    assert any("Now playing: Artist - First Song" in line for line in sent)
    old_revision = fake.revision

    fake.emit("ended")
    assert runner.dj_auto_advance(queue, cfg["dj"]) is True
    assert queue.current["video_id"] == "lmnopqrstuv"
    assert fake.video_id == "lmnopqrstuv"
    fake.emit("playing", video_id="abcdefghijk", revision=old_revision,
              title="Late event from previous video")
    runner.dj_auto_advance(queue, cfg["dj"])
    assert queue.current["title"] == "lmnopqrstuv"
    fake.emit("playing", title="Artist - Second Song")
    runner.dj_auto_advance(queue, cfg["dj"])
    assert queue.current["title"] == "Artist - Second Song"
    assert any("Now playing: Artist - Second Song" in line for line in sent)

    assert queue.save()
    restored = c.DJQueue(queue.path)
    restored.load()
    assert restored.current["video_id"] == "lmnopqrstuv"
    assert restored.current["title"] == "Artist - Second Song"


def test_bot_manager_skip_uses_live_youtube_runner(cfg, tmp_path):
    cfg["dj"]["audio_backend"] = "youtube"
    queue = c.DJQueue(tmp_path / "dj_queue.json")
    queue.current = {
        "title": "First video", "user": "Ann", "video_id": "abcdefghijk"}
    queue.queue = [{
        "title": "lmnopqrstuv", "user": "Bob", "video_id": "lmnopqrstuv"}]
    assert queue.save()
    _, response_path = _write_dj_control_request(
        tmp_path, "skip", "e" * 32, time.time())

    class FakeRunner:
        def __init__(self):
            self.started = []

        def dj_start_playback(self, current_queue, dj):
            self.started.append((current_queue.current, dj["audio_backend"]))
            return True

        def dj_stop_playback(self):
            pass

    runner = FakeRunner()
    assert c.process_dj_control_request(cfg, base=tmp_path, runner=runner) == 1
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["ok"] is True
    assert response["current"]["video_id"] == "lmnopqrstuv"
    assert runner.started == [(
        {"title": "lmnopqrstuv", "user": "Bob", "video_id": "lmnopqrstuv"},
        "youtube")]
