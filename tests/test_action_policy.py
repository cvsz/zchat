"""Tests for camfrog.action_policy — fail-closed UI action authorization."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "camfrog"))


from action_policy import ActionDecision, authorize_action, is_write_action


def test_action_denied_without_human_approval():
    result = authorize_action(
        kind="send_room_message",
        human_approved=False,
        adapter_verified=True,
        actor_authorized=True,
        room_allowlisted=True,
        dry_run=True,
    )
    assert result.allowed is False
    assert "human approval" in result.reason


def test_action_denied_without_actor_authorization():
    result = authorize_action(
        kind="send_room_message",
        human_approved=True,
        adapter_verified=True,
        actor_authorized=False,
        room_allowlisted=True,
        dry_run=True,
    )
    assert result.allowed is False
    assert "actor is not authorized" in result.reason


def test_action_denied_without_room_allowlist():
    result = authorize_action(
        kind="send_room_message",
        human_approved=True,
        adapter_verified=True,
        actor_authorized=True,
        room_allowlisted=False,
        dry_run=True,
    )
    assert result.allowed is False
    assert "room is not allowlisted" in result.reason


def test_action_denied_without_adapter_verification():
    result = authorize_action(
        kind="send_room_message",
        human_approved=True,
        adapter_verified=False,
        actor_authorized=True,
        room_allowlisted=True,
        dry_run=True,
    )
    assert result.allowed is False
    assert "adapter is not verified" in result.reason


def test_action_denied_in_dry_run():
    result = authorize_action(
        kind="send_room_message",
        human_approved=True,
        adapter_verified=True,
        actor_authorized=True,
        room_allowlisted=True,
        dry_run=True,
    )
    assert result.allowed is False
    assert "dry-run" in result.reason


def test_action_remains_denied_even_when_all_gates_pass():
    """Even with every gate passed, execution remains disabled until a
    reviewed UI executor is implemented."""
    result = authorize_action(
        kind="send_room_message",
        human_approved=True,
        adapter_verified=True,
        actor_authorized=True,
        room_allowlisted=True,
        dry_run=False,
    )
    assert result.allowed is False
    assert "disabled" in result.reason


def test_is_write_action():
    for kind in (
        "send_room_message",
        "welcome_message",
        "moderation_action",
        "status_update",
        "profile_change",
    ):
        assert is_write_action(kind) is True


def test_decision_is_frozen_dataclass():
    d = ActionDecision(allowed=False, reason="test")
    try:
        d.allowed = True  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ActionDecision should be frozen")
