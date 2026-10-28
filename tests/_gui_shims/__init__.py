"""TEMPORARY stand-ins for `smeltery.gui.results`, `smeltery.gui.scenes` and `smeltery.decisions`
(docs/gui-contract.md sections 2 and 3), written in parallel with the real modules so the viewers can be tested.
Delete this package once the real modules are merged.

`install(monkeypatch)` puts a shim in `sys.modules` under the real name, only where the real module is missing;
`bootstrap.py` does the same for a subprocess."""

from __future__ import annotations

import importlib.util
import sys

REAL = {
    "smeltery.gui.results": "results",
    "smeltery.gui.scenes": "scenes",
    "smeltery.decisions": "decisions",
}


def _missing(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is None
    except ModuleNotFoundError:
        return True


def shims() -> dict:
    """{real module name: shim module}, only for the real modules that do not exist yet."""
    from . import decisions, results, scenes

    mods = {"results": results, "scenes": scenes, "decisions": decisions}
    return {real: mods[short] for real, short in REAL.items() if _missing(real)}


def install(monkeypatch=None) -> None:
    import smeltery.gui  # noqa: F401  (the parents must exist to hang the shims on)

    for real, mod in shims().items():
        parent, _, attr = real.rpartition(".")
        if monkeypatch is None:
            sys.modules[real] = mod
            setattr(sys.modules[parent], attr, mod)
        else:
            monkeypatch.setitem(sys.modules, real, mod)
            monkeypatch.setattr(sys.modules[parent], attr, mod, raising=False)
