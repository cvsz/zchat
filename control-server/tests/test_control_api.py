from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from camfrog_control_server.models import (
    Agent,
    ControlCommand,
    SubscriptionEntitlement,
    SubscriptionOverlay,
    utcnow,
)
from camfrog_control_server.schemas import (
    ZChatFeatureCapability,
    ZChatWorkforceCapabilitiesV1,
)


def test_zworkforce_capability_schema_rejects_blank_labels_and_duplicate_features() -> None:
    feature = {
        "feature_id": "feature.one",
        "provider": "camfrog",
        "standalone_supported": False,
        "unified_supported": True,
        "requires_provider_client": True,
        "supports_offline": False,
        "supports_read_only": True,
        "supports_strict_background": False,
        "supports_minimized_client": False,
        "supports_hidden_client": False,
        "supports_remote_commands": True,
        "supports_external_outcome_verification": False,
        "requires_exclusive_resource": True,
        "verified_platforms": [],
        "verified_provider_versions": [],
        "implementation_status": "implemented",
        "evidence_status": "tests_only",
    }
    contract = {
        "protocol": "zchat-zworkforce",
        "protocol_version": "1.0",
        "compatible_versions": ["1.0"],
        "server_version": "1.0.0",
        "features": [feature],
        "read_only_resources": ["health"],
    }
    parsed = ZChatWorkforceCapabilitiesV1.model_validate(contract)
    assert parsed.features[0].feature_id == "feature.one"
    with pytest.raises(ValueError, match="labels must not be empty"):
        ZChatFeatureCapability.model_validate({**feature, "evidence_status": "  "})
    with pytest.raises(ValueError, match="feature IDs must be unique"):
        ZChatWorkforceCapabilitiesV1.model_validate({**contract, "features": [feature, feature]})
    with pytest.raises(ValueError, match="contract values must be unique"):
        ZChatWorkforceCapabilitiesV1.model_validate(
            {**contract, "compatible_versions": ["1.0", "1.0"]}
        )
    with pytest.raises(ValueError, match="contract values must be non-empty"):
        ZChatWorkforceCapabilitiesV1.model_validate(
            {**contract, "read_only_resources": ["health", " "]}
        )
    with pytest.raises(ValueError, match="evidence values must be unique"):
        ZChatFeatureCapability.model_validate(
            {**feature, "verified_platforms": ["windows", "windows"]}
        )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provision(
    api_client: tuple[TestClient, object, str],
) -> tuple[TestClient, object, str, str, str]:
    client, engine, admin_token = api_client
    created = client.post(
        "/api/v1/agents", headers=_headers(admin_token), json={"name": "agent-01"}
    )
    assert created.status_code == 201
    agent = created.json()
    account = client.post(
        "/api/v1/accounts",
        headers=_headers(admin_token),
        json={"nickname": "test-account", "agent_id": agent["id"]},
    )
    assert account.status_code == 201
    return client, engine, admin_token, account.json()["agent_token"], account.json()["id"]


