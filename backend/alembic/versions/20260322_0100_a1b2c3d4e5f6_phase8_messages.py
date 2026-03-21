"""phase8_messages

Add messages table for Phase 8: Messaging & MCP Polish.

  messages — direct agent-to-agent messages with read tracking

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-03-22 01:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "from_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.String(1000), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Indexes for inbox queries (messages to a specific agent)
    op.create_index("ix_messages_to_agent_id", "messages", ["to_agent_id"])
    op.create_index("ix_messages_from_agent_id", "messages", ["from_agent_id"])
    op.create_index(
        "ix_messages_to_agent_read", "messages", ["to_agent_id", "read"]
    )


def downgrade() -> None:
    op.drop_index("ix_messages_to_agent_read", table_name="messages")
    op.drop_index("ix_messages_from_agent_id", table_name="messages")
    op.drop_index("ix_messages_to_agent_id", table_name="messages")
    op.drop_table("messages")
