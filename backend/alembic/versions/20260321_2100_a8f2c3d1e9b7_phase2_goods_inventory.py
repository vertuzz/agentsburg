"""phase2_goods_inventory

Add goods catalog and inventory_items tables for Phase 2.
  - goods: seeded from goods.yaml, tier-based catalog with gathering info
  - inventory_items: polymorphic owner inventory (agents and businesses)

Revision ID: a8f2c3d1e9b7
Revises: 3df3b2f0daab
Create Date: 2026-03-21 21:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a8f2c3d1e9b7"
down_revision: Union[str, None] = "3df3b2f0daab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------ goods
    op.create_table(
        "goods",
        sa.Column("slug", sa.String(64), primary_key=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("storage_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("base_value", sa.Numeric(20, 2), nullable=False, server_default="1"),
        sa.Column("gatherable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("gather_cooldown_seconds", sa.Integer(), nullable=True),
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
    op.create_index("ix_goods_slug", "goods", ["slug"])

    # --------------------------------------------------------- inventory_items
    op.create_table(
        "inventory_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("owner_type", sa.String(16), nullable=False),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("good_slug", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
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
        sa.UniqueConstraint("owner_type", "owner_id", "good_slug", name="uq_inventory_owner_good"),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_quantity_nonneg"),
        sa.CheckConstraint("owner_type IN ('agent', 'business')", name="ck_inventory_owner_type"),
    )
    op.create_index("ix_inventory_items_id", "inventory_items", ["id"])
    op.create_index("ix_inventory_items_owner_type", "inventory_items", ["owner_type"])
    op.create_index("ix_inventory_items_owner_id", "inventory_items", ["owner_id"])
    op.create_index("ix_inventory_items_good_slug", "inventory_items", ["good_slug"])


def downgrade() -> None:
    op.drop_table("inventory_items")
    op.drop_table("goods")
