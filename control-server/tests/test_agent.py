from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest

from camfrog_control_server.adapters import (
    CloudBotRpcAdapter,
    build_cloud_bot_status_request,
    parse_cloud_bot_status_response,
)
from camfrog_control_server.agent import (
    AgentTransportError,
    ControlPlaneClient,
    NoCamfrogProvider,
    _has_command_lease_window,
    poll_once,
    validate_server_url,
)
from camfrog_control_server.transport import normalize_agent_token


def test_remote_agent_requires_https() -> None:
    assert validate_server_url("https://profiles.camfrog.com/") == "https://profiles.camfrog.com"
    assert validate_server_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    with pytest.raises(ValueError, match="HTTPS"):
        validate_server_url("http://profiles.camfrog.com")


def test_server_url_rejects_embedded_credentials_and_fragments() -> None:
    with pytest.raises(ValueError):
        validate_server_url("https://operator@profiles.camfrog.com")
    with pytest.raises(ValueError):
        validate_server_url("https://profiles.camfrog.com/path#fragment")
    with pytest.raises(ValueError, match="port"):
        validate_server_url("https://profiles.camfrog.com:invalid")


def test_server_url_rejects_dashboard_paths() -> None:
    with pytest.raises(ValueError, match="origin only"):
        validate_server_url("https://control.example/dashboard")


def test_agent_token_trims_boundary_whitespace_and_rejects_prefixes() -> None:
    assert normalize_agent_token("  issued_token-123  ") == "issued_token-123"
    with pytest.raises(ValueError, match="raw one-time-issued agent token"):
        normalize_agent_token("Bearer issued_token-123")


def test_control_plane_error_exposes_only_a_sanitized_cloudflare_ray_id() -> None:
    client = ControlPlaneClient("https://control.example", "agent-token")
    response = io.BytesIO(b"private edge response body")

    def reject_request(*_args, **_kwargs):
        from urllib.error import HTTPError

        raise HTTPError(
            "https://control.example/api/v1/agent/chat/session",
            403,
            "Forbidden",
            {"CF-Ray": "9f0fbb987bcf4b19-IAD"},
            response,
        )

    client._opener.open = reject_request
    with pytest.raises(AgentTransportError) as captured:
        client.chat_session()

    assert captured.value.status_code == 403
    assert captured.value.cf_ray_id == "9f0fbb987bcf4b19-IAD"
    assert "9f0fbb987bcf4b19-IAD" in str(captured.value)
    assert "private edge response body" not in str(captured.value)


def test_control_plane_request_identifies_the_zchat_agent() -> None:
    client = ControlPlaneClient("https://control.example", "agent-token")
    captured = {}

    class Response(io.BytesIO):
        status = 200
        headers = {}

    def fake_open(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response(b"{}")

    client._opener.open = fake_open

    assert client.request("GET", "/agent/chat/session") == {}
    assert captured["request"].get_header("User-agent") == "ZChat-Agent/1.0"


def test_provider_fails_closed_until_configured() -> None:
    provider = NoCamfrogProvider()
    assert provider.execute({"operation": "launch"}) == (
        "failed",
        "provider_not_configured",
    )
    assert provider.process_state() == "unknown"


@pytest.mark.parametrize("token", ["agent token", "agent\r\ntoken", "tökén"])
def test_control_plane_client_rejects_invalid_bearer_tokens(token: str) -> None:
    with pytest.raises(ValueError, match="one-time-issued agent token"):
        ControlPlaneClient("https://control.example", token)


def test_poll_once_includes_account_client_state_in_heartbeat() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict | None]] = []

        def request(self, method: str, path: str, body: dict | None = None) -> dict | None:
            self.calls.append((method, path, body))
            return {"command": None} if path == "/agent/commands/next" else None

    client = FakeClient()
    assert poll_once(client, NoCamfrogProvider()) is False
    assert client.calls[0] == (
        "POST",
        "/agent/heartbeat",
        {"state": "running", "client_process_state": "unknown"},
    )


def test_command_lease_preflight_uses_server_issued_duration_and_poll_round_trip() -> None:
    issued_at = datetime.now(UTC)
    command = {
        "claim_issued_at": issued_at.isoformat(),
        "claim_expires_at": (issued_at + timedelta(seconds=15)).isoformat(),
    }
    assert _has_command_lease_window(command, request_elapsed_seconds=0.1)
    assert not _has_command_lease_window(command, request_elapsed_seconds=10.1)
    assert not _has_command_lease_window(
        {**command, "claim_expires_at": (issued_at + timedelta(seconds=4)).isoformat()},
        request_elapsed_seconds=0,
    )
    assert not _has_command_lease_window(
        {**command, "claim_issued_at": issued_at.replace(tzinfo=None).isoformat()},
        request_elapsed_seconds=0,
    )


