"""Release-integrity regression tests."""

import hashlib
import json
from pathlib import Path

import pytest
from tools import release
from line.config_store import default_config


def test_public_line_config_is_generic_default_not_local_settings(tmp_path):
    local_config = tmp_path / "line_config.json"
    local_config.write_text('{"last_text":"private local status"}', encoding="utf-8")
    public_config = tmp_path / "release" / "line_config.json"

    release.write_public_line_config(public_config)

    data = json.loads(public_config.read_text(encoding="utf-8"))
    assert data == default_config()
    assert "private local status" not in public_config.read_text(encoding="utf-8")


def test_verify_feature_hashes_accepts_all_expected_files(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    files = {}
    lines = []
    for name in (
        "room-control",
        "chat-im-private",
        "status-random",
        "status-marquee",
        "status-changer",
        "im-autoreply",
        "music-dj",
        "web-status",
    ):
        path = dist / name / (name + ".exe")
        path.parent.mkdir()
        path.write_bytes(("test-" + name).encode())
        files[name + ".exe"] = path
        lines.append(
            hashlib.sha256(path.read_bytes()).hexdigest()
            + "  "
            + name
            + "/"
            + name
            + ".exe"
        )
    (dist / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(release, "DIST", dist)
    monkeypatch.setattr(release, "FEATURE_EXES", files)
    release.verify_feature_hashes()
    files["music-dj.exe"].write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="checksum mismatch"):
        release.verify_feature_hashes()


def test_verify_feature_hashes_rejects_missing_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "DIST", tmp_path)
    with pytest.raises(SystemExit, match="missing dist/SHA256SUMS"):
        release.verify_feature_hashes()


def test_verify_zchat_hash_checks_the_separate_client_manifest(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    output = dist / "zchat-client"
    output.mkdir(parents=True)
    executable = output / "zchat-client.exe"
    executable.write_bytes(b"zchat-client-build")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    (output / "SHA256SUMS.txt").write_text(
        f"{digest}  zchat-client.exe\n", encoding="ascii"
    )
    monkeypatch.setattr(release, "DIST", dist)

    release.verify_zchat_hash()

    executable.write_bytes(b"tampered-client-build")
    with pytest.raises(SystemExit, match="checksum mismatch: zchat-client.exe"):
        release.verify_zchat_hash()


def test_full_build_checks_and_lists_every_release_executable():
    root = Path(__file__).resolve().parents[1]
    script = (root / "full-build.bat").read_text(encoding="utf-8").lower()
    checks = script.split("rem --- verify all build outputs ---", 1)[1].split(
        "rem --- publish the github release", 1
    )[0]
    outputs = script.split("echo outputs:", 1)[1]

    for artifact in release.FEATURE_EXES.values():
        relative_path = artifact.relative_to(release.DIST).as_posix().replace("/", "\\")
        expected_path = "dist\\" + relative_path.lower()
        assert f"if not exist {expected_path}" in checks
        assert expected_path in outputs

    client = release.OTHER_ASSETS["zchat-client.exe"]
    relative_client = client.relative_to(release.DIST).as_posix().replace("/", "\\")
    expected_client = "dist\\" + relative_client.lower()
    assert f"if not exist {expected_client}" in checks
    assert expected_client in outputs
    assert "dist\\zchat-client\\sha256sums.txt" in checks


def test_windows_build_uses_fresh_pytest_basetemp_per_invocation():
    root = Path(__file__).resolve().parents[1]
    script = (root / "build.bat").read_text(encoding="utf-8")

    assert 'set "PYTEST_RUN_ID=%RANDOM%-%RANDOM%-%RANDOM%"' in script
    assert 'set "PYTEST_TMP_DIR=%CD%\\build\\pytest_tmp_%PYTEST_RUN_ID%"' in script
    assert 'rmdir /s /q "%PYTEST_TMP_DIR%"' not in script
    assert '--basetemp="%PYTEST_TMP_DIR%"' in script
    assert '--basetemp="%PYTEST_TMP_DIR%_private"' in script


def test_music_dj_packages_zeazdev_room_mascot_with_the_executable():
    root = Path(__file__).resolve().parents[1]
    build = (root / "build.bat").read_text(encoding="utf-8")
    artwork = root / "assets" / "zeazdev-room.png"
    assert "assets\\zeazdev-room.png;assets" in build
    assert artwork.is_file()
    assert artwork.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_release_rejects_existing_tag_without_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "DIST", tmp_path)
    monkeypatch.setattr(release, "app_version", lambda: "2.99.0")
    monkeypatch.setattr(release, "check_assets", lambda: None)
    monkeypatch.setattr(release, "verify_feature_hashes", lambda: None)
    monkeypatch.setattr(release, "verify_zchat_hash", lambda: None)
    monkeypatch.setattr(release, "flat_sums", lambda pairs: "abc\n")
    monkeypatch.setattr(release, "release_notes", lambda ver: "notes")
    monkeypatch.setattr(release, "release_target", lambda: "a" * 40)
    monkeypatch.setattr(release, "verify_build_source", lambda target: None)
    monkeypatch.setattr(release, "verify_remote_tag", lambda tag, target: None)
    monkeypatch.setattr(release, "gh_ready", lambda: True)

    class Completed:
        returncode = 0

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Completed()

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    assert release.main([]) == 3
    assert calls == [["gh", "release", "view", "v2.99.0"]]


