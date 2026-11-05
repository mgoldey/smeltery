import glob
import json
from pathlib import Path

import pytest
from golden_path import results_without_timing

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


@pytest.mark.needs_ferric
def _run_all_partials(cfg, out):
    for cand in ["ethanol", "propanol", "butanol"]:
        assert main(["run", str(cfg), "--out", str(out), "--only-candidate", cand]) == 0


@pytest.mark.needs_ferric
def test_split_then_combine_gives_the_same_record_as_one_process(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL)
    out = tmp_path / "out"
    _run_all_partials(cfg, out)
    assert main(["run", str(cfg), "--out", str(out), "--combine"]) == 0
    combined = glob.glob(str(out / "run-*.json"))[0]

    out2 = tmp_path / "out2"
    assert main(["run", str(cfg), "--out", str(out2)]) == 0
    single = glob.glob(str(out2 / "run-*.json"))[0]

    a = json.loads(Path(combined).read_text())
    b = json.loads(Path(single).read_text())
    for o in (a, b):
        for k in ("started_utc", "finished_utc"):
            o.pop(k, None)
        o["results"] = results_without_timing(o["results"])  # wall-clock seconds differ between two runs
    assert a == b


@pytest.mark.needs_ferric
def test_combine_carries_every_per_pose_quantity(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL)
    out = tmp_path / "out"
    _run_all_partials(cfg, out)
    assert main(["run", str(cfg), "--out", str(out), "--combine"]) == 0
    combined = json.loads(Path(glob.glob(str(out / "run-*.json"))[0]).read_text())
    for name in ("ethanol", "propanol", "butanol"):
        assert "dE_int" in combined["results"]["per_pose"][name]


@pytest.mark.needs_ferric
def test_combine_without_every_partial_refuses_naming_the_missing_ones(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL)
    out = tmp_path / "out"
    # only two of three partials
    for cand in ["ethanol", "propanol"]:
        assert main(["run", str(cfg), "--out", str(out), "--only-candidate", cand]) == 0
    rc = main(["run", str(cfg), "--out", str(out), "--combine"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "butanol" in err


@pytest.mark.needs_ferric
def test_an_unknown_candidate_name_is_a_config_error_with_a_message(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL)
    out = tmp_path / "out"
    rc = main(["run", str(cfg), "--out", str(out), "--only-candidate", "nope"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "nope" in err
