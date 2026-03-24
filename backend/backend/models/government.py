"""
Government models for Agent Economy.

Four tables:
  GovernmentState — singleton tracking the active government template and last election
  Vote            — one vote per agent (upserted on change)
  Violation       — criminal record entries for tax evasion / unlicensed business
  TaxRecord       — per-agent per-period tax accounting

The crime mechanic depends on the split between "marketplace" income (visible
to the tax authority) and "total_actual_income" (all income including direct
trades). Audits surface the discrepancy; fines are based on the tax that
*should* have been paid on the hidden income.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin, UUIDMixin


class GovernmentState(Base):
    """
    Singleton record for the active government template.

    Always id=1. Created during bootstrap with the default template.
    Updated weekly when tally_election() runs.
    """

    __tablename__ = "government_state"

    # Singleton — always id=1
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Slug of the currently active template (from government.yaml)
    current_template_slug: Mapped[str] = mapped_column(String(64), nullable=False, default="free_market")

    # When was the last election tallied
    last_election_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<GovernmentState template={self.current_template_slug!r}>"


class Vote(UUIDMixin, TimestampMixin, Base):
    """
    An agent's current vote for a government template.

    One row per agent (unique constraint on agent_id).
    Agents can change their vote anytime; this upserts.
    Votes are counted at the weekly tally; after tally they remain but
    are not carried forward (re-vote required each cycle).
    """

    __tablename__ = "votes"

    # FK to agents — one vote per agent enforced by unique constraint
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Which template this agent voted for
    template_slug: Mapped[str] = mapped_column(String(64), nullable=False)

    def __repr__(self) -> str:
        return f"<Vote agent={self.agent_id} template={self.template_slug!r}>"


class Violation(UUIDMixin, Base):
    """
    A law enforcement action against an agent.

    Types:
      tax_evasion          — audit found unreported income
      unlicensed_business  — operating without required license (future)

    Violations accumulate; escalating jail time is based on agent.violation_count.
    """

    __tablename__ = "violations"

    # FK to agents — indexed for efficient lookup
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # "tax_evasion" | "unlicensed_business"
    type: Mapped[str] = mapped_column(String(32), nullable=False)

    # How much income was hidden from the tax authority
    amount_evaded: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # The fine levied (evaded_tax * fine_multiplier)
    fine_amount: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # If jail was applied, the datetime until they are released
    jail_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # When the violation was detected
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Violation agent={self.agent_id} type={self.type!r} fine={self.fine_amount} jail_until={self.jail_until}>"
        )


class TaxRecord(UUIDMixin, Base):
    """
    Tax accounting record for one agent covering one billing period.

    The split between marketplace_income and total_actual_income is
    the core of the crime mechanic:

      marketplace_income   — income the tax authority can see
                             (marketplace order fills, storefront NPC sales)
      total_actual_income  — ALL income including direct trades
                             (from the full Transaction audit trail)
      discrepancy          — total_actual_income - marketplace_income
                             (the hidden income)

    Tax is assessed on marketplace_income * tax_rate.
    If audited and discrepancy > threshold:
      evaded_tax = discrepancy * tax_rate
      fine       = evaded_tax * fine_multiplier
    """

    __tablename__ = "tax_records"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The billing window covered by this record
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Income visible to the tax authority (marketplace, storefront NPC sales)
    marketplace_income: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # All income including direct trades (from Transaction table, server side)
    total_actual_income: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # Tax owed based on marketplace_income * tax_rate
    tax_owed: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # How much was actually collected (may be less if agent had insufficient funds)
    tax_paid: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # total_actual_income - marketplace_income (potential unreported income)
    discrepancy: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # Has this record been reviewed by the audit process?
    audited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return (
            f"<TaxRecord agent={self.agent_id} "
            f"marketplace={self.marketplace_income} "
            f"actual={self.total_actual_income} "
            f"discrepancy={self.discrepancy} "
            f"audited={self.audited}>"
        )
