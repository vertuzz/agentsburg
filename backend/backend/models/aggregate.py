"""
Data aggregate models for Phase 10: Data Maintenance.

PriceAggregate — OHLCV candlestick data for marketplace prices,
downsampled from raw MarketTrade records.

EconomySnapshot — periodic macro-level economy stats snapshot.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class PriceAggregate(Base):
    """
    OHLCV candlestick aggregate for a good over a time period.

    Raw MarketTrade records are downsampled into these aggregates:
    - Hourly aggregates created from raw trades older than 24h
    - Daily aggregates created from hourly aggregates older than 30 days

    Unique on (good_slug, period_type, period_start) — one candle per
    good per period bucket.
    """

    __tablename__ = "price_aggregates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    good_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "hourly" or "daily"
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # OHLCV data
    open_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    high_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    low_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    close_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_value: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "good_slug",
            "period_type",
            "period_start",
            name="uq_price_aggregate_good_period",
        ),
        Index("ix_price_aggregates_period_start", "period_start"),
    )


class EconomySnapshot(Base):
    """
    Periodic snapshot of macro-level economy statistics.

    Taken by the maintenance job every 6 hours. Provides a time series
    of high-level economic health indicators for the dashboard.
    """

    __tablename__ = "economy_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    # Macroeconomic indicators
    gdp: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)
    money_supply: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)
    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    employment_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gini_coefficient: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Business activity
    active_businesses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    npc_businesses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Government & prices
    government_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    avg_bread_price: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)
