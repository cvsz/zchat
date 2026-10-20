"""Structured JSONL logger for verification events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class VerificationLogger:
    def __init__(self, path: Path, redact_pii: bool = True) -> None:
        self.path = path
        self.redact_pii = redact_pii
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {
            "schema_version": "1.0",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        if self.redact_pii:
            for key in ("password", "token", "session_token"):
                if key in record:
                    record[key] = "[REDACTED]"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record
