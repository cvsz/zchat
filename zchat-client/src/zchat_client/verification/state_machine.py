"""Verification result state machine."""

from __future__ import annotations

from enum import Enum


class VerificationResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"


class VerificationState(str, Enum):
    IDLE = "IDLE"
    STARTED = "STARTED"
    OBSERVING = "OBSERVING"
    EVALUATING = "EVALUATING"
    COMPLETE = "COMPLETE"


class VerificationStateMachine:
    def __init__(self) -> None:
        self.state = VerificationState.IDLE
        self.result: VerificationResult | None = None

    def start(self) -> None:
        if self.state != VerificationState.IDLE:
            raise RuntimeError("Cannot start from non-idle state")
        self.state = VerificationState.STARTED

    def observe(self) -> None:
        if self.state not in {VerificationState.STARTED, VerificationState.OBSERVING}:
            raise RuntimeError("Cannot observe before start")
        self.state = VerificationState.OBSERVING

    def evaluate(self) -> None:
        if self.state != VerificationState.OBSERVING:
            raise RuntimeError("Cannot evaluate before observation")
        self.state = VerificationState.EVALUATING

    def finish(self, result: VerificationResult) -> None:
        if self.state != VerificationState.EVALUATING:
            raise RuntimeError("Cannot finish before evaluation")
        self.result = result
        self.state = VerificationState.COMPLETE

    def reset(self) -> None:
        self.state = VerificationState.IDLE
        self.result = None
