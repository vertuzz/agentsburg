"""
Recipe model for Agent Economy.

Recipes define how goods are produced from inputs. They are seeded from
recipes.yaml at startup and do not change at runtime.

The bonus_business_type + bonus_cooldown_multiplier pair implements soft
specialization: any business can use any recipe, but matching business types
get a faster cooldown (e.g., a bakery makes bread faster than a smithy).
"""

from __future__ import annotations

from sqlalchemy import Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class Recipe(TimestampMixin, Base):
    """
    A production recipe defining inputs and outputs.

    Seeded from recipes.yaml on startup. The slug is the primary key.

    inputs_json is a list of {good_slug, quantity} dicts, e.g.:
        [{"good_slug": "wheat", "quantity": 3}]

    The bonus mechanism:
    - If the business type_slug matches bonus_business_type, multiply
      the base cooldown by bonus_cooldown_multiplier (typically 0.60-0.75).
    - This gives matching businesses 25-40% faster production.
    """

    __tablename__ = "recipes"

    # Unique recipe identifier — used throughout the system
    slug: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)

    # The good produced by this recipe
    output_good: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # How many units are produced per successful work() call
    output_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # List of {good_slug: str, quantity: int} inputs required
    inputs_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Base cooldown in seconds before this agent can work() again
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    # Business type that gets the efficiency bonus (None = no bonus type)
    bonus_business_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Cooldown multiplier applied when business type matches (< 1.0 = faster)
    bonus_cooldown_multiplier: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0
    )

    def __repr__(self) -> str:
        return (
            f"<Recipe slug={self.slug!r} output={self.output_good!r} "
            f"qty={self.output_quantity} cooldown={self.cooldown_seconds}s>"
        )

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "output_good": self.output_good,
            "output_quantity": self.output_quantity,
            "inputs": self.inputs_json,
            "cooldown_seconds": self.cooldown_seconds,
            "bonus_business_type": self.bonus_business_type,
            "bonus_cooldown_multiplier": self.bonus_cooldown_multiplier,
        }
