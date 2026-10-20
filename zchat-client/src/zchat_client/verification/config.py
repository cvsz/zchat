"""Configuration validation for second-account verification."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationConfig:
    enabled: bool = False
    role: str = "primary"
    target_account: str = ""
    run_timeout_seconds: int = 60
    poll_interval_seconds: int = 3
    evidence_enabled: bool = True
    evidence_dir: str = "./evidence"
    evidence_retention_days: int = 14
    evidence_screenshots: bool = True
    evidence_redact_pii: bool = True
    observer_host: str = "127.0.0.1"
    observer_port: int = 8765
    observer_tls_enabled: bool = True


def load_config(env: dict[str, str] | None = None) -> VerificationConfig:
    source = env if env is not None else os.environ
    def _bool(key: str, default: bool) -> bool:
        raw = source.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    role = source.get("ZCHAT_VERIFICATION_ROLE", "primary").strip().lower()
    if role not in {"primary", "secondary"}:
        raise ValueError("ZCHAT_VERIFICATION_ROLE must be primary or secondary")

    timeout = int(source.get("ZCHAT_VERIFICATION_RUN_TIMEOUT_SECONDS", "60"))
    if timeout <= 0:
        raise ValueError("ZCHAT_VERIFICATION_RUN_TIMEOUT_SECONDS must be positive")

    poll = int(source.get("ZCHAT_VERIFICATION_POLL_INTERVAL_SECONDS", "3"))
    if poll <= 0:
        raise ValueError("ZCHAT_VERIFICATION_POLL_INTERVAL_SECONDS must be positive")

    port = int(source.get("ZCHAT_OBSERVER_PORT", "8765"))
    if not (1 <= port <= 65535):
        raise ValueError("ZCHAT_OBSERVER_PORT out of range")

    retention = int(source.get("ZCHAT_EVIDENCE_RETENTION_DAYS", "14"))
    if retention < 0:
        raise ValueError("ZCHAT_EVIDENCE_RETENTION_DAYS must be non-negative")

    return VerificationConfig(
        enabled=_bool("ZCHAT_VERIFICATION_ENABLED", False),
        role=role,
        target_account=source.get("ZCHAT_VERIFICATION_TARGET_ACCOUNT", "").strip(),
        run_timeout_seconds=timeout,
        poll_interval_seconds=poll,
        evidence_enabled=_bool("ZCHAT_EVIDENCE_ENABLED", True),
        evidence_dir=source.get("ZCHAT_EVIDENCE_DIR", "./evidence").strip() or "./evidence",
        evidence_retention_days=retention,
        evidence_screenshots=_bool("ZCHAT_EVIDENCE_SCREENSHOTS", True),
        evidence_redact_pii=_bool("ZCHAT_EVIDENCE_REDACT_PII", True),
        observer_host=source.get("ZCHAT_OBSERVER_HOST", "127.0.0.1").strip() or "127.0.0.1",
        observer_port=port,
        observer_tls_enabled=_bool("ZCHAT_OBSERVER_TLS_ENABLED", True),
    )
