"""
Economic Stress Tests for Agent Economy.

Two large-scale tests that push the simulation through extreme conditions:

1. test_economic_collapse_and_recovery
   - Phase 1: Build a thriving economy with 8 agents, businesses, and workers
   - Phase 2: Drain agent balances to trigger mass bankruptcy
   - Phase 3: Verify NPC gap-filling keeps the economy running; fresh agents can join

2. test_government_policy_transitions
   - Phase 1: Establish free_market baseline with 6 voting-age agents
   - Phase 2: Vote in authoritarian government, verify high taxes and enforcement
   - Phase 3: Vote in libertarian government, verify low taxes and enforcement
   - Phase 4: Final invariant checks

Both tests verify the "no negative inventory" invariant at every checkpoint
and exercise the full tick system through the real REST API.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.helpers import TestAgent
from tests.stress.collapse_recovery import phase2_economic_crisis, phase3_recovery
from tests.stress.collapse_setup import phase1_build_economy
from tests.stress.government_setup import phase1_free_market
from tests.stress.government_transitions import (
    phase2_authoritarian,
    phase3_libertarian,
    phase4_final_checks,
)


class DisconnectOnReceiveASGITransport(httpx.ASGITransport):
    """
    ASGI transport that simulates a client closing the connection on first read.

    This lets us verify the app handles ``http.disconnect`` without turning it
    into an unhandled 500/Sentry event.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": self.client,
            "root_path": self.root_path,
        }

        status_code = None
        response_headers = None
        body_parts: list[bytes] = []
        response_started = False
        response_complete = asyncio.Event()

        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            nonlocal status_code, response_headers, response_started

            if message["type"] == "http.response.start":
                assert not response_started
                status_code = int(message["status"])
                response_headers = message.get("headers", [])
                response_started = True
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = bool(message.get("more_body", False))
                if body and request.method != "HEAD":
                    body_parts.append(body)
                if not more_body:
                    response_complete.set()

        try:
            await self.app(scope, receive, send)
        except Exception:
            if self.raise_app_exceptions:
                raise
            response_complete.set()
            if status_code is None:
                status_code = 500
            if response_headers is None:
                response_headers = {}

        assert response_complete.is_set()
        assert status_code is not None
        assert response_headers is not None

        return httpx.Response(
            status_code,
            headers=response_headers,
            content=b"".join(body_parts),
            request=request,
        )


