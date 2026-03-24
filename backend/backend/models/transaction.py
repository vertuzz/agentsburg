"""
Transaction model for Agent Economy.

Every currency movement in the economy is recorded here. This table is the
master audit trail — it captures everything, including direct trades that
the tax authority cannot see from marketplace data alone.

Transaction types map to different economic events:
  wage                  — employer pays worker for work() call
  rent                  — zone rent deducted from agent
  food                  — survival food cost deducted
  tax                   — government tax collection
  fine                  — crime penalty payment
  trade                 — direct agent-to-agent trade (NOT visible to tax system)
  marketplace           — marketplace order fill
  storefront            — NPC consumer purchase from a business storefront
  loan_payment          — loan installment paid to bank
  deposit_interest      — interest earned on bank deposit
  loan_disbursement     — bank pays loan principal to agent
  deposit               — agent deposits money into bank account (wallet → account)
  withdrawal            — agent withdraws money from bank account (account → wallet)
  gathering             — agent sells gathered resources
  business_reg          — business registration fee
  bankruptcy_liquidation — asset sale during bankruptcy
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin, UUIDMixin

# All valid transaction type strings — used for validation and documentation
TRANSACTION_TYPES = frozenset(
    {
        "wage",
        "rent",
        "food",
        "tax",
        "fine",
        "trade",
        "marketplace",
        "storefront",  # NPC consumer purchases from business storefronts
        "loan_payment",
        "deposit_interest",
        "loan_disbursement",
        "deposit",  # agent deposits into bank account
        "withdrawal",  # agent withdraws from bank account
        "gathering",
        "business_reg",
        "bankruptcy_liquidation",
    }
)


class Transaction(UUIDMixin, TimestampMixin, Base):
    """
    A single currency transfer event.

    Both from_agent_id and to_agent_id can be null (e.g., money entering
    the economy via bank disbursement has no from_agent, money leaving via
    tax goes to bank reserves with no to_agent).
    """

    __tablename__ = "transactions"

    # What kind of economic event generated this transaction
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Source agent (null = bank/system origin)
    from_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    # Destination agent (null = bank/system destination)
    to_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    # Amount transferred (always positive; direction encoded by from/to)
    amount: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)

    # Arbitrary extra context (good slug, business id, order id, etc.)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<Transaction type={self.type!r} amount={self.amount} from={self.from_agent_id} to={self.to_agent_id}>"
