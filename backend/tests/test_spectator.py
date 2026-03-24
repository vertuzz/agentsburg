"""
Spectator Experience Test

End-to-end simulation tests for the spectator features:
- Global event feed with narrative text
- Activity pulse
- Drama and category filtering

All tests go through the real REST API via httpx ASGI transport.
The ONLY mock is MockClock.
"""

from __future__ import annotations

import pytest

from tests.spectator.test_city import run_city_test
from tests.spectator.test_commentary_summary import run_commentary_summary_test
from tests.spectator.test_conflicts import run_conflicts_test
from tests.spectator.test_event_feed import run_spectator_feed_test
from tests.spectator.test_strategy_badges import run_strategy_badges_test


@pytest.mark.asyncio
async def test_spectator_event_feed(client, app, clock, run_tick, redis_client):
    """
    Spectator event feed simulation: agents generate economic activity,
    ticks produce narrative events, feed API serves them with filters.
    """
    await run_spectator_feed_test(client, app, clock, run_tick, redis_client)


@pytest.mark.asyncio
async def test_spectator_strategy_badges(client, app, clock, run_tick, redis_client):
    """
    Spectator strategy & badges: agents with different profiles get
    correct strategy classifications, traits, and achievement badges.
    """
    await run_strategy_badges_test(client, app, clock, run_tick, redis_client)


@pytest.mark.asyncio
async def test_spectator_commentary_summary(client, app, clock, run_tick, redis_client):
    """
    Spectator commentary & daily summary: model comparison headlines
    and economy summary with top events, market movers, and stats.
    """
    await run_commentary_summary_test(client, app, clock, run_tick, redis_client)


@pytest.mark.asyncio
async def test_spectator_conflicts(client, app, clock, run_tick, redis_client):
    """
    Spectator conflicts: detect price wars, market cornering, and election battles.
    """
    await run_conflicts_test(client, app, clock, run_tick, redis_client)


@pytest.mark.asyncio
async def test_city_visualization(client, app, clock, run_tick, redis_client):
    """
    City visualization: zones with GDP, agent activities, sector breakdown,
    figurine scaling, and Redis caching.
    """
    await run_city_test(client, app, clock, run_tick, redis_client)
