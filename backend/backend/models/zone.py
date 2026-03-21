"""
Zone model for Agent Economy.

Zones are city districts with distinct economic properties. They are seeded
from zones.yaml at startup and do not change at runtime. Agents choose zones
to live in (housing) and open businesses in.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Float, JSON, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin, UUIDMixin


class Zone(UUIDMixin, TimestampMixin, Base):
    """
    A city district with distinct economic properties.

    Zones are fixed from YAML config — they are reference data, not player-
    created content. Seeded by economy/bootstrap.py on startup.
    """

    __tablename__ = "zones"

    # Human-readable identifier used throughout the system
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # Display name shown to agents
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Housing cost in currency units per slow tick (hourly)
    rent_cost: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Scales NPC foot traffic and demand in this zone
    foot_traffic: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # Additional demand scaling multiplier
    demand_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # JSON list of allowed business type slugs; null means all types allowed
    allowed_business_types: Mapped[list | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<Zone slug={self.slug!r} name={self.name!r}>"

    def to_dict(self) -> dict:
        """Return a public-facing dict representation of this zone."""
        return {
            "id": str(self.id),
            "slug": self.slug,
            "name": self.name,
            "rent_cost": float(self.rent_cost),
            "foot_traffic": self.foot_traffic,
            "demand_multiplier": self.demand_multiplier,
            "allowed_business_types": self.allowed_business_types,
        }
