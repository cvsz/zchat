import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import package


def test_sha256_matches_hashlib(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 3_000_000)
    assert package.sha256_file(f) == hashlib.sha256(b"x" * 3_000_000).hexdigest()


def test_sums_format_is_sha256sum_compatible(tmp_path):
    (tmp_path / "app.exe").write_bytes(b"abc")
    out = package.write_sums(tmp_path, "app")
    line = out.read_bytes()
    assert line == (hashlib.sha256(b"abc").hexdigest() + "  app.exe\n").encode()


def test_sums_missing_exe(tmp_path):
    try:
        package.write_sums(tmp_path, "nope")
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError")


def test_feature_package_requires_only_the_standalone_executables(tmp_path):
    for relative in package.FEATURE_EXECUTABLES:
        executable = tmp_path / relative
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(relative.encode())

    sums = package.write_sums(tmp_path, "camfrog-features")
    assert [line.split("  ", 1)[1] for line in sums.read_text().splitlines()] == list(
        package.FEATURE_EXECUTABLES)
    bundle = package.make_zip(tmp_path, tmp_path / "bundle.zip", executables_only=True)
    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == set(package.FEATURE_EXECUTABLES) | {"SHA256SUMS.txt"}

    (tmp_path / "legacy.exe").write_bytes(b"old app")
    try:
        package.write_sums(tmp_path, "camfrog-features")
    except ValueError as exc:
        assert "legacy.exe" in str(exc)
    else:
        raise AssertionError("expected an unexpected executable to stop packaging")


def test_feature_package_excludes_the_separate_zchat_client(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    dist = root / "dist"
    dist.mkdir(parents=True)
    monkeypatch.setattr(package, "ROOT", root)
    monkeypatch.setattr(package, "DIST", dist)

    for relative in package.FEATURE_EXECUTABLES:
        executable = dist / relative
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(relative.encode())

    for relative in package.COMPANION_EXECUTABLES:
        executable = dist / relative
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"separate zchat client")

    assert package.main(["camfrog-features", "--executables-only"]) == 0

    sums = dist / "SHA256SUMS.txt"
    assert [line.split("  ", 1)[1] for line in sums.read_text().splitlines()] == list(
        package.FEATURE_EXECUTABLES
    )

    with zipfile.ZipFile(root / "camfrog-features-windows.zip") as archive:
        assert set(archive.namelist()) == set(package.FEATURE_EXECUTABLES) | {
            "SHA256SUMS.txt"
        }


def test_feature_package_includes_each_app_icon(tmp_path):
    for relative in package.FEATURE_EXECUTABLES:
        executable = tmp_path / relative
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(relative.encode())
        (executable.parent / "app.ico").write_bytes(b"zchat icon")
    package.write_sums(tmp_path, "camfrog-features")

    bundle = package.make_zip(
        tmp_path,
        tmp_path / "bundle.zip",
        executables_only=True,
        included_executables=set(package.FEATURE_EXECUTABLES),
    )
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert all(
            f"{Path(relative).parent.as_posix()}/app.ico" in names
            for relative in package.FEATURE_EXECUTABLES
        )


def test_zip_contains_files_with_relative_paths(tmp_path):
    d = tmp_path / "dist"
    (d / "sub").mkdir(parents=True)
    (d / "a.exe").write_bytes(b"1")
    (d / "sub" / "b.txt").write_text("2")
    z = package.make_zip(d, tmp_path / "out.zip")
    with zipfile.ZipFile(z) as zf:
        assert sorted(zf.namelist()) == ["a.exe", "sub/b.txt"]
        assert zf.testzip() is None


def test_zip_inside_dist_does_not_include_its_temporary_file(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "input.txt").write_text("payload")

    archive = package.make_zip(dist, dist / "bundle.zip")

    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == ["input.txt"]
        assert zf.testzip() is None


def test_zip_overwrites_existing(tmp_path):
    d = tmp_path / "dist"
    d.mkdir()
    (d / "a.txt").write_text("1")
    z = tmp_path / "o.zip"
    z.write_text("junk")
    package.make_zip(d, z)
    assert zipfile.is_zipfile(z)


def test_zip_failure_preserves_existing_archive(tmp_path, monkeypatch):
    d = tmp_path / "dist"
    d.mkdir()
    (d / "a.txt").write_text("new content")
    target = tmp_path / "archive.zip"
    target.write_bytes(b"previous archive")

    def fail_write(*args, **kwargs):
        raise OSError("simulated archive write failure")

    monkeypatch.setattr(package.zipfile.ZipFile, "write", fail_write)
    with pytest.raises(OSError, match="simulated archive write failure"):
        package.make_zip(d, target)

    assert target.read_bytes() == b"previous archive"
    assert list(tmp_path.glob(".archive.zip.*.tmp")) == []