class FailOnReceiveASGITransport(httpx.ASGITransport):
    """ASGI transport that fails the test if the app tries to read the body."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": self.client,
            "root_path": self.root_path,
        }

        status_code = None
        response_headers = None
        body_parts: list[bytes] = []
        response_started = False
        response_complete = asyncio.Event()

        async def receive() -> dict[str, object]:
            raise AssertionError(
                f"{request.method} {request.url.path} unexpectedly tried to read a Content-Length: 0 body"
            )

        async def send(message: dict[str, object]) -> None:
            nonlocal status_code, response_headers, response_started

            if message["type"] == "http.response.start":
                assert not response_started
                status_code = int(message["status"])
                response_headers = message.get("headers", [])
                response_started = True
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = bool(message.get("more_body", False))
                if body and request.method != "HEAD":
                    body_parts.append(body)
                if not more_body:
                    response_complete.set()

        try:
            await self.app(scope, receive, send)
        except Exception:
            if self.raise_app_exceptions:
                raise
            response_complete.set()
            if status_code is None:
                status_code = 500
            if response_headers is None:
                response_headers = {}

        assert response_complete.is_set()
        assert status_code is not None
        assert response_headers is not None

        return httpx.Response(
            status_code,
            headers=response_headers,
            content=b"".join(body_parts),
            request=request,
        )


# ---------------------------------------------------------------------------
# Test 1: Economic Collapse and Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_economic_collapse_and_recovery(client, app, clock, run_tick, redis_client):
    """
    Simulate an economy that thrives, collapses via mass bankruptcy,
    and recovers through NPC gap-filling.
    """
    print(f"\n\n{'#' * 60}")
    print("# STRESS TEST: ECONOMIC COLLAPSE AND RECOVERY")
    print(f"# Start time: {clock.now().isoformat()}")
    print(f"{'#' * 60}")

    state = await phase1_build_economy(client, app, clock, run_tick)
    state = await phase2_economic_crisis(app, clock, run_tick, state)
    await phase3_recovery(client, app, clock, run_tick, state)

    print(f"\n{'=' * 60}")
    print("  STRESS TEST: Economic Collapse and Recovery -- PASSED")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Test 2: Government Policy Transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_government_policy_transitions(client, app, clock, run_tick, redis_client):
    """
    Test cascading effects of government policy changes across
    multiple election cycles: free_market -> authoritarian -> libertarian.
    """
    print(f"\n\n{'#' * 60}")
    print("# STRESS TEST: GOVERNMENT POLICY TRANSITIONS")
    print(f"# Start time: {clock.now().isoformat()}")
    print(f"{'#' * 60}")

    state = await phase1_free_market(client, app, clock, run_tick, redis_client)
    state = await phase2_authoritarian(app, clock, run_tick, redis_client, state)
    state = await phase3_libertarian(app, clock, run_tick, redis_client, state)
    await phase4_final_checks(app, state)

    print(f"\n{'=' * 60}")
    print("  STRESS TEST: Government Policy Transitions -- PASSED")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Test 3: Malformed JSON returns 400 instead of 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_returns_400(client, app, clock, run_tick, redis_client):
    """
    Regression test for Sentry issue 107538054: a literal newline inside a
    JSON string value caused an unhandled JSONDecodeError (500). The fix
    catches the error in _body_or_empty and returns INVALID_PARAMS (400).
    """
    agent = await TestAgent.signup(client, "json_tester")
    headers = {
        "Authorization": f"Bearer {agent.action_token}",
        "Content-Type": "application/json",
    }

    # Reproduce the exact payload from the Sentry trace: two UUIDs joined by
    # a literal newline inside business_id — invalid JSON.
    malformed_body = (
        b'{"action":"close_business","business_id":"'
        b"df23a5f9-64a2-4338-8008-1435a7276549"
        b"\n"
        b'ff544f88-35e4-4aef-80b4-ddd0a97dfd80"}'
    )

    response = await client.post("/v1/employees", content=malformed_body, headers=headers)
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"

    body = response.json()
    assert body["error_code"] == "INVALID_PARAMS"
    assert "Invalid JSON" in body["message"]

    # Also verify completely unparseable garbage
    response2 = await client.post("/v1/employees", content=b"not json at all", headers=headers)
    assert response2.status_code == 400
    assert response2.json()["error_code"] == "INVALID_PARAMS"


@pytest.mark.asyncio
async def test_client_disconnect_during_body_read_returns_400(app, client):
    """
    Regression test for Sentry issue 108216489: if the client disconnects while
    the app is reading an optional JSON body, return INVALID_PARAMS instead of
    letting ClientDisconnect bubble out as a 500.
    """
    agent = await TestAgent.signup(client, "disconnect_reader")
    headers = {
        "Authorization": f"Bearer {agent.action_token}",
        "Content-Type": "application/json",
    }

    async def streaming_body():
        if False:
            yield b"{}"

    transport = DisconnectOnReceiveASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as disconnect_client:
        request = disconnect_client.build_request(
            "POST",
            "/v1/jobs/apply",
            content=streaming_body(),
            headers=headers,
        )
        response = await disconnect_client.send(request)

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error_code"] == "INVALID_PARAMS"
    assert "Client disconnected" in body["message"]


@pytest.mark.asyncio
async def test_work_with_content_length_zero_does_not_read_body_stream(app, client):
    """
    Regression test for issue 108216489: POST /v1/work with Content-Length: 0
    should treat the body as empty without touching the ASGI receive channel.
    """
    agent = await TestAgent.signup(client, "disconnect_zero_len")
    headers = {
        "Authorization": f"Bearer {agent.action_token}",
        "Content-Type": "application/json",
    }

    transport = FailOnReceiveASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as strict_client:
        response = await strict_client.post("/v1/work", content=b"", headers=headers)

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error_code"] == "NOT_EMPLOYED"
