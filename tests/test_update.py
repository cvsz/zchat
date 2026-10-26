"""Tests for the self-update engine (no network, no GitHub)."""
import io
import json
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import camfrog_auto as ca  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_github(monkeypatch, payload):
    """Patch urllib.request.urlopen with a canned GitHub API response."""
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(ca.urllib.request, "urlopen", fake_urlopen)
    return calls


RELEASE = {
    "tag_name": "v2.20.0",
    "body": "notes here",
    "assets": [
        {"name": "room-control.exe",
         "browser_download_url": "https://github.com/OWNER/PROJECT/releases/download/v2.20.0/room-control.exe"},
        {"name": "SHA256SUMS.txt",
         "browser_download_url": "https://github.com/OWNER/PROJECT/releases/download/v2.20.0/SHA256SUMS.txt"},
    ],
}


def test_version_newer_compares_numerically():
    assert ca.version_newer("2.20.0", "2.19.0") is True
    assert ca.version_newer("2.19.0", "2.19.0") is False
    assert ca.version_newer("2.18.9", "2.19.0") is False
    assert ca.version_newer("v2.20", "2.19.9") is True


def test_github_latest_release_parses_tag_and_assets(monkeypatch):
    calls = _fake_github(monkeypatch, RELEASE)
    tag, notes, assets = ca.github_latest_release()
    assert tag == "2.20.0" and notes == "notes here"
    assert assets["room-control.exe"].endswith("/room-control.exe")
    assert calls and "releases/latest" in calls[0]


def test_check_for_update_finds_the_asset(monkeypatch):
    _fake_github(monkeypatch, RELEASE)
    found = ca.check_for_update("room-control.exe")
    assert found is not None
    tag, url, sums, notes = ca.check_release_assets("room-control.exe")
    assert tag == "2.20.0"
    assert url.endswith("room-control.exe")
    assert sums.endswith("SHA256SUMS.txt")


def test_check_for_update_ignores_missing_asset(monkeypatch):
    _fake_github(monkeypatch, RELEASE)
    assert ca.check_for_update("AutoDJ.exe") is None


def test_check_for_update_ignores_same_version(monkeypatch):
    payload = dict(RELEASE, tag_name="v2.19.0")
    _fake_github(monkeypatch, payload)
    assert ca.check_for_update("room-control.exe") is None


def test_check_for_update_requires_trusted_urls_and_a_checksum(monkeypatch):
    missing_sums = dict(RELEASE, assets=RELEASE["assets"][:1])
    _fake_github(monkeypatch, missing_sums)
    assert ca.check_for_update("room-control.exe") is None

    untrusted = dict(RELEASE, assets=[
        {"name": "room-control.exe", "browser_download_url": "file:///tmp/evil.exe"},
        RELEASE["assets"][1],
    ])
    _fake_github(monkeypatch, untrusted)
    assert ca.check_for_update("room-control.exe") is None


def test_self_update_reports_no_update(monkeypatch):
    monkeypatch.setattr(ca, "check_release_assets", lambda *a, **k: None)
    state, message = ca.self_update("room-control.exe")
    assert state == "no-update"
    assert "no update" in message


def test_self_update_declined_never_downloads(monkeypatch):
    monkeypatch.setattr(ca, "check_release_assets",
                        lambda *a, **k: ("2.20.0", "https://x/y.exe",
                                         "https://x/sums", "notes"))
    monkeypatch.setattr(ca, "download_update",
                        lambda *a, **k: pytest.fail("must not download"))
    state, message = ca.self_update("room-control.exe", confirm=lambda tag: False)
    assert state == "declined"
    assert "declined" in message


def test_stage_and_swap_requires_the_packaged_exe():
    ok, message = ca.stage_and_swap_update("https://example.test/x.exe", "x.exe")
    assert ok is False
    assert "packaged executable" in message


@pytest.mark.parametrize("asset_name", [
    "../room-control.exe",
    r"..\\room-control.exe",
    'room-control.exe"&calc.exe',
    "not-an-executable.zip",
])
def test_stage_and_swap_rejects_unsafe_asset_names_before_download(
        asset_name, monkeypatch):
    monkeypatch.setattr(ca.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        ca, "download_update",
        lambda *args, **kwargs: pytest.fail("unsafe asset must not download"))
    ok, message = ca.stage_and_swap_update(
        "https://github.com/OWNER/PROJECT/releases/download/v2.20.0/asset.exe",
        asset_name,
        "https://github.com/OWNER/PROJECT/releases/download/v2.20.0/SHA256SUMS.txt")
    assert ok is False
    assert "invalid update asset name" in message


def test_verify_against_sums_matches_the_release_list(tmp_path, monkeypatch):
    target = tmp_path / "room-control.exe"
    target.write_bytes(b"payload")
    import hashlib
    digest = hashlib.sha256(b"payload").hexdigest()
    sums = ("{0}  AutoDJ.exe\n{0}  room-control.exe\n".format(digest)).encode()

    def fake_urlopen(request, timeout=None):
        return FakeResponse(sums)

    monkeypatch.setattr(ca.urllib.request, "urlopen", fake_urlopen)
    assert ca.verify_against_sums(RELEASE["assets"][1]["browser_download_url"], "room-control.exe",
                                  target) is True
    assert ca.verify_against_sums(RELEASE["assets"][1]["browser_download_url"], "other.exe",
                                  target) is False


