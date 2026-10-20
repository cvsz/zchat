#!/usr/bin/env bash
# Run the Windows test suite from WSL and capture all output.
# Prerequisite (one-time, on Windows):
#   py -3.12 -m venv .venv-win
#   .venv-win\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
set -uo pipefail

case "$PWD" in
  /mnt/*) ;;
  *)
    echo "ERROR: this script must run from /mnt/<drive>/... (WSL-mounted Windows path)" >&2
    echo "Current path: $PWD" >&2
    exit 2
    ;;
esac

ts=$(date +%Y%m%d-%H%M%S)
out="reports/windows-runs/$ts"
mkdir -p "$out"
winrepo=$(wslpath -w "$PWD")
winout=$(wslpath -w "$PWD/$out")
pywin=".venv-win/Scripts/python.exe"

if [ ! -f "$pywin" ]; then
    echo "ERROR: $pywin not found." >&2
    echo "Create it first:" >&2
    echo "  py -3.12 -m venv .venv-win" >&2
    echo "  .venv-win\\Scripts\\python -m pip install -r requirements.txt -r requirements-dev.txt" >&2
    exit 2
fi

# Verify py -3.12 exists
if ! cmd.exe /c "py -3.12 --version" >/dev/null 2>&1; then
    echo "ERROR: py -3.12 (Python 3.12) is not installed on Windows." >&2
    echo "Install Python 3.12 from https://www.python.org/downloads/" >&2
    exit 2
fi

# Verify .venv-win uses non-free-threaded Python matching CI
winpy_abs=$(wslpath -w "$PWD/$pywin")
# Write a temp batch file to avoid quoting issues
tmpbat=$(mktemp /tmp/gilcheck_XXXX.bat)
printf '@echo off\r\n"%s" -c "import sys; print(getattr(sys,chr(95)+chr(95)+chr(105)+chr(115)+chr(95)+chr(103)+chr(105)+chr(108)+chr(95)+chr(101)+chr(110)+chr(97)+chr(98)+chr(108)+chr(101)+chr(100), lambda: True)())"\r\n' "$winpy_abs" > "$tmpbat"
winbat=$(wslpath -w "$tmpbat")
gil_check=$(cmd.exe /c "$winbat" 2>&1 | tr -d '\r')
rm -f "$tmpbat"
if [ "$gil_check" != "True" ]; then
    echo "ERROR: .venv-win Python is free-threaded (GIL disabled) or version mismatch." >&2
    echo "Expected non-free-threaded Python 3.12 (CI version). Got: $gil_check" >&2
    echo "Recreate with: py -3.12 -m venv .venv-win" >&2
    exit 2
fi

winpy=$(wslpath -w "$PWD/$pywin")
rc=0
status="PASSED"

trap 'echo "exit=$rc status=$status" > "$out/exit.txt"' EXIT
trap 'status=INTERRUPTED; rc=130; exit 130' INT
trap 'status=INTERRUPTED; rc=143; exit 143' TERM

# Run each windows_tests file in its own process
for f in windows_tests/*.py; do
    [ -f "$f" ] || continue
    echo "=== Running $f ===" | tee -a "$out/console.log"
    tmpbat=$(mktemp /tmp/runfile_XXXX.bat)
    printf '@echo off\r\n"%s" -X faulthandler -m pytest %s -rs -v --basetemp=%s/basetemp\r\n' "$winpy_abs" "$f" "$winout" > "$tmpbat"
    winbat=$(wslpath -w "$tmpbat")
    timeout 300 cmd.exe /c "cd /d $winrepo && $winbat" 2>&1 | tee -a "$out/console.log"
    rm -f "$tmpbat"
    file_rc=${PIPESTATUS[0]}
    echo "exit=$file_rc for $f" | tee -a "$out/console.log"
    if [ "$file_rc" -eq 124 ]; then
        echo "TIMEOUT: $f exceeded 300s" | tee -a "$out/console.log"
        status="CRASHED"
        rc=124
        break
    fi
    if grep -qi "Fatal Python error\|Windows fatal exception" "$out/console.log" 2>/dev/null; then
        echo "CRASHED: fatal Python error in $f" | tee -a "$out/console.log"
        # print traceback lines
        grep -A20 "Fatal Python error\|Windows fatal exception" "$out/console.log" | tail -20 | tee -a "$out/console.log"
        status="CRASHED"
        rc=1
        break
    fi
    if [ "$file_rc" -ne 0 ] && [ "$file_rc" -ne 5 ]; then
        status="FAILED"
        rc=$file_rc
    fi
done

# Run tests/ (root test suite, excluding live tests)
if [ "$status" != "CRASHED" ]; then
    echo "=== Running tests/ (excluding live) ===" | tee -a "$out/console.log"
    tmpbat2=$(mktemp /tmp/runtests_XXXX.bat)
    printf '@echo off\r\n"%s" -X faulthandler -m pytest tests -rs -v -m "not live" --basetemp=%s/basetemp --junitxml=%s\junit.xml --log-file=%s\pytest.log --log-file-level=DEBUG\r\n' "$winpy_abs" "$winout" "$winout" "$winout" > "$tmpbat2"
    winbat2=$(wslpath -w "$tmpbat2")
    timeout 600 cmd.exe /c "cd /d $winrepo && $winbat2" 2>&1 | tee -a "$out/console.log"
    rm -f "$tmpbat2"
    file_rc=${PIPESTATUS[0]}
    echo "exit=$file_rc for tests/" | tee -a "$out/console.log"
    # Parse junit.xml for errors
    if [ -f "$out/junit.xml" ]; then
        errors=$(python3 -c "import xml.etree.ElementTree as ET; t=ET.parse('$out/junit.xml'); r=t.getroot(); print(r.get('errors','0'))" 2>/dev/null || echo "0")
        echo "ERRORS=$errors" | tee -a "$out/console.log"
        if [ "$errors" -gt 0 ] 2>/dev/null; then
            status="SETUP_ERROR"
            rc=1
        fi
    fi
    if [ "$file_rc" -eq 124 ]; then
        status="CRASHED"
        rc=124
    elif grep -qi "Fatal Python error\|Windows fatal exception" "$out/console.log" 2>/dev/null; then
        status="CRASHED"
        rc=1
    elif [ "$file_rc" -ne 0 ] && [ "$file_rc" -ne 5 ]; then
        status="FAILED"
        rc=$file_rc
    fi
fi

# Final status
if [ "$status" = "PASSED" ] && [ "$rc" -eq 0 ]; then
    status="PASSED"
fi

exit "$rc"
