"""PortContract harness for bench contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


class PortContract:
    port: type = object

    @classmethod
    def adapters(cls) -> list[tuple[str, Callable[[Path], object]]]:
        return []

    @classmethod
    def parametrize(cls):
        return cls.adapters()