def test_verify_accepts_dist_relative_sums(tmp_path, monkeypatch):
    """Real releases write paths like 'room-control/room-control.exe'."""
    import hashlib

    target = tmp_path / "room-control.exe"
    target.write_bytes(b"payload")
    digest = hashlib.sha256(b"payload").hexdigest()
    sums = ("{0}  im-autoreply/im-autoreply.exe\n"
            "{0}  room-control/room-control.exe\n".format(digest)).encode()

    def fake_urlopen(request, timeout=None):
        return FakeResponse(sums)

    monkeypatch.setattr(ca.urllib.request, "urlopen", fake_urlopen)
    assert ca.verify_against_sums(RELEASE["assets"][1]["browser_download_url"],
                                  "room-control.exe", target) is True


def test_verify_accepts_flat_sums(tmp_path, monkeypatch):
    import hashlib

    target = tmp_path / "room-control.exe"
    target.write_bytes(b"payload")
    digest = hashlib.sha256(b"payload").hexdigest()

    def fake_urlopen(request, timeout=None):
        return FakeResponse(("{0}  room-control.exe\n".format(digest)).encode())

    monkeypatch.setattr(ca.urllib.request, "urlopen", fake_urlopen)
    assert ca.verify_against_sums(RELEASE["assets"][1]["browser_download_url"],
                                  "room-control.exe", target) is True


def test_cmd_update_dispatches_without_config(monkeypatch, capsys):
    """`update` needs no config file and maps the profile to its asset."""
    monkeypatch.setattr(ca, "APP_PROFILE", "room")
    monkeypatch.setattr(ca, "self_update",
                        lambda asset, **k: seen.append(asset) or ("ready", "done"))
    seen = []
    import argparse
    assert ca.cmd_update(argparse.Namespace(asset=None)) == 0
    assert seen == ["room-control.exe"]
    assert "done" in capsys.readouterr().out


def test_cmd_update_explicit_asset_wins(monkeypatch):
    import argparse
    monkeypatch.setattr(ca, "APP_PROFILE", "full")
    monkeypatch.setattr(ca, "self_update", lambda asset, **k: ("no-update", "none"))
    assert ca.cmd_update(argparse.Namespace(asset="web-status.exe")) == 0


def test_cmd_update_full_profile_needs_an_asset(capsys):
    import argparse
    assert ca.cmd_update(argparse.Namespace(asset=None)) == 2
    assert "pass one" in capsys.readouterr().out


def test_update_allowed_in_every_profile():
    import argparse
    args = argparse.Namespace(cmd="update", config=None, lang=None)
    for profile in ("room", "im_reply", "status", "music"):
        assert ca.profile_command_error(profile, args) is None, profile


def test_update_rejected_for_unknown_command():
    import argparse
    args = argparse.Namespace(cmd="marquee", config=None, lang=None)
    assert ca.profile_command_error("music", args) is not None


def test_sums_lookup_falls_back_to_prefixed_sums_file(monkeypatch):
    """v2.19.1 published the flat sums as release-SHA256SUMS.txt."""
    payload = dict(RELEASE, assets=[
        {"name": "room-control.exe",
         "browser_download_url": RELEASE["assets"][0]["browser_download_url"]},
        {"name": "release-SHA256SUMS.txt",
         "browser_download_url": "https://github.com/OWNER/PROJECT/releases/download/v2.20.0/release-SHA256SUMS.txt"},
    ])
    _fake_github(monkeypatch, payload)
    tag, url, sums, notes = ca.check_release_assets("room-control.exe")
    assert tag == "2.20.0"
    assert sums.endswith("/release-SHA256SUMS.txt")


def test_download_update_streams_bytes(tmp_path, monkeypatch):
    body = b"0123456789" * 1024

    def fake_urlopen(request, timeout=None):
        return FakeResponse(body)

    monkeypatch.setattr(ca.urllib.request, "urlopen", fake_urlopen)
    target = tmp_path / "asset.exe"
    assert ca.download_update(
        "https://github.com/OWNER/PROJECT/releases/download/v2.20.0/a.exe",
        str(target)) == len(body)
    assert target.read_bytes() == body


@pytest.mark.parametrize("url", [
    "file:///tmp/asset.exe",
    "http://github.com/OWNER/PROJECT/releases/download/v2.20.0/asset.exe",
    "https://github.com.evil.example/OWNER/PROJECT/releases/download/v2.20.0/asset.exe",
    "https://github.com@evil.example/OWNER/PROJECT/releases/download/v2.20.0/asset.exe",
    "https://github.com:8443/OWNER/PROJECT/releases/download/v2.20.0/asset.exe",
])
def test_download_rejects_untrusted_release_urls_before_network(url, tmp_path, monkeypatch):
    monkeypatch.setattr(
        ca.urllib.request, "urlopen",
        lambda *args, **kwargs: pytest.fail("untrusted URL must not reach the network"))
    target = tmp_path / "asset.exe"
    with pytest.raises(ValueError, match="update URL"):
        ca.download_update(url, target)
    assert not target.exists()


