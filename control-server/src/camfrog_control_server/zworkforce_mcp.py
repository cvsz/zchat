"""Read-only MCP surface for authenticated zWorkforce clients."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__
from .api import get_db, require_zworkforce_integration
from .feature_capabilities import get_feature_capabilities
from .schemas import ZChatWorkforceCapabilitiesV1

router = APIRouter()

MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
MCP_CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
MCP_CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
MCP_SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
MAX_MCP_REQUEST_BYTES = 65_536

_EMPTY_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
_TOOLS = [
    {
        "name": "zchat.capabilities",
        "description": "Read ZChat feature capability claims and their evidence status.",
        "inputSchema": _EMPTY_OBJECT_SCHEMA,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "zchat.health",
        "description": "Read ZChat control-plane and database health without account data.",
        "inputSchema": _EMPTY_OBJECT_SCHEMA,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]
_RESOURCES = [
    {
        "uri": "zchat://capabilities",
        "name": "ZChat feature capabilities",
        "description": "Feature capability claims with evidence status.",
        "mimeType": "application/json",
    },
    {
        "uri": "zchat://health",
        "name": "ZChat service health",
        "description": "Control-plane and database health; contains no account data.",
        "mimeType": "application/json",
    },
]


def _result(
    request_id: int | str, value: dict[str, Any], *, cacheable: bool = False
) -> JSONResponse:
    value.setdefault("resultType", "complete")
    if cacheable:
        value.setdefault("ttlMs", 0)
        value.setdefault("cacheScope", "private")
    metadata = value.setdefault("_meta", {})
    metadata.setdefault(
        MCP_SERVER_INFO_META_KEY,
        {"name": "zchat-control-server", "version": __version__},
    )
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": value})


def _error(request_id: int | str | None, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )


def _capabilities(request: Request) -> dict[str, Any]:
    response = ZChatWorkforceCapabilitiesV1(
        protocol="zchat-zworkforce",
        protocol_version="1.0",
        compatible_versions=["1.0"],
        server_version=request.app.version,
        read_only_resources=["health", "capabilities"],
        features=get_feature_capabilities(),
    )
    return response.model_dump(mode="json")


def _health(db: Session) -> dict[str, str] | None:
    try:
        db.execute(select(1))
    except Exception:
        db.rollback()
        return None
    return {"status": "healthy", "database": "reachable"}


async def _read_request(request: Request) -> dict[str, Any] | JSONResponse:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_MCP_REQUEST_BYTES:
                return JSONResponse({"detail": "MCP request is too large."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_MCP_REQUEST_BYTES:
            return JSONResponse({"detail": "MCP request is too large."}, status_code=413)
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse({"detail": "MCP request must be valid JSON."}, status_code=400)
    if not isinstance(value, dict):
        return JSONResponse({"detail": "MCP request must be a JSON object."}, status_code=400)
    return value


@router.post(
    "/integrations/zworkforce/v1/mcp",
)
async def zworkforce_mcp(
    request: Request,
    _service: Annotated[None, Depends(require_zworkforce_integration)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Serve only read-only MCP resources and tools to the authenticated service."""
    payload = await _read_request(request)
    if isinstance(payload, JSONResponse):
        return payload

    request_id = payload.get("id")
    if (
        payload.get("jsonrpc") != "2.0"
        or isinstance(request_id, bool)
        or not isinstance(request_id, (int, str))
        or not isinstance(payload.get("method"), str)
        or not isinstance(payload.get("params", {}), dict)
        or set(payload) - {"jsonrpc", "id", "method", "params"}
    ):
        safe_id = request_id if isinstance(request_id, (int, str)) else None
        return _error(safe_id, -32600, "Invalid JSON-RPC request.")

    method = payload["method"]
    params = payload.get("params", {})
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return _error(request_id, -32022, "Missing MCP protocol metadata.")
    client_info = metadata.get(MCP_CLIENT_INFO_META_KEY)
    if (
        request.headers.get("mcp-protocol-version") != MCP_PROTOCOL_VERSION
        or metadata.get(MCP_PROTOCOL_VERSION_META_KEY) != MCP_PROTOCOL_VERSION
        or not isinstance(metadata.get(MCP_CLIENT_CAPABILITIES_META_KEY), dict)
        or not isinstance(client_info, dict)
        or not isinstance(client_info.get("name"), str)
        or not client_info["name"].strip()
        or not isinstance(client_info.get("version"), str)
        or not client_info["version"].strip()
    ):
        return _error(request_id, -32022, "Unsupported or incomplete MCP protocol metadata.")

    if request.headers.get("mcp-method") != method:
        return _error(request_id, -32020, "Mcp-Method must match the JSON-RPC method.")

    expected_name = None
    if method == "tools/call":
        expected_name = params.get("name")
    elif method == "resources/read":
        expected_name = params.get("uri")
    if expected_name is not None and (
        not isinstance(expected_name, str)
        or not expected_name
        or request.headers.get("mcp-name") != expected_name
    ):
        return _error(request_id, -32020, "Mcp-Name must match the requested tool or resource.")

    if method == "server/discover":
        result = {
            "capabilities": {"tools": {}, "resources": {}},
            "supportedVersions": [MCP_PROTOCOL_VERSION],
            "_meta": {
                MCP_SERVER_INFO_META_KEY: {
                    "name": "zchat-control-server",
                    "version": __version__,
                }
            },
        }
        return _result(request_id, result, cacheable=True)
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": _TOOLS}, cacheable=True)
    if method == "resources/list":
        return _result(request_id, {"resources": _RESOURCES}, cacheable=True)
    if method == "resources/read":
        if set(params) - {"uri", "_meta"}:
            return _error(request_id, -32602, "Invalid resources/read parameters.")
        uri = params.get("uri")
        if uri == "zchat://capabilities":
            value = _capabilities(request)
            content = {"uri": uri, "mimeType": "application/json", "text": json.dumps(value)}
            return _result(request_id, {"contents": [content]}, cacheable=True)
        if uri == "zchat://health":
            value = _health(db)
            if value is None:
                return _error(request_id, -32003, "ZChat database is unavailable.")
            content = {"uri": uri, "mimeType": "application/json", "text": json.dumps(value)}
            return _result(request_id, {"contents": [content]}, cacheable=True)
        return _error(request_id, -32004, "Resource not found.")
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if (
            set(params) - {"name", "arguments", "_meta"}
            or not isinstance(name, str)
            or not isinstance(arguments, dict)
        ):
            return _error(request_id, -32602, "Invalid tools/call parameters.")
        if arguments:
            return _error(request_id, -32602, "Read-only ZChat tools accept no arguments.")
        if name == "zchat.capabilities":
            value = _capabilities(request)
        elif name == "zchat.health":
            value = _health(db)
            if value is None:
                return _result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": "ZChat database is unavailable."}],
                        "isError": True,
                    },
                )
        else:
            return _result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "Unsupported or unauthorized ZChat tool.",
                        }
                    ],
                    "isError": True,
                },
            )
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                "structuredContent": value,
                "isError": False,
            },
        )
    return _error(request_id, -32601, "Unsupported MCP method.")
