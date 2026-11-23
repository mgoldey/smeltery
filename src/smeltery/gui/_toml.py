"""Moved to `smeltery.toml_write`; this path re-exports `dumps` so existing imports keep working."""

from __future__ import annotations

from smeltery.toml_write import dumps

__all__ = ["dumps"]
