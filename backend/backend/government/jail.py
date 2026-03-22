"""
Jail enforcement utilities for Agent Economy.

Jailed agents cannot make strategic economic decisions. The following tools
should raise IN_JAIL when the calling agent is jailed:
  - register_business
  - marketplace_order (buy/sell; cancel is allowed)
  - trade (propose only; respond/cancel are allowed)
  - manage_employees (post_job, hire_npc, fire)
  - configure_production

These tools are NOT blocked while jailed:
  - get_status()            — always available
  - rent_housing()          — can still pay for shelter
  - messages               — communication not blocked
  - bank operations        — can pay off fines, manage savings
  - marketplace_browse     — view-only, not blocked
  - trade(respond/cancel)  — must be able to respond to existing proposals

Jail mechanics from spec:
  "Businesses keep running but at reduced efficiency. Agent can't make
  strategic changes. Only NPC staff operate. Higher costs."

The efficiency penalty (jail_efficiency_penalty from economy.yaml) is applied
to NPC worker production in Phase 7. Here we enforce the action restriction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from backend.models.agent import Agent
    from backend.clock import Clock


def is_jailed(agent: "Agent", clock: "Clock") -> bool:
    """
    Return True if the agent is currently serving jail time.

    Args:
        agent: The agent to check.
        clock: Clock for current time.

    Returns:
        True if jailed, False otherwise.
    """
    now = clock.now()
    return agent.jail_until is not None and agent.jail_until > now


def get_jail_remaining_seconds(agent: "Agent", clock: "Clock") -> float | None:
    """
    Return seconds remaining in jail sentence, or None if not jailed.

    Args:
        agent: The agent to check.
        clock: Clock for current time.

    Returns:
        Float seconds remaining, or None.
    """
    if not is_jailed(agent, clock):
        return None
    now = clock.now()
    return (agent.jail_until - now).total_seconds()  # type: ignore[operator]


def check_jail(agent: "Agent", clock: "Clock") -> None:
    """
    Raise ValueError if the agent is currently jailed.

    Call this at the start of any tool handler that should be blocked while jailed.

    Args:
        agent: The agent to check.
        clock: Clock for current time.

    Raises:
        ValueError: With an IN_JAIL message and remaining time info if jailed.
    """
    remaining = get_jail_remaining_seconds(agent, clock)
    if remaining is not None:
        hours = remaining / 3600
        raise ValueError(
            f"IN_JAIL: You are currently in jail for {hours:.1f} more hours "
            f"(until {agent.jail_until.isoformat()}). "  # type: ignore[union-attr]
            "You cannot perform economic activities while jailed. "
            "You can still manage your bank account and read messages."
        )
