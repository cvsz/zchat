"""Sync local keep-log to a remote evidence endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .transport import EvidenceTransport, TransportResult


def sync_keep_log(transport: EvidenceTransport, log_path: Path, sender: Callable) -> TransportResult:
    if not log_path.exists():
        return TransportResult(ok=True, payload={"synced": 0})
    lines = log_path.read_text(encoding="utf-8").splitlines()
    return transport.submit("/evidence/sync", {"lines": lines}, sender)
