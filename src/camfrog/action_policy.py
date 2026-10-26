"""Fail-closed policy for Camfrog UI actions.

This module does not click or send messages. It only validates an action
request against explicit authorization conditions. A real adapter requires
target-specific UI selectors, explicit human approval, and post-action
verification before any write action can be enabled.

All decisions default to denied. Every gate must pass; a single failure
denies the action. Dry-run mode always denies execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActionKind = Literal[
    "send_room_message",
    "welcome_message",
    "moderation_action",
    "status_update",
    "profile_change",
]


@dataclass(frozen=True)
class ActionDecision:
    """Result of authorizing a UI action."""

    allowed: bool
    reason: str


def authorize_action(
    *,
    kind: ActionKind,
    human_approved: bool,
    adapter_verified: bool,
    actor_authorized: bool,
    room_allowlisted: bool,
    dry_run: bool = True,
) -> ActionDecision:
    """Authorize a Camfrog UI action under fail-closed policy.

    Args:
        kind: The action being requested.
        human_approved: A human reviewer has explicitly approved this action.
        adapter_verified: The Camfrog UI adapter for this action has been
            verified against the target client version.
        actor_authorized: The calling actor is authorized to perform actions.
        room_allowlisted: The target room is on the allowlist.
        dry_run: When True, no UI action is ever executed regardless of other
            gates.

    Returns:
        ActionDecision with allowed=False unless every gate passes and
        dry_run is False.
    """
    if not human_approved:
        return ActionDecision(False, "human approval required")
    if not actor_authorized:
        return ActionDecision(False, "actor is not authorized")
    if not room_allowlisted:
        return ActionDecision(False, "target room is not allowlisted")
    if not adapter_verified:
        return ActionDecision(False, "Camfrog UI adapter is not verified")
    if dry_run:
        return ActionDecision(False, "dry-run mode; no UI action executed")
    return ActionDecision(
        False,
        "execution remains disabled until a reviewed UI executor is implemented",
    )


def is_write_action(kind: ActionKind) -> bool:
    """Return True if the action kind mutates Camfrog client state."""
    return kind in (
        "send_room_message",
        "welcome_message",
        "moderation_action",
        "status_update",
        "profile_change",
    )
