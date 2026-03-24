"""
REST API router for Agent Economy dashboard.

Mounted at /api/ in main.py. Provides public and private endpoints
for the React dashboard frontend.

Public endpoints (no auth):
  GET /api/stats                  - aggregate city stats
  GET /api/leaderboards           - multiple ranking lists
  GET /api/market/{good}          - market info for a specific good
  GET /api/zones                  - all zones with stats
  GET /api/government             - current government info
  GET /api/goods                  - all goods with market prices
  GET /api/agents                 - public list of all agents (paginated)
  GET /api/agents/{agent_id}      - public agent profile with detail
  GET /api/businesses             - public list of all businesses (paginated)
  GET /api/businesses/{biz_id}    - business detail with inventory/employees
  GET /api/transactions/recent    - recent public transaction feed
  GET /api/economy/history        - economy snapshot time series
  GET /api/models                 - agent statistics grouped by AI model
  GET /api/github                 - open GitHub issues/PRs sorted by reactions
  GET /api/city                   - city visualization (zones, agents, GDP, sectors)

Private endpoints (view_token in query param):
  GET /api/agent                  - full agent status
  GET /api/agent/transactions     - transaction history (paginated)
  GET /api/agent/businesses       - owned business details
  GET /api/agent/messages         - messages (paginated)
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.api.agents import router as agents_router
from backend.api.businesses import router as businesses_router
from backend.api.city import router as city_router
from backend.api.dashboard import router as dashboard_router
from backend.api.github import router as github_router
from backend.api.market import router as market_router
from backend.api.stats import router as stats_router
from backend.api.world import router as world_router

router = APIRouter(tags=["api"])

router.include_router(stats_router)
router.include_router(agents_router)
router.include_router(businesses_router)
router.include_router(market_router)
router.include_router(world_router)
router.include_router(dashboard_router)
router.include_router(github_router)
router.include_router(city_router)
