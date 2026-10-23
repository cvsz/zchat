"""Evidence bundle helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def create_run_dir(base: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
        raise ValueError("Invalid evidence run ID")
    root = base.resolve()
    run_dir = (root / run_id).resolve()
    if run_dir.parent != root:
        raise ValueError("Evidence run ID escapes root")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "primary").mkdir(exist_ok=True)
    (run_dir / "secondary" / "screenshots").mkdir(parents=True, exist_ok=True)
    return run_dir


def write_manifest(run_dir: Path, data: dict) -> Path:
    manifest = run_dir / "manifest.json"
    manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(run_dir: Path) -> Path:
    out = run_dir / "checksums.sha256"
    lines = []
    for p in sorted(run_dir.rglob("*")):
        if p.is_file() and p.name != "checksums.sha256":
            lines.append(f"{sha256_file(p)}  {p.relative_to(run_dir).as_posix()}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
