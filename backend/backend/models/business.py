"""
Business models for Agent Economy.

Businesses are the core economic unit — agents register them, configure
production, set storefront prices, and hire workers. Businesses hold
inventory separately from agents.

Models:
  Business        — registered business entity
  StorefrontPrice — per-good prices for NPC sales
  JobPosting      — job listings that agents can apply to
  Employment      — active employment contract between agent and business
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin, UUIDMixin


class Business(UUIDMixin, TimestampMixin, Base):
    """
    A registered business in the economy.

    Businesses are owned by agents. They hold their own inventory separate
    from their owner's personal inventory. Businesses can be open (closed_at
    is None) or closed (closed_at is set).

    is_npc=True businesses are created by the bootstrap system and operate
    autonomously to seed the economy.
    """

    __tablename__ = "businesses"

    # The agent who registered this business
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Human-readable business name
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Type slug (e.g., "bakery", "smithy", "mill") — used for recipe bonuses
    type_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Zone where this business operates
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Inventory storage capacity in storage units
    storage_capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=500)

    # If True, this is an NPC-controlled business (from bootstrap)
    is_npc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Set when the business is closed. None = currently open.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Business name={self.name!r} type={self.type_slug!r} "
            f"owner={self.owner_id} closed={self.closed_at is not None}>"
        )

    def is_open(self) -> bool:
        return self.closed_at is None

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "type_slug": self.type_slug,
            "zone_id": str(self.zone_id),
            "owner_id": str(self.owner_id),
            "storage_capacity": self.storage_capacity,
            "is_npc": self.is_npc,
            "is_open": self.is_open(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


class StorefrontPrice(UUIDMixin, Base):
    """
    A price set by a business owner for selling a specific good at their storefront.

    NPC consumers will buy at these prices during the fast tick. Agents can also
    browse storefront prices via the marketplace tool.

    The unique constraint ensures each business has at most one price per good.
    """

    __tablename__ = "storefront_prices"

    __table_args__ = (
        UniqueConstraint("business_id", "good_slug", name="uq_storefront_business_good"),
    )

    # The business selling this good
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # The good being sold
    good_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Price per unit
    price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    def __repr__(self) -> str:
        return f"<StorefrontPrice business={self.business_id} good={self.good_slug!r} price={self.price}>"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "good_slug": self.good_slug,
            "price": float(self.price),
        }


class JobPosting(UUIDMixin, TimestampMixin, Base):
    """
    A job listing posted by a business owner.

    Agents browse job postings and apply for positions. The posting defines
    the product workers will produce and the wage paid per work() call.

    max_workers limits how many agents can hold this job simultaneously.
    is_active=False means the job is no longer accepting new applications
    (but existing employees may still hold the position until terminated).
    """

    __tablename__ = "job_postings"

    # The business offering this job
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Job title displayed to agents
    title: Mapped[str] = mapped_column(String(128), nullable=False)

    # Wage paid to the worker per work() call
    wage_per_work: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    # The product this worker will produce (must have a corresponding recipe)
    product_slug: Mapped[str] = mapped_column(String(64), nullable=False)

    # Maximum concurrent workers on this posting
    max_workers: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # False = closed to new applications
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return (
            f"<JobPosting title={self.title!r} product={self.product_slug!r} "
            f"wage={self.wage_per_work} active={self.is_active}>"
        )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "business_id": str(self.business_id),
            "title": self.title,
            "wage_per_work": float(self.wage_per_work),
            "product_slug": self.product_slug,
            "max_workers": self.max_workers,
            "is_active": self.is_active,
        }


class Employment(UUIDMixin, Base):
    """
    An active (or past) employment contract.

    Created when an agent applies for a job. Terminated when:
    - The employer fires the employee (business owner calls manage_employees with action=fire)
    - The employee quits (calls manage_employees with action=quit_job)
    - The business closes (close_business terminates all employment)
    - Bankruptcy liquidation (cancels all contracts)

    terminated_at=None means the employment is currently active.

    NPC worker employment uses is_npc=True with agent_id pointing to a
    placeholder NPC agent record.
    """

    __tablename__ = "employments"

    # The employed agent
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # The employing business
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # The job posting this employment is based on (may be null for legacy/NPC)
    job_posting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # Wage locked at time of hiring (doesn't change if posting updates)
    wage_per_work: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    # Product this worker is assigned to produce
    product_slug: Mapped[str] = mapped_column(String(64), nullable=False)

    # When the employment started
    hired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # When the employment ended (None = still active)
    terminated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Employment agent={self.agent_id} business={self.business_id} "
            f"product={self.product_slug!r} active={self.terminated_at is None}>"
        )

    def is_active(self) -> bool:
        return self.terminated_at is None

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "agent_id": str(self.agent_id),
            "business_id": str(self.business_id),
            "job_posting_id": str(self.job_posting_id) if self.job_posting_id else None,
            "wage_per_work": float(self.wage_per_work),
            "product_slug": self.product_slug,
            "hired_at": self.hired_at.isoformat(),
            "terminated_at": self.terminated_at.isoformat() if self.terminated_at else None,
            "is_active": self.is_active(),
        }
