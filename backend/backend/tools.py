"""
Tool handler functions for Agent Economy.

Handlers have been split into domain-specific modules under backend/handlers/.
This module re-exports all handlers for backwards compatibility.
"""

from backend.handlers import (  # noqa: F401
    _handle_apply_job,
    _handle_bank,
    _handle_business_inventory,
    _handle_configure_production,
    _handle_events,
    _handle_gather,
    _handle_get_economy,
    _handle_get_status,
    _handle_inventory_discard,
    _handle_leaderboard,
    _handle_list_jobs,
    _handle_manage_employees,
    _handle_market_demand,
    _handle_marketplace_browse,
    _handle_marketplace_order,
    _handle_messages,
    _handle_my_orders,
    _handle_register_business,
    _handle_rent_housing,
    _handle_set_prices,
    _handle_signup,
    _handle_trade,
    _handle_vote,
    _handle_work,
)