def test_poll_once_does_not_execute_command_without_safe_lease_window() -> None:
    now = datetime.now(UTC)
    command = {
        "id": "8b07c248-5ac8-4375-bb14-a46e32ad4457",
        "claim_token": "c" * 43,
        "claim_issued_at": now.isoformat(),
        "claim_expires_at": (now + timedelta(seconds=4)).isoformat(),
        "operation": "launch",
        "payload": {},
    }

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict | None]] = []

        def request(self, method: str, path: str, body: dict | None = None):
            self.calls.append((method, path, body))
            if path == "/agent/commands/next":
                return {"command": command}
            return None

    class CountingProvider(NoCamfrogProvider):
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _command: dict) -> tuple[str, str]:
            self.calls += 1
            return "succeeded", "ok"

    client = FakeClient()
    provider = CountingProvider()
    assert poll_once(client, provider) is False
    assert provider.calls == 0
    assert not any(path.endswith("/result") for _, path, _ in client.calls)


def test_poll_once_acknowledges_messages_only_after_provider_acceptance() -> None:
    message = {
        "id": "3a16ee61-7e66-4bf4-bf83-b2a8ed4939b5",
        "claim_token": "a" * 43,
    }

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict | None]] = []

        def request(self, method: str, path: str, body: dict | None = None):
            self.calls.append((method, path, body))
            if path == "/agent/commands/next":
                return {"command": None}
            if path == "/agent/messages/next":
                return {"message": message}
            return None

        def poll_chat_message(self):
            return self.request("GET", "/agent/messages/next")["message"]

        def acknowledge_chat_message(self, message_id: str, claim_token: str) -> None:
            self.request(
                "POST",
                f"/agent/messages/{message_id}/ack",
                {"claim_token": claim_token},
            )

    class AcceptingProvider(NoCamfrogProvider):
        def receive_chat_message(self, _message: dict) -> bool:
            return True

    client = FakeClient()
    assert poll_once(client, AcceptingProvider()) is True
    assert client.calls[-1] == (
        "POST",
        f"/agent/messages/{message['id']}/ack",
        {"claim_token": message["claim_token"]},
    )

    class RejectingProvider(NoCamfrogProvider):
        def receive_chat_message(self, _message: dict) -> bool:
            return False

    rejected_client = FakeClient()
    assert poll_once(rejected_client, RejectingProvider()) is False
    assert not any(path.endswith("/ack") for _, path, _ in rejected_client.calls)


def test_control_plane_client_exposes_idempotent_chat_transport() -> None:
    client = ControlPlaneClient("https://control.example", "agent-token")
    calls: list[tuple[str, str, dict | None]] = []
    claimed = {
        "id": "3a16ee61-7e66-4bf4-bf83-b2a8ed4939b5",
        "account_id": "f6f17f92-ae7a-4c65-94d3-8376f08c909e",
        "client_message_id": "ce5dbd3a-c6d7-46fa-963f-5350a9fc0c70",
        "body": "Operator message",
        "claim_token": "a" * 43,
        "claim_expires_at": "2026-10-15T10:00:00Z",
    }

    def fake_request(method: str, path: str, body: dict | None = None):
        calls.append((method, path, body))
        if path == "/agent/messages/next":
            return {"message": claimed}
        if path == "/agent/messages":
            return {
                "id": "ef8a3056-60d8-42f5-b3d9-a11d2a2f62bf",
                "account_id": claimed["account_id"],
                "agent_id": "7b34b6a3-9475-4b32-8d5c-2cefe34f2381",
                "client_message_id": claimed["client_message_id"],
                "direction": "agent_to_operator",
                "body": "Agent message",
                "state": "received",
                "claim_attempts": 0,
                "created_at": "2026-10-15T10:00:00Z",
                "delivered_at": None,
            }
        return None

    client.request = fake_request
    submitted = client.submit_chat_message(
        claimed["account_id"], claimed["client_message_id"], "Agent message"
    )
    polled = client.poll_chat_message()
    client.acknowledge_chat_message(claimed["id"], claimed["claim_token"])

    assert submitted["state"] == "received"
    assert submitted["body"] == "Agent message"
    assert polled == claimed
    assert calls == [
        (
            "POST",
            "/agent/messages",
            {
                "account_id": claimed["account_id"],
                "client_message_id": claimed["client_message_id"],
                "body": "Agent message",
            },
        ),
        ("GET", "/agent/messages/next", None),
        (
            "POST",
            f"/agent/messages/{claimed['id']}/ack",
            {"claim_token": claimed["claim_token"]},
        ),
    ]

    with pytest.raises(ValueError, match="UUIDs"):
        client.submit_chat_message("invalid", claimed["client_message_id"], "message")
    with pytest.raises(ValueError, match="token"):
        client.acknowledge_chat_message(claimed["id"], "short")


