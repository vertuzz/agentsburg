"""
MCP endpoint router for Agent Economy.

Exposes a single POST /mcp endpoint implementing the MCP Streamable HTTP
transport (stateless request/response, no SSE). All agent interactions
flow through this endpoint using JSON-RPC 2.0 envelopes.

Supported JSON-RPC methods:
  initialize  — client capability exchange (unauthenticated)
  tools/list  — enumerate all available tools (unauthenticated)
  tools/call  — invoke a named tool (authenticated except signup)

Authentication:
  Bearer token in the Authorization header. The signup tool is exempt —
  it's how agents get their token in the first place.

Error handling:
  Malformed JSON         → HTTP 200, JSON-RPC parse error (-32700)
  Invalid envelope       → HTTP 200, JSON-RPC invalid request (-32600)
  Unknown method         → HTTP 200, JSON-RPC method not found (-32601)
  Invalid tool params    → HTTP 200, JSON-RPC invalid params (-32602)
  Tool raises ToolError  → HTTP 200, JSON-RPC internal error (-32603) with error data
  Unhandled exception    → HTTP 200, JSON-RPC internal error (-32603)

All errors return HTTP 200 per JSON-RPC spec (the error is in the body).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.mcp.auth import get_agent_from_request
from backend.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JsonRpcRequest,
    make_error,
    make_result,
    parse_request,
)
from backend.mcp.tools import ToolError, registry

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# JSON-RPC error code for rate limiting
RATE_LIMITED = -32029


async def _check_rate_limit(
    redis: "aioredis.Redis",
    key: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    """Check rate limit. Raises ValueError if exceeded."""
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window_seconds)
    if current > max_requests:
        raise ValueError(f"Rate limit exceeded. Max {max_requests} requests per {window_seconds}s.")

router = APIRouter()

# Server info returned in the initialize response
SERVER_INFO = {
    "name": "agent-economy",
    "version": "1.0.0",
    "description": "A real-time multiplayer economic simulator for AI agents",
}

SERVER_CAPABILITIES = {
    "tools": {},
}


@router.post("/mcp", tags=["mcp"])
async def mcp_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    MCP Streamable HTTP endpoint.

    Accepts JSON-RPC 2.0 requests and returns JSON-RPC 2.0 responses.
    All agent interactions with the economy flow through this single endpoint.
    """
    # --- 1. Parse JSON body ---
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            content=make_error(None, PARSE_ERROR, "Invalid JSON in request body"),
            status_code=200,
        )

    # --- 2. Parse JSON-RPC envelope ---
    try:
        rpc_request: JsonRpcRequest = parse_request(body)
    except ValueError as exc:
        return JSONResponse(
            content=make_error(None, INVALID_REQUEST, str(exc)),
            status_code=200,
        )

    request_id = rpc_request.id
    method = rpc_request.method
    params = rpc_request.params

    # --- 3. Route to method handler ---

    if method == "initialize":
        result = _handle_initialize(params)
        return JSONResponse(content=make_result(request_id, result))

    if method == "tools/list":
        result = _handle_tools_list()
        return JSONResponse(content=make_result(request_id, result))

    if method == "tools/call":
        return await _handle_tools_call(request, rpc_request, db)

    # Unknown method
    return JSONResponse(
        content=make_error(
            request_id,
            METHOD_NOT_FOUND,
            f"Method not found: {method!r}. Supported: initialize, tools/list, tools/call",
        ),
        status_code=200,
    )


def _handle_initialize(params: dict) -> dict:
    """
    Handle the MCP initialize handshake.

    Returns server capabilities and info. The client sends this first to
    discover what the server supports. No authentication required.

    Protocol version 2024-11-05 is the stable MCP spec version.
    """
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": SERVER_CAPABILITIES,
        "serverInfo": SERVER_INFO,
    }


def _handle_tools_list() -> dict:
    """
    Return the complete list of available MCP tools.

    Each tool entry includes name, description, and inputSchema (JSON Schema).
    No authentication required — agents need this to know what they can do.
    """
    return {"tools": registry.get_tool_list()}


async def _handle_tools_call(
    request: Request,
    rpc_request: JsonRpcRequest,
    db: AsyncSession,
) -> JSONResponse:
    """
    Dispatch a tools/call request to the appropriate tool handler.

    Authenticates the request (except for the signup tool), validates the
    tool name, and delegates to the tool registry.
    """
    request_id = rpc_request.id
    params = rpc_request.params

    # Extract tool name and arguments from params
    tool_name = params.get("name")
    if not tool_name or not isinstance(tool_name, str):
        return JSONResponse(
            content=make_error(
                request_id,
                INVALID_PARAMS,
                "tools/call requires 'name' parameter specifying the tool to invoke",
            ),
            status_code=200,
        )

    tool_arguments = params.get("arguments", {})
    if not isinstance(tool_arguments, dict):
        return JSONResponse(
            content=make_error(
                request_id,
                INVALID_PARAMS,
                "'arguments' must be a JSON object",
            ),
            status_code=200,
        )

    # Authenticate — signup is the only tool that skips this
    if tool_name == "signup":
        agent = None
    else:
        agent = await get_agent_from_request(request, db)
        if agent is None:
            return JSONResponse(
                content=make_error(
                    request_id,
                    INVALID_REQUEST,
                    "Authentication required. "
                    "Include 'Authorization: Bearer <action_token>' header. "
                    "Use the signup tool to create a new agent.",
                ),
                status_code=200,
            )

    # Pull shared resources from app state
    clock = request.app.state.clock
    redis = request.app.state.redis
    settings = request.app.state.settings

    # --- Rate limiting ---
    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not client_ip:
        client_ip = request.client.host if request.client else "unknown"

    try:
        # Global per-IP rate limit
        await _check_rate_limit(redis, f"ratelimit:ip:{client_ip}", 120, 60)

        if tool_name == "signup":
            # Stricter limit for unauthenticated signup
            await _check_rate_limit(redis, f"ratelimit:ip:{client_ip}:signup", 5, 60)
        elif agent is not None:
            # Per-agent rate limit for authenticated calls
            await _check_rate_limit(redis, f"ratelimit:agent:{agent.id}", 60, 60)
    except ValueError as exc:
        return JSONResponse(
            content=make_error(
                request_id,
                RATE_LIMITED,
                str(exc),
            ),
            status_code=200,
        )

    # Dispatch to tool handler
    try:
        result = await registry.call_tool(
            name=tool_name,
            params=tool_arguments,
            agent=agent,
            db=db,
            clock=clock,
            redis=redis,
            settings=settings,
        )
    except KeyError:
        return JSONResponse(
            content=make_error(
                request_id,
                METHOD_NOT_FOUND,
                f"Tool not found: {tool_name!r}. Call tools/list to see available tools.",
            ),
            status_code=200,
        )
    except ToolError as exc:
        return JSONResponse(
            content=make_error(
                request_id,
                INTERNAL_ERROR,
                exc.message,
                data={"code": exc.code},
            ),
            status_code=200,
        )
    except Exception as exc:
        logger.exception("Unhandled exception in tool %r", tool_name)
        return JSONResponse(
            content=make_error(
                request_id,
                INTERNAL_ERROR,
                "An unexpected error occurred. Please try again.",
                data={"tool": tool_name},
            ),
            status_code=200,
        )

    return JSONResponse(content=make_result(request_id, result))
