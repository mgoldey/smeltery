"""Port: the current UTC time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current time in UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
