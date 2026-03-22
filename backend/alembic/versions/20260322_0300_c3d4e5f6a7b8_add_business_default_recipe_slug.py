"""add_business_default_recipe_slug

Add default_recipe_slug column to businesses table so configure_production()
can persist the chosen product for self-employed work() routing.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-22 03:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("default_recipe_slug", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("businesses", "default_recipe_slug")
