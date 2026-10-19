"""Validated HTTP input/output models."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentRuntimeState(StrEnum):
    unknown = "unknown"
    starting = "starting"
    running = "running"
    error = "error"
    disconnected = "disconnected"


class ClientProcessState(StrEnum):
    unknown = "unknown"
    starting = "starting"
    running = "running"
    stopped = "stopped"
    error = "error"


class AccountRoomState(StrEnum):
    unknown = "unknown"
    disconnected = "disconnected"
    joining = "joining"
    in_room = "in_room"


class CommandOperation(StrEnum):
    launch = "launch"
    stop = "stop"
    restart = "restart"
    join_room = "join_room"
    leave_room = "leave_room"
    add_friend = "add_friend"
    open_im = "open_im"


class CommandState(StrEnum):
    pending = "pending"
    claimed = "claimed"
    succeeded = "succeeded"
    failed = "failed"
    effect_unknown = "effect_unknown"
    operator_reconciled = "operator_reconciled"
    canceled = "canceled"


class CommandResultState(StrEnum):
    succeeded = "succeeded"
    failed = "failed"
    effect_unknown = "effect_unknown"


class CommandResultCode(StrEnum):
    ok = "ok"
    handoff_accepted = "handoff_accepted"
    provider_not_configured = "provider_not_configured"
    client_not_found = "client_not_found"
    timed_out = "timed_out"
    execution_failed = "execution_failed"
    lease_expired = "lease_expired"
    operator_reconciled = "operator_reconciled"


class CommandReconciliationOutcome(StrEnum):
    effect_observed = "effect_observed"
    no_effect_observed = "no_effect_observed"


class CommandReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: CommandReconciliationOutcome
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    acknowledge_manual_attestation: Literal[True]

    @field_validator("observed_at")
    @classmethod
    def require_aware_observation_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Observation time must include a timezone.")
        if value > datetime.now(UTC):
            raise ValueError("Observation time cannot be in the future.")
        return value


class ChatMessageDirection(StrEnum):
    operator_to_agent = "operator_to_agent"
    agent_to_operator = "agent_to_operator"


class ChatMessageState(StrEnum):
    queued = "queued"
    claimed = "claimed"
    received = "received"
    delivered = "delivered"
    failed = "failed"
    canceled = "canceled"
    delivery_unknown = "delivery_unknown"


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Agent name must be printable text.")
        return value


class AgentCreated(BaseModel):
    id: UUID
    name: str
    agent_token: str = Field(repr=False)
    created_at: datetime


class AgentTokenRotated(BaseModel):
    agent_id: UUID
    agent_token: str = Field(repr=False)


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    runtime_state: AgentRuntimeState
    created_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None


class AccountCreate(BaseModel):
    nickname: str = Field(min_length=1, max_length=256)
    agent_id: UUID | None = None
    enabled: bool = True

    @field_validator("nickname")
    @classmethod
    def clean_nickname(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Nickname must be printable text.")
        return value


class AccountAgentAssign(BaseModel):
    agent_id: UUID | None


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nickname: str
    agent_id: UUID | None
    enabled: bool
    created_at: datetime


class AccountAgentAssignmentRead(AccountRead):
    agent_token: str | None = Field(default=None, repr=False)


class HeartbeatRequest(BaseModel):
    state: AgentRuntimeState = AgentRuntimeState.running
    client_process_state: ClientProcessState | None = None


class HeartbeatResponse(BaseModel):
    agent_id: UUID
    state: AgentRuntimeState
    account_id: UUID | None
    client_process_state: ClientProcessState | None
    last_seen_at: datetime


class AgentChatSessionRead(BaseModel):
    """Read the current assignment without changing command-agent presence."""

    account_id: UUID | None
    account_nickname: str | None = Field(default=None, max_length=256)
    subscription_tier: str = Field(default="unknown", pattern="^(unknown|free|pro|extreme|gold)$")
    subscription_source: str = Field(
        default="baseline_not_recorded",
        pattern="^(temporary_overlay|durable_baseline|overlapping_overlays|baseline_not_recorded|baseline_unverified)$",
    )
    subscription_evidence_source: str = Field(
        default="unknown", pattern="^(unknown|operator_report|official_record|manual_entry)$"
    )
    subscription_expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_assignment_summary(self) -> AgentChatSessionRead:
        if self.account_id is None and self.account_nickname is not None:
            raise ValueError("An unassigned agent cannot have an account nickname.")
        if self.account_id is None and self.subscription_tier != "unknown":
            raise ValueError("An unassigned agent cannot have a subscription tier.")
        return self


class AccountRuntimeRead(BaseModel):
    account_id: UUID
    nickname: str
    process_state: ClientProcessState
    room_state: AccountRoomState
    observed_at: datetime | None


class ZChatFeatureCapability(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    feature_id: str
    provider: str
    standalone_supported: bool
    unified_supported: bool
    requires_provider_client: bool
    supports_offline: bool
    supports_read_only: bool
    supports_strict_background: bool
    supports_minimized_client: bool
    supports_hidden_client: bool
    supports_remote_commands: bool
    supports_external_outcome_verification: bool
    requires_exclusive_resource: bool
    verified_platforms: list[str]
    verified_provider_versions: list[str]
    implementation_status: str
    evidence_status: str

    @field_validator("feature_id", "provider", "implementation_status", "evidence_status")
    @classmethod
    def require_non_empty_contract_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Capability contract labels must not be empty.")
        return normalized

    @field_validator("verified_platforms", "verified_provider_versions")
    @classmethod
    def require_unique_non_empty_evidence_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Capability evidence values must be non-empty and trimmed.")
        if len(values) != len(set(values)):
            raise ValueError("Capability evidence values must be unique.")
        return values


class ZChatWorkforceCapabilitiesV1(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    protocol: str
    protocol_version: str
    compatible_versions: list[str]
    server_version: str
    features: list[ZChatFeatureCapability]
    read_only_resources: list[str]

    @field_validator("protocol", "protocol_version", "server_version")
    @classmethod
    def require_non_empty_contract_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Capability contract versions must not be empty.")
        return normalized

    @field_validator("compatible_versions", "read_only_resources")
    @classmethod
    def require_unique_non_empty_contract_values(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Capability contract values must be non-empty and trimmed.")
        if len(values) != len(set(values)):
            raise ValueError("Capability contract values must be unique.")
        return values

    @model_validator(mode="after")
    def reject_duplicate_feature_ids(self) -> ZChatWorkforceCapabilitiesV1:
        feature_ids = [feature.feature_id for feature in self.features]
        if len(feature_ids) != len(set(feature_ids)):
            raise ValueError("Capability feature IDs must be unique.")
        return self


class CommandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    operation: CommandOperation
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_operation_payload(self) -> CommandCreate:
        if self.operation == CommandOperation.join_room:
            if set(self.payload) != {"room"} or not isinstance(self.payload["room"], str):
                raise ValueError("join_room payload must contain only a room string.")
            room = self.payload["room"].strip()
            if not room or len(room) > 128:
                raise ValueError("Room name must contain 1 to 128 characters.")
            if any(ord(char) < 32 or ord(char) == 127 for char in room):
                raise ValueError("Room name cannot contain control characters.")
            self.payload = {"room": room}
        elif self.operation in {CommandOperation.add_friend, CommandOperation.open_im}:
            if set(self.payload) != {"nickname"} or not isinstance(self.payload["nickname"], str):
                raise ValueError(f"{self.operation.value} payload must contain only a nickname.")
            nickname = self.payload["nickname"].strip()
            if not nickname or len(nickname) > 256:
                raise ValueError("Nickname must contain 1 to 256 characters.")
            if any(ord(char) < 32 or ord(char) == 127 for char in nickname):
                raise ValueError("Nickname cannot contain control characters.")
            self.payload = {"nickname": nickname}
        elif self.payload:
            raise ValueError(f"{self.operation.value} does not accept a payload.")
        return self


class CommandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    account_id: UUID
    operation: CommandOperation
    payload: dict[str, Any]
    state: CommandState
    claim_attempts: int
    result_code: CommandResultCode | None
    created_at: datetime
    claimed_at: datetime | None
    finished_at: datetime | None


class AgentCommand(BaseModel):
    id: UUID
    account_id: UUID
    operation: CommandOperation
    payload: dict[str, Any]
    claim_token: str = Field(repr=False)
    claim_issued_at: datetime
    claim_expires_at: datetime


class CommandPollResponse(BaseModel):
    command: AgentCommand | None


class ChatMessageCreate(BaseModel):
    account_id: UUID
    client_message_id: UUID
    body: str = Field(min_length=1, max_length=4000, repr=False)

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        return clean_chat_body(value)


def clean_chat_body(value: str) -> str:
    """Normalize and validate the text body shared by both chat directions."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        raise ValueError("Message body cannot be empty or whitespace only.")
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        raise ValueError("Message body cannot contain control characters.")
    if len(value) > 4000:
        raise ValueError("Message body must contain at most 4000 characters.")
    return value


