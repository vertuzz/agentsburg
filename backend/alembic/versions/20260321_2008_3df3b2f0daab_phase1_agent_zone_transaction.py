"""phase1_agent_zone_transaction

Create the initial three tables for Phase 1:
  - zones:        city district reference data (seeded from YAML)
  - agents:       agent identity and account state
  - transactions: master audit trail for all currency movements

Revision ID: 3df3b2f0daab
Revises:
Create Date: 2026-03-21 20:08:52.469733+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "3df3b2f0daab"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ zones
    op.create_table(
        "zones",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("rent_cost", sa.Numeric(10, 2), nullable=False),
        sa.Column("foot_traffic", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("demand_multiplier", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("allowed_business_types", sa.JSON(), nullable=True),
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
    )
    op.create_index("ix_zones_id", "zones", ["id"])
    op.create_index("ix_zones_slug", "zones", ["slug"], unique=True)

    # ----------------------------------------------------------------- agents
    op.create_table(
        "agents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("action_token", sa.String(128), nullable=False),
        sa.Column("view_token", sa.String(128), nullable=False),
        sa.Column("balance", sa.Numeric(20, 2), nullable=False, server_default="0"),
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("zones.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "housing_zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("zones.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("bankruptcy_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jail_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("violation_count", sa.Integer(), nullable=False, server_default="0"),
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
    )
    op.create_index("ix_agents_id", "agents", ["id"])
    op.create_index("ix_agents_name", "agents", ["name"], unique=True)
    op.create_index("ix_agents_action_token", "agents", ["action_token"], unique=True)
    op.create_index("ix_agents_view_token", "agents", ["view_token"], unique=True)
    op.create_index("ix_agents_model", "agents", ["model"])

    # ------------------------------------------------------------ transactions
    op.create_table(
        "transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column(
            "from_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        # TimestampMixin (only created_at is meaningful for transactions)
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
    )
    op.create_index("ix_transactions_id", "transactions", ["id"])
    op.create_index("ix_transactions_type", "transactions", ["type"])
    op.create_index("ix_transactions_from_agent_id", "transactions", ["from_agent_id"])
    op.create_index("ix_transactions_to_agent_id", "transactions", ["to_agent_id"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("agents")
    op.drop_table("zones")
