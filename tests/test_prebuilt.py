"""The prebuilt pipelines: each config is valid, and each committed run is that config's own run."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from smeltery.bench.verdict import verdict_of

PREBUILT = Path(__file__).resolve().parent.parent / "src" / "smeltery" / "prebuilt"
NAMES = ["benzoic-rhf", "benzoic-pbe", "docking", "structure"]


def _plan_digest(folder: Path) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "smeltery", "run", str(folder / "pipeline.toml"), "--plan"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    m = re.search(r"^input_digest (\w+)$", out, re.M)
    assert m, out
    return m.group(1)


@pytest.mark.parametrize("name", NAMES)
def test_every_prebuilt_config_validates(name):
    folder = PREBUILT / name
    assert (folder / "pipeline.toml").is_file()
    assert len(folder.joinpath("about.md").read_text().strip().splitlines()) == 2
    assert _plan_digest(folder)


@pytest.mark.parametrize("name", NAMES)
def test_each_prebuilt_run_is_its_configs_run(name):
    folder = PREBUILT / name
    if not (folder / "run").exists():
        pytest.skip(f"{name} has no run/ yet: generate it with scripts/build_prebuilt.py {name}")
    digest = _plan_digest(folder)
    path = folder / "run" / f"run-{digest[:12]}.json"
    assert path.is_file(), f"run/ has no record for the current config digest ({path.name})"
    record = json.loads(path.read_text())
    assert record["input_digest"] == digest
    assert verdict_of(record).state != "INCOMPLETE"