class AgentChatMessageCreate(ChatMessageCreate):
    pass


class OperatorChatMessageCreate(ChatMessageCreate):
    pass


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    agent_id: UUID
    client_message_id: UUID
    direction: ChatMessageDirection
    body: str = Field(repr=False)
    state: ChatMessageState
    claim_attempts: int
    created_at: datetime
    delivered_at: datetime | None


class AgentChatMessage(BaseModel):
    id: UUID
    account_id: UUID
    client_message_id: UUID
    body: str = Field(min_length=1, max_length=4000, repr=False)
    claim_token: str = Field(min_length=32, max_length=128, repr=False)
    claim_expires_at: datetime

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        return clean_chat_body(value)

    @field_validator("claim_expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Claim expiry must include a timezone.")
        return value


class ChatMessagePollResponse(BaseModel):
    message: AgentChatMessage | None


class ChatMessageAckRequest(BaseModel):
    claim_token: str = Field(min_length=32, max_length=128, repr=False)


class CommandResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_token: str = Field(min_length=32, max_length=128, repr=False)
    state: CommandResultState
    code: CommandResultCode

    @model_validator(mode="after")
    def result_state_matches_code(self) -> CommandResultRequest:
        if self.code == CommandResultCode.operator_reconciled:
            raise ValueError("Only the operator reconciliation route can use this result code.")
        if self.state == CommandResultState.succeeded and self.code != CommandResultCode.ok:
            raise ValueError("Successful commands must use result code 'ok'.")
        if self.state == CommandResultState.failed and self.code in {
            CommandResultCode.ok,
            CommandResultCode.handoff_accepted,
        }:
            raise ValueError("Failed commands must use a failure result code.")
        if (
            self.state == CommandResultState.effect_unknown
            and self.code != CommandResultCode.handoff_accepted
        ):
            raise ValueError("Unknown external effects must use result code 'handoff_accepted'.")
        return self


