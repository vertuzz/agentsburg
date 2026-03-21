"""
Test agent helper for Agent Economy simulation tests.

TestAgent wraps an httpx.AsyncClient and a stored action_token to provide
a clean interface for driving scripted agent behaviors in simulation tests.

This is the ONLY abstraction layer over the real system. Everything under it
is real: real HTTP, real auth, real JSON-RPC parsing, real DB writes.

Usage:
    agent = await TestAgent.signup(client, "agent_alice")
    result = await agent.call("gather", {"resource": "berries"})
    status = await agent.status()
    print(status["balance"], status["inventory"])
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class TestAgent:
    """
    Thin wrapper around httpx.AsyncClient for simulating an agent.

    Stores the agent's name and action_token, provides:
    - signup() classmethod to register a new agent
    - call() to invoke any MCP tool via JSON-RPC
    - status() shorthand for get_status
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        name: str,
        action_token: str,
        view_token: str,
    ) -> None:
        self.client = client
        self.name = name
        self.action_token = action_token
        self.view_token = view_token
        self._call_count = 0

    @classmethod
    async def signup(cls, client: httpx.AsyncClient, name: str) -> "TestAgent":
        """
        Sign up a new agent via the real POST /mcp endpoint.

        Sends a real JSON-RPC tools/call for 'signup'. No mocks, no shortcuts.
        Parses the response to extract action_token and view_token.

        Args:
            client: The httpx.AsyncClient pointed at the test app.
            name:   The agent name to register.

        Returns:
            TestAgent instance with tokens populated.

        Raises:
            AssertionError: If signup fails (name taken, validation error, etc.)
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "signup",
                "arguments": {"name": name},
            },
        }

        response = await client.post("/mcp", json=payload)
        assert response.status_code == 200, f"Signup HTTP error: {response.status_code}"

        body = response.json()
        assert "error" not in body, f"Signup failed for {name!r}: {body.get('error')}"
        assert "result" in body, f"No result in signup response: {body}"

        # The tool result is wrapped in MCP content format
        content = body["result"]["content"]
        assert content, f"Empty content in signup response: {body}"

        result_text = content[0]["text"]
        result_data = json.loads(result_text)

        assert "action_token" in result_data, f"No action_token in signup result: {result_data}"
        assert "view_token" in result_data, f"No view_token in signup result: {result_data}"

        return cls(
            client=client,
            name=name,
            action_token=result_data["action_token"],
            view_token=result_data["view_token"],
        )

    async def call(self, tool_name: str, params: dict | None = None) -> dict:
        """
        Invoke an MCP tool as this agent.

        Sends a real POST /mcp with Bearer auth and JSON-RPC envelope.
        Returns the parsed result dict on success.

        Args:
            tool_name: Name of the tool to invoke.
            params:    Tool arguments dict (empty dict if None).

        Returns:
            The parsed result dict from the tool.

        Raises:
            RuntimeError: If the tool returns an error response.
        """
        self._call_count += 1
        call_id = self._call_count

        payload = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": params or {},
            },
        }

        response = await self.client.post(
            "/mcp",
            json=payload,
            headers={"Authorization": f"Bearer {self.action_token}"},
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code} calling {tool_name!r} for {self.name}: "
                f"{response.text[:200]}"
            )

        body = response.json()

        if "error" in body:
            error = body["error"]
            code = error.get("data", {}).get("code", "UNKNOWN") if isinstance(error.get("data"), dict) else "UNKNOWN"
            raise ToolCallError(
                tool_name=tool_name,
                agent_name=self.name,
                code=code,
                message=error.get("message", str(error)),
            )

        result = body.get("result", {})

        # Unwrap MCP content format
        content = result.get("content", [])
        if content and isinstance(content, list) and content[0].get("type") == "text":
            return json.loads(content[0]["text"])

        return result

    async def try_call(self, tool_name: str, params: dict | None = None) -> tuple[dict | None, str | None]:
        """
        Call a tool, returning (result, None) on success or (None, error_code) on failure.

        Useful for testing that cooldowns, storage limits, etc. return proper errors.

        Returns:
            (result_dict, None) on success
            (None, error_code_string) on failure
        """
        try:
            result = await self.call(tool_name, params)
            return result, None
        except ToolCallError as e:
            return None, e.code

    async def status(self) -> dict:
        """Get this agent's current status."""
        return await self.call("get_status")

    def __repr__(self) -> str:
        return f"<TestAgent name={self.name!r}>"


class ToolCallError(Exception):
    """Raised when an MCP tool call returns an error response."""

    def __init__(self, tool_name: str, agent_name: str, code: str, message: str) -> None:
        super().__init__(f"[{code}] {tool_name} failed for {agent_name}: {message}")
        self.tool_name = tool_name
        self.agent_name = agent_name
        self.code = code
        self.message = message
