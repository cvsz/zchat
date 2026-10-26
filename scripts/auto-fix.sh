#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/mnt/d/data/zchat}"
BRANCH_NAME="${BRANCH_NAME:-pr-b-evidence}"

echo "auto-fix: cleaning merge state..."
git merge --abort 2>/dev/null || true
git reset --merge HEAD 2>/dev/null || true
git checkout -f HEAD 2>/dev/null || true

echo "auto-fix: checking gh auth..."
if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: gh is not authenticated. Run: gh auth login"
    exit 1
fi

echo "auto-fix: checkout pr-b-evidence..."
if ! git checkout "$BRANCH_NAME" 2>/dev/null; then
    if ! gh pr checkout 87; then
        echo "ERROR: could not checkout $BRANCH_NAME"
        exit 1
    fi
fi

echo "auto-fix: merge origin/main into $BRANCH_NAME..."
set +e
git merge origin/main 2>&1
merge_rc=$?
set -e
if [ $merge_rc -ne 0 ]; then
    echo "auto-fix: merge produced conflicts (expected) — proceeding to resolution"
fi

echo "auto-fix: resolving conflict markers (keep pr-b-evidence side)..."
python3 - <<'PYEOF'
import re
from pathlib import Path

files = [
    "tests/test_evidence_reconciliation.py",
    "tests/test_evidence_validator.py",
    "tools/evidence_reconciliation.py",
]

def resolve_conflicts(text: str) -> str:
    """Keep the TOP (pr-b-evidence / HEAD) side of every conflict block."""
    lines = text.split("\n")
    out = []
    mode = "normal"  # normal | keep_top | skip_bottom
    for line in lines:
        if line.startswith("<<<<<<<"):
            mode = "keep_top"
            continue
        elif line.startswith("=======") and mode == "keep_top":
            mode = "skip_bottom"
            continue
        elif line.startswith(">>>>>>>") and mode in ("keep_top", "skip_bottom"):
            mode = "normal"
            continue
        if mode == "normal" or mode == "keep_top":
            out.append(line)
    return "\n".join(out)

for f in files:
    p = Path(f)
    if not p.exists():
        print("  skip (not found): " + f)
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    new_text = resolve_conflicts(text)
    p.write_text(new_text, encoding="utf-8")
    print("  resolved: " + f)
PYEOF

echo "auto-fix: verifying no conflict markers remain..."
conflict_found=0
for f in tests/test_evidence_reconciliation.py tests/test_evidence_validator.py tools/evidence_reconciliation.py; do
    if grep -qE '^(<<<<<<<|=======|>>>>>>>)' "$f" 2>/dev/null; then
        echo "WARN: conflict markers still in $f"
        conflict_found=1
    fi
done
if [ "$conflict_found" -ne 0 ]; then
    echo "ERROR: conflicts remain. aborting."
    exit 1
fi
echo "auto-fix: all conflict markers removed."

echo "auto-fix: python compile check..."
python3 -m py_compile tools/evidence_reconciliation.py tests/test_evidence_reconciliation.py tests/test_evidence_validator.py

echo "auto-fix: running evidence tests..."
python3 -m pytest tests/test_evidence_reconciliation.py tests/test_evidence_validator.py -v

echo "auto-fix: committing and pushing..."
git add tests/test_evidence_reconciliation.py tests/test_evidence_validator.py tools/evidence_reconciliation.py
git commit -m "resolve PR #87 conflicts: keep hardened evidence reconciliation (pr-b-evidence side)"
git push origin "$BRANCH_NAME"

echo "auto-fix: DONE"
