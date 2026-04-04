"""Helpers for classifying SQLAlchemy / asyncpg database errors."""

from __future__ import annotations


def is_deadlock_error(exc: BaseException) -> bool:
    """Return True when the exception represents a PostgreSQL deadlock victim."""
    sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    if sqlstate == "40P01":
        return True

    orig = getattr(exc, "orig", None)
    if orig is not None:
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        if sqlstate == "40P01":
            return True

    return "deadlock detected" in str(exc).lower()
