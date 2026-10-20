#!/usr/bin/env bash
# Run the Windows test suite from WSL and capture all output.
# Expected invocation: bash scripts/win-test.sh  (from /mnt/<drive>/... )
# Prerequisite (one-time, on Windows):
#   py -3.12 -m venv .venv-win
#   .venv-win\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
set -uo pipefail

case "$PWD" in
  /mnt/*) ;;
  *)
    echo "ERROR: this script must run from /mnt/<drive>/... (WSL-mounted Windows path)" >&2
    echo "Current path: $PWD" >&2
    mkdir -p "reports/windows-runs" 2>/dev/null || true
    ts0=$(date +%Y%m%d-%H%M%S)
    out0="reports/windows-runs/$ts0"
    mkdir -p "$out0"
    echo "exit=2 status=CONFIG_ERROR" > "$out0/exit.txt"
    exit 2
    ;;
esac

ts=$(date +%Y%m%d-%H%M%S)
out="reports/windows-runs/$ts"
mkdir -p "$out"
winrepo=$(wslpath -w "$PWD")
winout=$(wslpath -w "$PWD/$out")
pywin=".venv-win/Scripts/python.exe"

write_exit() {
    echo "exit=$rc status=$status" > "$out/exit.txt"
}
trap 'write_exit' EXIT
trap 'status=INTERRUPTED; rc=130; exit 130' INT
trap 'status=INTERRUPTED; rc=143; exit 143' TERM

status_rank() {
    case "$1" in
        PASSED) echo 0 ;;
        UNVERIFIED) echo 1 ;;
        FAILED) echo 2 ;;
        SETUP_ERROR) echo 3 ;;
        CRASHED) echo 4 ;;
        CONFIG_ERROR) echo 5 ;;
        INTERRUPTED) echo 6 ;;
        *) echo 0 ;;
    esac
}

promote_status() {
    local new="$1"
    if [ "$(status_rank "$new")" -gt "$(status_rank "$status")" ]; then
        status="$new"
    fi
}

if [ ! -f "$pywin" ]; then
    echo "ERROR: $pywin not found." >&2
    echo "Create it first (requires py -3.12):" >&2
    echo "  py -3.12 -m venv .venv-win" >&2
    echo "  .venv-win\\Scripts\\python -m pip install -r requirements.txt -r requirements-dev.txt" >&2
    status=CONFIG_ERROR
    rc=2
    exit 2
fi

winpy_abs=$(wslpath -w "$PWD/$pywin")
tmpbat=$(mktemp /tmp/vercheck_XXXX.bat)
printf '@echo off\r\n"%s" --version > %s\\verout.txt 2>&1\r\n' "$winpy_abs" "$winout" > "$tmpbat"
winbat=$(wslpath -w "$tmpbat")
cmd.exe /c "$winbat" >/dev/null 2>&1
rm -f "$tmpbat"
pyver=$(tr -d '\r' < "$out/verout.txt" 2>/dev/null || echo "")
if ! echo "$pyver" | grep -qE '^Python 3\.12\.'; then
    echo "ERROR: .venv-win Python is not 3.12.x (CI requires python-version 3.12)." >&2
    echo "Found: $pyver" >&2
    echo "Recreate with: py -3.12 -m venv .venv-win" >&2
    status=CONFIG_ERROR
    rc=2
    exit 2
fi

rc=0
status=PASSED

# Run each windows_tests file in its own process
for f in windows_tests/*.py; do
    [ -f "$f" ] || continue
    echo "=== Running $f ===" | tee -a "$out/console.log"
    tmpbat=$(mktemp /tmp/runfile_XXXX.bat)
    printf '@echo off\r\n"%s" -X faulthandler -m pytest %s -rs -v --basetemp=%s/basetemp > %s/%s.log 2>&1\r\n' "$winpy_abs" "$f" "$winout" "$winout" "$(basename "$f")" > "$tmpbat"
    winbat=$(wslpath -w "$tmpbat")
    timeout 300 cmd.exe /c "cd /d $winrepo && $winbat" >/dev/null 2>&1
    file_rc=$?
    cat "$out/$(basename "$f").log" >> "$out/console.log" 2>/dev/null
    cat "$out/$(basename "$f").log" 2>/dev/null
    echo "exit=$file_rc for $f" | tee -a "$out/console.log"

    if grep -qi "Fatal Python error\|Windows fatal exception" "$out/$(basename "$f").log" 2>/dev/null; then
        echo "CRASHED: fatal Python error in $f" | tee -a "$out/console.log"
        grep -A20 "Fatal Python error\|Windows fatal exception" "$out/$(basename "$f").log" | tail -20 | tee -a "$out/console.log"
        promote_status "CRASHED"
        rc=1
        continue
    fi
    if [ "$file_rc" -eq 124 ]; then
        # timeout: kill child python process tree
        cmd.exe /c "taskkill /T /F /IM python.exe" >/dev/null 2>&1 || true
        echo "CRASHED: timeout in $f" | tee -a "$out/console.log"
        promote_status "CRASHED"
        rc=124
        continue
    fi
    if [ "$file_rc" -ne 0 ] && [ "$file_rc" -ne 5 ]; then
        promote_status "FAILED"
        rc=$file_rc
    fi
    rm -f "$tmpbat"
done

# Run tests/ (root test suite, excluding live tests)
echo "=== Running tests/ (excluding live) ===" | tee -a "$out/console.log"
tmpbat2=$(mktemp /tmp/runtests_XXXX.bat)
printf '@echo off\r\n"%s" -X faulthandler -m pytest tests -rs -v -m "not live" --basetemp=%s/basetemp --junitxml=%s/junit.xml --log-file=%s/pytest.log --log-file-level=DEBUG > %s/tests_console.log 2>&1\r\n' "$winpy_abs" "$winout" "$winout" "$winout" "$winout" > "$tmpbat2"
winbat2=$(wslpath -w "$tmpbat2")
timeout 600 cmd.exe /c "cd /d $winrepo && $winbat2" >/dev/null 2>&1
file_rc=$?
cat "$out/tests_console.log" >> "$out/console.log" 2>/dev/null
cat "$out/tests_console.log" 2>/dev/null
echo "exit=$file_rc for tests/" | tee -a "$out/console.log"

if grep -qi "Fatal Python error\|Windows fatal exception" "$out/tests_console.log" 2>/dev/null; then
    echo "CRASHED: fatal Python error in tests/" | tee -a "$out/console.log"
    grep -A20 "Fatal Python error\|Windows fatal exception" "$out/tests_console.log" | tail -20 | tee -a "$out/console.log"
    promote_status "CRASHED"
    rc=1
elif [ "$file_rc" -eq 124 ]; then
    cmd.exe /c "taskkill /T /F /IM python.exe" >/dev/null 2>&1 || true
    promote_status "CRASHED"
    rc=124
elif [ "$file_rc" -ne 0 ] && [ "$file_rc" -ne 5 ]; then
    promote_status "FAILED"
    rc=$file_rc
fi
rm -f "$tmpbat2"

# Parse junit.xml with Windows venv python
if [ -f "$out/junit.xml" ]; then
    tmp_py=$(mktemp /tmp/parsexml_XXXX.py)
    cat > "$tmp_py" <<'PYEOF'
import xml.etree.ElementTree as ET, sys
try:
    t = ET.parse(sys.argv[1])
    r = t.getroot()
    total = 0
    for ts in r.iter("testsuite"):
        total += int(ts.get("errors", "0"))
    print(total)
except Exception:
    print("MISSING")
PYEOF
    winpy_script=$(wslpath -w "$tmp_py")
    winjunit=$(wslpath -w "$out/junit.xml")
    tmpbat3=$(mktemp /tmp/parsexml_XXXX.bat)
    printf '@echo off\r\n"%s" "%s" "%s" > %s/errout.txt 2>&1\r\n' "$winpy_abs" "$winpy_script" "$winjunit" "$winout" > "$tmpbat3"
    winbat3=$(wslpath -w "$tmpbat3")
    cmd.exe /c "$winbat3" >/dev/null 2>&1
    rm -f "$tmpbat3" "$tmp_py"
    errors=$(tr -d '\r' < "$out/errout.txt" 2>/dev/null | tail -1 || echo "")
    if [ "$errors" = "MISSING" ] || [ -z "$errors" ]; then
        echo "ERRORS=UNVERIFIED (junit.xml parse failed)" | tee -a "$out/console.log"
        promote_status "UNVERIFIED"
        rc=1
    else
        echo "ERRORS=$errors" | tee -a "$out/console.log"
        if [ "$errors" -gt 0 ] 2>/dev/null; then
            promote_status "SETUP_ERROR"
            rc=1
        fi
    fi
else
    echo "ERRORS=UNVERIFIED (junit.xml not found)" | tee -a "$out/console.log"
    promote_status "UNVERIFIED"
    rc=1
fi

# Final status
if [ "$status" = "PASSED" ] && [ "$rc" -eq 0 ]; then
    status="PASSED"
fi

exit "$rc"