def test_download_rejects_untrusted_redirect_before_writing(tmp_path, monkeypatch):
    class RedirectResponse(FakeResponse):
        def geturl(self):
            return "file:///tmp/asset.exe"

    monkeypatch.setattr(ca.urllib.request, "urlopen",
                        lambda *args, **kwargs: RedirectResponse(b"asset"))
    target = tmp_path / "asset.exe"
    with pytest.raises(ValueError, match="update URL"):
        ca.download_update(
            "https://github.com/OWNER/PROJECT/releases/download/v2.20.0/asset.exe",
            target)
    assert not target.exists()


def test_self_update_full_flow_stages_the_swap(tmp_path, monkeypatch):
    """Frozen exe: download -> verify -> <asset>.new + camfrog-update.cmd."""
    import hashlib

    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    exe = exe_dir / "room-control.exe"
    exe.write_bytes(b"old")
    monkeypatch.setattr(ca.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ca.sys, "executable", str(exe))
    _fake_github(monkeypatch, RELEASE)
    body = b"new build bytes"
    digest = hashlib.sha256(body).hexdigest()

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url.endswith("releases/latest"):
            return FakeResponse(json.dumps(RELEASE).encode("utf-8"))
        if url.endswith("SHA256SUMS.txt"):
            return FakeResponse(("{0}  room-control.exe\n".format(digest)).encode())
        return FakeResponse(body)

    monkeypatch.setattr(ca.urllib.request, "urlopen", fake_urlopen)
    spawned = []
    monkeypatch.setattr(ca.subprocess, "Popen",
                        lambda *a, **k: spawned.append((a, k)))
    state, message = ca.self_update("room-control.exe", confirm=lambda tag: True)
    assert state == "ready", message
    staged = exe_dir / "room-control.exe.new"
    assert staged.read_bytes() == body
    script = exe_dir / "camfrog-update.cmd"
    text = script.read_text(encoding="utf-8")
    assert 'move /Y' in text and str(staged) in text and str(exe) in text
    # the swap script is launched, and only after this process is gone
    assert spawned and "cmd" in str(spawned[0][0])


def test_self_update_refuses_a_checksum_mismatch(tmp_path, monkeypatch):
    import hashlib

    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    (exe_dir / "room-control.exe").write_bytes(b"old")
    monkeypatch.setattr(ca.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ca.sys, "executable",
                        str(exe_dir / "room-control.exe"))
    _fake_github(monkeypatch, RELEASE)

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url.endswith("releases/latest"):
            return FakeResponse(json.dumps(RELEASE).encode("utf-8"))
        if url.endswith("SHA256SUMS.txt"):
            line = "{0}  room-control.exe\n".format(
                hashlib.sha256(b"different").hexdigest())
            return FakeResponse(line.encode("utf-8"))
        return FakeResponse(b"new build bytes")

    monkeypatch.setattr(ca.urllib.request, "urlopen", fake_urlopen)
    state, message = ca.self_update("room-control.exe", confirm=lambda tag: True)
    assert state == "failed"
    assert "checksum mismatch" in message
    assert not (exe_dir / "room-control.exe.new").exists()


def test_run_remote_tests_uses_explicit_windows_target(monkeypatch):
    import run_remote_tests as rrt

    monkeypatch.setattr(rrt, "HOST", "windows.example")
    monkeypatch.setattr(rrt, "USER", "tester")
    monkeypatch.setattr(rrt, "REMOTE_DIR", r"C:\project")
    cmd = rrt.build_command(["tests/test_web_status_gui.py", "-q"])
    joined = " ".join(cmd)
    assert "tester@windows.example" in joined
    assert "-m pytest tests/test_web_status_gui.py -q" in joined


def test_run_remote_tests_requires_explicit_windows_target(monkeypatch):
    import run_remote_tests as rrt

    monkeypatch.setattr(rrt, "HOST", "")
    monkeypatch.setattr(rrt, "USER", "")
    monkeypatch.setattr(rrt, "REMOTE_DIR", "")
    with pytest.raises(ValueError, match="CAMFROG_WINDOWS_HOST"):
        rrt.build_command(["tests/test_web_status_gui.py", "-q"])


def test_run_remote_tests_default_is_the_gui_suite(monkeypatch):
    import run_remote_tests as rrt

    monkeypatch.setattr(rrt, "HOST", "windows.example")
    monkeypatch.setattr(rrt, "USER", "tester")
    monkeypatch.setattr(rrt, "REMOTE_DIR", r"C:\project")
    seen = []
    monkeypatch.setattr(rrt.subprocess, "run",
                        lambda cmd: seen.append(cmd) or type("R", (), {"returncode": 0})())
    assert rrt.main([]) == 0
    assert any("tests/test_web_status_gui.py" in str(c) for c in seen)
