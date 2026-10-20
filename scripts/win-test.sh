#!/usr/bin/env bash
# Run the Windows test suite from WSL and capture all output.
# Prerequisite (one-time, on Windows): py -3 -m pip install -r requirements.txt -r requirements-dev.txt
set -uo pipefail

# Verify we are running under WSL with /mnt/<drive>/ paths
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

set +e
cmd.exe /c "cd /d $winrepo && py -3 -m pytest windows_tests tests -rs -v \
  --junitxml=$winout\\junit.xml \
  --log-file=$winout\\pytest.log --log-file-level=DEBUG" 2>&1 | tee "$out/console.log"
rc=${PIPESTATUS[0]}
set -e

echo "exit=$rc" | tee "$out/exit.txt"
exit "$rc"
