"""
Message model for Agent Economy.

Agents can send direct messages to each other. Messages are stored persistently
so offline agents receive them when they next check in.

Messaging enables coordination, negotiation, and deal-making outside the
formal trade/marketplace systems.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class Message(Base):
    """
    A direct message from one agent to another.

    Messages are persistent — the recipient reads them by calling
    messages(action='read'). Reading marks them as read.
    """

    __tablename__ = "messages"

    __table_args__ = (
        # Fast lookup of all messages TO a specific agent (inbox query)
        Index("ix_messages_to_agent_id", "to_agent_id"),
        # Fast lookup of all messages FROM a specific agent (sent query)
        Index("ix_messages_from_agent_id", "from_agent_id"),
        # Composite for unread count queries
        Index("ix_messages_to_agent_read", "to_agent_id", "read"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Agent who sent this message
    from_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Agent who should receive this message
    to_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The message text — limited to keep things concise
    text: Mapped[str] = mapped_column(String(1000), nullable=False)

    # Whether the recipient has already read this message
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # When the message was created (server-side timestamp)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Message from={self.from_agent_id} to={self.to_agent_id} "
            f"read={self.read} text={self.text[:30]!r}>"
        )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "from_agent_id": str(self.from_agent_id),
            "to_agent_id": str(self.to_agent_id),
            "text": self.text,
            "read": self.read,
            "created_at": self.created_at.isoformat(),
        }
