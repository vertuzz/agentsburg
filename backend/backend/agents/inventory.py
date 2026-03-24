"""
Inventory management domain logic for Agent Economy.

Handles adding/removing items from agent and business inventories,
enforcing storage capacity limits, and querying inventory state.

Storage capacity is measured in "storage units" — each good has a
storage_size that determines how many units it takes up.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.models.inventory import InventoryItem

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.config import Settings

logger = logging.getLogger(__name__)


async def get_inventory(
    db: AsyncSession,
    owner_type: str,
    owner_id: uuid.UUID,
) -> list[InventoryItem]:
    """
    Return all inventory items for the given owner.

    Args:
        db:         Active async database session.
        owner_type: "agent" or "business"
        owner_id:   UUID of the owner.

    Returns:
        List of InventoryItem objects (may be empty).
    """
    result = await db.execute(
        select(InventoryItem).where(
            InventoryItem.owner_type == owner_type,
            InventoryItem.owner_id == owner_id,
            InventoryItem.quantity > 0,
        )
    )
    return list(result.scalars().all())


async def get_storage_used(
    db: AsyncSession,
    owner_type: str,
    owner_id: uuid.UUID,
    settings: Settings,
) -> int:
    """
    Calculate total storage units currently used by this owner.

    Storage used = sum(good.storage_size * item.quantity) for all items.

    Args:
        db:         Active async database session.
        owner_type: "agent" or "business"
        owner_id:   UUID of the owner.
        settings:   Application settings (for goods config).

    Returns:
        Total storage units currently consumed.
    """
    items = await get_inventory(db, owner_type, owner_id)
    if not items:
        return 0

    # Build a lookup of storage_size from config
    goods_config = {g["slug"]: g for g in settings.goods}

    total = 0
    for item in items:
        good_data = goods_config.get(item.good_slug)
        if good_data:
            storage_size = good_data.get("storage_size", 1)
        else:
            storage_size = 1  # fallback for unknown goods
        total += storage_size * item.quantity

    return total


async def add_to_inventory(
    db: AsyncSession,
    owner_type: str,
    owner_id: uuid.UUID,
    good_slug: str,
    quantity: int,
    settings: Settings,
) -> InventoryItem:
    """
    Add items to an owner's inventory, enforcing storage capacity.

    If the owner already has some of this good, the quantity is incremented.
    If not, a new InventoryItem row is created.

    Args:
        db:         Active async database session.
        owner_type: "agent" or "business"
        owner_id:   UUID of the owner.
        good_slug:  The good to add.
        quantity:   How many units to add (must be > 0).
        settings:   Application settings (for capacity limits and goods config).

    Returns:
        The updated (or newly created) InventoryItem.

    Raises:
        ValueError: If adding the quantity would exceed storage capacity,
                    or if the good is not found in config.
    """
    if quantity <= 0:
        raise ValueError(f"Quantity must be positive, got {quantity}")

    # Look up the good in config
    goods_config = {g["slug"]: g for g in settings.goods}
    good_data = goods_config.get(good_slug)
    if good_data is None:
        raise ValueError(f"Unknown good: {good_slug!r}")

    storage_size = good_data.get("storage_size", 1)
    additional_storage = storage_size * quantity

    # Check capacity
    capacity = (
        settings.economy.agent_storage_capacity if owner_type == "agent" else settings.economy.business_storage_capacity
    )
    current_used = await get_storage_used(db, owner_type, owner_id, settings)
    if current_used + additional_storage > capacity:
        available = capacity - current_used
        raise ValueError(
            f"Storage full. Adding {quantity}x {good_slug} requires {additional_storage} units "
            f"but only {available} available (capacity: {capacity}, used: {current_used})"
        )

    # Upsert the inventory item (locked to prevent concurrent double-add)
    result = await db.execute(
        select(InventoryItem)
        .where(
            InventoryItem.owner_type == owner_type,
            InventoryItem.owner_id == owner_id,
            InventoryItem.good_slug == good_slug,
        )
        .with_for_update()
    )
    item = result.scalar_one_or_none()

    if item is None:
        item = InventoryItem(
            owner_type=owner_type,
            owner_id=owner_id,
            good_slug=good_slug,
            quantity=quantity,
        )
        db.add(item)
    else:
        item.quantity += quantity

    await db.flush()
    return item


async def remove_from_inventory(
    db: AsyncSession,
    owner_type: str,
    owner_id: uuid.UUID,
    good_slug: str,
    quantity: int,
) -> InventoryItem:
    """
    Remove items from an owner's inventory.

    If the resulting quantity would be zero, the row is kept but zeroed
    (not deleted) to preserve history via the unique constraint.

    Args:
        db:         Active async database session.
        owner_type: "agent" or "business"
        owner_id:   UUID of the owner.
        good_slug:  The good to remove.
        quantity:   How many units to remove (must be > 0).

    Returns:
        The updated InventoryItem.

    Raises:
        ValueError: If the owner doesn't have enough of the good.
    """
    if quantity <= 0:
        raise ValueError(f"Quantity must be positive, got {quantity}")

    result = await db.execute(
        select(InventoryItem)
        .where(
            InventoryItem.owner_type == owner_type,
            InventoryItem.owner_id == owner_id,
            InventoryItem.good_slug == good_slug,
        )
        .with_for_update()
    )
    item = result.scalar_one_or_none()

    if item is None or item.quantity < quantity:
        have = item.quantity if item else 0
        raise ValueError(f"Insufficient inventory: have {have}x {good_slug}, need {quantity}")

    item.quantity -= quantity
    await db.flush()
    return item
