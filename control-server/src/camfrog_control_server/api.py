"""Control-plane HTTP interface for accounts, agents, commands, and audit history."""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, timedelta
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_identity import account_nickname_key
from .feature_capabilities import get_feature_capabilities
from .models import (
    Account,
    AccountNicknameClaim,
    AccountRuntimeSnapshot,
    Agent,
    AuditEvent,
    ChatMessage,
    CloudBot,
    ControlCommand,
    ProfileSnapshot,
    PurchaseIntent,
    SubscriptionEntitlement,
    SubscriptionOverlay,
    utcnow,
)
from .schemas import (
    AccountAgentAssign,
    AccountAgentAssignmentRead,
    AccountCreate,
    AccountRead,
    AgentChatMessage,
    AgentChatMessageCreate,
    AgentChatSessionRead,
    AgentCommand,
    AgentCreate,
    AgentCreated,
    AgentRead,
    AgentRuntimeState,
    AgentTokenRotated,
    AuditRead,
    ChatMessageAckRequest,
    ChatMessageDirection,
    ChatMessagePollResponse,
    ChatMessageRead,
    ChatMessageState,
    ClientProcessState,
    CommandCreate,
    CommandPollResponse,
    CommandRead,
    CommandReconciliationRequest,
    CommandResultCode,
    CommandResultRequest,
    CommandState,
    HeartbeatRequest,
    HeartbeatResponse,
    OperatorChatMessageCreate,
    Overview,
    ZChatWorkforceCapabilitiesV1,
)
from .security import (
    hash_token,
    issue_dashboard_token,
    new_agent_token,
    verify_dashboard_token,
)
from .subscriptions import effective_subscription

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def get_db(request: Request):
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = request.app.state.settings
    if verify_dashboard_token(credentials.credentials, settings.jwt_signing_secret) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired dashboard token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_zworkforce_integration(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    expected = request.app.state.settings.zworkforce_integration_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ZChat–zWorkforce integration is not configured.",
        )
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(
            credentials.credentials.encode("utf-8"), expected.encode("utf-8")
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid zWorkforce service credentials are required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get(
    "/integrations/zworkforce/v1/capabilities",
    response_model=ZChatWorkforceCapabilitiesV1,
)
def zworkforce_capabilities(
    _service: Annotated[None, Depends(require_zworkforce_integration)],
    request: Request,
) -> ZChatWorkforceCapabilitiesV1:
    """Read-only capability negotiation for the authenticated zWorkforce service."""
    return ZChatWorkforceCapabilitiesV1(
        protocol="zchat-zworkforce",
        protocol_version="1.0",
        compatible_versions=["1.0"],
        server_version=request.app.version,
        read_only_resources=["health", "capabilities"],
        features=get_feature_capabilities(),
    )


@router.get("/integrations/zworkforce/v1/health")
def zworkforce_health(
    _service: Annotated[None, Depends(require_zworkforce_integration)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    """Report control-plane and database reachability without exposing configuration."""
    try:
        db.execute(select(1))
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ZChat database is unavailable.",
        ) from exc
    return {"status": "healthy", "database": "reachable"}


@router.post("/auth/session")
def create_dashboard_session(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, int | str]:
    """Exchange the bootstrap admin token for a short-lived dashboard JWT."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bootstrap admin bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = request.app.state.settings
    if len(credentials.credentials) > 256 or not hmac.compare_digest(
        credentials.credentials, settings.admin_api_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bootstrap admin token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "access_token": issue_dashboard_token(
            settings.jwt_signing_secret,
            ttl_seconds=settings.dashboard_jwt_ttl_seconds,
        ),
        "token_type": "bearer",
        "expires_in": settings.dashboard_jwt_ttl_seconds,
    }


def require_agent(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> Agent:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raw_token = credentials.credentials
    if len(raw_token) > 256:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token.")
    agent = db.scalar(
        select(Agent)
        .where(
            Agent.token_hash == hash_token(raw_token),
            Agent.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if agent is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token.")
    return agent


def add_audit(
    db: Session,
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
    )


def _account_assignment_read(
    account: Account, agent_token: str | None = None
) -> AccountAgentAssignmentRead:
    result = AccountAgentAssignmentRead.model_validate(account)
    if agent_token is None:
        return result
    return result.model_copy(update={"agent_token": agent_token})


def _rotate_agent_token(agent: Agent) -> str:
    token = new_agent_token()
    agent.token_hash = hash_token(token)
    return token


def _find_idempotent_chat_message(
    db: Session,
    *,
    account_id: UUID,
    direction: ChatMessageDirection,
    client_message_id: UUID,
    body: str,
) -> ChatMessage | None:
    existing = db.scalar(
        select(ChatMessage).where(
            ChatMessage.account_id == account_id,
            ChatMessage.direction == direction.value,
            ChatMessage.client_message_id == client_message_id,
        )
    )
    if existing is not None and existing.body != body:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="client_message_id was already used with a different body.",
        )
    return existing


@router.get("/overview", response_model=Overview)
def overview(
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Overview:
    return Overview(
        agents_active=db.scalar(
            select(func.count()).select_from(Agent).where(Agent.revoked_at.is_(None))
        )
        or 0,
        accounts_enabled=db.scalar(
            select(func.count()).select_from(Account).where(Account.enabled.is_(True))
        )
        or 0,
        commands_pending=db.scalar(
            select(func.count())
            .select_from(ControlCommand)
            .where(ControlCommand.state == CommandState.pending.value)
        )
        or 0,
        commands_claimed=db.scalar(
            select(func.count())
            .select_from(ControlCommand)
            .where(ControlCommand.state == CommandState.claimed.value)
        )
        or 0,
        profiles_reported=db.scalar(select(func.count()).select_from(ProfileSnapshot)) or 0,
        cloud_bots_registered=db.scalar(select(func.count()).select_from(CloudBot)) or 0,
        purchase_intents_pending=db.scalar(
            select(func.count())
            .select_from(PurchaseIntent)
            .where(PurchaseIntent.state == "pending_confirmation")
        )
        or 0,
    )


@router.post("/agents", response_model=AgentCreated, status_code=status.HTTP_201_CREATED)
def create_agent(
    body: AgentCreate,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AgentCreated:
    token = new_agent_token()
    agent = Agent(name=body.name, token_hash=hash_token(token))
    db.add(agent)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Agent name already exists."
        ) from exc

    add_audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="agent.created",
        resource_type="agent",
        resource_id=str(agent.id),
        details={"name": agent.name},
    )
    db.commit()
    return AgentCreated(
        id=agent.id, name=agent.name, agent_token=token, created_at=agent.created_at
    )


@router.get("/agents", response_model=list[AgentRead])
def list_agents(
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[Agent]:
    return list(db.scalars(select(Agent).order_by(Agent.created_at.desc())).all())


@router.post("/agents/{agent_id}/revoke", response_model=AgentRead)
def revoke_agent(
    agent_id: UUID,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> Agent:
    agent = db.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    if agent.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Agent is already revoked."
        )

    now = utcnow()
    agent.revoked_at = now
    agent.runtime_state = AgentRuntimeState.disconnected.value
    outstanding = db.scalars(
        select(ControlCommand)
        .where(
            ControlCommand.agent_id == agent.id,
            ControlCommand.state.in_((CommandState.pending.value, CommandState.claimed.value)),
        )
        .with_for_update()
    ).all()
    pending_canceled = 0
    claimed_effect_unknown = 0
    for command in outstanding:
        if command.state == CommandState.pending.value:
            pending_canceled += 1
            command.state = CommandState.canceled.value
        else:
            claimed_effect_unknown += 1
            command.state = CommandState.effect_unknown.value
            add_audit(
                db,
                actor_type="admin",
                actor_id="admin",
                action="command.effect_unknown",
                resource_type="command",
                resource_id=str(command.id),
                details={"operation": command.operation, "reason": "agent_revoked"},
            )
        command.claim_token_hash = None
        command.claim_expires_at = None
        command.finished_at = now

    outstanding_messages = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.agent_id == agent.id,
            ChatMessage.direction == ChatMessageDirection.operator_to_agent.value,
            ChatMessage.state.in_((ChatMessageState.queued.value, ChatMessageState.claimed.value)),
        )
        .with_for_update()
    ).all()
    pending_messages_canceled = 0
    claimed_messages_unknown = 0
    for message in outstanding_messages:
        if message.state == ChatMessageState.queued.value:
            pending_messages_canceled += 1
            message.state = ChatMessageState.canceled.value
        else:
            claimed_messages_unknown += 1
            message.state = ChatMessageState.delivery_unknown.value
            add_audit(
                db,
                actor_type="admin",
                actor_id="admin",
                action="chat_message.delivery_unknown",
                resource_type="chat_message",
                resource_id=str(message.id),
                details={"reason": "agent_revoked"},
            )
        message.claim_token_hash = None
        message.claim_expires_at = None

    add_audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="agent.revoked",
        resource_type="agent",
        resource_id=str(agent.id),
        details={
            "pending_commands_canceled": pending_canceled,
            "claimed_commands_effect_unknown": claimed_effect_unknown,
            "pending_messages_canceled": pending_messages_canceled,
            "claimed_messages_delivery_unknown": claimed_messages_unknown,
        },
    )
    db.commit()
    db.refresh(agent)
    return agent


@router.post("/agents/{agent_id}/rotate-token", response_model=AgentTokenRotated)
def rotate_agent_token(
    agent_id: UUID,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AgentTokenRotated:
    agent = db.scalar(select(Agent).where(Agent.id == agent_id).with_for_update())
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    if agent.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Revoked agents cannot rotate tokens."
        )

    token = _rotate_agent_token(agent)
    account_id = db.scalar(select(Account.id).where(Account.agent_id == agent.id))
    add_audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="agent.token_rotated",
        resource_type="agent",
        resource_id=str(agent.id),
        details={"account_id": str(account_id) if account_id else None},
    )
    db.commit()
    return AgentTokenRotated(agent_id=agent.id, agent_token=token)


@router.post(
    "/accounts",
    response_model=AccountAgentAssignmentRead,
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
)
def create_account(
    body: AccountCreate,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AccountAgentAssignmentRead:
    agent = None
    if body.agent_id is not None:
        agent = db.scalar(
            select(Agent)
            .where(Agent.id == body.agent_id, Agent.revoked_at.is_(None))
            .with_for_update()
        )
        if agent is None:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail="Active agent not found."
            )
        if db.scalar(select(Account.id).where(Account.agent_id == body.agent_id)) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Agent is already assigned to another account.",
            )

    nickname_key = account_nickname_key(body.nickname)
    if db.get(AccountNicknameClaim, nickname_key) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account nickname is already registered.",
        )
    db.add(AccountNicknameClaim(nickname_key=nickname_key))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account nickname is already registered.",
        ) from exc

    account = Account(
        nickname=body.nickname,
        nickname_key=nickname_key,
        agent_id=body.agent_id,
        enabled=body.enabled,
    )
    agent_token = _rotate_agent_token(agent) if agent is not None else None
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already assigned to another account.",
        ) from exc
    add_audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="account.created",
        resource_type="account",
        resource_id=str(account.id),
        details={"agent_id": str(account.agent_id) if account.agent_id else None},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already assigned to another account.",
        ) from exc
    db.refresh(account)
    return _account_assignment_read(account, agent_token)


@router.patch(
    "/accounts/{account_id}/agent",
    response_model=AccountAgentAssignmentRead,
    response_model_exclude_unset=True,
)
def assign_account_agent(
    account_id: UUID,
    body: AccountAgentAssign,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AccountAgentAssignmentRead:
    observed = db.execute(
        select(Account.id, Account.agent_id).where(Account.id == account_id)
    ).one_or_none()
    if observed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    previous_agent_id = observed.agent_id
    agent_ids = {
        agent_id for agent_id in (previous_agent_id, body.agent_id) if agent_id is not None
    }
    locked_agents = (
        db.scalars(
            select(Agent).where(Agent.id.in_(agent_ids)).order_by(Agent.id).with_for_update()
        ).all()
        if agent_ids
        else []
    )
    agents_by_id = {agent.id: agent for agent in locked_agents}
    new_agent = agents_by_id.get(body.agent_id) if body.agent_id is not None else None
    if body.agent_id is not None and (new_agent is None or new_agent.revoked_at is not None):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail="Active agent not found."
        )

    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    if account.agent_id != previous_agent_id:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account assignment changed concurrently; retry the request.",
        )

    if new_agent is not None:
        assigned_account = db.scalar(
            select(Account).where(Account.agent_id == new_agent.id, Account.id != account.id)
        )
        if assigned_account is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Agent is already assigned to another account.",
            )

    claimed = db.scalar(
        select(ControlCommand.id)
        .where(
            ControlCommand.account_id == account.id,
            ControlCommand.state == CommandState.claimed.value,
        )
        .limit(1)
    )
    if claimed is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account has a claimed command; wait for it to finish before reassignment.",
        )

    now = utcnow()
    claimed_message = db.scalar(
        select(ChatMessage.id)
        .where(
            ChatMessage.account_id == account.id,
            ChatMessage.direction == ChatMessageDirection.operator_to_agent.value,
            ChatMessage.state == ChatMessageState.claimed.value,
            ChatMessage.claim_expires_at > now,
        )
        .limit(1)
        .with_for_update()
    )
    if claimed_message is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Account has a claimed chat message; wait for delivery or lease expiry "
                "before reassignment."
            ),
        )

    expired_claimed_messages = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.account_id == account.id,
            ChatMessage.direction == ChatMessageDirection.operator_to_agent.value,
            ChatMessage.state == ChatMessageState.claimed.value,
            ChatMessage.claim_expires_at <= now,
        )
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .with_for_update()
    ).all()

    if previous_agent_id == body.agent_id:
        return _account_assignment_read(account)

    pending = db.scalars(
        select(ControlCommand).where(
            ControlCommand.account_id == account.id,
            ControlCommand.state == CommandState.pending.value,
        )
    ).all()
    pending_messages = db.scalars(
        select(ChatMessage).where(
            ChatMessage.account_id == account.id,
            ChatMessage.direction == ChatMessageDirection.operator_to_agent.value,
            ChatMessage.state == ChatMessageState.queued.value,
        )
    ).all()
    for command in pending:
        command.state = CommandState.canceled.value
        command.finished_at = now
    for message in pending_messages:
        message.state = ChatMessageState.canceled.value
        message.claim_token_hash = None
        message.claim_expires_at = None
    for message in expired_claimed_messages:
        message.state = ChatMessageState.delivery_unknown.value
        message.claim_token_hash = None
        message.claim_expires_at = None
        add_audit(
            db,
            actor_type="admin",
            actor_id="admin",
            action="chat_message.delivery_unknown",
            resource_type="chat_message",
            resource_id=str(message.id),
            details={"reason": "account_reassigned"},
        )
    account.agent_id = body.agent_id
    previous_agent = agents_by_id.get(previous_agent_id) if previous_agent_id is not None else None
    if previous_agent is not None:
        _rotate_agent_token(previous_agent)
    agent_token = _rotate_agent_token(new_agent) if new_agent is not None else None
    add_audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="account.agent_assigned",
        resource_type="account",
        resource_id=str(account.id),
        details={
            "previous_agent_id": str(previous_agent_id) if previous_agent_id else None,
            "agent_id": str(body.agent_id) if body.agent_id else None,
            "pending_commands_canceled": len(pending),
            "pending_messages_canceled": len(pending_messages),
            "expired_claimed_messages_delivery_unknown": len(expired_claimed_messages),
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already assigned to another account.",
        ) from exc
    db.refresh(account)
    return _account_assignment_read(account, agent_token)


@router.get("/accounts", response_model=list[AccountRead])
def list_accounts(
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.created_at.desc())).all())


@router.post(
    "/messages",
    response_model=ChatMessageRead,
    status_code=status.HTTP_201_CREATED,
)
def create_operator_chat_message(
    body: OperatorChatMessageCreate,
    response: Response,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatMessage:
    account = db.get(Account, body.account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    if not account.enabled or account.agent_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Account is not assigned to an agent."
        )
    agent = db.scalar(
        select(Agent)
        .where(Agent.id == account.agent_id, Agent.revoked_at.is_(None))
        .with_for_update()
    )
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Assigned agent is unavailable."
        )
    account = db.scalar(
        select(Account)
        .where(Account.id == body.account_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if account is None or not account.enabled or account.agent_id != agent.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account assignment changed; retry with current state.",
        )

    direction = ChatMessageDirection.operator_to_agent
    existing = _find_idempotent_chat_message(
        db,
        account_id=account.id,
        direction=direction,
        client_message_id=body.client_message_id,
        body=body.body,
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    message = ChatMessage(
        account_id=account.id,
        agent_id=agent.id,
        client_message_id=body.client_message_id,
        direction=direction.value,
        body=body.body,
        state=ChatMessageState.queued.value,
    )
    db.add(message)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _find_idempotent_chat_message(
            db,
            account_id=body.account_id,
            direction=direction,
            client_message_id=body.client_message_id,
            body=body.body,
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Message could not be queued."
            ) from None
        response.status_code = status.HTTP_200_OK
        return existing

    add_audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="chat_message.queued",
        resource_type="chat_message",
        resource_id=str(message.id),
        details={"account_id": str(account.id), "direction": direction.value},
    )
    db.commit()
    db.refresh(message)
    return message


@router.get("/accounts/{account_id}/messages", response_model=list[ChatMessageRead])
def list_chat_messages(
    account_id: UUID,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 100,
    before: UUID | None = None,
) -> list[ChatMessage]:
    if not 1 <= limit <= 200:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail="limit must be 1..200."
        )
    if db.get(Account, account_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    query = select(ChatMessage).where(ChatMessage.account_id == account_id)
    if before is not None:
        cursor = db.scalar(
            select(ChatMessage).where(
                ChatMessage.id == before, ChatMessage.account_id == account_id
            )
        )
        if cursor is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Message cursor not found."
            )
        query = query.where(
            or_(
                ChatMessage.created_at < cursor.created_at,
                and_(ChatMessage.created_at == cursor.created_at, ChatMessage.id < cursor.id),
            )
        )

    messages = list(
        db.scalars(
            query.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit)
        ).all()
    )
    messages.reverse()
    return messages


@router.post("/commands", response_model=CommandRead, status_code=status.HTTP_201_CREATED)
def create_command(
    body: CommandCreate,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ControlCommand:
    account = db.get(Account, body.account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    if not account.enabled or account.agent_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Account is not assigned to an agent."
        )
    agent = db.scalar(
        select(Agent)
        .where(Agent.id == account.agent_id, Agent.revoked_at.is_(None))
        .with_for_update()
    )
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Assigned agent is unavailable."
        )
    account = db.scalar(
        select(Account)
        .where(Account.id == body.account_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if account is None or not account.enabled or account.agent_id != agent.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account assignment changed; retry with current state.",
        )
    has_unknown_effect = db.scalar(
        select(ControlCommand.id)
        .where(
            ControlCommand.account_id == account.id,
            ControlCommand.state == CommandState.effect_unknown.value,
        )
        .limit(1)
    )
    if has_unknown_effect is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account is blocked by an unresolved external command outcome.",
        )

    command = ControlCommand(
        agent_id=agent.id,
        account_id=account.id,
        operation=body.operation.value,
        payload=body.payload,
    )
    db.add(command)
    db.flush()
    add_audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="command.queued",
        resource_type="command",
        resource_id=str(command.id),
        details={"operation": command.operation, "account_id": str(account.id)},
    )
    db.commit()
    db.refresh(command)
    return command


@router.get("/commands", response_model=list[CommandRead])
def list_commands(
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 100,
) -> list[ControlCommand]:
    if not 1 <= limit <= 200:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail="limit must be 1..200."
        )
    return list(
        db.scalars(
            select(ControlCommand).order_by(ControlCommand.created_at.desc()).limit(limit)
        ).all()
    )


@router.post("/commands/{command_id}/cancel", response_model=CommandRead)
def cancel_command(
    command_id: UUID,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ControlCommand:
    command = db.scalar(
        select(ControlCommand).where(ControlCommand.id == command_id).with_for_update()
    )
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found.")
    if command.state != CommandState.pending.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot cancel a claimed command; it may be running.",
        )
    command.state = CommandState.canceled.value
    command.finished_at = utcnow()
    add_audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="command.canceled",
        resource_type="command",
        resource_id=str(command.id),
        details={"operation": command.operation},
    )
    db.commit()
    db.refresh(command)
    return command


@router.post("/commands/{command_id}/reconcile", response_model=CommandRead)
def reconcile_command(
    command_id: UUID,
    body: CommandReconciliationRequest,
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ControlCommand:
    """Record an explicit operator attestation for an uncertain external effect.

    This does not verify provider state. The evidence digest identifies operator-held
    evidence; the control server does not fetch or validate that evidence.
    """
    command = db.scalar(
        select(ControlCommand).where(ControlCommand.id == command_id).with_for_update()
    )
    if command is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found.")
    expected_details = {
        "outcome": body.outcome.value,
        "evidence_sha256": body.evidence_sha256,
        "observed_at": body.observed_at.isoformat(),
        "attestation": "operator_reported_not_provider_verified",
    }
    if command.state == CommandState.operator_reconciled.value:
        audit_event = db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.action == "command.operator_reconciled",
                AuditEvent.resource_type == "command",
                AuditEvent.resource_id == str(command.id),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )
        if audit_event is not None and all(
            audit_event.details.get(key) == value for key, value in expected_details.items()
        ):
            return command
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Command was reconciled with different evidence.",
        )
    if command.state != CommandState.effect_unknown.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only commands with an unresolved external outcome can be reconciled.",
        )
    created_at = command.created_at
    if created_at.tzinfo is None:
        # SQLite drops timezone metadata for DATETIME values; stored values are UTC.
        created_at = created_at.replace(tzinfo=UTC)
    if body.observed_at < created_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Evidence observation time cannot precede command creation.",
        )

    now = utcnow()
    command.state = CommandState.operator_reconciled.value
    command.result_code = CommandResultCode.operator_reconciled.value
    command.finished_at = now
    add_audit(
        db,
        actor_type="admin",
        actor_id="operator",
        action="command.operator_reconciled",
        resource_type="command",
        resource_id=str(command.id),
        details={
            "operation": command.operation,
            **expected_details,
        },
    )
    db.commit()
    db.refresh(command)
    return command


@router.get("/audit", response_model=list[AuditRead])
def list_audit(
    _admin: Annotated[None, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 100,
) -> list[AuditEvent]:
    if not 1 <= limit <= 200:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail="limit must be 1..200."
        )
    return list(
        db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    )


@router.post("/agent/heartbeat", response_model=HeartbeatResponse)
def agent_heartbeat(
    body: HeartbeatRequest,
    agent: Annotated[Agent, Depends(require_agent)],
    db: Annotated[Session, Depends(get_db)],
) -> HeartbeatResponse:
    previous_state = agent.runtime_state
    now = utcnow()
    agent.runtime_state = body.state.value
    agent.last_seen_at = now
    if previous_state != body.state.value:
        add_audit(
            db,
            actor_type="agent",
            actor_id=str(agent.id),
            action="agent.runtime_state_changed",
            resource_type="agent",
            resource_id=str(agent.id),
            details={"from": previous_state, "to": body.state.value},
        )
    account = db.scalar(
        select(Account).where(Account.agent_id == agent.id, Account.enabled.is_(True))
    )
    runtime_snapshot = db.get(AccountRuntimeSnapshot, account.id) if account is not None else None
    if account is not None and body.client_process_state is not None:
        old_process_state = runtime_snapshot.process_state if runtime_snapshot else "unknown"
        if runtime_snapshot is None:
            runtime_snapshot = AccountRuntimeSnapshot(account_id=account.id)
            db.add(runtime_snapshot)
        runtime_snapshot.process_state = body.client_process_state.value
        runtime_snapshot.observed_at = now
        if old_process_state != body.client_process_state.value:
            add_audit(
                db,
                actor_type="agent",
                actor_id=str(agent.id),
                action="account.process_state_changed",
                resource_type="account",
                resource_id=str(account.id),
                details={"from": old_process_state, "to": body.client_process_state.value},
            )
    db.commit()
    return HeartbeatResponse(
        agent_id=agent.id,
        state=body.state,
        account_id=account.id if account else None,
        client_process_state=(
            ClientProcessState(runtime_snapshot.process_state) if runtime_snapshot else None
        ),
        last_seen_at=now,
    )


@router.get("/agent/chat/session", response_model=AgentChatSessionRead)
def get_agent_chat_session(
    agent: Annotated[Agent, Depends(require_agent)],
    db: Annotated[Session, Depends(get_db)],
) -> AgentChatSessionRead:
    """Read the active account assignment without refreshing command-agent presence."""
    account = db.scalar(
        select(Account).where(Account.agent_id == agent.id, Account.enabled.is_(True))
    )
    if account is None:
        return AgentChatSessionRead(account_id=None)

    baseline = db.get(SubscriptionEntitlement, account.id)
    overlays = list(
        db.scalars(
            select(SubscriptionOverlay)
            .where(SubscriptionOverlay.account_id == account.id)
            .order_by(SubscriptionOverlay.starts_at)
        ).all()
    )
    tier, source, active_overlay = effective_subscription(baseline, overlays)
    if source == "overlapping_overlays":
        evidence_source = "unknown"
    elif active_overlay is not None:
        evidence_source = active_overlay.evidence_source
    else:
        evidence_source = baseline.evidence_source if baseline is not None else "unknown"

    return AgentChatSessionRead(
        account_id=account.id,
        account_nickname=account.nickname,
        subscription_tier=tier,
        subscription_source=source,
        subscription_evidence_source=evidence_source,
        subscription_expires_at=(active_overlay.expires_at if active_overlay else None),
    )


@router.post(
    "/agent/messages",
    response_model=ChatMessageRead,
    status_code=status.HTTP_201_CREATED,
)
def submit_agent_chat_message(
    body: AgentChatMessageCreate,
    response: Response,
    agent: Annotated[Agent, Depends(require_agent)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatMessage:
    account = db.scalar(select(Account).where(Account.id == body.account_id).with_for_update())
    if account is None or not account.enabled or account.agent_id != agent.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    direction = ChatMessageDirection.agent_to_operator
    existing = _find_idempotent_chat_message(
        db,
        account_id=account.id,
        direction=direction,
        client_message_id=body.client_message_id,
        body=body.body,
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    now = utcnow()
    message = ChatMessage(
        account_id=account.id,
        agent_id=agent.id,
        client_message_id=body.client_message_id,
        direction=direction.value,
        body=body.body,
        state=ChatMessageState.received.value,
        delivered_at=now,
    )
    db.add(message)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _find_idempotent_chat_message(
            db,
            account_id=body.account_id,
            direction=direction,
            client_message_id=body.client_message_id,
            body=body.body,
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Message could not be accepted."
            ) from None
        response.status_code = status.HTTP_200_OK
        return existing

    add_audit(
        db,
        actor_type="agent",
        actor_id=str(agent.id),
        action="chat_message.received",
        resource_type="chat_message",
        resource_id=str(message.id),
        details={"account_id": str(account.id), "direction": direction.value},
    )
    db.commit()
    db.refresh(message)
    return message


@router.get("/agent/messages/next", response_model=ChatMessagePollResponse)
def claim_next_chat_message(
    request: Request,
    agent: Annotated[Agent, Depends(require_agent)],
    db: Annotated[Session, Depends(get_db)],
) -> ChatMessagePollResponse:
    now = utcnow()
    maximum_attempts = request.app.state.settings.command_max_attempts
    expired = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.agent_id == agent.id,
            ChatMessage.direction == ChatMessageDirection.operator_to_agent.value,
            ChatMessage.state == ChatMessageState.claimed.value,
            ChatMessage.claim_expires_at <= now,
            ChatMessage.claim_attempts >= maximum_attempts,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for message in expired:
        message.state = ChatMessageState.delivery_unknown.value
        message.claim_token_hash = None
        message.claim_expires_at = None
        add_audit(
            db,
            actor_type="system",
            actor_id=None,
            action="chat_message.delivery_unknown",
            resource_type="chat_message",
            resource_id=str(message.id),
            details={"account_id": str(message.account_id), "reason": "lease_retry_limit"},
        )

    eligible = or_(
        ChatMessage.state == ChatMessageState.queued.value,
        and_(
            ChatMessage.state == ChatMessageState.claimed.value,
            ChatMessage.claim_expires_at <= now,
        ),
    )
    message = db.scalar(
        select(ChatMessage)
        .join(Account, Account.id == ChatMessage.account_id)
        .join(Agent, Agent.id == ChatMessage.agent_id)
        .where(
            ChatMessage.agent_id == agent.id,
            ChatMessage.direction == ChatMessageDirection.operator_to_agent.value,
            Account.agent_id == agent.id,
            Account.enabled.is_(True),
            Agent.revoked_at.is_(None),
            eligible,
            ChatMessage.claim_attempts < maximum_attempts,
        )
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .with_for_update(skip_locked=True, of=(ChatMessage, Account))
        .limit(1)
    )
    if message is None:
        if expired:
            db.commit()
        return ChatMessagePollResponse(message=None)

    claim_token = secrets.token_urlsafe(32)
    message.state = ChatMessageState.claimed.value
    message.claim_attempts += 1
    message.claim_token_hash = hash_token(claim_token)
    message.claim_expires_at = now + timedelta(
        seconds=request.app.state.settings.command_lease_seconds
    )
    add_audit(
        db,
        actor_type="agent",
        actor_id=str(agent.id),
        action="chat_message.claimed",
        resource_type="chat_message",
        resource_id=str(message.id),
        details={"account_id": str(message.account_id), "attempt": message.claim_attempts},
    )
    db.commit()
    return ChatMessagePollResponse(
        message=AgentChatMessage(
            id=message.id,
            account_id=message.account_id,
            client_message_id=message.client_message_id,
            body=message.body,
            claim_token=claim_token,
            claim_expires_at=message.claim_expires_at,
        )
    )


@router.post(
    "/agent/messages/{message_id}/ack",
    response_class=Response,
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
)
def acknowledge_chat_message(
    message_id: UUID,
    body: ChatMessageAckRequest,
    agent: Annotated[Agent, Depends(require_agent)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    message = db.scalar(select(ChatMessage).where(ChatMessage.id == message_id).with_for_update())
    if (
        message is None
        or message.agent_id != agent.id
        or message.direction != ChatMessageDirection.operator_to_agent.value
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")
    if message.state != ChatMessageState.claimed.value or message.claim_token_hash is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Message is not claimed.")
    expires_at = message.claim_expires_at
    if expires_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message lease is missing."
        )
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= utcnow():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message lease has expired."
        )
    if not hmac.compare_digest(message.claim_token_hash, hash_token(body.claim_token)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Claim token is stale or invalid."
        )

    now = utcnow()
    message.state = ChatMessageState.delivered.value
    message.claim_token_hash = None
    message.claim_expires_at = None
    message.delivered_at = now
    add_audit(
        db,
        actor_type="agent",
        actor_id=str(agent.id),
        action="chat_message.delivered",
        resource_type="chat_message",
        resource_id=str(message.id),
        details={"account_id": str(message.account_id)},
    )
    db.commit()


@router.get("/agent/commands/next", response_model=CommandPollResponse)
def claim_next_command(
    request: Request,
    agent: Annotated[Agent, Depends(require_agent)],
    db: Annotated[Session, Depends(get_db)],
) -> CommandPollResponse:
    now = utcnow()

    expired = db.scalars(
        select(ControlCommand)
        .where(
            ControlCommand.agent_id == agent.id,
            ControlCommand.state == CommandState.claimed.value,
            ControlCommand.claim_expires_at <= now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for command in expired:
        command.state = CommandState.effect_unknown.value
        command.result_code = "lease_expired"
        command.claim_token_hash = None
        command.claim_expires_at = None
        command.finished_at = now
        add_audit(
            db,
            actor_type="system",
            actor_id=None,
            action="command.effect_unknown",
            resource_type="command",
            resource_id=str(command.id),
            details={"operation": command.operation},
        )

    command = db.scalar(
        select(ControlCommand)
        .join(Account, Account.id == ControlCommand.account_id)
        .join(Agent, Agent.id == ControlCommand.agent_id)
        .where(
            ControlCommand.agent_id == agent.id,
            Account.agent_id == agent.id,
            Account.enabled.is_(True),
            Agent.revoked_at.is_(None),
            ControlCommand.state == CommandState.pending.value,
            ~select(ControlCommand.id)
            .where(
                ControlCommand.account_id == Account.id,
                ControlCommand.state.in_(
                    (CommandState.claimed.value, CommandState.effect_unknown.value)
                ),
            )
            .correlate(Account)
            .exists(),
        )
        .order_by(ControlCommand.created_at.asc())
        .with_for_update(skip_locked=True, of=(ControlCommand, Account))
        .limit(1)
    )
    if command is None:
        if expired:
            db.commit()
        return CommandPollResponse(command=None)

    claim_token = secrets.token_urlsafe(32)
    command.state = CommandState.claimed.value
    command.claim_attempts += 1
    command.claim_token_hash = hash_token(claim_token)
    command.claim_expires_at = now + timedelta(
        seconds=request.app.state.settings.command_lease_seconds
    )
    command.claimed_at = now
    add_audit(
        db,
        actor_type="agent",
        actor_id=str(agent.id),
        action="command.claimed",
        resource_type="command",
        resource_id=str(command.id),
        details={"operation": command.operation, "attempt": command.claim_attempts},
    )
    db.commit()
    return CommandPollResponse(
        command=AgentCommand(
            id=command.id,
            account_id=command.account_id,
            operation=command.operation,
            payload=command.payload,
            claim_token=claim_token,
            claim_issued_at=now,
            claim_expires_at=command.claim_expires_at,
        )
    )


@router.post(
    "/agent/commands/{command_id}/result",
    response_class=Response,
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
)
def report_command_result(
    command_id: UUID,
    body: CommandResultRequest,
    agent: Annotated[Agent, Depends(require_agent)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    command = db.scalar(
        select(ControlCommand).where(ControlCommand.id == command_id).with_for_update()
    )
    if command is None or command.agent_id != agent.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found.")
    if command.state != CommandState.claimed.value or command.claim_token_hash is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Command is not claimable."
        )
    expires_at = command.claim_expires_at
    if expires_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Command lease is missing."
        )
    if expires_at.tzinfo is None:
        # SQLite drops timezone metadata for DATETIME values; treat stored UTC as UTC.
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= utcnow():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Command lease has expired."
        )
    if not hmac.compare_digest(command.claim_token_hash, hash_token(body.claim_token)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Claim token is stale or invalid."
        )
    uri_handoff_operations = {"join_room", "add_friend", "open_im"}
    if command.operation in uri_handoff_operations and body.state == "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="URI handoff cannot be reported as externally verified success.",
        )
    if body.code == "handoff_accepted" and command.operation not in uri_handoff_operations:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="URI handoff result does not match the command operation.",
        )

    now = utcnow()
    command.state = body.state.value
    command.result_code = body.code.value
    command.claim_token_hash = None
    command.claim_expires_at = None
    command.finished_at = now
    add_audit(
        db,
        actor_type="agent",
        actor_id=str(agent.id),
        action=f"command.{body.state.value}",
        resource_type="command",
        resource_id=str(command.id),
        details={"operation": command.operation, "result_code": body.code.value},
    )
    db.commit()