def test_account_assignment_rotates_agent_tokens_and_hides_them_from_reads(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token = api_client
    admin_headers = _headers(admin_token)
    first_agent = client.post(
        "/api/v1/agents", headers=admin_headers, json={"name": "bound-agent-first"}
    ).json()
    account = client.post(
        "/api/v1/accounts",
        headers=admin_headers,
        json={"nickname": "bound-account", "agent_id": first_agent["id"]},
    )
    assert account.status_code == 201
    first_account = account.json()
    assert first_account["agent_token"] != first_agent["agent_token"]
    assert "agent_token" not in client.get("/api/v1/accounts", headers=admin_headers).text

    replacement = client.post(
        "/api/v1/agents", headers=admin_headers, json={"name": "bound-agent-replacement"}
    ).json()
    reassigned = client.patch(
        f"/api/v1/accounts/{first_account['id']}/agent",
        headers=admin_headers,
        json={"agent_id": replacement["id"]},
    )
    assert reassigned.status_code == 200
    replacement_account = reassigned.json()
    assert replacement_account["agent_token"] != replacement["agent_token"]
    assert (
        client.post(
            "/api/v1/agent/heartbeat",
            headers=_headers(first_account["agent_token"]),
            json={"state": "running"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/agent/heartbeat",
            headers=_headers(replacement["agent_token"]),
            json={"state": "running"},
        ).status_code
        == 401
    )
    accepted = client.post(
        "/api/v1/agent/heartbeat",
        headers=_headers(replacement_account["agent_token"]),
        json={"state": "running"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["account_id"] == first_account["id"]

    unassigned = client.patch(
        f"/api/v1/accounts/{first_account['id']}/agent",
        headers=admin_headers,
        json={"agent_id": None},
    )
    assert unassigned.status_code == 200
    assert "agent_token" not in unassigned.json()
    assert (
        client.post(
            "/api/v1/agent/heartbeat",
            headers=_headers(replacement_account["agent_token"]),
            json={"state": "running"},
        ).status_code
        == 401
    )


def test_health_docs_and_admin_auth(api_client: tuple[TestClient, object, str]) -> None:
    client, _, admin_token = api_client
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/docs").status_code == 404
    unauthenticated = client.get("/api/v1/overview")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "no-store"
    wrong_headers = _headers(secrets.token_urlsafe(48))
    assert client.get("/api/v1/overview", headers=wrong_headers).status_code == 401
    authenticated = client.get("/api/v1/overview", headers=_headers(admin_token))
    assert authenticated.status_code == 200
    assert authenticated.headers["cache-control"] == "no-store"


def test_zworkforce_capability_endpoint_is_read_only_and_service_scoped(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, _ = api_client
    path = "/api/v1/integrations/zworkforce/v1/capabilities"
    assert client.get(path).status_code == 503

    service_token = secrets.token_urlsafe(48)
    client.app.state.settings = replace(
        client.app.state.settings, zworkforce_integration_token=service_token
    )
    assert client.get(path, headers=_headers(secrets.token_urlsafe(48))).status_code == 401

    response = client.get(path, headers=_headers(service_token))
    assert response.status_code == 200
    payload = response.json()
    assert payload["protocol"] == "zchat-zworkforce"
    assert payload["protocol_version"] == "1.0"
    assert payload["compatible_versions"] == ["1.0"]
    assert payload["read_only_resources"] == ["health", "capabilities"]
    health = client.get(
        "/api/v1/integrations/zworkforce/v1/health", headers=_headers(service_token)
    )
    assert health.status_code == 200
    assert health.json() == {"status": "healthy", "database": "reachable"}
    capabilities = {item["feature_id"]: item for item in payload["features"]}
    assert len(capabilities) == 9
    assert capabilities["camfrog.room_control"]["standalone_supported"] is True
    assert capabilities["camfrog.room_control"]["unified_supported"] is False
    assert capabilities["camfrog.status_changer"]["unified_supported"] is True
    assert capabilities["camfrog.status_random"]["unified_supported"] is True
    assert capabilities["camfrog.status_marquee"]["unified_supported"] is True
    assert capabilities["camfrog.private_chat"]["unified_supported"] is True
    assert capabilities["camfrog.private_chat"]["supports_read_only"] is True
    assert all(item["supports_remote_commands"] is False for item in capabilities.values())
    assert all(
        item["supports_external_outcome_verification"] is False for item in capabilities.values()
    )
    assert all(item["verified_platforms"] == [] for item in capabilities.values())
    assert all(item["verified_provider_versions"] == [] for item in capabilities.values())
    assert service_token not in response.text


def test_zworkforce_integration_token_is_excluded_from_settings_repr() -> None:
    from camfrog_control_server.config import Settings

    token = "z" * 48
    settings = Settings(
        database_url="sqlite:///unused.db",
        admin_api_token="a" * 48,
        jwt_signing_secret="j" * 48,
        zworkforce_integration_token=token,
    )

    assert token not in repr(settings)


def test_dashboard_is_zchat_branded_and_serves_accessible_assets(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, _ = api_client
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "<title>ZChat Control</title>" in dashboard.text
    assert 'aria-label="Command queue" tabindex="0"' in dashboard.text
    stylesheet = client.get("/dashboard/assets/dashboard.css")
    assert stylesheet.status_code == 200
    assert "min-width: 640px" in stylesheet.text


def test_bootstrap_admin_token_exchanges_for_short_lived_jwt(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, jwt_token = api_client
    bootstrap = client.app.state.settings.admin_api_token
    session = client.post("/api/v1/auth/session", headers=_headers(bootstrap))
    assert session.status_code == 200
    data = session.json()
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900
    assert client.get("/api/v1/overview", headers=_headers(data["access_token"])).status_code == 200
    assert client.get("/api/v1/overview", headers=_headers(bootstrap)).status_code == 401
    assert client.get("/api/v1/overview", headers=_headers(jwt_token + "x")).status_code == 401


def test_agent_token_is_one_time_and_commands_are_scoped(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, admin_token, agent_token, account_id = _provision(api_client)
    headers = _headers(admin_token)

    listed = client.get("/api/v1/agents", headers=headers)
    assert listed.status_code == 200
    assert "agent_token" not in listed.text
    with Session(engine) as session:
        saved_hash = session.scalar(select(Agent.token_hash))
        assert saved_hash is not None
        assert saved_hash != agent_token

    queued = client.post(
        "/api/v1/commands",
        headers=headers,
        json={"account_id": account_id, "operation": "join_room", "payload": {"room": "ZeaZDev"}},
    )
    assert queued.status_code == 201
    assert queued.json()["state"] == "pending"

    poll = client.get("/api/v1/agent/commands/next", headers=_headers(agent_token))
    assert poll.status_code == 200
    command = poll.json()["command"]
    assert command["operation"] == "join_room"
    assert command["payload"] == {"room": "ZeaZDev"}
    assert command["claim_issued_at"] < command["claim_expires_at"]

    unrecognized_result_claim = client.post(
        f"/api/v1/agent/commands/{command['id']}/result",
        headers=_headers(agent_token),
        json={
            "claim_token": command["claim_token"],
            "state": "effect_unknown",
            "code": "handoff_accepted",
            "external_status": "verified",
        },
    )
    assert unrecognized_result_claim.status_code == 422

    wrong_result = client.post(
        f"/api/v1/agent/commands/{command['id']}/result",
        headers=_headers(agent_token),
        json={
            "claim_token": secrets.token_urlsafe(48),
            "state": "failed",
            "code": "execution_failed",
        },
    )
    assert wrong_result.status_code == 409

    invalid_unknown = client.post(
        f"/api/v1/agent/commands/{command['id']}/result",
        headers=_headers(agent_token),
        json={
            "claim_token": command["claim_token"],
            "state": "effect_unknown",
            "code": "ok",
        },
    )
    assert invalid_unknown.status_code == 422

    agent_reconciliation = client.post(
        f"/api/v1/agent/commands/{command['id']}/result",
        headers=_headers(agent_token),
        json={
            "claim_token": command["claim_token"],
            "state": "effect_unknown",
            "code": "operator_reconciled",
        },
    )
    assert agent_reconciliation.status_code == 422

    unverified_success = client.post(
        f"/api/v1/agent/commands/{command['id']}/result",
        headers=_headers(agent_token),
        json={
            "claim_token": command["claim_token"],
            "state": "succeeded",
            "code": "ok",
        },
    )
    assert unverified_success.status_code == 409

    result = client.post(
        f"/api/v1/agent/commands/{command['id']}/result",
        headers=_headers(agent_token),
        json={
            "claim_token": command["claim_token"],
            "state": "effect_unknown",
            "code": "handoff_accepted",
        },
    )
    assert result.status_code == 204
    replay = client.post(
        f"/api/v1/agent/commands/{command['id']}/result",
        headers=_headers(agent_token),
        json={
            "claim_token": command["claim_token"],
            "state": "effect_unknown",
            "code": "handoff_accepted",
        },
    )
    assert replay.status_code == 409

    audit = client.get("/api/v1/audit", headers=headers)
    assert audit.status_code == 200
    assert agent_token not in audit.text
    assert command["claim_token"] not in audit.text
    with Session(engine) as session:
        saved_command = session.get(ControlCommand, UUID(command["id"]))
        assert saved_command is not None
        assert saved_command.claim_token_hash is None
        assert saved_command.state == "effect_unknown"

    assert (
        client.get("/api/v1/agent/commands/next", headers=_headers(agent_token)).json()["command"]
        is None
    )


def test_command_create_rejects_unrecognized_approval_fields(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token, _, account_id = _provision(api_client)
    headers = _headers(admin_token)

    response = client.post(
        "/api/v1/commands",
        headers=headers,
        json={
            "account_id": account_id,
            "operation": "join_room",
            "payload": {"room": "ZeaZDev"},
            "approval_grant": {"approved": True, "nonce": "ignored-fields-must-fail"},
        },
    )

    assert response.status_code == 422
    assert client.get("/api/v1/commands", headers=headers).json() == []


def test_unknown_external_effect_blocks_later_commands_for_same_account(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, admin_token, agent_token, account_id = _provision(api_client)
    admin_headers = _headers(admin_token)
    agent_headers = _headers(agent_token)

    first = client.post(
        "/api/v1/commands",
        headers=admin_headers,
        json={"account_id": account_id, "operation": "join_room", "payload": {"room": "first"}},
    )
    second = client.post(
        "/api/v1/commands",
        headers=admin_headers,
        json={"account_id": account_id, "operation": "join_room", "payload": {"room": "second"}},
    )
    assert first.status_code == 201
    assert second.status_code == 201

    claimed = client.get("/api/v1/agent/commands/next", headers=agent_headers)
    command = claimed.json()["command"]
    assert command["id"] == first.json()["id"]

    concurrent_poll = client.get("/api/v1/agent/commands/next", headers=agent_headers)
    assert concurrent_poll.status_code == 200
    assert concurrent_poll.json()["command"] is None

    result = client.post(
        f"/api/v1/agent/commands/{command['id']}/result",
        headers=agent_headers,
        json={
            "claim_token": command["claim_token"],
            "state": "effect_unknown",
            "code": "handoff_accepted",
        },
    )
    assert result.status_code == 204

    rejected = client.post(
        "/api/v1/commands",
        headers=admin_headers,
        json={"account_id": account_id, "operation": "stop"},
    )
    assert rejected.status_code == 409
    assert "unresolved external command outcome" in rejected.json()["detail"]

    next_claim = client.get("/api/v1/agent/commands/next", headers=agent_headers)
    assert next_claim.status_code == 200
    assert next_claim.json()["command"] is None
    listed = client.get("/api/v1/commands", headers=admin_headers)
    assert listed.status_code == 200
    states = {item["id"]: item["state"] for item in listed.json()}
    assert states[first.json()["id"]] == "effect_unknown"
    assert states[second.json()["id"]] == "pending"

    with Session(engine) as session:
        pending = session.get(ControlCommand, UUID(second.json()["id"]))
        assert pending is not None
        assert pending.state == "pending"


def test_agent_token_rotation_returns_one_time_secret_without_changing_assignment(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token, old_token, account_id = _provision(api_client)
    admin_headers = _headers(admin_token)
    agent = next(
        item
        for item in client.get("/api/v1/agents", headers=admin_headers).json()
        if item["name"] == "agent-01"
    )

    rotated = client.post(f"/api/v1/agents/{agent['id']}/rotate-token", headers=admin_headers)
    assert rotated.status_code == 200
    new_token = rotated.json()["agent_token"]
    assert rotated.json()["agent_id"] == agent["id"]
    assert new_token != old_token
    assert (
        client.post(
            "/api/v1/agent/heartbeat",
            headers=_headers(old_token),
            json={"state": "running"},
        ).status_code
        == 401
    )
    heartbeat = client.post(
        "/api/v1/agent/heartbeat",
        headers=_headers(new_token),
        json={"state": "running"},
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["account_id"] == account_id
    assert new_token not in client.get("/api/v1/agents", headers=admin_headers).text
    assert new_token not in client.get("/api/v1/audit", headers=admin_headers).text


def test_chat_session_reads_assignment_without_refreshing_agent_presence(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, _, agent_token, account_id = _provision(api_client)

    with Session(engine) as session:
        agent = session.scalar(select(Agent).where(Agent.name == "agent-01"))
        assert agent is not None
        assert agent.runtime_state == "unknown"
        assert agent.last_seen_at is None

    response = client.get("/api/v1/agent/chat/session", headers=_headers(agent_token))

    assert response.status_code == 200
    assert response.json() == {
        "account_id": account_id,
        "account_nickname": "test-account",
        "subscription_tier": "unknown",
        "subscription_source": "baseline_not_recorded",
        "subscription_evidence_source": "unknown",
        "subscription_expires_at": None,
    }
    with Session(engine) as session:
        agent = session.scalar(select(Agent).where(Agent.name == "agent-01"))
        assert agent is not None
        assert agent.runtime_state == "unknown"
        assert agent.last_seen_at is None


def test_chat_session_exposes_only_assigned_account_vip_summary(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, _, agent_token, account_id = _provision(api_client)
    starts_at = utcnow() - timedelta(minutes=1)
    expires_at = utcnow() + timedelta(days=7)
    with Session(engine) as session:
        session.add(
            SubscriptionEntitlement(
                account_id=UUID(account_id),
                base_tier="free",
                base_term="unknown",
                evidence_source="official_record",
                evidence_note="Private operator note must not be sent to an agent.",
                observed_at=utcnow(),
            )
        )
        session.add(
            SubscriptionOverlay(
                account_id=UUID(account_id),
                tier="gold",
                starts_at=starts_at,
                expires_at=expires_at,
                evidence_source="manual_entry",
                evidence_note="Private overlay note must not be sent to an agent.",
            )
        )
        session.commit()

    response = client.get("/api/v1/agent/chat/session", headers=_headers(agent_token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["account_id"] == account_id
    assert payload["account_nickname"] == "test-account"
    assert payload["subscription_tier"] == "gold"
    assert payload["subscription_source"] == "temporary_overlay"
    assert payload["subscription_evidence_source"] == "manual_entry"
    assert payload["subscription_expires_at"] is not None
    assert "evidence_note" not in payload
    assert "Private" not in response.text


def test_expired_lease_cannot_submit_result_and_is_not_retried(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, admin_token, agent_token, account_id = _provision(api_client)
    queued = client.post(
        "/api/v1/commands",
        headers=_headers(admin_token),
        json={"account_id": account_id, "operation": "launch"},
    )
    command_id = queued.json()["id"]
    later = client.post(
        "/api/v1/commands",
        headers=_headers(admin_token),
        json={"account_id": account_id, "operation": "stop"},
    )
    assert later.status_code == 201
    agent_headers = _headers(agent_token)
    first = client.get("/api/v1/agent/commands/next", headers=agent_headers).json()["command"]

    with Session(engine) as session:
        command = session.get(ControlCommand, UUID(command_id))
        assert command is not None
        command.claim_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()

    stale = client.post(
        f"/api/v1/agent/commands/{command_id}/result",
        headers=agent_headers,
        json={
            "claim_token": first["claim_token"],
            "state": "failed",
            "code": "execution_failed",
        },
    )
    assert stale.status_code == 409

    second = client.get("/api/v1/agent/commands/next", headers=agent_headers).json()["command"]
    assert second is None
    with Session(engine) as session:
        command = session.get(ControlCommand, UUID(command_id))
        assert command is not None
        assert command.state == "effect_unknown"
        assert command.result_code == "lease_expired"
        later_command = session.get(ControlCommand, UUID(later.json()["id"]))
        assert later_command is not None
        assert later_command.state == "pending"


def test_operator_reconciliation_is_audited_and_does_not_claim_provider_verification(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token, agent_token, account_id = _provision(api_client)
    admin_headers = _headers(admin_token)
    agent_headers = _headers(agent_token)
    created = client.post(
        "/api/v1/commands",
        headers=admin_headers,
        json={
            "account_id": account_id,
            "operation": "join_room",
            "payload": {"room": "ZeaZDev"},
        },
    )
    claimed = client.get("/api/v1/agent/commands/next", headers=agent_headers).json()["command"]
    uncertain = client.post(
        f"/api/v1/agent/commands/{claimed['id']}/result",
        headers=agent_headers,
        json={
            "claim_token": claimed["claim_token"],
            "state": "effect_unknown",
            "code": "handoff_accepted",
        },
    )
    assert created.status_code == 201
    assert uncertain.status_code == 204

    blocked = client.post(
        "/api/v1/commands",
        headers=admin_headers,
        json={"account_id": account_id, "operation": "leave_room"},
    )
    assert blocked.status_code == 409

    request = {
        "outcome": "no_effect_observed",
        "evidence_sha256": "a" * 64,
        "observed_at": datetime.now(UTC).isoformat(),
        "acknowledge_manual_attestation": True,
    }
    unauthorized = client.post(f"/api/v1/commands/{created.json()['id']}/reconcile", json=request)
    assert unauthorized.status_code == 401
    invalid = client.post(
        f"/api/v1/commands/{created.json()['id']}/reconcile",
        headers=admin_headers,
        json={**request, "evidence_sha256": "not-a-digest"},
    )
    assert invalid.status_code == 422
    stale_observation = client.post(
        f"/api/v1/commands/{created.json()['id']}/reconcile",
        headers=admin_headers,
        json={
            **request,
            "observed_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )
    assert stale_observation.status_code == 422

    reconciled = client.post(
        f"/api/v1/commands/{created.json()['id']}/reconcile",
        headers=admin_headers,
        json=request,
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["state"] == "operator_reconciled"
    assert reconciled.json()["result_code"] == "operator_reconciled"

    replay = client.post(
        f"/api/v1/commands/{created.json()['id']}/reconcile",
        headers=admin_headers,
        json=request,
    )
    assert replay.status_code == 200
    conflicting_replay = client.post(
        f"/api/v1/commands/{created.json()['id']}/reconcile",
        headers=admin_headers,
        json={**request, "evidence_sha256": "b" * 64},
    )
    assert conflicting_replay.status_code == 409
    unblocked = client.post(
        "/api/v1/commands",
        headers=admin_headers,
        json={"account_id": account_id, "operation": "leave_room"},
    )
    assert unblocked.status_code == 201

    audit = client.get("/api/v1/audit", headers=admin_headers).json()
    events = [item for item in audit if item["action"] == "command.operator_reconciled"]
    assert len(events) == 1
    event = events[0]
    assert event["actor_id"] == "operator"
    assert event["details"]["outcome"] == "no_effect_observed"
    assert event["details"]["evidence_sha256"] == request["evidence_sha256"]
    assert event["details"]["attestation"] == "operator_reported_not_provider_verified"


def test_purchase_and_unrecognized_operations_are_rejected(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token, _, account_id = _provision(api_client)
    headers = _headers(admin_token)
    purchase = client.post(
        "/api/v1/commands",
        headers=headers,
        json={"account_id": account_id, "operation": "purchase", "payload": {"item_id": 7}},
    )
    assert purchase.status_code == 422

    extra_room_data = client.post(
        "/api/v1/commands",
        headers=headers,
        json={
            "account_id": account_id,
            "operation": "join_room",
            "payload": {"room": "ZeaZDev", "password": "ignored"},
        },
    )
    assert extra_room_data.status_code == 422


def test_revoked_agent_cannot_heartbeat_or_poll(api_client: tuple[TestClient, object, str]) -> None:
    client, _, admin_token, agent_token, _ = _provision(api_client)
    agent_id = client.get("/api/v1/agents", headers=_headers(admin_token)).json()[0]["id"]
    revoked = client.post(f"/api/v1/agents/{agent_id}/revoke", headers=_headers(admin_token))
    assert revoked.status_code == 200
    assert (
        client.post(
            "/api/v1/agent/heartbeat",
            headers=_headers(agent_token),
            json={"state": "running"},
        ).status_code
        == 401
    )


def test_account_agent_assignment_is_one_to_one_and_cancels_pending_commands(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token, _, account_id = _provision(api_client)
    headers = _headers(admin_token)
    first_agent_id = next(
        item["id"]
        for item in client.get("/api/v1/agents", headers=headers).json()
        if item["name"] == "agent-01"
    )
    second_agent = client.post("/api/v1/agents", headers=headers, json={"name": "agent-02"}).json()
    other_account = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"nickname": "second-account", "agent_id": second_agent["id"]},
    )
    assert other_account.status_code == 201

    second_agent_conflict = client.patch(
        f"/api/v1/accounts/{account_id}/agent",
        headers=headers,
        json={"agent_id": second_agent["id"]},
    )
    assert second_agent_conflict.status_code == 409

    queued = client.post(
        "/api/v1/commands",
        headers=headers,
        json={"account_id": account_id, "operation": "stop"},
    )
    assert queued.status_code == 201
    reassigned = client.patch(
        f"/api/v1/accounts/{account_id}/agent",
        headers=headers,
        json={"agent_id": None},
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["agent_id"] is None

    commands = client.get("/api/v1/commands", headers=headers).json()
    assert commands[0]["state"] == "canceled"
    assert first_agent_id != second_agent["id"]


def test_revoking_agent_marks_claimed_command_unknown_and_cancels_pending(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, admin_token, agent_token, account_id = _provision(api_client)
    headers = _headers(admin_token)
    queued = client.post(
        "/api/v1/commands",
        headers=headers,
        json={"account_id": account_id, "operation": "launch"},
    )
    assert queued.status_code == 201
    command_id = queued.json()["id"]

    pending = client.post(
        "/api/v1/commands",
        headers=headers,
        json={"account_id": account_id, "operation": "stop"},
    )
    assert pending.status_code == 201
    pending_id = pending.json()["id"]

    claimed = client.get("/api/v1/agent/commands/next", headers=_headers(agent_token))
    assert claimed.status_code == 200
    assert claimed.json()["command"]["id"] == command_id

    agent_id = client.get("/api/v1/agents", headers=headers).json()[0]["id"]
    revoked = client.post(f"/api/v1/agents/{agent_id}/revoke", headers=headers)
    assert revoked.status_code == 200

    with Session(engine) as session:
        command = session.get(ControlCommand, UUID(command_id))
        assert command is not None
        assert command.state == "effect_unknown"
        assert command.claim_token_hash is None
        assert command.claim_expires_at is None
        assert command.finished_at is not None
        pending_command = session.get(ControlCommand, UUID(pending_id))
        assert pending_command is not None
        assert pending_command.state == "canceled"
    audit = client.get("/api/v1/audit?limit=100", headers=headers)
    assert audit.status_code == 200
    unknown_event = next(
        item for item in audit.json() if item["action"] == "command.effect_unknown"
    )
    assert unknown_event["details"]["reason"] == "agent_revoked"

    replacement = client.post(
        "/api/v1/agents", headers=headers, json={"name": "replacement-after-revoke"}
    )
    assert replacement.status_code == 201
    reassigned = client.patch(
        f"/api/v1/accounts/{account_id}/agent",
        headers=headers,
        json={"agent_id": replacement.json()["id"]},
    )
    assert reassigned.status_code == 200
    assert (
        client.get(
            "/api/v1/agent/commands/next",
            headers=_headers(reassigned.json()["agent_token"]),
        ).json()["command"]
        is None
    )


def test_expired_lease_cleanup_is_scoped_to_polling_agent(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, admin_token, first_token, _ = _provision(api_client)
    headers = _headers(admin_token)

    created = client.post(
        "/api/v1/agents",
        headers=headers,
        json={"name": "agent-02"},
    )
    assert created.status_code == 201
    second = created.json()
    account = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"nickname": "second-account", "agent_id": second["id"]},
    )
    assert account.status_code == 201
    second_token = account.json()["agent_token"]

    queued = client.post(
        "/api/v1/commands",
        headers=headers,
        json={"account_id": account.json()["id"], "operation": "launch"},
    )
    assert queued.status_code == 201
    command_id = queued.json()["id"]

    claimed = client.get(
        "/api/v1/agent/commands/next",
        headers=_headers(second_token),
    )
    assert claimed.status_code == 200
    assert claimed.json()["command"]["id"] == command_id

    with Session(engine) as session:
        command = session.get(ControlCommand, UUID(command_id))
        assert command is not None
        command.claim_attempts = 1
        command.claim_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()

    unrelated_poll = client.get(
        "/api/v1/agent/commands/next",
        headers=_headers(first_token),
    )
    assert unrelated_poll.status_code == 200
    assert unrelated_poll.json()["command"] is None

    with Session(engine) as session:
        command = session.get(ControlCommand, UUID(command_id))
        assert command is not None
        assert command.state == "claimed"
        assert command.claim_token_hash is not None

    blocked_reassignment = client.patch(
        f"/api/v1/accounts/{account.json()['id']}/agent",
        headers=headers,
        json={"agent_id": None},
    )
    assert blocked_reassignment.status_code == 409
    assert "claimed command" in blocked_reassignment.json()["detail"]

    owner_poll = client.get(
        "/api/v1/agent/commands/next",
        headers=_headers(second_token),
    )
    assert owner_poll.status_code == 200
    assert owner_poll.json()["command"] is None

    with Session(engine) as session:
        command = session.get(ControlCommand, UUID(command_id))
        assert command is not None
        assert command.state == "effect_unknown"
        assert command.result_code == "lease_expired"
        assert command.claim_token_hash is None
        assert command.claim_expires_at is None
