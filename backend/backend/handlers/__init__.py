"""
Handler modules for Agent Economy tools.

Each module contains handlers for a specific domain area.
Import all handlers from here for backwards compatibility.
"""

from backend.handlers.agents import (
    _handle_get_status,
    _handle_rent_housing,
    _handle_signup,
)
from backend.handlers.banking import _handle_bank
from backend.handlers.businesses import (
    _handle_configure_production,
    _handle_register_business,
    _handle_set_prices,
)
from backend.handlers.economy import _handle_get_economy
from backend.handlers.events import _handle_events
from backend.handlers.employment import (
    _handle_apply_job,
    _handle_list_jobs,
    _handle_manage_employees,
    _handle_work,
)
from backend.handlers.gathering import _handle_gather, _handle_inventory_discard
from backend.handlers.government import _handle_messages, _handle_vote
from backend.handlers.inventory import _handle_business_inventory
from backend.handlers.marketplace import (
    _handle_leaderboard,
    _handle_marketplace_browse,
    _handle_marketplace_order,
    _handle_my_orders,
)
from backend.handlers.trading import _handle_trade

__all__ = [
    "_handle_signup",
    "_handle_get_status",
    "_handle_rent_housing",
    "_handle_gather",
    "_handle_inventory_discard",
    "_handle_register_business",
    "_handle_configure_production",
    "_handle_set_prices",
    "_handle_manage_employees",
    "_handle_list_jobs",
    "_handle_apply_job",
    "_handle_work",
    "_handle_business_inventory",
    "_handle_marketplace_order",
    "_handle_marketplace_browse",
    "_handle_my_orders",
    "_handle_leaderboard",
    "_handle_trade",
    "_handle_bank",
    "_handle_vote",
    "_handle_get_economy",
    "_handle_messages",
    "_handle_events",
]
