#!/usr/bin/env bash
# Run the Windows test suite from WSL and capture all output.
# Prerequisite (one-time, on Windows): py -3 -m pip install -r requirements.txt -r requirements-dev.txt
set -euo pipefail
ts=$(date +%Y%m%d-%H%M%S)
out="reports/windows-runs/$ts"; mkdir -p "$out"
winrepo=$(wslpath -w "$PWD")
winout=$(wslpath -w "$PWD/$out")
cmd.exe /c "cd /d $winrepo && py -3 -m pytest windows_tests tests -rs -v \
  --junitxml=$winout\\junit.xml \
  --log-file=$winout\\pytest.log --log-file-level=DEBUG" 2>&1 | tee "$out/console.log"
echo "exit=${PIPESTATUS[0]}" | tee "$out/exit.txt"
