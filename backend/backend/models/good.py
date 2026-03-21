"""
Good model for Agent Economy.

Goods are the items that agents gather, produce, and trade. They are seeded
from goods.yaml at startup and do not change at runtime.

Tier 1 goods are gatherable (free extraction with cooldown).
Tier 2/3 goods require production recipes.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class Good(TimestampMixin, Base):
    """
    A tradeable good in the economy.

    Seeded from goods.yaml on startup. The slug is the primary key and
    is used throughout the system as the canonical good identifier.
    """

    __tablename__ = "goods"

    # Unique slug is the primary key — no UUID needed for reference data
    slug: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)

    # Human-readable name shown to agents
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Production tier: 1=raw/gatherable, 2=intermediate, 3=finished
    tier: Mapped[int] = mapped_column(Integer, nullable=False)

    # Storage units this good occupies in inventory
    storage_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Estimated fair market value — used for bankruptcy liquidation price
    base_value: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=1)

    # Whether this good can be gathered for free (tier 1 only)
    gatherable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Per-agent cooldown in seconds between gather calls for this good
    # Only relevant when gatherable=True
    gather_cooldown_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<Good slug={self.slug!r} tier={self.tier} gatherable={self.gatherable}>"

    def to_dict(self) -> dict:
        """Public-facing dict for MCP responses."""
        return {
            "slug": self.slug,
            "name": self.name,
            "tier": self.tier,
            "storage_size": self.storage_size,
            "base_value": float(self.base_value),
            "gatherable": self.gatherable,
            "gather_cooldown_seconds": self.gather_cooldown_seconds,
        }
