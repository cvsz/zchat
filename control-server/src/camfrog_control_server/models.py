"""Persistent control-plane state. No Camfrog credentials or session material live here."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint(
            "runtime_state IN ('unknown', 'starting', 'running', 'error', 'disconnected')",
            name="ck_agents_runtime_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    runtime_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_accounts_agent_id"),
        Index("ix_accounts_agent_enabled", "agent_id", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    nickname: Mapped[str] = mapped_column(String(256), nullable=False)
    nickname_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AccountNicknameClaim(Base):
    """Unique, normalized nickname reservations that preserve legacy account rows."""

    __tablename__ = "account_nickname_claims"

    nickname_key: Mapped[str] = mapped_column(String(64), primary_key=True)


class ControlCommand(Base):
    __tablename__ = "commands"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('launch', 'stop', 'restart', 'join_room', 'leave_room', "
            "'add_friend', 'open_im')",
            name="ck_commands_operation",
        ),
        CheckConstraint(
            "state IN ('pending', 'claimed', 'succeeded', 'failed', 'effect_unknown', "
            "'operator_reconciled', 'canceled')",
            name="ck_commands_state",
        ),
        CheckConstraint(
            "result_code IS NULL OR result_code IN "
            "('ok', 'handoff_accepted', 'provider_not_configured', "
            "'client_not_found', 'timed_out', "
            "'execution_failed', 'lease_expired', 'operator_reconciled')",
            name="ck_commands_result_code",
        ),
        Index("ix_commands_agent_state_created", "agent_id", "state", "created_at"),
        Index("ix_commands_account_created", "account_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    claim_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token_hash: Mapped[str | None] = mapped_column(String(64))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    """Durable operator/agent messages; this is not a Camfrog network message."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "direction IN ('operator_to_agent', 'agent_to_operator')",
            name="ck_chat_messages_direction",
        ),
        CheckConstraint(
            "state IN ('queued', 'claimed', 'received', 'delivered', 'failed', 'canceled', "
            "'delivery_unknown')",
            name="ck_chat_messages_state",
        ),
        CheckConstraint(
            "(direction = 'agent_to_operator' AND state = 'received') OR "
            "(direction = 'operator_to_agent' AND state != 'received')",
            name="ck_chat_messages_direction_state",
        ),
        CheckConstraint(
            "(state = 'claimed' AND claim_token_hash IS NOT NULL AND claim_expires_at IS NOT NULL) "
            "OR (state != 'claimed' AND claim_token_hash IS NULL AND claim_expires_at IS NULL)",
            name="ck_chat_messages_claim_state",
        ),
        CheckConstraint(
            "(direction = 'agent_to_operator' AND delivered_at IS NOT NULL) OR "
            "(direction = 'operator_to_agent' AND "
            "((state = 'delivered' AND delivered_at IS NOT NULL) OR "
            "(state != 'delivered' AND delivered_at IS NULL)))",
            name="ck_chat_messages_delivery_timestamp",
        ),
        CheckConstraint("claim_attempts >= 0", name="ck_chat_messages_claim_attempts"),
        UniqueConstraint(
            "account_id", "direction", "client_message_id", name="uq_chat_messages_idempotency"
        ),
        Index(
            "ix_chat_messages_agent_state_created", "agent_id", "direction", "state", "created_at"
        ),
        Index("ix_chat_messages_account_created", "account_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    client_message_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(24), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    claim_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token_hash: Mapped[str | None] = mapped_column(String(64))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_created", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ProfileSnapshot(Base):
    """Latest structured profile data; authentication and payment fields are excluded."""

    __tablename__ = "profile_snapshots"
    __table_args__ = (
        CheckConstraint(
            "client_presence IN ('unknown', 'online', 'away', 'busy', 'offline')",
            name="ck_profile_snapshots_presence",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    nickname_color: Mapped[str | None] = mapped_column(String(24))
    nickname_flair: Mapped[str | None] = mapped_column(String(80))
    avatar_id: Mapped[int | None] = mapped_column(Integer)
    alevel: Mapped[int | None] = mapped_column(Integer)
    age: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(128))
    status_text: Mapped[str | None] = mapped_column(String(500))
    client_presence: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    subscription_type: Mapped[str | None] = mapped_column(String(32))
    xp_count: Mapped[int | None] = mapped_column(BigInteger)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AuthStateSnapshot(Base):
    """Sanitized browser authentication evidence; never stores cookies or CSRF values."""

    __tablename__ = "auth_state_snapshots"
    __table_args__ = (
        CheckConstraint(
            "state IN ('unknown', 'login_required', 'authenticated', 'public')",
            name="ck_auth_state_snapshots_state",
        ),
        CheckConstraint(
            "page_type IS NULL OR page_type IN ('profile', 'settings', 'public')",
            name="ck_auth_state_snapshots_page_type",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    page_type: Mapped[str | None] = mapped_column(String(16))
    csrf_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class FriendSnapshot(Base):
    """Latest friend-list entry received from the account's assigned agent."""

    __tablename__ = "friend_snapshots"
    __table_args__ = (
        UniqueConstraint("account_id", "nickname", name="uq_friend_snapshots_account_nickname"),
        CheckConstraint(
            "presence IN ('unknown', 'online', 'away', 'busy', 'offline')",
            name="ck_friend_snapshots_presence",
        ),
        Index("ix_friend_snapshots_account_presence", "account_id", "presence"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    nickname: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    presence: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    nickname_color: Mapped[str | None] = mapped_column(String(24))
    avatar_id: Mapped[int | None] = mapped_column(Integer)
    alevel: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class PresenceSnapshot(Base):
    """Latest human presence observation, separate from agent process state."""

    __tablename__ = "presence_snapshots"
    __table_args__ = (
        CheckConstraint(
            "state IN ('unknown', 'online', 'away', 'busy', 'offline')",
            name="ck_presence_snapshots_state",
        ),
        CheckConstraint(
            "source IN ('profile', 'friend_list', 'public_profile', 'status_image', 'operator')",
            name="ck_presence_snapshots_source",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="profile")
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AccountRuntimeSnapshot(Base):
    """Account-scoped process and room states, separate from agent/auth/presence state."""

    __tablename__ = "account_runtime_snapshots"
    __table_args__ = (
        CheckConstraint(
            "process_state IN ('unknown', 'starting', 'running', 'stopped', 'error')",
            name="ck_account_runtime_process_state",
        ),
        CheckConstraint(
            "room_state IN ('unknown', 'disconnected', 'joining', 'in_room')",
            name="ck_account_runtime_room_state",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    process_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    room_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class SubscriptionEntitlement(Base):
    """Durable baseline entitlement; temporary paid terms live in SubscriptionOverlay."""

    __tablename__ = "subscription_entitlements"
    __table_args__ = (
        CheckConstraint(
            "base_tier IN ('unknown', 'free', 'pro', 'extreme', 'gold')",
            name="ck_subscription_entitlements_tier",
        ),
        CheckConstraint(
            "base_term IN ('unknown', 'lifetime')",
            name="ck_subscription_entitlements_term",
        ),
        CheckConstraint(
            "evidence_source IN ('unknown', 'operator_report', 'official_record', 'manual_entry')",
            name="ck_subscription_entitlements_source",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    base_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    base_term: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    evidence_source: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    evidence_note: Mapped[str | None] = mapped_column(String(300))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class SubscriptionOverlay(Base):
    """A finite upgrade layered over an account's durable baseline."""

    __tablename__ = "subscription_overlays"
    __table_args__ = (
        CheckConstraint("tier IN ('pro', 'extreme', 'gold')", name="ck_subscription_overlays_tier"),
        CheckConstraint("expires_at > starts_at", name="ck_subscription_overlays_interval"),
        Index("ix_subscription_overlays_account_interval", "account_id", "starts_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual_entry")
    evidence_note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CloudBot(Base):
    """Cloud Bot registry metadata; it does not contain a bot password or session cookie."""

    __tablename__ = "cloud_bots"
    __table_args__ = (UniqueConstraint("agent_id", name="uq_cloud_bots_agent_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CloudBotStatusSnapshot(Base):
    """Append-only status observations reported by the bot's assigned agent."""

    __tablename__ = "cloud_bot_status_snapshots"
    __table_args__ = (
        CheckConstraint(
            "state IN ('unknown', 'starting', 'disconnected', 'authenticating', 'authenticated', "
            "'room_session_closed', 'joining_room', 'online', 'paused', 'invalid_room_key', "
            "'bot_not_allowed', 'room_not_found')",
            name="ck_cloud_bot_status_state",
        ),
        Index("ix_cloud_bot_status_snapshots_bot_observed", "cloud_bot_id", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    cloud_bot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cloud_bots.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    raw_status: Mapped[str | None] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(String(160))
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class StoreCatalogItem(Base):
    """Sanitized catalog metadata imported by an operator; never a payment token."""

    __tablename__ = "store_catalog_items"
    __table_args__ = (
        UniqueConstraint("source_item_id", "item_type", name="uq_store_items_source_type"),
        CheckConstraint(
            "item_type IN ('gift', 'sticker', 'chest', 'background', 'subscription')",
            name="ck_store_items_type",
        ),
        CheckConstraint("price_minor >= 0", name="ck_store_items_price"),
        Index("ix_store_items_available", "available", "item_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    source_item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    item_type: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class PurchaseIntent(Base):
    """A consent record only. The current implementation has no purchase executor."""

    __tablename__ = "purchase_intents"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending_confirmation', 'confirmed', 'canceled', 'expired')",
            name="ck_purchase_intents_state",
        ),
        CheckConstraint("price_minor >= 0", name="ck_purchase_intents_price"),
        Index("ix_purchase_intents_account_created", "account_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    catalog_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("store_catalog_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    item_name: Mapped[str] = mapped_column(String(256), nullable=False)
    item_type: Mapped[str] = mapped_column(String(24), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_confirmation")
    confirmation_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
