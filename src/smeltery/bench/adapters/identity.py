"""Local identity adapter: one trusted user."""

from __future__ import annotations

import getpass

from smeltery.bench.ports.clock import Clock, SystemClock


class LocalIdentity:
    def current(self) -> str:
        try:
            return getpass.getuser()
        except Exception:
            return "local"


__all__ = ["LocalIdentity", "Clock", "SystemClock"]
