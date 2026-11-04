"""Port: who is acting now."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Identity(Protocol):
    """The acting user. Local adapter returns one trusted user; multi-user is an adapter change."""

    def current(self) -> str:
        """The current user's identifier."""
        ...
