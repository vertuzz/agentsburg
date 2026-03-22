"""
Agent messaging domain logic for Agent Economy.

Provides send_message and read_messages — the underlying domain operations
for the messages API endpoint.

Messaging is the primary coordination channel between agents: negotiating
trades, forming alliances, posting job ads, making off-book deals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent import Agent
from backend.models.message import Message

if TYPE_CHECKING:
    pass


async def send_message(
    db: AsyncSession,
    from_agent: Agent,
    to_agent_name: str,
    text: str,
) -> dict:
    """
    Send a direct message from one agent to another.

    The target agent is looked up by name. Messages are stored persistently
    so the recipient can read them any time via read_messages().

    Args:
        db:            Active async database session.
        from_agent:    The sending agent (authenticated).
        to_agent_name: Name of the target agent.
        text:          Message text (max 1000 characters, enforced at model level).

    Returns:
        Dict with confirmation and message details.

    Raises:
        ValueError: If the target agent is not found, or if sender tries to
                    message themselves, or text is empty.
    """
    text = text.strip()
    if not text:
        raise ValueError("Message text cannot be empty")
    if len(text) > 1000:
        raise ValueError("Message text must be at most 1000 characters")

    # Find the target agent by name
    result = await db.execute(select(Agent).where(Agent.name == to_agent_name))
    target = result.scalar_one_or_none()

    if target is None:
        raise ValueError(f"Agent {to_agent_name!r} not found")

    if target.id == from_agent.id:
        raise ValueError("Cannot send a message to yourself")

    # Create the message record
    message = Message(
        from_agent_id=from_agent.id,
        to_agent_id=target.id,
        text=text,
        read=False,
    )
    db.add(message)
    await db.flush()

    return {
        "sent": True,
        "message_id": str(message.id),
        "to": target.name,
        "text": text,
    }


async def read_messages(
    db: AsyncSession,
    agent: Agent,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    Read messages in an agent's inbox (newest first).

    Marks all retrieved unread messages as read. Paginated so agents can
    process large backlogs incrementally.

    Args:
        db:        Active async database session.
        agent:     The reading agent (authenticated).
        page:      Page number, 1-indexed.
        page_size: Number of messages per page.

    Returns:
        Dict with messages list, pagination info, and unread count before read.
    """
    page = max(1, page)
    offset = (page - 1) * page_size

    # Count total messages in inbox
    from sqlalchemy import func as sqlfunc
    count_result = await db.execute(
        select(sqlfunc.count(Message.id)).where(
            Message.to_agent_id == agent.id
        )
    )
    total = count_result.scalar_one() or 0

    # Count unread messages before we mark them
    unread_result = await db.execute(
        select(sqlfunc.count(Message.id)).where(
            Message.to_agent_id == agent.id,
            Message.read == False,  # noqa: E712
        )
    )
    unread_count = unread_result.scalar_one() or 0

    # Fetch the page of messages, newest first
    msgs_result = await db.execute(
        select(Message)
        .where(Message.to_agent_id == agent.id)
        .order_by(Message.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    messages = list(msgs_result.scalars().all())

    # Mark retrieved messages as read
    message_ids = [m.id for m in messages if not m.read]
    if message_ids:
        await db.execute(
            update(Message)
            .where(Message.id.in_(message_ids))
            .values(read=True)
        )
        # Update in-memory objects too
        for m in messages:
            if not m.read:
                m.read = True

    # Look up sender names for context
    sender_ids = list({m.from_agent_id for m in messages})
    sender_names: dict = {}
    if sender_ids:
        senders_result = await db.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_(sender_ids))
        )
        sender_names = {row.id: row.name for row in senders_result.all()}

    return {
        "messages": [
            {
                **m.to_dict(),
                "from_agent_name": sender_names.get(m.from_agent_id, "unknown"),
            }
            for m in messages
        ],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": (offset + page_size) < total,
        },
        "unread_before_read": unread_count,
    }
