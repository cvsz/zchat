from __future__ import annotations

import secrets
from dataclasses import replace

from fastapi.testclient import TestClient

MCP_VERSION = "2026-07-28"
VERSION_META = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META = "io.modelcontextprotocol/clientCapabilities"


def _headers(token: str, method: str, name: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "MCP-Protocol-Version": MCP_VERSION,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _payload(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    metadata = {
        VERSION_META: MCP_VERSION,
        CLIENT_INFO_META: {"name": "zworkforce", "version": "test"},
        CLIENT_CAPABILITIES_META: {},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {**(params or {}), "_meta": metadata},
    }


def _enable_integration(client: TestClient) -> str:
    token = secrets.token_urlsafe(48)
    client.app.state.settings = replace(
        client.app.state.settings, zworkforce_integration_token=token
    )
    return token


def test_mcp_discovery_lists_only_read_only_tools_and_resources(api_client) -> None:
    client, _, _ = api_client
    token = _enable_integration(client)
    response = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "server/discover"),
        json=_payload("server/discover"),
    )
    assert response.status_code == 200
    assert response.json()["result"]["supportedVersions"] == [MCP_VERSION]

    tools = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "tools/list"),
        json=_payload("tools/list"),
    ).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"zchat.capabilities", "zchat.health"}
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)

    resources = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "resources/list"),
        json=_payload("resources/list"),
    ).json()["result"]["resources"]
    assert {resource["uri"] for resource in resources} == {
        "zchat://capabilities",
        "zchat://health",
    }


def test_mcp_reads_capabilities_and_health_through_zworkforce_client_contract(api_client) -> None:
    client, _, _ = api_client
    token = _enable_integration(client)

    capability_response = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "tools/call", "zchat.capabilities"),
        json=_payload("tools/call", {"name": "zchat.capabilities", "arguments": {}}),
    )
    assert capability_response.status_code == 200
    capability_result = capability_response.json()["result"]
    assert capability_result["isError"] is False
    assert capability_result["structuredContent"]["protocol"] == "zchat-zworkforce"
    assert "health" in capability_result["structuredContent"]["read_only_resources"]
    assert "capabilities" in capability_result["structuredContent"]["read_only_resources"]

    health_response = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "resources/read", "zchat://health"),
        json=_payload("resources/read", {"uri": "zchat://health"}),
    )
    assert health_response.status_code == 200
    health_content = health_response.json()["result"]["contents"][0]
    assert health_content["uri"] == "zchat://health"
    assert '"database": "reachable"' in health_content["text"]


def test_mcp_rejects_invalid_protocol_and_mismatched_method_headers(api_client) -> None:
    client, _, _ = api_client
    token = _enable_integration(client)

    invalid_version = _payload("tools/list")
    invalid_version["params"]["_meta"][VERSION_META] = "2025-11-25"
    response = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "tools/list"),
        json=invalid_version,
    )
    assert response.json()["error"]["code"] == -32022

    response = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "tools/list"),
        json=_payload("resources/list"),
    )
    assert response.json()["error"]["code"] == -32020


def test_mcp_rejects_missing_or_incorrect_tool_name_header(api_client) -> None:
    client, _, _ = api_client
    token = _enable_integration(client)
    payload = _payload("tools/call", {"name": "zchat.health", "arguments": {}})

    missing_header = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "tools/call"),
        json=payload,
    )
    wrong_header = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "tools/call", "zchat.capabilities"),
        json=payload,
    )
    assert missing_header.json()["error"]["code"] == -32020
    assert wrong_header.json()["error"]["code"] == -32020


def test_mcp_does_not_expose_command_mutation_tool(api_client) -> None:
    client, _, _ = api_client
    token = _enable_integration(client)
    response = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers(token, "tools/call", "zchat.submit_command"),
        json=_payload("tools/call", {"name": "zchat.submit_command", "arguments": {}}),
    )
    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True


def test_mcp_requires_the_configured_service_token(api_client) -> None:
    client, _, _ = api_client
    token = _enable_integration(client)
    response = client.post(
        "/api/v1/integrations/zworkforce/v1/mcp",
        headers=_headers("wrong-token", "tools/list"),
        json=_payload("tools/list"),
    )
    assert response.status_code == 401
    assert token not in response.text