def test_release_pins_tag_to_verified_commit(monkeypatch, tmp_path):
    target = "a" * 40
    monkeypatch.setattr(release, "DIST", tmp_path)
    monkeypatch.setattr(release, "app_version", lambda: "2.19.2")
    monkeypatch.setattr(release, "check_assets", lambda: None)
    monkeypatch.setattr(release, "verify_feature_hashes", lambda: None)
    monkeypatch.setattr(release, "verify_zchat_hash", lambda: None)
    monkeypatch.setattr(release, "FEATURE_EXES", {})
    monkeypatch.setattr(release, "OTHER_ASSETS", {})
    monkeypatch.setattr(release, "flat_sums", lambda pairs: "abc\n")
    monkeypatch.setattr(release, "release_notes", lambda ver: "notes")
    monkeypatch.setattr(release, "release_target", lambda: target)
    monkeypatch.setattr(release, "verify_build_source", lambda source: None)
    monkeypatch.setattr(release, "verify_remote_tag", lambda tag, source: None)
    monkeypatch.setattr(release, "gh_ready", lambda: True)

    class Completed:
        def __init__(self, returncode):
            self.returncode = returncode

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Completed(1 if cmd[:3] == ["gh", "release", "view"] else 0)

    monkeypatch.setattr(release.subprocess, "run", fake_run)

    assert release.main([]) == 0
    create = next(
        command for command in calls if command[:3] == ["gh", "release", "create"]
    )
    target_index = create.index("--target")
    assert create[target_index + 1] == target
    checksum_assets = [
        Path(asset.split("#", 1)[0]).name
        for asset in create[4:target_index]
        if Path(asset.split("#", 1)[0]).name.endswith("SHA256SUMS.txt")
    ]
    assert checksum_assets == ["SHA256SUMS.txt"]


def test_verify_build_source_requires_clean_matching_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "BUILD_SOURCE", tmp_path / "BUILD_SOURCE.txt")
    target = "a" * 40
    with pytest.raises(SystemExit, match="dist/BUILD_SOURCE.txt"):
        release.verify_build_source(target)

    release.BUILD_SOURCE.write_text(
        "commit={0}\nclean=false\n".format(target), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="built from a dirty working tree"):
        release.verify_build_source(target)

    release.BUILD_SOURCE.write_text(
        "commit={0}\nclean=true\n".format("b" * 40), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="does not match release HEAD"):
        release.verify_build_source(target)

    release.BUILD_SOURCE.write_text(
        "commit={0}\nclean=true\n".format(target), encoding="utf-8"
    )
    assert release.verify_build_source(target) is None


def test_remote_tag_check_uses_peeled_annotated_target(monkeypatch):
    target = "a" * 40
    annotated = "b" * 40
    calls = []

    class Completed:
        returncode = 0
        stdout = "{0}\trefs/tags/v2.19.2\n{1}\trefs/tags/v2.19.2^{{}}\n".format(
            annotated, target
        )

    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or Completed(),
    )

    assert release.remote_tag_target("v2.19.2") == target
    release.verify_remote_tag("v2.19.2", target)
    assert len(calls) == 2
    assert "refs/tags/v2.19.2^{}" in calls[0]


def test_release_rejects_mismatched_remote_tag_before_gh(monkeypatch, tmp_path):
    target = "a" * 40
    monkeypatch.setattr(release, "DIST", tmp_path)
    monkeypatch.setattr(release, "app_version", lambda: "2.19.2")
    monkeypatch.setattr(release, "check_assets", lambda: None)
    monkeypatch.setattr(release, "verify_feature_hashes", lambda: None)
    monkeypatch.setattr(release, "verify_zchat_hash", lambda: None)
    monkeypatch.setattr(release, "FEATURE_EXES", {})
    monkeypatch.setattr(release, "OTHER_ASSETS", {})
    monkeypatch.setattr(release, "flat_sums", lambda pairs: "abc\n")
    monkeypatch.setattr(release, "release_notes", lambda version: "notes")
    monkeypatch.setattr(release, "release_target", lambda: target)
    monkeypatch.setattr(release, "verify_build_source", lambda source: None)
    monkeypatch.setattr(release, "remote_tag_target", lambda tag: "b" * 40)
    monkeypatch.setattr(release, "gh_ready", lambda: True)

    calls = []
    monkeypatch.setattr(
        release.subprocess, "run", lambda command, **kwargs: calls.append(command)
    )

    with pytest.raises(SystemExit, match="remote tag v2.19.2 points to"):
        release.main([])

    assert calls == []


def test_release_rejects_mismatched_tag(monkeypatch):
    monkeypatch.setattr(release, "app_version", lambda: "2.99.0")
    with pytest.raises(SystemExit, match="must match APP_VERSION"):
        release.main(["--tag", "v0.0.1"])
