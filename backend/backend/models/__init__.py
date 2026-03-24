"""
SQLAlchemy model registry.

Import all models here so that:
1. Alembic's env.py can find them via metadata inspection
2. There's a single canonical import point for the model layer

Add new model modules here as they are created in later phases.
"""

# Phase 1: Core identity, zone, and transaction tables
from backend.models.agent import Agent  # noqa: F401

# Phase 10: Data Maintenance aggregates
from backend.models.aggregate import EconomySnapshot, PriceAggregate  # noqa: F401

# Phase 5: Banking
from backend.models.banking import BankAccount, CentralBank, Loan  # noqa: F401
from backend.models.base import Base, TimestampMixin, UUIDMixin  # noqa: F401

# Phase 3: Businesses, production, employment, recipes
from backend.models.business import Business, Employment, JobPosting, StorefrontPrice  # noqa: F401

# Phase 2: Goods catalog and inventory
from backend.models.good import Good  # noqa: F401

# Phase 6: Government, Taxes, Crime
from backend.models.government import GovernmentState, TaxRecord, Violation, Vote  # noqa: F401
from backend.models.inventory import InventoryItem  # noqa: F401

# Phase 4: Marketplace & Direct Trading
from backend.models.marketplace import MarketOrder, MarketTrade, Trade  # noqa: F401

# Phase 8: Messaging
from backend.models.message import Message  # noqa: F401
from backend.models.recipe import Recipe  # noqa: F401
from backend.models.transaction import Transaction  # noqa: F401
from backend.models.zone import Zone  # noqa: F401

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    "Agent",
    "Zone",
    "Transaction",
    "Good",
    "InventoryItem",
    "Business",
    "StorefrontPrice",
    "JobPosting",
    "Employment",
    "Recipe",
    "MarketOrder",
    "MarketTrade",
    "Trade",
    "BankAccount",
    "Loan",
    "CentralBank",
    "GovernmentState",
    "Vote",
    "Violation",
    "TaxRecord",
    "Message",
    "PriceAggregate",
    "EconomySnapshot",
]
