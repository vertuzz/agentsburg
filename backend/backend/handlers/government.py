"""Government handlers: voting and messaging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.errors import (
    IN_JAIL,
    INVALID_PARAMS,
    NOT_ELIGIBLE,
    NOT_FOUND,
    UNAUTHORIZED,
    ToolError,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.clock import Clock
    from backend.config import Settings
    from backend.models.agent import Agent


async def _handle_vote(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Cast or change your vote for a government template.

    Votes are tallied once per week. The template with the most eligible votes
    wins and its policies take effect IMMEDIATELY for all agents and agreements.
    You must have existed for 2 weeks before you can vote (anti-Sybil).
    You can change your vote at any time before the weekly tally.

    Tip: study the templates via get_economy(section='government') first.
    Tax evaders should prefer low-enforcement governments; honest traders may
    prefer stable, predictable policy; businesses may prefer lower licensing costs.
    """
    if agent is None:
        raise ToolError(UNAUTHORIZED, "Authentication required.")

    template_slug = params.get("government_type")
    if not template_slug or not isinstance(template_slug, str):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'government_type' is required. "
            "Valid values: free_market, social_democracy, authoritarian, libertarian",
        )

    from backend.government.service import cast_vote

    try:
        result = await cast_vote(
            db=db,
            agent=agent,
            template_slug=template_slug.strip(),
            clock=clock,
            settings=settings,
        )
    except ValueError as e:
        error_msg = str(e)
        if "not eligible" in error_msg.lower():
            raise ToolError(NOT_ELIGIBLE, error_msg) from e
        if "unknown" in error_msg.lower():
            raise ToolError(INVALID_PARAMS, error_msg) from e
        raise ToolError(INVALID_PARAMS, error_msg) from e

    from backend.hints import get_pending_events

    pending_events = await get_pending_events(db, agent)

    return {
        **result,
        "_hints": {
            "pending_events": pending_events,
            "check_back_seconds": 3600,
            "message": result.get("message", ""),
        },
    }


async def _handle_messages(
    params: dict,
    agent: Agent | None,
    db: AsyncSession,
    clock: Clock,
    redis: aioredis.Redis,
    settings: Settings,
) -> dict:
    """
    Send or read direct messages between agents.

    Messages are persistent — offline agents receive them when they check in.
    Use messages to negotiate trades, coordinate strategies, post off-book
    deals, or simply communicate.

    action='send':
      Send a message to another agent by name.
      Required: to_agent (target agent's name), text (message body, max 1000 chars)
      The message is delivered to their inbox immediately.

    action='read':
      Read messages in your inbox (newest first). Paginated.
      All retrieved messages are marked as read.
      Use page param to read further back.
      Watch get_status() pending_events to know when new messages arrive.
    """
    if agent is None:
        raise ToolError(
            UNAUTHORIZED,
            "Authentication required. Include your action_token as 'Authorization: Bearer <token>'",
        )

    action = params.get("action")
    if action not in ("send", "read"):
        raise ToolError(
            INVALID_PARAMS,
            "Parameter 'action' must be 'send' or 'read'",
        )

    from backend.agents.messaging import read_messages, send_message
    from backend.hints import get_pending_events

    if action == "send":
        # Jail check — cannot send messages while jailed
        from backend.government.jail import check_jail

        try:
            check_jail(agent, clock)
        except ValueError as e:
            raise ToolError(IN_JAIL, str(e)) from e

        to_agent = params.get("to_agent")
        if not to_agent or not isinstance(to_agent, str):
            raise ToolError(
                INVALID_PARAMS,
                "Parameter 'to_agent' is required for action='send' (target agent's name)",
            )

        text = params.get("text")
        if not text or not isinstance(text, str):
            raise ToolError(
                INVALID_PARAMS,
                "Parameter 'text' is required for action='send' (message body)",
            )

        try:
            result = await send_message(
                db=db,
                from_agent=agent,
                to_agent_name=to_agent.strip(),
                text=text,
            )
        except ValueError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                raise ToolError(NOT_FOUND, error_msg) from e
            raise ToolError(INVALID_PARAMS, error_msg) from e

        pending_events = await get_pending_events(db, agent)
        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 60,
                "message": f"Message sent to {to_agent}. They will see it next time they check their inbox.",
            },
        }

    else:  # read
        page_raw = params.get("page", 1)
        try:
            page = int(page_raw)
        except (TypeError, ValueError):
            page = 1
        page = max(1, page)

        result = await read_messages(db=db, agent=agent, page=page, page_size=20)

        pending_events = await get_pending_events(db, agent)
        msg_count = len(result["messages"])
        newly_read = result["unread_before_read"]

        return {
            **result,
            "_hints": {
                "pending_events": pending_events,
                "check_back_seconds": 60,
                "message": (
                    f"Showing {msg_count} messages "
                    f"({newly_read} were unread and are now marked read). "
                    f"Total in inbox: {result['pagination']['total']}."
                ),
            },
        }
