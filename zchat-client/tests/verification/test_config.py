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
