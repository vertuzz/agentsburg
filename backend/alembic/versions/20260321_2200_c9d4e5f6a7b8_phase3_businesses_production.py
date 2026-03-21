"""phase3_businesses_production

Add Phase 3 tables: businesses, storefront_prices, job_postings, employments, recipes.

  - businesses: agent-owned business entities with zone and type
  - storefront_prices: per-good prices set by business owners for NPC sales
  - job_postings: job listings that agents can apply to
  - employments: active and historical employment contracts
  - recipes: production recipes seeded from recipes.yaml

Revision ID: c9d4e5f6a7b8
Revises: a8f2c3d1e9b7
Create Date: 2026-03-21 22:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c9d4e5f6a7b8"
down_revision: Union[str, None] = "a8f2c3d1e9b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----------------------------------------------------------- businesses
    op.create_table(
        "businesses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type_slug", sa.String(64), nullable=False),
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("zones.id", ondelete="SET NULL"),
            nullable=False,
        ),
        sa.Column("storage_capacity", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("is_npc", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_businesses_id", "businesses", ["id"])
    op.create_index("ix_businesses_owner_id", "businesses", ["owner_id"])
    op.create_index("ix_businesses_type_slug", "businesses", ["type_slug"])
    op.create_index("ix_businesses_zone_id", "businesses", ["zone_id"])

    # ------------------------------------------------------ storefront_prices
    op.create_table(
        "storefront_prices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("good_slug", sa.String(64), nullable=False),
        sa.Column("price", sa.Numeric(20, 2), nullable=False),
        # Unique: one price per good per business
        sa.UniqueConstraint("business_id", "good_slug", name="uq_storefront_business_good"),
    )
    op.create_index("ix_storefront_prices_id", "storefront_prices", ["id"])
    op.create_index("ix_storefront_prices_business_id", "storefront_prices", ["business_id"])
    op.create_index("ix_storefront_prices_good_slug", "storefront_prices", ["good_slug"])

    # ---------------------------------------------------------- job_postings
    op.create_table(
        "job_postings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("wage_per_work", sa.Numeric(20, 2), nullable=False),
        sa.Column("product_slug", sa.String(64), nullable=False),
        sa.Column("max_workers", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
    op.create_index("ix_job_postings_id", "job_postings", ["id"])
    op.create_index("ix_job_postings_business_id", "job_postings", ["business_id"])

    # ------------------------------------------------------------ employments
    op.create_table(
        "employments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "business_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_posting_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("wage_per_work", sa.Numeric(20, 2), nullable=False),
        sa.Column("product_slug", sa.String(64), nullable=False),
        sa.Column("hired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_employments_id", "employments", ["id"])
    op.create_index("ix_employments_agent_id", "employments", ["agent_id"])
    op.create_index("ix_employments_business_id", "employments", ["business_id"])
    op.create_index("ix_employments_job_posting_id", "employments", ["job_posting_id"])

    # --------------------------------------------------------------- recipes
    op.create_table(
        "recipes",
        sa.Column("slug", sa.String(64), primary_key=True, nullable=False),
        sa.Column("output_good", sa.String(64), nullable=False),
        sa.Column("output_quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("inputs_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("bonus_business_type", sa.String(64), nullable=True),
        sa.Column("bonus_cooldown_multiplier", sa.Float(), nullable=False, server_default="1.0"),
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
    op.create_index("ix_recipes_slug", "recipes", ["slug"])
    op.create_index("ix_recipes_output_good", "recipes", ["output_good"])


def downgrade() -> None:
    op.drop_table("recipes")
    op.drop_table("employments")
    op.drop_table("job_postings")
    op.drop_table("storefront_prices")
    op.drop_table("businesses")
