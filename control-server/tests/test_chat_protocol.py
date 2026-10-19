from __future__ import annotations

import secrets
from datetime import timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from camfrog_control_server.models import ChatMessage, utcnow


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provision_account(client: TestClient, admin_token: str, suffix: str) -> tuple[dict, dict]:
    headers = _headers(admin_token)
    agent_response = client.post(
        "/api/v1/agents", headers=headers, json={"name": f"chat-agent-{suffix}"}
    )
    assert agent_response.status_code == 201
    agent = agent_response.json()
    account_response = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"nickname": f"chat-account-{suffix}", "agent_id": agent["id"]},
    )
    assert account_response.status_code == 201
    account = account_response.json()
    return {**agent, "agent_token": account["agent_token"]}, account


def test_operator_message_is_idempotent_leased_and_acknowledged(
    api_client: tuple[TestClient, object, str],
    caplog,
) -> None:
    client, engine, admin_token = api_client
    agent, account = _provision_account(client, admin_token, "outbound")
    message_id = "7d21c617-7f6c-4cb5-ae81-45db53df14be"
    payload = {
        "account_id": account["id"],
        "client_message_id": message_id,
        "body": "Hello from the operator\nSecond line",
    }

    unauthenticated = client.post("/api/v1/messages", json=payload)
    assert unauthenticated.status_code == 401

    created = client.post("/api/v1/messages", headers=_headers(admin_token), json=payload)
    assert created.status_code == 201
    assert created.json()["direction"] == "operator_to_agent"
    assert created.json()["state"] == "queued"

    replay = client.post("/api/v1/messages", headers=_headers(admin_token), json=payload)
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]

    conflicting_replay = client.post(
        "/api/v1/messages",
        headers=_headers(admin_token),
        json={**payload, "body": "Different content"},
    )
    assert conflicting_replay.status_code == 409

    unauthorized_poll = client.get(
        "/api/v1/agent/messages/next", headers=_headers(secrets.token_urlsafe(48))
    )
    assert unauthorized_poll.status_code == 401

    poll = client.get("/api/v1/agent/messages/next", headers=_headers(agent["agent_token"]))
    assert poll.status_code == 200
    message = poll.json()["message"]
    assert message["id"] == created.json()["id"]
    assert message["body"] == payload["body"]

    with Session(engine) as session:
        stored = session.scalar(select(ChatMessage).where(ChatMessage.id == UUID(message["id"])))
        assert stored is not None
        assert stored.claim_token_hash != message["claim_token"]
        assert stored.claim_attempts == 1

    wrong_claim = client.post(
        f"/api/v1/agent/messages/{message['id']}/ack",
        headers=_headers(agent["agent_token"]),
        json={"claim_token": secrets.token_urlsafe(32)},
    )
    assert wrong_claim.status_code == 409

    acknowledged = client.post(
        f"/api/v1/agent/messages/{message['id']}/ack",
        headers=_headers(agent["agent_token"]),
        json={"claim_token": message["claim_token"]},
    )
    assert acknowledged.status_code == 204

    history = client.get(
        f"/api/v1/accounts/{account['id']}/messages", headers=_headers(admin_token)
    )
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["state"] == "delivered"
    assert history.json()[0]["body"] == payload["body"]
    assert client.get(f"/api/v1/accounts/{account['id']}/messages").status_code == 401
    audit = client.get("/api/v1/audit", headers=_headers(admin_token))
    assert audit.status_code == 200
    assert payload["body"] not in audit.text
    assert payload["body"] not in caplog.text


def test_agent_message_is_account_scoped_and_idempotent(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token = api_client
    agent, account = _provision_account(client, admin_token, "inbound")
    other_agent, other_account = _provision_account(client, admin_token, "other")
    headers = _headers(agent["agent_token"])
    payload = {
        "account_id": account["id"],
        "client_message_id": "adad03f1-a4f8-471e-8f04-733e6674fbe9",
        "body": "Message submitted by the assigned agent",
    }

    created = client.post("/api/v1/agent/messages", headers=headers, json=payload)
    assert created.status_code == 201
    assert created.json()["direction"] == "agent_to_operator"
    assert created.json()["state"] == "received"

    replay = client.post("/api/v1/agent/messages", headers=headers, json=payload)
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]

    conflicting_replay = client.post(
        "/api/v1/agent/messages",
        headers=headers,
        json={**payload, "body": "Changed retry content"},
    )
    assert conflicting_replay.status_code == 409

    cross_account = client.post(
        "/api/v1/agent/messages",
        headers=headers,
        json={**payload, "account_id": other_account["id"]},
    )
    assert cross_account.status_code == 404

    history = client.get(
        f"/api/v1/accounts/{account['id']}/messages", headers=_headers(admin_token)
    )
    assert [message["id"] for message in history.json()] == [created.json()["id"]]
    assert other_agent["id"] != agent["id"]


