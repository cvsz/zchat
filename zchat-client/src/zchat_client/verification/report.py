"""Verification report generation."""

from __future__ import annotations

import json
from pathlib import Path


def write_report(run_dir: Path, data: dict) -> Path:
    report_path = run_dir / "verification-report.json"
    report_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report_path
