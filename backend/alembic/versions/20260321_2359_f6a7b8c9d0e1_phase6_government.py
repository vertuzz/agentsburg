"""phase6_government

Add government, tax, and crime tables for Phase 6:
  government_state — singleton tracking active template + last election
  votes            — one vote per agent (upserted on change)
  violations       — criminal record entries (tax evasion, etc.)
  tax_records      — per-agent per-period tax accounting

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-21 23:59:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------- government_state
    op.create_table(
        "government_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "current_template_slug",
            sa.String(64),
            nullable=False,
            server_default="free_market",
        ),
        sa.Column(
            "last_election_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # -------------------------------------------------------------- votes
    op.create_table(
        "votes",
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
            unique=True,
        ),
        sa.Column("template_slug", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_votes_agent_id", "votes", ["agent_id"])

    # -------------------------------------------------------------- violations
    op.create_table(
        "violations",
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
        # "tax_evasion" | "unlicensed_business"
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column(
            "amount_evaded",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "fine_amount",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("jail_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_violations_agent_id", "violations", ["agent_id"])

    # -------------------------------------------------------------- tax_records
    op.create_table(
        "tax_records",
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
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "marketplace_income",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_actual_income",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tax_owed",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tax_paid",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "discrepancy",
            sa.Numeric(20, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "audited",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_tax_records_agent_id", "tax_records", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_tax_records_agent_id", table_name="tax_records")
    op.drop_table("tax_records")

    op.drop_index("ix_violations_agent_id", table_name="violations")
    op.drop_table("violations")

    op.drop_index("ix_votes_agent_id", table_name="votes")
    op.drop_table("votes")

    op.drop_table("government_state")
