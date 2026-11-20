"""Moved to `smeltery.config_text` (core: `smeltery run` stages inputs with it); this path re-exports the same
objects so existing imports keep working. Patch `smeltery.config_text`, not this module."""

from __future__ import annotations

from smeltery.config_text import ConfigTextError, apply, diff_lines, set_value

__all__ = ["ConfigTextError", "apply", "diff_lines", "set_value"]
