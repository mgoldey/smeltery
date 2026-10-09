"""Opt-in no-ferric mode for sandboxes where the engine cannot be built.

Only when SMELTERY_NO_FERRIC=1 is set AND `ferric` is not importable, a clearly
labelled STUB module is installed in sys.modules and every test marked
`needs_ferric` is skipped (reason names ferric). With the variable unset this
file does nothing: CI still runs everything and fails if ferric is missing.
Use scripts/dev-check.sh; never set the variable in CI.
"""

import importlib.util
import os
import sys
import types

import pytest

STUB_NOTICE = "SMELTERY_NO_FERRIC stub: the real ferric engine is NOT installed"
SKIP_REASON = "needs the ferric engine (not importable; SMELTERY_NO_FERRIC=1 stub active)"


def no_ferric_requested(environ=None) -> bool:
    return (os.environ if environ is None else environ).get("SMELTERY_NO_FERRIC") == "1"


def _ferric_importable() -> bool:
    try:
        return importlib.util.find_spec("ferric") is not None
    except (ImportError, ValueError):
        return False


def install_ferric_stub(environ=None) -> bool:
    """Install the stub if requested and ferric is absent. Returns True if installed."""
    if not no_ferric_requested(environ) or "ferric" in sys.modules or _ferric_importable():
        return False
    stub = types.ModuleType("ferric")
    stub.__doc__ = STUB_NOTICE
    stub.__stub__ = True

    def _missing(name):
        raise AttributeError(f"ferric.{name}: {STUB_NOTICE}")

    stub.__getattr__ = _missing
    sys.modules["ferric"] = stub
    return True


STUB_ACTIVE = install_ferric_stub()


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_ferric: test genuinely needs the ferric engine")


def pytest_collection_modifyitems(config, items):
    if not STUB_ACTIVE:
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if "needs_ferric" in item.keywords:
            item.add_marker(skip)


def pytest_report_header(config):
    if STUB_ACTIVE:
        return f"WARNING: {STUB_NOTICE}; needs_ferric tests are skipped"