def test_chat_message_body_validation_and_history_cursor(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token = api_client
    agent, account = _provision_account(client, admin_token, "validation")
    headers = _headers(admin_token)
    base = {
        "account_id": account["id"],
        "client_message_id": "23058e7b-e931-4d11-8bb3-7e7f4d42b250",
    }

    blank = client.post("/api/v1/messages", headers=headers, json={**base, "body": " \n "})
    assert blank.status_code == 422
    controls = client.post(
        "/api/v1/messages", headers=headers, json={**base, "body": "bad\u0000text"}
    )
    assert controls.status_code == 422

    first = client.post("/api/v1/messages", headers=headers, json={**base, "body": "first"})
    second = client.post(
        "/api/v1/messages",
        headers=headers,
        json={
            **base,
            "client_message_id": "8f34b422-3f9a-453d-b5b0-6589746cff7b",
            "body": "second",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201

    first_page = client.get(f"/api/v1/accounts/{account['id']}/messages?limit=1", headers=headers)
    assert first_page.status_code == 200
    assert len(first_page.json()) == 1
    next_page = client.get(
        f"/api/v1/accounts/{account['id']}/messages?limit=1&before={first_page.json()[0]['id']}",
        headers=headers,
    )
    assert next_page.status_code == 200
    assert len(next_page.json()) == 1
    assert next_page.json()[0]["id"] != first_page.json()[0]["id"]
    assert (
        client.get(
            "/api/v1/accounts/00000000-0000-0000-0000-000000000001/messages?limit=0",
            headers=headers,
        ).status_code
        == 422
    )


def test_account_reassignment_and_agent_revocation_preserve_message_ownership(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, _, admin_token = api_client
    original_agent, account = _provision_account(client, admin_token, "assignment")
    admin_headers = _headers(admin_token)
    next_agent = client.post(
        "/api/v1/agents", headers=admin_headers, json={"name": "chat-agent-next"}
    ).json()

    queued = client.post(
        "/api/v1/messages",
        headers=admin_headers,
        json={
            "account_id": account["id"],
            "client_message_id": "e169f68e-13f7-47d5-b79f-0be24f23eef1",
            "body": "Queued for the original agent",
        },
    )
    assert queued.status_code == 201

    reassigned = client.patch(
        f"/api/v1/accounts/{account['id']}/agent",
        headers=admin_headers,
        json={"agent_id": next_agent["id"]},
    )
    assert reassigned.status_code == 200
    replacement_token = reassigned.json()["agent_token"]
    assert replacement_token != next_agent["agent_token"]
    assert (
        client.get(
            "/api/v1/agent/messages/next", headers=_headers(original_agent["agent_token"])
        ).status_code
        == 401
    )
    history = client.get(f"/api/v1/accounts/{account['id']}/messages", headers=admin_headers).json()
    assert history[0]["state"] == "canceled"
    assert history[0]["agent_id"] == original_agent["id"]

    next_message = client.post(
        "/api/v1/messages",
        headers=admin_headers,
        json={
            "account_id": account["id"],
            "client_message_id": "2a4bc2d4-5fcf-4755-8b48-b9cbde993b9e",
            "body": "Claimed by the replacement agent",
        },
    )
    claimed = client.get("/api/v1/agent/messages/next", headers=_headers(replacement_token)).json()[
        "message"
    ]
    assert claimed["id"] == next_message.json()["id"]

    blocked_reassignment = client.patch(
        f"/api/v1/accounts/{account['id']}/agent",
        headers=admin_headers,
        json={"agent_id": None},
    )
    assert blocked_reassignment.status_code == 409

    revoked = client.post(f"/api/v1/agents/{next_agent['id']}/revoke", headers=admin_headers)
    assert revoked.status_code == 200
    history = client.get(f"/api/v1/accounts/{account['id']}/messages", headers=admin_headers).json()
    assert history[-1]["state"] == "delivery_unknown"
    assert (
        client.post(
            f"/api/v1/agent/messages/{claimed['id']}/ack",
            headers=_headers(replacement_token),
            json={"claim_token": claimed["claim_token"]},
        ).status_code
        == 401
    )


def test_reassignment_marks_expired_chat_claim_unknown_and_invalidates_token(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, admin_token = api_client
    original_agent, account = _provision_account(client, admin_token, "expired-assignment")
    admin_headers = _headers(admin_token)
    replacement_agent = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "chat-agent-expired-replacement"},
    ).json()
    queued = client.post(
        "/api/v1/messages",
        headers=admin_headers,
        json={
            "account_id": account["id"],
            "client_message_id": "6e35ac75-48f0-49b3-9f0b-20f4da5dc662",
            "body": "Cancel the expired claim during reassignment",
        },
    )
    claimed = client.get(
        "/api/v1/agent/messages/next", headers=_headers(original_agent["agent_token"])
    ).json()["message"]

    with Session(engine) as session:
        message = session.get(ChatMessage, UUID(queued.json()["id"]))
        assert message is not None
        message.claim_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()

    reassigned = client.patch(
        f"/api/v1/accounts/{account['id']}/agent",
        headers=admin_headers,
        json={"agent_id": replacement_agent["id"]},
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["agent_id"] == replacement_agent["id"]
    assert reassigned.json()["agent_token"] != replacement_agent["agent_token"]

    history = client.get(f"/api/v1/accounts/{account['id']}/messages", headers=admin_headers).json()
    assert history[0]["state"] == "delivery_unknown"
    assert history[0]["agent_id"] == original_agent["id"]
    with Session(engine) as session:
        message = session.get(ChatMessage, UUID(claimed["id"]))
        assert message is not None
        assert message.claim_token_hash is None
        assert message.claim_expires_at is None

    stale_ack = client.post(
        f"/api/v1/agent/messages/{claimed['id']}/ack",
        headers=_headers(original_agent["agent_token"]),
        json={"claim_token": claimed["claim_token"]},
    )
    assert stale_ack.status_code == 401


def test_expired_chat_leases_redeliver_and_fail_after_the_retry_limit(
    api_client: tuple[TestClient, object, str],
) -> None:
    client, engine, admin_token = api_client
    agent, account = _provision_account(client, admin_token, "retry")
    created = client.post(
        "/api/v1/messages",
        headers=_headers(admin_token),
        json={
            "account_id": account["id"],
            "client_message_id": "b685520d-e242-4e35-8681-cc7b271eac92",
            "body": "Retry until the configured limit",
        },
    )
    assert created.status_code == 201

    previous_claim = None
    for attempt in range(1, 4):
        poll = client.get("/api/v1/agent/messages/next", headers=_headers(agent["agent_token"]))
        assert poll.status_code == 200
        claim = poll.json()["message"]
        assert claim["id"] == created.json()["id"]
        if previous_claim is not None:
            stale_claim = client.post(
                f"/api/v1/agent/messages/{claim['id']}/ack",
                headers=_headers(agent["agent_token"]),
                json={"claim_token": previous_claim},
            )
            assert stale_claim.status_code == 409
        with Session(engine) as session:
            message = session.get(ChatMessage, UUID(created.json()["id"]))
            assert message is not None
            assert message.claim_attempts == attempt
            message.claim_expires_at = utcnow() - timedelta(seconds=1)
            session.commit()
        expired_claim = client.post(
            f"/api/v1/agent/messages/{claim['id']}/ack",
            headers=_headers(agent["agent_token"]),
            json={"claim_token": claim["claim_token"]},
        )
        assert expired_claim.status_code == 409
        previous_claim = claim["claim_token"]

    exhausted = client.get("/api/v1/agent/messages/next", headers=_headers(agent["agent_token"]))
    assert exhausted.status_code == 200
    assert exhausted.json()["message"] is None
    history = client.get(
        f"/api/v1/accounts/{account['id']}/messages", headers=_headers(admin_token)
    ).json()
    assert history[0]["state"] == "delivery_unknown"
    assert history[0]["claim_attempts"] == 3
