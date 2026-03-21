"""
Clock abstraction for the Agent Economy backend.

All domain code must use Clock.now() — never datetime.now() or datetime.utcnow().
This makes time injectable for testing via MockClock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Protocol for time abstraction. Inject this everywhere time is needed."""

    def now(self) -> datetime:
        """Return current UTC datetime."""
        ...


class RealClock:
    """Production clock that returns actual UTC time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class MockClock:
    """
    Deterministic clock for tests.

    Start at a fixed point in time and advance manually.
    The ONLY thing that gets mocked in the simulation tests.
    """

    def __init__(self, start: datetime | None = None) -> None:
        if start is None:
            # Default to a fixed epoch for reproducibility
            start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self._current: datetime = start

    def now(self) -> datetime:
        return self._current

    def advance(self, seconds: float) -> None:
        """Move time forward by the given number of seconds."""
        from datetime import timedelta

        self._current = self._current + timedelta(seconds=seconds)

    def advance_hours(self, hours: float) -> None:
        self.advance(hours * 3600)

    def advance_days(self, days: float) -> None:
        self.advance(days * 86400)