@pytest.mark.parametrize(
    "name", ["../outside", "..\\outside", "/absolute", "", "x" * 65]
)
def test_write_sums_rejects_unsafe_package_names(tmp_path, name):
    with pytest.raises(ValueError, match="package name"):
        package.write_sums(tmp_path, name)


def test_main_rejects_traversal_before_touching_artifacts(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    dist = root / "dist"
    dist.mkdir(parents=True)
    sentinel = tmp_path / "sentinel-windows.zip"
    sentinel.write_bytes(b"keep this file")
    monkeypatch.setattr(package, "ROOT", root)
    monkeypatch.setattr(package, "DIST", dist)

    with pytest.raises(SystemExit) as exc:
        package.main(["../sentinel"])

    assert exc.value.code == 2
    assert "package name" in capsys.readouterr().err
    assert sentinel.read_bytes() == b"keep this file"


def test_write_sums_rejects_symlink_executables(tmp_path, monkeypatch):
    executable = tmp_path / "app.exe"
    executable.write_bytes(b"not a symlink on disk")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == executable or original(path))

    with pytest.raises(ValueError, match="symbolic link"):
        package.write_sums(tmp_path, "app")


def test_feature_package_rejects_extra_symlink_executable(tmp_path, monkeypatch):
    for relative in package.FEATURE_EXECUTABLES:
        executable = tmp_path / relative
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(relative.encode())
    extra = tmp_path / "legacy.exe"
    extra.write_bytes(b"untrusted target")
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == extra or original(path),
    )

    with pytest.raises(ValueError, match="symbolic link.*legacy.exe"):
        package.write_sums(tmp_path, "camfrog-features")


@pytest.mark.parametrize("previous_sums", [None, b"previous checksums\n"])
def test_main_restores_checksums_when_zip_creation_fails(
    tmp_path, monkeypatch, capsys, previous_sums
):
    root = tmp_path / "repo"
    dist = root / "dist"
    dist.mkdir(parents=True)
    (dist / "app.exe").write_bytes(b"new executable")
    sums_path = dist / "SHA256SUMS.txt"
    if previous_sums is not None:
        sums_path.write_bytes(previous_sums)
    monkeypatch.setattr(package, "ROOT", root)
    monkeypatch.setattr(package, "DIST", dist)

    def fail_zip(*args, **kwargs):
        raise OSError("simulated archive failure")

    monkeypatch.setattr(package, "make_zip", fail_zip)

    assert package.main(["app"]) == 1
    assert "simulated archive failure" in capsys.readouterr().out
    if previous_sums is None:
        assert not sums_path.exists()
    else:
        assert sums_path.read_bytes() == previous_sums


def test_icon_is_valid_multisize():
    import struct
    d = (Path(__file__).resolve().parent.parent / "app.ico").read_bytes()
    reserved, kind, n = struct.unpack("<HHH", d[:6])
    assert (reserved, kind) == (0, 1)
    sizes = {
        (width or 256, height or 256)
        for width, height, _colors, _reserved, _planes, _bpp, _length, _offset
        in (struct.unpack_from("<BBBBHHII", d, 6 + i * 16) for i in range(n))
    }
    assert sizes == {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}


def test_doctor_rejects_icon_missing_a_required_resolution(tmp_path, monkeypatch):
    import doctor

    icon = (Path(__file__).resolve().parent.parent / "app.ico").read_bytes()
    # Keep the header and first image only to simulate an accidentally incomplete icon.
    incomplete = icon[:6 + 16 + int.from_bytes(icon[14:18], "little")]
    incomplete = incomplete[:4] + (1).to_bytes(2, "little") + incomplete[6:]
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    (tmp_path / "app.ico").write_bytes(incomplete)

    assert "incomplete" in doctor.icon_problem()[0]


# ---- audit regressions ----
import json

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import camfrog_auto as ca


def test_load_cfg_accepts_utf8_bom(tmp_path):
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"dry_run": True}).encode())
    assert ca.load_cfg(p)["dry_run"] is True


def test_validate_never_raises_on_bad_types():
    cfg = ca.deep_merge(ca.DEFAULTS, {"status": {"messages": ["x"], "interval_seconds": "300"},
                                      "autoreply": {"delay_range_seconds": [1]}})
    errs, _ = ca.validate(cfg)
    assert errs  # reported as errors, no exception


