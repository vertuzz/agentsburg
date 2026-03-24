"""
Marketplace models for Agent Economy.

Three tables cover the marketplace and direct trading systems:

  MarketOrder  — a limit order placed on the order book (buy or sell)
  MarketTrade  — an executed match between a buy and sell order
  Trade        — a direct agent-to-agent barter proposal with escrow

Design decisions:
  - Buy orders lock FUNDS at placement time (deducted from balance)
  - Sell orders lock GOODS at placement time (removed from inventory)
  - Matching executes at SELL price; buyers may pay less than their limit
  - Excess locked buy funds are refunded when matching occurs at a lower price
  - Direct trades use type="trade" transactions — NOT visible to the tax system
    (this is the intentional tax-evasion pathway described in the spec)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin, UUIDMixin


class MarketOrder(UUIDMixin, TimestampMixin, Base):
    """
    A limit order on the order book for a specific good.

    Statuses:
        open             — active, can be matched
        partially_filled — some quantity filled, remainder still open
        filled           — fully executed
        cancelled        — cancelled by agent or bankruptcy

    When placed:
        sell side → goods removed from inventory into the "order lock"
        buy side  → funds deducted from balance into the "fund lock"
    """

    __tablename__ = "market_orders"

    # Composite index for the matching engine (side + good + price priority)
    # Simple indexes for agent_id, good_slug, status are defined via index=True
    # on the mapped_columns to avoid duplicate index definitions.
    __table_args__ = (Index("ix_market_orders_side_good_price", "side", "good_slug", "price"),)

    # The agent who placed the order
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # The good being bought or sold
    good_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # "buy" or "sell"
    side: Mapped[str] = mapped_column(String(4), nullable=False)

    # Total quantity in the order
    quantity_total: Mapped[int] = mapped_column(Integer, nullable=False)

    # How much has already been filled
    quantity_filled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Limit price per unit (Numeric for exact decimal arithmetic)
    price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    # open | partially_filled | filled | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)

    def __repr__(self) -> str:
        remaining = self.quantity_total - self.quantity_filled
        return (
            f"<MarketOrder {self.side} {remaining}/{self.quantity_total}x "
            f"{self.good_slug!r} @ {self.price} [{self.status}]>"
        )

    @property
    def quantity_remaining(self) -> int:
        return self.quantity_total - self.quantity_filled

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "agent_id": str(self.agent_id),
            "good_slug": self.good_slug,
            "side": self.side,
            "quantity_total": self.quantity_total,
            "quantity_filled": self.quantity_filled,
            "quantity_remaining": self.quantity_remaining,
            "price": float(self.price),
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class MarketTrade(Base):
    """
    A recorded execution of a matched buy/sell order pair.

    The execution price is always the SELL order's price (seller gets exactly
    what they asked for; buyer may get a better deal than their limit).
    """

    __tablename__ = "market_trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    buy_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    sell_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Denormalized for easy querying
    good_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Quantity transferred in this execution
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Execution price (= seller's ask price)
    price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    # When the match occurred
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<MarketTrade {self.quantity}x {self.good_slug!r} @ {self.price} at {self.executed_at}>"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "buy_order_id": str(self.buy_order_id),
            "sell_order_id": str(self.sell_order_id),
            "good_slug": self.good_slug,
            "quantity": self.quantity,
            "price": float(self.price),
            "executed_at": self.executed_at.isoformat(),
        }


class Trade(UUIDMixin, TimestampMixin, Base):
    """
    A direct agent-to-agent barter proposal with escrow.

    One agent proposes terms (offer_items, request_items, money adjustments).
    The target can accept or reject. Items are locked in escrow during the
    pending period and returned automatically if not responded to within
    expires_at.

    IMPORTANT: Accepted trades create Transaction records with type="trade",
    which is intentionally NOT visible to the tax authority. This is the
    designed tax-evasion pathway — agents who want to avoid marketplace taxes
    can use direct trades, but risk audits if detected.

    Statuses:
        pending   — waiting for target's response (items in escrow)
        accepted  — trade completed, items exchanged
        rejected  — target declined, escrow returned to proposer
        cancelled — proposer cancelled before target responded
        expired   — timeout elapsed, escrow returned to proposer
    """

    __tablename__ = "trades"

    # Agent who created the proposal
    proposer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Agent who must respond
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # What the proposer is offering: [{"good_slug": str, "quantity": int}, ...]
    offer_items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # What the proposer is requesting from the target: [{"good_slug": str, "quantity": int}, ...]
    request_items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Currency the proposer offers (on top of items)
    offer_money: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # Currency the proposer requests from the target (on top of items)
    request_money: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False, default=0)

    # pending | accepted | rejected | cancelled | expired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)

    # True while items/money are locked in escrow (set False after return/exchange)
    escrow_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # When escrow auto-expires and items are returned
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<Trade proposer={self.proposer_id} target={self.target_id} status={self.status!r}>"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "proposer_id": str(self.proposer_id),
            "target_id": str(self.target_id),
            "offer_items": self.offer_items,
            "request_items": self.request_items,
            "offer_money": float(self.offer_money),
            "request_money": float(self.request_money),
            "status": self.status,
            "escrow_locked": self.escrow_locked,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }
