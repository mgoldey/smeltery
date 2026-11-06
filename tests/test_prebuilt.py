"""The prebuilt pipelines: each config is valid, and each committed run is that config's own run."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from smeltery.bench.verdict import verdict_of
from smeltery.pocket import pdb2pqr_version

PREBUILT = Path(__file__).resolve().parent.parent / "src" / "smeltery" / "prebuilt"
NAMES = ["benzoic-rhf", "benzoic-pbe", "docking", "structure", "danuglipron-halogen"]


#: Folders whose full plan embeds every analogue (a 74-atom parent: minutes of MCS), so the config test plans
#: the parent and one analogue instead. The config is validated whole either way.
ONE_CANDIDATE = {"danuglipron-halogen": "bzim-C9-F"}


def _plan_digest(folder: Path, *extra: str) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "smeltery", "run", str(folder / "pipeline.toml"), "--plan", *extra],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    m = re.search(r"^input_digest (\w+)$", out, re.M)
    assert m, out
    return m.group(1)


def _needs(text: str) -> str | None:
    """Why this config cannot be planned in this environment, or None.

    Planning a `[docking]` or `[structure]` section builds a receptor, so it needs the docking extra; the `test`
    job installs dev/posebusters/experiments/bench and not docking, where `smeltery run --plan` exits 3 on an
    honest ImportError. That is the environment's answer, not a broken config, so those configs skip."""
    if "[docking]" in text or "[structure]" in text:
        missing = [m for m in ("vina", "meeko") if importlib.util.find_spec(m) is None]
        if missing:
            return f"needs the docking extra ({', '.join(missing)} missing)"
    if "posebusters = true" in text and importlib.util.find_spec("posebusters") is None:
        return "needs the posebusters extra"
    return None


@pytest.mark.parametrize("name", NAMES)
def test_every_prebuilt_config_validates(name):
    folder = PREBUILT / name
    assert (folder / "pipeline.toml").is_file()
    assert len(folder.joinpath("about.md").read_text().strip().splitlines()) == 2
    why = _needs((folder / "pipeline.toml").read_text())
    if why:
        pytest.skip(why)
    only = ONE_CANDIDATE.get(name)
    assert _plan_digest(folder, *(["--only-candidate", only] if only else []))


@pytest.mark.parametrize("name", NAMES)
def test_each_prebuilt_run_is_its_configs_run(name):
    folder = PREBUILT / name
    if not (folder / "run").exists() and (folder / "imported").exists():
        pytest.skip(f"{name} has no run yet: only imported/ (the 2026-10-16 record), which is not this config's run")
    if not (folder / "run").exists():
        pytest.skip(f"{name} has no run/ yet: generate it with scripts/build_prebuilt.py {name}")
    records = sorted((folder / "run").glob("run-*.json"))
    assert len(records) == 1, f"run/ should hold exactly one record, found {[r.name for r in records]}"
    record = json.loads(records[0].read_text())
    assert verdict_of(record).state != "INCOMPLETE"
    # The digest includes the pdb2pqr version, so it only reproduces with the tool the run used.
    recorded = (record["inputs"].get("pocket") or {}).get("pdb2pqr_version")
    here = pdb2pqr_version()
    if recorded is not None and recorded != here:
        pytest.skip(f"{name} was run with {recorded!r}; this machine's pdb2pqr30 reports {here!r}")
    # Re-planning needs the config's own extras, like the test above: everything before this point checks the
    # committed record itself, which is worth doing wherever the suite runs.
    why = _needs((folder / "pipeline.toml").read_text())
    if why:
        pytest.skip(why)
    digest = _plan_digest(folder)
    assert records[0].name == f"run-{digest[:12]}.json", "run/ has no record for the current config digest"
    assert record["input_digest"] == digest
