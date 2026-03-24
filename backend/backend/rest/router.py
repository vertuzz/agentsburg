"""
REST API router for Agent Economy.

Thin combiner that includes all sub-routers so existing URL paths
remain the same. Import ``router`` and ``register_error_handlers``
from here — the public API is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.rest.catalog import meta_router
from backend.rest.common import register_error_handlers
from backend.rest.routes_core import core_router
from backend.rest.routes_economy import economy_router
from backend.rest.rules import rules_router

router = APIRouter()
router.include_router(core_router)
router.include_router(economy_router)
router.include_router(meta_router)
router.include_router(rules_router)

__all__ = ["register_error_handlers", "router"]