def test_validate_bad_rule_shape_reported():
    cfg = ca.deep_merge(ca.DEFAULTS, {"status": {"messages": ["x"]},
                                      "autoreply": {"rules": ["not-a-dict"]}})
    assert ca.validate(cfg)[0]


def test_child_env_resets_pyinstaller_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("_MEIPASS2", "C:/tmp/_MEI123")
    env = ca.child_env()
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1" and "_MEIPASS2" not in env


def test_child_env_untouched_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in ca.child_env()


def test_doctor_bat_runs_once_and_matches_build_selection():
    root = Path(__file__).resolve().parent.parent
    doc = (root / "doctor.bat").read_text(encoding="utf-8")
    bld = (root / "build.bat").read_text(encoding="utf-8")
    assert "||" not in doc  # the old `py ... || python ...` ran doctor twice on failure
    selection = 'py -3.12-64 -c "import sys"'
    assert selection in doc and selection in bld
    assert 'set "PYSEL=py -3.12-64"' in doc and 'set "PYSEL=py -3.12-64"' in bld


def test_missing_window_is_a_clean_error_not_a_traceback(tmp_path, monkeypatch):
    import shutil
    root = Path(__file__).resolve().parent.parent
    cfgp = tmp_path / "config.json"
    shutil.copy(root / "config.example.json", cfgp)
    monkeypatch.setattr(ca, "BASE", tmp_path)

    def boom(cfg):
        raise RuntimeError("Camfrog window not found (check window_title_regex)")
    monkeypatch.setattr(ca, "get_window", boom)
    assert ca.main(["--config", str(cfgp), "discover"]) == 1
    import logging
    logging.shutdown()
    assert "Traceback" not in (tmp_path / "camfrog_auto.log").read_text(encoding="utf-8")


def test_windows_command_registered():
    assert ca.build_parser().parse_args(["windows"]).cmd == "windows"


class _Info:
    def __init__(self, aid="", name=""):
        self.automation_id, self.name = aid, name


class _Ctl:
    def __init__(self, aid="", name=""):
        self.element_info = _Info(aid, name)


class _Win:
    def __init__(self, items):
        self.items, self.kw = items, None

    def descendants(self, **kw):
        self.kw = kw
        return self.items


def test_find_never_passes_auto_id_or_title_re_to_pywinauto():
    w = _Win([_Ctl("1001"), _Ctl("1002"), _Ctl("1002")])
    c = ca.find(w, {"control_type": "Edit", "auto_id": "1002", "index": 1})
    assert c is w.items[2]
    assert set(w.kw) == {"control_type"}


def test_find_title_re_and_missing_control():
    w = _Win([_Ctl("a", "Hello"), _Ctl("b", "World")])
    assert ca.find(w, {"title_re": "Wor.*"}) is w.items[1]
    try:
        ca.find(w, {"auto_id": "zzz"})
    except LookupError:
        return
    raise AssertionError("expected LookupError")


def test_find_without_filters_keeps_old_behaviour():
    w = _Win([_Ctl(), _Ctl()])
    assert ca.find(w, {"index": 1}) is w.items[1]


def test_sums_include_gui_exe(tmp_path):
    (tmp_path / "app.exe").write_bytes(b"a")
    (tmp_path / "app-gui.exe").write_bytes(b"b")
    lines = package.write_sums(tmp_path, "app").read_text(encoding="utf-8").splitlines()
    assert [ln.split("  ")[1] for ln in lines] == ["app.exe", "app-gui.exe"]


def test_bat_files_are_crlf():
    root = Path(__file__).resolve().parent.parent
    for p in list(root.glob("*.bat")) + list((root / "extras").glob("*.bat")):
        b = p.read_bytes()
        assert b.count(b"\n") == b.count(b"\r\n"), f"{p.name} has bare LF (cmd.exe breaks)"


def test_build_cleans_stale_root_web_status_exe_before_packaging():
    root = Path(__file__).resolve().parent.parent
    build = (root / "build.bat").read_text(encoding="utf-8")
    cleanup = r'if exist "dist\web-status.exe" del /q "dist\web-status.exe"'
    assert cleanup in build
    assert build.index(cleanup) < build.index("tools\\package.py")


def test_build_requires_and_bundles_the_zchat_icon_before_packaging():
    root = Path(__file__).resolve().parent.parent
    build = (root / "build.bat").read_text(encoding="utf-8")
    assert "refusing to build apps with a default Python icon" in build
    assert "set \"ICONARG=--icon app.ico --add-data app.ico;.\"" in build
    assert build.index('copy /y "app.ico" "dist\\%%D\\app.ico"') < build.index(
        "tools\\package.py"
    )
