"""The ruff pin in pyproject.toml (dev extras) and in the test workflow must agree.

An unpinned linter gives a different baseline under every install, so the version
is pinned exactly in both places; this test fails if they drift apart.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXACT = re.compile(r"^\d+\.\d+\.\d+$")


def _pyproject_pin() -> str:
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["dev"]
    pins = [m.group(1) for d in dev if (m := re.fullmatch(r"ruff==(\S+)", d.strip()))]
    assert len(pins) == 1, f"dev extras must pin ruff with exactly one '==' requirement, got {dev}"
    return pins[0]


def _workflow_pin() -> str:
    text = (ROOT / ".github" / "workflows" / "test.yml").read_text()
    pins = re.findall(r"^\s*RUFF_VERSION:\s*\"?([^\s\"#]+)\"?", text, flags=re.MULTILINE)
    assert len(pins) == 1, f"test.yml must define RUFF_VERSION exactly once, got {pins}"
    return pins[0]


def test_ruff_pin_is_exact_in_pyproject_and_workflow():
    for pin in (_pyproject_pin(), _workflow_pin()):
        assert EXACT.match(pin), f"ruff pin {pin!r} is not an exact X.Y.Z version"


def test_ruff_pins_agree():
    assert _pyproject_pin() == _workflow_pin()