class Overview(BaseModel):
    agents_active: int
    accounts_enabled: int
    commands_pending: int
    commands_claimed: int
    profiles_reported: int = 0
    cloud_bots_registered: int = 0
    purchase_intents_pending: int = 0


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_type: str
    actor_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    details: dict[str, Any]
    created_at: datetime


class HumanPresence(StrEnum):
    unknown = "unknown"
    online = "online"
    away = "away"
    busy = "busy"
    offline = "offline"


class AuthenticatedState(StrEnum):
    unknown = "unknown"
    login_required = "login_required"
    authenticated = "authenticated"
    public = "public"


class AuthStateObservationInput(BaseModel):
    nav_user_logged: bool = False
    login_form_visible: bool = False
    external_data_type: str | None = Field(default=None, pattern="^(profile|settings|public)$")
    csrf_present: bool = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("observed_at")
    @classmethod
    def require_aware_auth_observation(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone.")
        return value


class BalanceSummary(BaseModel):
    points: float | None = Field(default=None, ge=0)
    coins: float | None = Field(default=None, ge=0)
    rewards: float | None = Field(default=None, ge=0)
    next_level_remain: float | None = Field(default=None, ge=0)
    next_level_difference: float | None = Field(default=None, ge=0)

    @field_validator("points", "coins", "rewards", "next_level_remain", "next_level_difference")
    @classmethod
    def finite_balance(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Balance values must be finite numbers.")
        return value


class RewardProgramSummary(BaseModel):
    engine: str | None = Field(default=None, max_length=32)
    enabled: bool | None = None
    show: bool | None = None
    balance: float | None = Field(default=None, ge=0)
    streak_count: int | None = Field(default=None, ge=0)
    activity_count: int | None = Field(default=None, ge=0)


class FriendCountsSummary(BaseModel):
    all: int | None = Field(default=None, ge=0)
    online: int | None = Field(default=None, ge=0)
    added: int | None = Field(default=None, ge=0)


class ProfileDataSummary(BaseModel):
    balance: BalanceSummary = Field(default_factory=BalanceSummary)
    reward_program: RewardProgramSummary = Field(default_factory=RewardProgramSummary)
    gift_count: int | None = Field(default=None, ge=0)
    xpback_count: int | None = Field(default=None, ge=0)
    friend_counts: FriendCountsSummary = Field(default_factory=FriendCountsSummary)
    have_room: bool | None = None
    notifications_count: int | None = Field(default=None, ge=0)


class ProfileSnapshotInput(BaseModel):
    nickname_color: str | None = Field(default=None, max_length=24)
    nickname_flair: str | None = Field(default=None, max_length=80)
    avatar_id: int | None = Field(default=None, ge=0)
    alevel: int | None = Field(default=None, ge=0)
    age: int | None = Field(default=None, ge=0, le=130)
    gender: int | None = Field(default=None, ge=0, le=16)
    country: str | None = Field(default=None, max_length=128)
    status_text: str | None = Field(default=None, max_length=500)
    client_presence: HumanPresence = HumanPresence.unknown
    subscription_type: str | None = Field(default=None, max_length=32)
    xp_count: int | None = Field(default=None, ge=0)
    summary: ProfileDataSummary = Field(default_factory=ProfileDataSummary)


class FriendSnapshotInput(BaseModel):
    nickname: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)
    presence: HumanPresence = HumanPresence.unknown
    nickname_color: str | None = Field(default=None, max_length=24)
    avatar_id: int | None = Field(default=None, ge=0)
    alevel: int | None = Field(default=None, ge=0)
    gender: int | None = Field(default=None, ge=0, le=16)

    @field_validator("nickname", "display_name")
    @classmethod
    def clean_friend_names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Friend names must be printable text.")
        return value


class AccountSnapshotInput(BaseModel):
    account_id: UUID
    profile: ProfileSnapshotInput
    friends: list[FriendSnapshotInput] = Field(default_factory=list, max_length=1000)
    auth: AuthStateObservationInput | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("observed_at")
    @classmethod
    def require_aware_observation(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone.")
        return value

    @model_validator(mode="after")
    def reject_duplicate_friends(self) -> AccountSnapshotInput:
        nicknames = [friend.nickname.casefold() for friend in self.friends]
        if len(nicknames) != len(set(nicknames)):
            raise ValueError("Friend list must not contain duplicate nicknames.")
        return self


class ProfileSnapshotRead(BaseModel):
    account_id: UUID
    nickname: str
    nickname_color: str | None
    nickname_flair: str | None
    avatar_id: int | None
    alevel: int | None
    age: int | None
    gender: int | None
    country: str | None
    status_text: str | None
    client_presence: HumanPresence
    subscription_type: str | None
    xp_count: int | None
    summary: ProfileDataSummary
    auth_state: AuthenticatedState = AuthenticatedState.unknown
    observed_at: datetime


class AuthStateRead(BaseModel):
    account_id: UUID
    nickname: str
    state: AuthenticatedState
    page_type: str | None
    csrf_present: bool
    observed_at: datetime | None


class FriendSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    nickname: str
    display_name: str | None
    presence: HumanPresence
    nickname_color: str | None
    avatar_id: int | None
    alevel: int | None
    gender: int | None
    observed_at: datetime


class PresenceRead(BaseModel):
    account_id: UUID
    nickname: str
    state: HumanPresence
    source: str
    observed_at: datetime


class CloudBotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    agent_id: UUID

    @field_validator("name")
    @classmethod
    def clean_bot_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Cloud Bot name must be printable text.")
        return value


class CloudBotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    agent_id: UUID
    created_at: datetime


class CloudBotStatusInput(BaseModel):
    raw_status: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=160)
    uptime_seconds: int | None = Field(default=None, ge=0)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("observed_at")
    @classmethod
    def require_aware_bot_observation(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone.")
        return value


class CloudBotStatusRead(BaseModel):
    cloud_bot_id: UUID
    state: str
    raw_status: str | None
    reason: str | None
    uptime_seconds: int | None
    observed_at: datetime | None


class StoreItemType(StrEnum):
    gift = "gift"
    sticker = "sticker"
    chest = "chest"
    background = "background"
    subscription = "subscription"


class StoreCatalogItemCreate(BaseModel):
    source_item_id: str = Field(min_length=1, max_length=120)
    item_type: StoreItemType
    name: str = Field(min_length=1, max_length=256)
    currency: str = Field(min_length=1, max_length=12)
    price_minor: int = Field(ge=0)
    available: bool = True
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict, max_length=32)

    @field_validator("source_item_id", "name", "currency")
    @classmethod
    def clean_store_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Store item fields must be printable text.")
        return value

    @field_validator("metadata")
    @classmethod
    def reject_sensitive_store_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = ("password", "cookie", "token", "csrf", "captcha", "payment", "secret")
        for key, item in value.items():
            if any(word in key.casefold() for word in forbidden):
                raise ValueError("Store metadata cannot contain credential or payment fields.")
            if len(key) > 64 or (isinstance(item, str) and len(item) > 512):
                raise ValueError("Store metadata values exceed the allowed size.")
        return value


class StoreCatalogItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_item_id: str
    item_type: StoreItemType
    name: str
    currency: str
    price_minor: int
    available: bool
    metadata_json: dict[str, Any]
    updated_at: datetime


class PurchaseIntentCreate(BaseModel):
    account_id: UUID
    catalog_item_id: UUID


class PurchaseIntentConfirm(BaseModel):
    confirmed: bool
    expected_item_name: str = Field(min_length=1, max_length=256)
    expected_currency: str = Field(min_length=1, max_length=12)
    expected_price_minor: int = Field(ge=0)

    @model_validator(mode="after")
    def require_explicit_confirmation(self) -> PurchaseIntentConfirm:
        if self.confirmed is not True:
            raise ValueError("Explicit purchase confirmation is required.")
        return self


class PurchaseIntentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    catalog_item_id: UUID
    item_name: str
    item_type: StoreItemType
    currency: str
    price_minor: int
    state: str
    confirmation_expires_at: datetime
    confirmed_at: datetime | None
    created_at: datetime


class SubscriptionTier(StrEnum):
    unknown = "unknown"
    free = "free"
    pro = "pro"
    extreme = "extreme"
    gold = "gold"


class EntitlementEvidenceSource(StrEnum):
    unknown = "unknown"
    operator_report = "operator_report"
    official_record = "official_record"
    manual_entry = "manual_entry"


class SubscriptionBaselinePut(BaseModel):
    base_tier: SubscriptionTier
    base_term: str = Field(pattern="^(unknown|lifetime)$")
    evidence_source: EntitlementEvidenceSource = EntitlementEvidenceSource.manual_entry
    evidence_note: str | None = Field(default=None, max_length=300)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("observed_at")
    @classmethod
    def require_aware_entitlement_observation(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_lifetime_tier(self) -> SubscriptionBaselinePut:
        if self.base_term == "lifetime" and self.base_tier == SubscriptionTier.unknown:
            raise ValueError("Lifetime entitlement needs a known baseline tier.")
        return self


class SubscriptionOverlayCreate(BaseModel):
    tier: SubscriptionTier
    starts_at: datetime
    expires_at: datetime
    evidence_source: EntitlementEvidenceSource = EntitlementEvidenceSource.manual_entry
    evidence_note: str | None = Field(default=None, max_length=300)

    @field_validator("starts_at", "expires_at")
    @classmethod
    def require_aware_overlay_dates(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Subscription dates must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_overlay(self) -> SubscriptionOverlayCreate:
        if self.tier not in {SubscriptionTier.pro, SubscriptionTier.extreme, SubscriptionTier.gold}:
            raise ValueError("A temporary overlay must be Pro, Extreme, or Gold.")
        if self.expires_at <= self.starts_at:
            raise ValueError("Overlay expiry must be later than its start.")
        return self


class SubscriptionOverlayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tier: SubscriptionTier
    starts_at: datetime
    expires_at: datetime
    evidence_source: EntitlementEvidenceSource
    evidence_note: str | None
    created_at: datetime


class SubscriptionRead(BaseModel):
    account_id: UUID
    base_tier: SubscriptionTier
    base_term: str
    evidence_source: EntitlementEvidenceSource
    evidence_note: str | None
    observed_at: datetime | None
    effective_tier: SubscriptionTier
    effective_source: str
    active_overlay: SubscriptionOverlayRead | None
    overlays: list[SubscriptionOverlayRead]
