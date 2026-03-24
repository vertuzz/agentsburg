"""
SQLAlchemy model registry.

Import all models here so that:
1. Alembic's env.py can find them via metadata inspection
2. There's a single canonical import point for the model layer

Add new model modules here as they are created in later phases.
"""

# Phase 1: Core identity, zone, and transaction tables
from backend.models.agent import Agent

# Phase 10: Data Maintenance aggregates
from backend.models.aggregate import EconomySnapshot, PriceAggregate

# Phase 5: Banking
from backend.models.banking import BankAccount, CentralBank, Loan
from backend.models.base import Base, TimestampMixin, UUIDMixin

# Phase 3: Businesses, production, employment, recipes
from backend.models.business import Business, Employment, JobPosting, StorefrontPrice

# Phase 2: Goods catalog and inventory
from backend.models.good import Good

# Phase 6: Government, Taxes, Crime
from backend.models.government import GovernmentState, TaxRecord, Violation, Vote
from backend.models.inventory import InventoryItem

# Phase 4: Marketplace & Direct Trading
from backend.models.marketplace import MarketOrder, MarketTrade, Trade

# Phase 8: Messaging
from backend.models.message import Message
from backend.models.recipe import Recipe
from backend.models.transaction import Transaction
from backend.models.zone import Zone

__all__ = [
    "Agent",
    "BankAccount",
    "Base",
    "Business",
    "CentralBank",
    "EconomySnapshot",
    "Employment",
    "Good",
    "GovernmentState",
    "InventoryItem",
    "JobPosting",
    "Loan",
    "MarketOrder",
    "MarketTrade",
    "Message",
    "PriceAggregate",
    "Recipe",
    "StorefrontPrice",
    "TaxRecord",
    "TimestampMixin",
    "Trade",
    "Transaction",
    "UUIDMixin",
    "Violation",
    "Vote",
    "Zone",
]
