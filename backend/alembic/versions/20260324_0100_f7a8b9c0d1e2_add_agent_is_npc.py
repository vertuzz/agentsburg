"""add_agent_is_npc

Add is_npc column to agents table. NPC agents are controlled by the
economy engine, not by real players. Backfills existing NPC agents
identified by name prefix.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-24 01:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("is_npc", sa.Boolean, nullable=False, server_default="false"),
    )
    # Backfill: mark existing NPC agents
    op.execute("UPDATE agents SET is_npc = true WHERE name LIKE 'NPC_%'")


def downgrade() -> None:
    op.drop_column("agents", "is_npc")
