"""
JSON-RPC 2.0 protocol helpers for the Agent Economy MCP layer.

Implements the envelope parsing and response formatting required by the
MCP (Model Context Protocol) Streamable HTTP transport:
  https://spec.modelcontextprotocol.io/specification/basic/transports/

Supported JSON-RPC methods:
  initialize   — client capability exchange, returns server info
  tools/list   — enumerate available tools
  tools/call   — invoke a named tool with parameters

Error codes follow the JSON-RPC 2.0 spec:
  -32700  Parse error       — the body is not valid JSON
  -32600  Invalid request   — missing required JSON-RPC fields
  -32601  Method not found  — unknown method name
  -32602  Invalid params    — missing or malformed tool parameters
  -32603  Internal error    — unhandled exception in handler
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Standard JSON-RPC 2.0 error codes
# ---------------------------------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcRequest:
    """Parsed JSON-RPC 2.0 request envelope."""

    __slots__ = ("jsonrpc", "method", "params", "id")

    def __init__(
        self,
        jsonrpc: str,
        method: str,
        params: dict | None,
        id: Any,
    ) -> None:
        self.jsonrpc = jsonrpc
        self.method = method
        self.params = params if params is not None else {}
        self.id = id


def parse_request(body: Any) -> JsonRpcRequest:
    """
    Parse a JSON-RPC 2.0 request from an already-decoded dict.

    The caller is responsible for JSON decoding; this function validates the
    envelope structure and raises ValueError on invalid requests.

    Args:
        body: Python dict (already JSON-decoded).

    Returns:
        JsonRpcRequest with jsonrpc, method, params, id.

    Raises:
        ValueError with a descriptive message if the request is malformed.
    """
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")

    jsonrpc = body.get("jsonrpc")
    if jsonrpc != "2.0":
        raise ValueError(f"Invalid jsonrpc version: {jsonrpc!r}. Must be '2.0'")

    method = body.get("method")
    if not isinstance(method, str) or not method:
        raise ValueError("Missing or invalid 'method' field")

    params = body.get("params")
    if params is not None and not isinstance(params, dict):
        raise ValueError("'params' must be a JSON object when present")

    # id is optional (notifications have no id), but MCP tools always expect one
    request_id = body.get("id")

    return JsonRpcRequest(
        jsonrpc=jsonrpc,
        method=method,
        params=params,
        id=request_id,
    )


def make_result(request_id: Any, result: Any) -> dict:
    """
    Build a successful JSON-RPC 2.0 response.

    Args:
        request_id: The id from the originating request.
        result:     The tool/method result payload.

    Returns:
        JSON-serializable dict conforming to JSON-RPC 2.0 success response.
    """
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def make_error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    """
    Build a JSON-RPC 2.0 error response.

    Args:
        request_id: The id from the originating request (may be None).
        code:       Numeric error code (use module-level constants).
        message:    Human-readable error description.
        data:       Optional additional error details.

    Returns:
        JSON-serializable dict conforming to JSON-RPC 2.0 error response.
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }
