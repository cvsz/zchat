import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import doctor  # noqa: E402


def info(**kw):
    base = {
        "version": (3, 12, 10),
        "bits": 64,
        "machine": "AMD64",
        "impl": "CPython",
        "free_threaded": False,
        "windows": True,
    }
    base.update(kw)
    return base


def test_only_python_312_is_supported():
    assert doctor.diagnose(info(version=(3, 12, 10))) == []
    for v in ((3, 11, 9), (3, 13, 1), (3, 15, 0)):
        assert doctor.diagnose(info(version=v)), v


def test_only_64bit_python_312_is_supported():
    assert doctor.diagnose(info(bits=32, version=(3, 12, 4)))
    assert doctor.diagnose(info(bits=64, version=(3, 13, 0)))


def test_free_threaded_pypy_old_and_future_flagged():
    assert doctor.diagnose(info(free_threaded=True))
    assert doctor.diagnose(info(impl="PyPy"))
    assert doctor.diagnose(info(version=(3, 7, 9)))
    assert doctor.diagnose(info(version=(3, 16, 0)))
    assert doctor.diagnose(info(windows=False))


def test_every_problem_is_bilingual():
    for i in (
        info(bits=32),
        info(free_threaded=True),
        info(impl="PyPy"),
        info(windows=False),
    ):
        for en, th in doctor.diagnose(i):
            assert en and th


def test_stale_file_detection(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text(
        "pywinauto>=0.6.8\ncomtypes\n", encoding="utf-8"
    )
    (tmp_path / "build.bat").write_text(
        "pip install -r requirements.txt\n", encoding="utf-8"
    )
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    assert len(doctor.stale_files()) == 2
    (tmp_path / "requirements.txt").write_text(
        "# c\npywin32>=311; sys_platform == 'win32'\npywinauto\n"
    )
    (tmp_path / "build.bat").write_text("echo Using Python\n", encoding="utf-8")
    assert doctor.stale_files() == []


def test_shipped_project_files_are_not_stale():
    assert doctor.stale_files() == []


def test_download_probe_has_timeout_and_handles_hang(monkeypatch):
    import subprocess

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(cmd="pip", timeout=90)

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    ok, tail = doctor.try_download()
    assert ok is False
    assert any("timed out" in line for line in tail)
    assert calls and calls[0].get("timeout") == 90
