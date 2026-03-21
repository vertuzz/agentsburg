"""phase5_banking

Add banking tables for Phase 5: Central Bank, Bank Accounts, and Loans.

Tables:
  central_bank    — singleton record tracking total reserves and outstanding loans
  bank_accounts   — one deposit account per agent (separate from wallet balance)
  loans           — installment loans with 24 hourly payments

Revision ID: e5f6a7b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-03-21 23:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------ central_bank
    # Singleton table (always id=1). Tracks total reserves and outstanding loans.
    # No TimestampMixin — just a singleton config record.
    op.create_table(
        "central_bank",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        # Actual money held in reserve
        sa.Column(
            "reserves",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        # Total outstanding loan principal across all active loans
        sa.Column(
            "total_loaned",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        # Constraints
        sa.CheckConstraint("reserves >= 0", name="ck_central_bank_reserves_nonneg"),
        sa.CheckConstraint("total_loaned >= 0", name="ck_central_bank_loaned_nonneg"),
        sa.CheckConstraint("id = 1", name="ck_central_bank_singleton"),
    )

    # --------------------------------------------------------- bank_accounts
    # One deposit account per agent (unique constraint on agent_id).
    # Balance here is SEPARATE from agent.balance (wallet).
    op.create_table(
        "bank_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,  # One account per agent
        ),
        # Deposit balance — separate from agent wallet
        sa.Column(
            "balance",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        # TimestampMixin columns
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
        sa.CheckConstraint("balance >= 0", name="ck_bank_accounts_balance_nonneg"),
    )
    op.create_index("ix_bank_accounts_id", "bank_accounts", ["id"])
    op.create_index("ix_bank_accounts_agent_id", "bank_accounts", ["agent_id"])

    # ------------------------------------------------------------------ loans
    # Installment loans from the central bank to agents.
    # Each loan is repaid in 24 equal hourly installments.
    op.create_table(
        "loans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Original principal lent
        sa.Column("principal", sa.Numeric(20, 2), nullable=False),
        # Outstanding balance (decrements with each payment)
        sa.Column("remaining_balance", sa.Numeric(20, 2), nullable=False),
        # Annual interest rate (stored as fraction, e.g., 0.05 = 5%)
        sa.Column("interest_rate", sa.Float(), nullable=False),
        # Fixed installment amount = (principal * (1 + interest_rate)) / 24
        sa.Column("installment_amount", sa.Numeric(20, 2), nullable=False),
        # How many installments remain (starts at 24, counts to 0)
        sa.Column(
            "installments_remaining",
            sa.Integer(),
            nullable=False,
            server_default="24",
        ),
        # When the next installment is due
        sa.Column("next_payment_at", sa.DateTime(timezone=True), nullable=False),
        # active | paid_off | defaulted
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
        # TimestampMixin columns
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
        sa.CheckConstraint("principal > 0", name="ck_loans_principal_positive"),
        sa.CheckConstraint("remaining_balance >= 0", name="ck_loans_remaining_nonneg"),
        sa.CheckConstraint("installment_amount > 0", name="ck_loans_installment_positive"),
        sa.CheckConstraint("installments_remaining >= 0", name="ck_loans_remaining_nonneg_count"),
        sa.CheckConstraint(
            "status IN ('active', 'paid_off', 'defaulted')",
            name="ck_loans_status",
        ),
        sa.CheckConstraint("interest_rate >= 0", name="ck_loans_rate_nonneg"),
    )
    op.create_index("ix_loans_id", "loans", ["id"])
    op.create_index("ix_loans_agent_id", "loans", ["agent_id"])
    op.create_index("ix_loans_status", "loans", ["status"])
    # Composite index for the slow tick payment query
    op.create_index(
        "ix_loans_status_next_payment",
        "loans",
        ["status", "next_payment_at"],
    )


def downgrade() -> None:
    op.drop_table("loans")
    op.drop_table("bank_accounts")
    op.drop_table("central_bank")
