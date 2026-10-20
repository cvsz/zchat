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
gil_check=$(cmd.exe /c "cd /d $winrepo && $pywin -c \"import sys; print(getattr(sys,'_is_gil_enabled',lambda:True)())\"" 2>&1 | tr -d '\r')
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
    timeout 300 cmd.exe /c "cd /d $winrepo && $winpy -X faulthandler -m pytest $f -rs -v" 2>&1 | tee -a "$out/console.log"
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
    timeout 600 cmd.exe /c "cd /d $winrepo && $winpy -X faulthandler -m pytest tests -rs -v -m \"not live\" --junitxml=$winout\\junit.xml --log-file=$winout\\pytest.log --log-file-level=DEBUG" 2>&1 | tee -a "$out/console.log"
    file_rc=${PIPESTATUS[0]}
    echo "exit=$file_rc for tests/" | tee -a "$out/console.log"
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
