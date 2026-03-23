"""
Test agent helper for Agent Economy simulation tests.

TestAgent wraps an httpx.AsyncClient and a stored action_token to provide
a clean interface for driving scripted agent behaviors in simulation tests.

This is the ONLY abstraction layer over the real system. Everything under it
is real: real HTTP, real auth, real REST endpoints, real DB writes.

Usage:
    agent = await TestAgent.signup(client, "agent_alice")
    result = await agent.call("gather", {"resource": "berries"})
    status = await agent.status()
    print(status["balance"], status["inventory"])
"""

from __future__ import annotations

from typing import Any

import httpx


# Maps tool names to (HTTP method, REST endpoint path).
TOOL_ROUTES = {
    "signup": ("POST", "/v1/signup"),
    "get_status": ("GET", "/v1/me"),
    "rent_housing": ("POST", "/v1/housing"),
    "gather": ("POST", "/v1/gather"),
    "register_business": ("POST", "/v1/businesses"),
    "configure_production": ("POST", "/v1/businesses/production"),
    "set_prices": ("POST", "/v1/businesses/prices"),
    "manage_employees": ("POST", "/v1/employees"),
    "list_jobs": ("GET", "/v1/jobs"),
    "apply_job": ("POST", "/v1/jobs/apply"),
    "work": ("POST", "/v1/work"),
    "marketplace_order": ("POST", "/v1/market/orders"),
    "marketplace_browse": ("GET", "/v1/market"),
    "trade": ("POST", "/v1/trades"),
    "bank": ("POST", "/v1/bank"),
    "vote": ("POST", "/v1/vote"),
    "get_economy": ("GET", "/v1/economy"),
    "messages": ("POST", "/v1/messages"),
}


class TestAgent:
    """
    Thin wrapper around httpx.AsyncClient for simulating an agent.

    Stores the agent's name and action_token, provides:
    - signup() classmethod to register a new agent
    - call() to invoke any REST API endpoint by tool name
    - try_call() to call and capture errors without raising
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
        Sign up a new agent via POST /v1/signup.

        Sends a plain JSON body with the agent name. No auth required.
        Parses the response to extract action_token and view_token.

        Args:
            client: The httpx.AsyncClient pointed at the test app.
            name:   The agent name to register.

        Returns:
            TestAgent instance with tokens populated.

        Raises:
            AssertionError: If signup fails (name taken, validation error, etc.)
        """
        response = await client.post("/v1/signup", json={"name": name})
        assert response.status_code == 200, (
            f"Signup HTTP error {response.status_code} for {name!r}: {response.text[:200]}"
        )

        body = response.json()
        assert body.get("ok") is True, f"Signup failed for {name!r}: {body}"

        result_data = body["data"]
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
        Invoke a REST API endpoint by tool name as this agent.

        Looks up the route from TOOL_ROUTES, sends the request with Bearer auth,
        and returns the parsed result dict on success.

        Args:
            tool_name: Name of the tool to invoke (key in TOOL_ROUTES).
            params:    Request parameters dict (JSON body for POST, query params for GET).
                       Defaults to empty dict if None.

        Returns:
            The parsed data dict from the response.

        Raises:
            ToolCallError: If the endpoint returns a 400 or 401 error.
            RuntimeError:  If the tool_name is not in TOOL_ROUTES or an unexpected status is returned.
        """
        self._call_count += 1

        route = TOOL_ROUTES.get(tool_name)
        if route is None:
            raise RuntimeError(f"Unknown tool {tool_name!r}: not found in TOOL_ROUTES")

        method, path = route
        headers = {"Authorization": f"Bearer {self.action_token}"}

        if method == "GET":
            response = await self.client.get(path, params=params or {}, headers=headers)
        else:
            response = await self.client.post(path, json=params or {}, headers=headers)

        if response.status_code == 401:
            raise ToolCallError(
                tool_name=tool_name,
                agent_name=self.name,
                code="UNAUTHORIZED",
                message=response.text[:200],
            )

        if response.status_code == 403:
            raise ToolCallError(
                tool_name=tool_name,
                agent_name=self.name,
                code="AGENT_DEACTIVATED",
                message=response.json().get("detail", response.text[:200]),
            )

        if response.status_code == 400:
            body = response.json()
            raise ToolCallError(
                tool_name=tool_name,
                agent_name=self.name,
                code=body.get("error_code", "UNKNOWN"),
                message=body.get("message", str(body)),
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"HTTP {response.status_code} calling {tool_name!r} for {self.name}: "
                f"{response.text[:200]}"
            )

        body = response.json()
        return body.get("data", body)

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
    """Raised when a REST API call returns an error response."""

    def __init__(self, tool_name: str, agent_name: str, code: str, message: str) -> None:
        super().__init__(f"[{code}] {tool_name} failed for {agent_name}: {message}")
        self.tool_name = tool_name
        self.agent_name = agent_name
        self.code = code
        self.message = message
