"""Integration helpers for status/name changer verification."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .logger import VerificationLogger


@dataclass
class VerificationRun:
    run_id: str = field(default_factory=lambda: "run-" + uuid.uuid4().hex[:12])
    requested_value: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def record_request(logger: VerificationLogger, run: VerificationRun, module: str) -> None:
    logger.log(
        "status_change_requested",
        verification_run_id=run.run_id,
        module=module,
        expected_status=run.requested_value,
    )
