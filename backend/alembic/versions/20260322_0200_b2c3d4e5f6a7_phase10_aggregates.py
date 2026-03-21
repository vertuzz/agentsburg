"""phase10_aggregates

Add price_aggregates and economy_snapshots tables for Phase 10: Data Maintenance.

  price_aggregates   — OHLCV candlestick data downsampled from raw MarketTrades
  economy_snapshots  — Periodic macro-level economy statistics snapshots

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-22 02:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # price_aggregates — OHLCV candlesticks per good per time period
    # ------------------------------------------------------------------
    op.create_table(
        "price_aggregates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("good_slug", sa.String(64), nullable=False),
        sa.Column("period_type", sa.String(16), nullable=False),  # "hourly" | "daily"
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric(20, 2), nullable=False),
        sa.Column("high_price", sa.Numeric(20, 2), nullable=False),
        sa.Column("low_price", sa.Numeric(20, 2), nullable=False),
        sa.Column("close_price", sa.Numeric(20, 2), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_value", sa.Numeric(20, 2), nullable=False, server_default="0"),
        # Unique constraint: one candle per good per period type per period start
        sa.UniqueConstraint(
            "good_slug",
            "period_type",
            "period_start",
            name="uq_price_aggregate_good_period",
        ),
    )
    op.create_index(
        "ix_price_aggregates_good_slug",
        "price_aggregates",
        ["good_slug"],
    )
    op.create_index(
        "ix_price_aggregates_period_start",
        "price_aggregates",
        ["period_start"],
    )

    # ------------------------------------------------------------------
    # economy_snapshots — periodic macro stats
    # ------------------------------------------------------------------
    op.create_table(
        "economy_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("gdp", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("money_supply", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("population", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("employment_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gini_coefficient", sa.Float(), nullable=True),
        sa.Column("active_businesses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("npc_businesses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("government_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("avg_bread_price", sa.Numeric(20, 2), nullable=True),
    )
    op.create_index(
        "ix_economy_snapshots_timestamp",
        "economy_snapshots",
        ["timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_economy_snapshots_timestamp", table_name="economy_snapshots")
    op.drop_table("economy_snapshots")

    op.drop_index("ix_price_aggregates_period_start", table_name="price_aggregates")
    op.drop_index("ix_price_aggregates_good_slug", table_name="price_aggregates")
    op.drop_table("price_aggregates")
