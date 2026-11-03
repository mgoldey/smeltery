import glob
import json
from pathlib import Path

import pytest

from smeltery.cli import main

SMALL = """
campaign = "small"
parent = "ethanol"

[candidates]
ethanol = "CCO"
propanol = "CCCO"
butanol = "CCCCO"

[poses]
n_poses = 3
relax_unmapped = true

[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


@pytest.mark.needs_ferric
def test_only_candidate_writes_a_partial_and_no_cut(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL)
    out = tmp_path / "out"
    assert main(["run", str(cfg), "--out", str(out), "--only-candidate", "propanol"]) == 0
    partials = list(out.glob("partial-*.json"))
    assert len(partials) == 1
    p = out / "partial-propanol.json"
    assert p.exists()
    data = json.loads(p.read_text())
    assert "cut" not in data.get("results", {})
    assert not list(out.glob("run-*.json"))


@pytest.mark.needs_ferric
def test_an_analogue_partial_does_not_remeasure_the_parent(tmp_path, monkeypatch):
    import smeltery.cli as cli_mod

    calls = {"n": 0}
    orig = cli_mod.FieldInteraction.run

    def counting(self, candidates, ctx):
        calls["n"] += len(candidates)
        return orig(self, candidates, ctx)

    monkeypatch.setattr(cli_mod.FieldInteraction, "run", counting)

    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL)
    out = tmp_path / "out"
    assert main(["run", str(cfg), "--out", str(out), "--only-candidate", "propanol"]) == 0
    assert calls["n"] == 1
