"""
InventoryItem model for Agent Economy.

Tracks the goods held by agents and businesses. Uses a polymorphic owner
pattern (owner_type + owner_id) rather than separate FK columns, allowing
businesses and agents to share the same inventory system.

The unique constraint on (owner_type, owner_id, good_slug) means there is
at most one row per owner-good combination — quantities are accumulated, not
stored as separate rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    import uuid


class InventoryItem(UUIDMixin, TimestampMixin, Base):
    """
    A single (owner, good) inventory slot with a quantity.

    owner_type: "agent" or "business"
    owner_id:   UUID of the owning agent or business (not a FK — polymorphic)
    good_slug:  Matches Good.slug from goods.yaml
    quantity:   Current amount held (always >= 0)
    """

    __tablename__ = "inventory_items"

    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "good_slug", name="uq_inventory_owner_good"),
        CheckConstraint("quantity >= 0", name="ck_inventory_quantity_nonneg"),
        CheckConstraint("owner_type IN ('agent', 'business')", name="ck_inventory_owner_type"),
    )

    # "agent" or "business"
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # UUID of the owning entity — not a FK to allow polymorphism
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Matches Good.slug (or goods.yaml slug)
    good_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Current quantity — never negative
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<InventoryItem owner={self.owner_type}:{self.owner_id} good={self.good_slug!r} qty={self.quantity}>"

    def to_dict(self) -> dict:
        return {
            "good_slug": self.good_slug,
            "quantity": self.quantity,
        }
