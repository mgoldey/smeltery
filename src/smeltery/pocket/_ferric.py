"""Lazy handle on `ferric`, so `import smeltery` never needs it installed.

Attribute access imports ferric on first use; the call sites keep reading
`ferric.run_rhf(...)`. Mirrors how `smeltery.structure` defers the import.
"""
from __future__ import annotations


class _LazyFerric:
    def __getattr__(self, name: str):
        import ferric as _ferric

        return getattr(_ferric, name)


ferric = _LazyFerric()
