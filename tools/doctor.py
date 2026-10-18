"""Build-environment doctor / ตรวจสภาพแวดล้อมก่อน build  (stdlib only)

  python tools/doctor.py            full check (also tries to download the pywin32 wheel)
  python tools/doctor.py --quick    interpreter checks only (no network); exit 1 if unusable
"""
import os
import platform
import struct
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def interpreter_info():
    return {
        "version": sys.version_info[:3],
        "bits": struct.calcsize("P") * 8,
        "machine": platform.machine(),
        "impl": platform.python_implementation(),
        "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "windows": os.name == "nt",
    }


def diagnose(info):
    """Return build-environment problems for the supported Windows runtime."""
    out, v = [], info["version"]
    if not info["windows"]:
        out.append(("This tool targets Windows; run build.bat on Windows.",
                    "เครื่องมือนี้สำหรับ Windows เท่านั้น"))
    if v[:2] != (3, 12):
        out.append((f"Python 3.12 is required; found {v[0]}.{v[1]}. Install CPython 3.12 64-bit.",
                    f"ต้องใช้ Python 3.12 แต่พบ {v[0]}.{v[1]} ให้ติดตั้ง CPython 3.12 แบบ 64-bit"))
    if info["bits"] != 64:
        out.append(("A 64-bit Python 3.12 interpreter is required.",
                    "ต้องใช้ Python 3.12 แบบ 64-bit"))
    if info["impl"] != "CPython":
        out.append((f"{info['impl']} is not supported. Use CPython 3.12.",
                    f"ไม่รองรับ {info['impl']} ให้ใช้ CPython 3.12"))
    if info["free_threaded"]:
        out.append(("Free-threaded Python is not supported. Install the standard CPython 3.12 build.",
                    "ไม่รองรับ Python แบบ free-threaded ให้ติดตั้ง CPython 3.12 รุ่นปกติ"))
    return out


def stale_files():
    """Detect an old, partially-updated project folder."""
    out = []
    req = ROOT / "requirements.txt"
    if req.exists():
        lines = [x.strip() for x in req.read_text(encoding="utf-8").splitlines()
                 if x.strip() and not x.strip().startswith("#")]
        if lines and lines[0].lower().startswith("pywinauto"):
            out.append(("requirements.txt is an OLD version (pywinauto first, no pywin32 pin). Re-extract the latest zip into a clean folder.",
                        "requirements.txt เป็นไฟล์เก่า ให้แตก zip ล่าสุดลงโฟลเดอร์ใหม่"))
    bat = ROOT / "build.bat"
    if bat.exists() and "Using Python" not in bat.read_text(encoding="utf-8", errors="ignore"):
        out.append(("build.bat is an OLD version (no Python auto-selection). Re-extract the latest zip.",
                    "build.bat เป็นไฟล์เก่า ให้แตก zip ล่าสุด"))
    return out


def icon_problem():
    """Require the complete ZChat icon set used by Windows and packaged EXEs."""
    ico = ROOT / "app.ico"
    if not ico.exists():
        return ("app.ico not found: exe will be built WITHOUT an icon.",
                "ไม่พบ app.ico: exe จะไม่มีไอคอน")
    d = ico.read_bytes()
    if len(d) < 22 or d[:4] != b"\x00\x00\x01\x00":
        return ("app.ico is not a valid .ico file (renamed PNG?). Re-export it as a real .ico.",
                "app.ico ไม่ใช่ไฟล์ .ico จริง (อาจแค่เปลี่ยนนามสกุลจาก PNG) ให้ export ใหม่")
    try:
        reserved, kind, count = struct.unpack_from("<HHH", d)
        sizes = {
            (width or 256, height or 256)
            for width, height, _colors, _reserved, _planes, _bpp, _length, _offset
            in (struct.unpack_from("<BBBBHHII", d, 6 + i * 16) for i in range(count))
        }
    except struct.error:
        reserved, kind, count, sizes = 1, 0, 0, set()
    expected = {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
    if reserved != 0 or kind != 1 or count != len(expected) or sizes != expected:
        return ("app.ico is incomplete; expected image sizes 16, 24, 32, 48, 64, 128, and 256 px.",
                "app.ico มีขนาดภาพไม่ครบ ต้องมี 16, 24, 32, 48, 64, 128 และ 256 px")
    return None


def try_download():
    with tempfile.TemporaryDirectory() as d:
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "download", "pywin32>=311",
                                "--only-binary=:all:", "--no-deps", "-d", d, "-q"],
                               capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            return False, ["pip download timed out after 90 s (offline?)"]
        return r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-3:]


def main(argv):
    quick = "--quick" in argv
    info = interpreter_info()
    v = info["version"]
    print(f"Python      : {v[0]}.{v[1]}.{v[2]}  {info['impl']}  {info['bits']}-bit  ({info['machine']})")
    print(f"Executable  : {sys.executable}")
    print(f"Free-thread : {info['free_threaded']}")
    problems = diagnose(info) + ([] if quick else stale_files())
    if not quick:
        ip = icon_problem()
        if ip:
            problems.append(ip)
    for en, th in problems:
        print(f"\n[PROBLEM] {en}\n          {th}")
    if quick:
        return 1 if diagnose(info) else 0
    if not diagnose(info) and info["windows"]:
        ok, tail = try_download()
        print("\npywin32 wheel download:", "OK" if ok else "FAILED")
        if not ok:
            print("\n".join("  " + x for x in tail))
            print("[PROBLEM] pip could not fetch a pywin32 wheel for this interpreter (offline? proxy? mirror without wheels?).\n"
                  "          pip โหลด pywin32 ไม่ได้ (ออฟไลน์/proxy/mirror?) ลอง: python -m pip install --upgrade pip")
            problems.append(("download", "download"))
    if problems:
        print("\nFIX / วิธีแก้:  winget install Python.Python.3.12   (64-bit)  then delete the .venv folder and run build.bat")
        return 1
    print("\nNo problems found / ไม่พบปัญหา")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
