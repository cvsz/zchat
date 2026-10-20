import pytest

from zchat_client.verification.config import load_config


def test_load_config_defaults():
    cfg = load_config({})
    assert cfg.enabled is False
    assert cfg.role == "primary"


def test_load_config_invalid_role():
    with pytest.raises(ValueError):
        load_config({"ZCHAT_VERIFICATION_ROLE": "foo"})


def test_load_config_timeout():
    with pytest.raises(ValueError):
        load_config({"ZCHAT_VERIFICATION_RUN_TIMEOUT_SECONDS": "0"})


@pytest.mark.parametrize(
    "key",
    [
        "ZCHAT_OBSERVER_TLS_ENABLED",
        "ZCHAT_EVIDENCE_REDACT_PII",
        "ZCHAT_VERIFICATION_ENABLED",
    ],
)
def test_invalid_boolean_fails_closed(key):
    with pytest.raises(ValueError, match=key):
        load_config({key: "treu"})


def test_explicit_false_boolean_is_accepted():
    assert (
        load_config({"ZCHAT_EVIDENCE_REDACT_PII": "false"}).evidence_redact_pii is False
    )
