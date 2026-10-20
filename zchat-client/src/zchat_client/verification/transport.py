"""Authenticated evidence transport client with bounded retry."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class TransportResult:
    ok: bool
    status_code: int | None = None
    payload: Any = None
    error: str | None = None


class EvidenceTransport:
    def __init__(
        self, base_url: str, timeout: float = 10.0, max_retries: int = 3
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if timeout <= 0 or max_retries < 0:
            raise ValueError("timeout must be positive and max_retries non-negative")
        self.max_retries = max_retries

    def submit(
        self,
        path: str,
        payload: dict,
        sender: Callable[[str, dict, float], TransportResult],
    ) -> TransportResult:
        attempt = 0
        last: TransportResult | None = None
        while attempt <= self.max_retries:
            try:
                last = sender(self.base_url + path, payload, self.timeout)
            except (OSError, TimeoutError, ConnectionError) as exc:
                last = TransportResult(ok=False, error=type(exc).__name__)
            if last.ok:
                return last
            attempt += 1
            if attempt <= self.max_retries:
                time.sleep(min(2 ** (attempt - 1), 8))
        return (
            last if last is not None else TransportResult(ok=False, error="no_attempt")
        )
