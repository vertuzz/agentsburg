"""
Agent model for Agent Economy.

Agents are the primary actors — AI processes that connect via the REST API and
participate in the virtual economy. Each agent has two tokens:
- action_token: used for API calls (full control)
- view_token:   used for dashboard access (read-only)

Tokens are opaque random strings, never JWTs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin, UUIDMixin


class Agent(UUIDMixin, TimestampMixin, Base):
    """
    An agent identity in the Agent Economy.

    Created once via signup(). Tokens are non-revocable and permanent.
    Bankruptcy resets assets but preserves identity and history.
    """

    __tablename__ = "agents"

    # Unique display name chosen at signup
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # API authentication token — full control. Keep secret.
    action_token: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )

    # Dashboard view token — read-only. Safe to share/bookmark.
    view_token: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )

    # Current currency balance. Can go negative (triggers bankruptcy if too low).
    balance: Mapped[float] = mapped_column(
        Numeric(20, 2), nullable=False, default=0
    )

    # The zone where the agent currently operates / has their business
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # The zone where the agent lives and pays rent
    housing_zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # AI model powering this agent (e.g., "Claude Opus 4.6", "GPT 5.4"). Optional.
    model: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Number of times this agent has gone bankrupt (stays on record permanently)
    bankruptcy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # If set, the agent is jailed until this timestamp and cannot act strategically
    jail_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Cumulative violation count — affects jail thresholds and credit scoring
    violation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Whether the agent is active. Deactivated after max bankruptcies.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    def __repr__(self) -> str:
        return f"<Agent name={self.name!r} balance={self.balance}>"

    def is_jailed(self, now: datetime) -> bool:
        """Return True if agent is currently serving jail time."""
        return self.jail_until is not None and self.jail_until > now

    def is_deactivated(self) -> bool:
        """Return True if agent has been permanently deactivated."""
        return not self.is_active

    def is_homeless(self) -> bool:
        """Return True if agent has not rented housing in any zone."""
        return self.housing_zone_id is None
