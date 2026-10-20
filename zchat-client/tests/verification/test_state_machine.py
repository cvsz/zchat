import pytest

from zchat_client.verification.state_machine import (
    VerificationResult,
    VerificationStateMachine,
)


def test_state_flow():
    sm = VerificationStateMachine()
    sm.start()
    sm.observe()
    sm.evaluate()
    sm.finish(VerificationResult.PASS)
    assert sm.result == VerificationResult.PASS


def test_invalid_finish():
    sm = VerificationStateMachine()
    with pytest.raises(RuntimeError):
        sm.finish(VerificationResult.PASS)
