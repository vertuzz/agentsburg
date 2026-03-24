"""
Spectator Commentary & Daily Summary Test (Phase 3)

Verifies that /api/models/commentary returns model comparison headlines
and that /api/summary/daily returns top events, market movers, and stats.

Sets up agents with different models and balances, runs a tick, then
checks both endpoints return well-formed data.
"""

from __future__ import annotations

from tests.conftest import give_balance
from tests.helpers import TestAgent


async def run_commentary_summary_test(client, app, clock, run_tick, redis_client):
    """
    Phase 3 spectator test: model commentary and daily summary.

    1. Sign up agents with different models
    2. Give different balances
    3. Run a tick
    4. Verify /api/models/commentary
    5. Verify /api/summary/daily
    """

    # ── Step 1: Sign up agents with different models ──
    alice = await TestAgent.signup(client, "Comm-Alice", model="ModelAlpha")
    bob = await TestAgent.signup(client, "Comm-Bob", model="ModelBeta")

    # Ensure agents are usable
    assert alice.action_token
    assert bob.action_token

    # ── Step 2: Give different balances ──
    await give_balance(app, "Comm-Alice", 5000)
    await give_balance(app, "Comm-Bob", 2000)

    # ── Step 3: Run a tick to generate events and stats ──
    await run_tick(hours=1.1)

    # ── Step 4: Verify /api/models/commentary ──
    resp = await client.get("/api/models/commentary")
    assert resp.status_code == 200, f"Commentary status {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "headline" in data
    assert isinstance(data["headline"], str)
    assert len(data["headline"]) > 0, "Headline should be non-empty"

    assert "comparisons" in data
    assert isinstance(data["comparisons"], list)

    assert "model_count" in data
    assert data["model_count"] >= 2, f"Expected model_count >= 2, got {data['model_count']}"

    # Headline should mention at least one model name (may include models from other tests)
    headline_lower = data["headline"].lower()
    known_models = ["modelalpha", "modelbeta", "modela", "modelb", "modelc", "test-model"]
    assert any(m in headline_lower for m in known_models), f"Headline should mention a model name: {data['headline']}"

    # ── Step 5: Verify /api/summary/daily ──
    resp = await client.get("/api/summary/daily")
    assert resp.status_code == 200, f"Daily summary status {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "top_events" in data
    assert isinstance(data["top_events"], list)

    assert "stats" in data
    assert "population" in data["stats"]
    assert data["stats"]["population"] > 0, "Population should be > 0"

    assert "generated_at" in data
    assert isinstance(data["generated_at"], str)
    assert len(data["generated_at"]) > 0
