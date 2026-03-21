"""phase4_marketplace_trading

Add marketplace order book and direct trading tables for Phase 4:
  - market_orders:  limit order book entries (buy/sell) with fund/good locking
  - market_trades:  executed match records (price history)
  - trades:         direct agent-to-agent barter proposals with escrow

Revision ID: d1e2f3a4b5c6
Revises: c9d4e5f6a7b8
Create Date: 2026-03-21 22:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c9d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------- market_orders
    op.create_table(
        "market_orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        # FK to agents (not enforced with ondelete to survive agent deletion gracefully)
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("good_slug", sa.String(64), nullable=False),
        # "buy" or "sell"
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity_total", sa.Integer(), nullable=False),
        sa.Column("quantity_filled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price", sa.Numeric(20, 2), nullable=False),
        # open | partially_filled | filled | cancelled
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        # TimestampMixin
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Constraints
        sa.CheckConstraint("side IN ('buy', 'sell')", name="ck_market_orders_side"),
        sa.CheckConstraint(
            "status IN ('open', 'partially_filled', 'filled', 'cancelled')",
            name="ck_market_orders_status",
        ),
        sa.CheckConstraint("quantity_total > 0", name="ck_market_orders_qty_positive"),
        sa.CheckConstraint("quantity_filled >= 0", name="ck_market_orders_filled_nonneg"),
        sa.CheckConstraint(
            "quantity_filled <= quantity_total",
            name="ck_market_orders_filled_lte_total",
        ),
        sa.CheckConstraint("price >= 0", name="ck_market_orders_price_nonneg"),
    )
    op.create_index("ix_market_orders_id", "market_orders", ["id"])
    op.create_index("ix_market_orders_agent_id", "market_orders", ["agent_id"])
    op.create_index("ix_market_orders_good_slug", "market_orders", ["good_slug"])
    op.create_index("ix_market_orders_status", "market_orders", ["status"])
    # Composite index for matching engine: side + good + price
    op.create_index(
        "ix_market_orders_side_good_price",
        "market_orders",
        ["side", "good_slug", "price"],
    )

    # -------------------------------------------------------------- market_trades
    op.create_table(
        "market_trades",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "buy_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("market_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sell_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("market_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("good_slug", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(20, 2), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        # Constraints
        sa.CheckConstraint("quantity > 0", name="ck_market_trades_qty_positive"),
        sa.CheckConstraint("price >= 0", name="ck_market_trades_price_nonneg"),
    )
    op.create_index("ix_market_trades_id", "market_trades", ["id"])
    op.create_index("ix_market_trades_buy_order_id", "market_trades", ["buy_order_id"])
    op.create_index("ix_market_trades_sell_order_id", "market_trades", ["sell_order_id"])
    op.create_index("ix_market_trades_good_slug", "market_trades", ["good_slug"])
    op.create_index("ix_market_trades_executed_at", "market_trades", ["executed_at"])

    # -------------------------------------------------------------------- trades
    op.create_table(
        "trades",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "proposer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # JSON arrays: [{"good_slug": str, "quantity": int}, ...]
        sa.Column("offer_items", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("request_items", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("offer_money", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column("request_money", sa.Numeric(20, 2), nullable=False, server_default="0"),
        # pending | accepted | rejected | cancelled | expired
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        # True while items/money are locked in escrow
        sa.Column("escrow_locked", sa.Boolean(), nullable=False, server_default="true"),
        # When the escrow auto-expires
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # TimestampMixin
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Constraints
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'cancelled', 'expired')",
            name="ck_trades_status",
        ),
        sa.CheckConstraint("offer_money >= 0", name="ck_trades_offer_money_nonneg"),
        sa.CheckConstraint("request_money >= 0", name="ck_trades_request_money_nonneg"),
        sa.CheckConstraint("proposer_id != target_id", name="ck_trades_no_self_trade"),
    )
    op.create_index("ix_trades_id", "trades", ["id"])
    op.create_index("ix_trades_proposer_id", "trades", ["proposer_id"])
    op.create_index("ix_trades_target_id", "trades", ["target_id"])
    op.create_index("ix_trades_status", "trades", ["status"])
    # Index for fast tick expiry query
    op.create_index("ix_trades_expires_at", "trades", ["expires_at"])


def downgrade() -> None:
    op.drop_table("trades")
    op.drop_table("market_trades")
    op.drop_table("market_orders")
