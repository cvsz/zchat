#!/usr/bin/env bash
# Run the Windows test suite from WSL and capture all output.
# Prerequisite (one-time, on Windows): py -3.12 -m venv .venv-win && .venv-win\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
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
    echo "ERROR: $pywin not found. Create it first:" >&2
    echo "  py -3.12 -m venv .venv-win" >&2
    echo "  .venv-win\\Scripts\\python -m pip install -r requirements.txt -r requirements-dev.txt" >&2
    exit 2
fi

set +e
winpy=$(wslpath -w "$PWD/$pywin")
for f in windows_tests/test_runtime_adapter.py windows_tests/test_camfrog_windows_runtime_live.py windows_tests/test_dj_mci_integration.py; do
    echo "=== Running $f ===" | tee -a "$out/console.log"
    cmd.exe /c "cd /d $winrepo && $winpy -X faulthandler -m pytest $f -rs -v" 2>&1 | tee -a "$out/console.log"
    rc=${PIPESTATUS[0]}
    echo "exit=$rc for $f" | tee -a "$out/console.log"
done
# Also run tests/ (Linux-compatible subset)
cmd.exe /c "cd /d $winrepo && $winpy -X faulthandler -m pytest tests -rs -v --junitxml=$winout\\junit.xml --log-file=$winout\\pytest.log --log-file-level=DEBUG" 2>&1 | tee -a "$out/console.log"
rc=${PIPESTATUS[0]}
set -e

echo "exit=$rc" | tee "$out/exit.txt"
exit "$rc"
