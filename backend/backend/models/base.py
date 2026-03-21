"""
SQLAlchemy declarative base and shared mixins.

All models should inherit from Base and apply TimestampMixin where appropriate.

UUID primary keys are used throughout for:
- Security (non-sequential, non-guessable)
- Compatibility with distributed writes (if ever needed)
- Consistent FK types across the schema
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Project-wide SQLAlchemy declarative base.

    All ORM models must inherit from this class. Alembic's env.py
    imports this Base to generate migrations from model metadata.
    """

    pass


class UUIDMixin:
    """
    Adds a UUID primary key column named `id`.

    Uses PostgreSQL's native UUID type for efficient storage and indexing.
    The default generates a new UUID4 at the Python layer (not the DB),
    so the value is available before flush/commit.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )


class TimestampMixin:
    """
    Adds created_at and updated_at columns managed by SQLAlchemy events.

    - created_at: set once on INSERT, never changes
    - updated_at: updated on every UPDATE via server_onupdate / onupdate
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