@pytest.mark.parametrize(
    "invalid_message",
    [
        {},
        {
            "id": "invalid",
            "account_id": "f6f17f92-ae7a-4c65-94d3-8376f08c909e",
            "client_message_id": "ce5dbd3a-c6d7-46fa-963f-5350a9fc0c70",
            "body": "Operator message",
            "claim_token": "a" * 43,
            "claim_expires_at": "2026-10-15T10:00:00Z",
        },
        {
            "id": "3a16ee61-7e66-4bf4-bf83-b2a8ed4939b5",
            "account_id": "not-a-uuid",
            "client_message_id": "ce5dbd3a-c6d7-46fa-963f-5350a9fc0c70",
            "body": "Operator message",
            "claim_token": "a" * 43,
            "claim_expires_at": "2026-10-15T10:00:00Z",
        },
        {
            "id": "3a16ee61-7e66-4bf4-bf83-b2a8ed4939b5",
            "account_id": "f6f17f92-ae7a-4c65-94d3-8376f08c909e",
            "client_message_id": "ce5dbd3a-c6d7-46fa-963f-5350a9fc0c70",
            "body": "Operator message",
            "claim_token": "short",
            "claim_expires_at": "2026-10-15T10:00:00Z",
        },
        {
            "id": "3a16ee61-7e66-4bf4-bf83-b2a8ed4939b5",
            "account_id": "f6f17f92-ae7a-4c65-94d3-8376f08c909e",
            "client_message_id": "ce5dbd3a-c6d7-46fa-963f-5350a9fc0c70",
            "body": "x" * 4001,
            "claim_token": "a" * 43,
            "claim_expires_at": "2026-10-15T10:00:00Z",
        },
        {
            "id": "3a16ee61-7e66-4bf4-bf83-b2a8ed4939b5",
            "account_id": "f6f17f92-ae7a-4c65-94d3-8376f08c909e",
            "client_message_id": "ce5dbd3a-c6d7-46fa-963f-5350a9fc0c70",
            "body": "Operator message",
            "claim_token": "a" * 43,
            "claim_expires_at": "not-a-datetime",
        },
        {
            "id": "3a16ee61-7e66-4bf4-bf83-b2a8ed4939b5",
            "account_id": "f6f17f92-ae7a-4c65-94d3-8376f08c909e",
            "client_message_id": "ce5dbd3a-c6d7-46fa-963f-5350a9fc0c70",
            "body": "Operator message",
            "claim_token": "a" * 43,
            "claim_expires_at": "2026-10-15T10:00:00",
        },
    ],
)
def test_control_plane_client_rejects_malformed_claimed_chat_messages(
    invalid_message: dict,
) -> None:
    client = ControlPlaneClient("https://control.example", "agent-token")
    client.request = lambda *_args, **_kwargs: {"message": invalid_message}

    with pytest.raises(AgentTransportError, match="invalid chat message"):
        client.poll_chat_message()


def test_control_plane_client_validates_heartbeat_assignment() -> None:
    client = ControlPlaneClient("https://control.example", "agent-token")
    client.request = lambda *_args, **_kwargs: {
        "agent_id": "7b34b6a3-9475-4b32-8d5c-2cefe34f2381",
        "state": "running",
        "account_id": "f6f17f92-ae7a-4c65-94d3-8376f08c909e",
        "client_process_state": None,
        "last_seen_at": "2026-10-15T10:00:00Z",
    }

    heartbeat = client.heartbeat()

    assert heartbeat["account_id"] == "f6f17f92-ae7a-4c65-94d3-8376f08c909e"

    client.request = lambda *_args, **_kwargs: {"account_id": "invalid"}
    with pytest.raises(AgentTransportError, match="invalid heartbeat"):
        client.heartbeat()


def test_control_plane_client_validates_chat_session_assignment() -> None:
    client = ControlPlaneClient("https://control.example", "agent-token")
    requests: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, *_args, **_kwargs):
        requests.append((method, path))
        return {"account_id": "f6f17f92-ae7a-4c65-94d3-8376f08c909e"}

    client.request = fake_request

    session = client.chat_session()

    assert session["account_id"] == "f6f17f92-ae7a-4c65-94d3-8376f08c909e"
    assert requests == [("GET", "/agent/chat/session")]

    client.request = lambda *_args, **_kwargs: {"account_id": "invalid"}
    with pytest.raises(AgentTransportError, match="invalid chat session"):
        client.chat_session()


def test_poll_once_does_not_deliver_unvalidated_server_chat_data() -> None:
    client = ControlPlaneClient("https://control.example", "agent-token")

    def fake_request(_method: str, path: str, _body: dict | None = None):
        if path == "/agent/commands/next":
            return {"command": None}
        if path == "/agent/messages/next":
            return {"message": {"id": "invalid", "body": "untrusted"}}
        return None

    client.request = fake_request

    class Provider(NoCamfrogProvider):
        called = False

        def receive_chat_message(self, _message: dict) -> bool:
            self.called = True
            return True

    provider = Provider()
    with pytest.raises(AgentTransportError):
        poll_once(client, provider)
    assert provider.called is False


def test_cloud_bot_rpc_adapter_uses_observed_request_and_reason_precedence() -> None:
    sent: list[tuple[str, dict]] = []

    def transport(path: str, body: dict) -> dict:
        sent.append((path, body))
        return {"result": {"uptime": 12, "status": "paused", "reason": "working"}}

    adapter = CloudBotRpcAdapter(transport)
    assert build_cloud_bot_status_request(3) == {"id": 3, "method": "status", "params": []}
    assert adapter.status()["state"] == "online"
    assert sent == [("/bot/ajax/", {"id": 1, "method": "status", "params": []})]
    assert parse_cloud_bot_status_response({"status": "not-a-known-state"})["state"] == "unknown"
