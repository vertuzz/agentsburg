"""Shared helpers for stress tests."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.models.business import Business
from backend.models.inventory import InventoryItem


async def assert_no_negative_inventory(app, label: str) -> None:
    """Verify no inventory row has quantity < 0."""
    async with app.state.session_factory() as session:
        result = await session.execute(
            select(InventoryItem).where(InventoryItem.quantity < 0)
        )
        negatives = result.scalars().all()
        if negatives:
            details = [
                f"{item.good_slug}={item.quantity} (owner={item.owner_type}:{item.owner_id})"
                for item in negatives
            ]
            pytest.fail(
                f"[{label}] Negative inventory found: {details}"
            )
    print(f"  [{label}] No negative inventory -- OK")


async def get_open_business_count(app, *, is_npc: bool | None = None) -> int:
    """Count open (non-closed) businesses, optionally filtered by NPC status."""
    async with app.state.session_factory() as session:
        q = select(func.count(Business.id)).where(Business.closed_at.is_(None))
        if is_npc is not None:
            q = q.where(Business.is_npc == is_npc)
        result = await session.execute(q)
        return result.scalar_one()
