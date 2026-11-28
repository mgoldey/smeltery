"""The baseline control (smeltery.baseline, [baseline]): synthetic ddE and labels, no SCF and no quantum chemistry.

The plan is built for real (RDKit poses, a point-charge field) and the SCF tier is replaced by fixed per-pose numbers
through `run_plan(..., measure=...)`, as tests/test_run_outputs.py does.
"""

import json
import math
import re

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import Crippen

from smeltery import baseline as core
from smeltery.cli import ConfigError, build_plan, load_config, run_plan, validate_config, write_outputs
from smeltery.methods import baseline as bl
from smeltery.methods import registry

BASE = """
campaign = "baseline"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"
4-F = "OC(=O)c1ccc(F)cc1"
4-Cl = "OC(=O)c1ccc(Cl)cc1"
4-Br = "OC(=O)c1ccc(Br)cc1"
4-Me = "Cc1ccc(C(=O)O)cc1"
4-OMe = "COc1ccc(C(=O)O)cc1"
4-NO2 = "OC(=O)c1ccc([N+](=O)[O-])cc1"

[poses]
n_poses = 3

[[pocket.site]]
q = 1.0
atom = 0
from_atom = 1
distance = 3.0

[cut]
keep = 2
z = 2.0
floor = 0.0
"""
ANALOGUES = ["4-F", "4-Cl", "4-Br", "4-Me", "4-OMe", "4-NO2"]
#: synthetic per-analogue ddE (kcal/mol) the stand-in SCF produces
DDE = {"4-F": 0.5, "4-Cl": 2.0, "4-Br": 1.0, "4-Me": 3.5, "4-OMe": 2.5, "4-NO2": 4.0}


def fill(plan):
    for c in plan.candidates:
        shift = 0.0 if c is plan.parent else DDE[c.name]
        c.per_pose["dE_int"] = [-60.0 + 0.7 * i + shift for i in range(len(c.poses))]


def make(tmp_path, extra="", labels=None, text=BASE):
    if labels is not None:
        (tmp_path / "labels.csv").write_text("".join(f"{k},{v}\n" for k, v in labels.items()))
    p = tmp_path / "c.toml"
    p.write_text(text + extra)
    return build_plan(load_config(p), out_dir=tmp_path / "out")


def sub(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def untimed(text):
    """A record's bytes with the wall-clock `seconds` of each stage and its ISO timestamps zeroed: what two runs never
    share (the timestamps differ whenever the runs straddle a second boundary, which CI did hit)."""
    text = re.sub(r'"seconds": [0-9.eE+-]+', '"seconds": 0', text)
    text = re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", "1970-01-01T00:00:00Z", text)
    return re.sub(r'"record_sha256": "[0-9a-f]+"', '"record_sha256": ""', text)  # a hash of the seconds too


def logp(smiles):
    return Crippen.MolLogP(Chem.MolFromSmiles(smiles))


# ---------------------------------------------------------------------------------------------- the statistics


def test_detectable_reproduces_the_limits_L4_and_L8():
    d8, d12 = core.detectable(8), core.detectable(12)
    assert d8["critical_abs_rho"] == pytest.approx(0.738, abs=5e-4)  # L4
    assert d12["critical_abs_rho"] == pytest.approx(0.587, abs=5e-3)  # L8 (Monte Carlo null at n = 12)
    assert 0.3 < d8["power_at_r_0_7"] < 0.5 and 0.6 < d12["power_at_r_0_7"] < 0.8
    assert core.detectable(4)["critical_abs_rho"] is None  # no |rho| is significant at n = 4
    assert core.detectable(2)["critical_abs_rho"] is None


def test_a_baseline_that_ranks_better_is_called_so_only_when_the_ci_excludes_zero():
    n = 24
    rng = np.random.default_rng(1)
    labels = {f"a{i}": float(i) for i in range(n)}
    base = {k: v + 0.1 * rng.normal() for k, v in labels.items()}  # nearly the labels
    ddE = {k: rng.normal() for k in labels}  # unrelated to them
    e = core.compare("crippen_logp", base, ddE, labels, seed=7, n_boot=2000)
    assert e["delta_rho"] > 0.5 and e["delta_rho_ci95"][0] > 0
    assert e["verdict_key"] == "baseline_better"
    assert e["verdict"].startswith("does not beat the logP baseline")
    swapped = core.compare("crippen_logp", ddE, base, labels, seed=7, n_boot=2000)
    assert swapped["verdict_key"] == "ddE_better" and "not a general claim" in swapped["verdict"]
    assert swapped["delta_rho"] == pytest.approx(-e["delta_rho"])


def test_equal_rankings_are_indistinguishable_and_never_called_equivalent():
    labels = {f"a{i}": float(i) for i in range(10)}
    e = core.compare("crippen_logp", labels, dict(labels), labels, seed=1, n_boot=1000)
    assert e["delta_rho"] == 0 and e["verdict_key"] == "indistinguishable"
    assert "not evidence they are equivalent" in e["verdict"]
    assert "matches" not in e["verdict"] and "equivalent to" not in e["verdict"]


def test_a_small_n_is_underpowered_and_says_so():
    labels = {f"a{i}": float(i) for i in range(8)}
    rng = np.random.default_rng(3)
    ddE = {k: v + rng.normal() for k, v in labels.items()}
    base = {k: v + rng.normal() for k, v in labels.items()}
    e = core.compare("crippen_logp", base, ddE, labels, seed=1, n_boot=1000)
    assert e["underpowered"] is True
    assert "underpowered: smallest significant |rho| 0.738" in e["verdict"]


def test_n_below_five_is_too_small_to_conclude_and_has_no_statistic():
    labels = {f"a{i}": float(i) for i in range(4)}
    e = core.compare("crippen_logp", labels, labels, labels, seed=1, n_boot=500)
    assert e["verdict_key"] == "too_small" and e["verdict"].startswith("n too small to conclude")
    assert "delta_rho" not in e and "rho_ddE" not in e


def test_a_constant_baseline_is_undefined_not_a_comparison():
    labels = {f"a{i}": float(i) for i in range(8)}
    e = core.compare("crippen_logp", dict.fromkeys(labels, 1.0), labels, labels, seed=1, n_boot=500)
    assert e["verdict_key"] == "undefined" and e["rho_baseline"] is None


def test_without_labels_baselines_are_computed_and_nothing_is_compared():
    out = core.baseline_control({"a": 1.0, "b": 2.0}, None, {"crippen_logp": lambda: {"a": 1.0, "b": 0.5}})
    e = out["baselines"]["crippen_logp"]
    assert e["verdict_key"] == "no_labels" and e["values"] == {"a": 1.0, "b": 0.5} and "delta_rho" not in e
    assert out["n_labelled"] == 0 and out["verdict"] == core.PHRASES["no_labels"]


def test_a_skipped_baseline_keeps_its_place_and_reason():
    labels = {f"a{i}": float(i) for i in range(6)}
    out = core.baseline_control(
        labels, labels, {"mmgbsa": "needs openmm", "crippen_logp": lambda: dict(labels)}, seed=1, n_boot=500
    )
    assert list(out["baselines"]) == ["mmgbsa", "crippen_logp"]
    assert out["baselines"]["mmgbsa"]["status"] == "skipped" and out["baselines"]["mmgbsa"]["reason"] == "needs openmm"
    assert "MM/GBSA baseline not computed: needs openmm" in out["verdict"]


def test_the_bootstrap_is_seeded():
    labels = {f"a{i}": float(i) for i in range(9)}
    rng = np.random.default_rng(0)
    a, b = ({k: v + rng.normal() for k, v in labels.items()} for _ in range(2))
    runs = [core.compare("crippen_logp", a, b, labels, seed=5, n_boot=800) for _ in range(2)]
    assert runs[0]["delta_rho_ci95"] == runs[1]["delta_rho_ci95"]


# ---------------------------------------------------------------------------------------- the config and the hook


def test_the_method_is_registered_as_an_analysis_after_the_cut():
    spec = registry()["baseline"]
    assert spec.slot == "analysis" and spec.section == "baseline" and spec.file_knobs == ("labels", "mmgbsa_file")


def test_off_by_default_the_record_is_byte_identical_to_a_run_without_the_method(tmp_path):
    plan = make(sub(tmp_path, "a"))
    rec, _ = run_plan(plan, measure=fill)
    assert "baseline" not in rec.results
    off_bytes = write_outputs(plan, rec, tmp_path / "a" / "out").read_bytes()
    # the method unregistered altogether: the same bytes
    from smeltery.methods import _REGISTRY

    spec = _REGISTRY.pop("baseline")
    try:
        plan2 = make(sub(tmp_path, "b"))
        rec2, _ = run_plan(plan2, measure=fill)
        none_bytes = write_outputs(plan2, rec2, tmp_path / "b" / "out").read_bytes()
    finally:
        _REGISTRY["baseline"] = spec
    assert untimed(off_bytes.decode()) == untimed(none_bytes.decode())


def test_turning_it_on_changes_only_its_own_key_and_never_the_ranking(tmp_path):
    off, off_out = run_plan(make(sub(tmp_path, "off")), measure=fill)
    on, on_out = run_plan(make(sub(tmp_path, "on"), "\n[baseline]\n"), measure=fill)
    assert set(on.results) - set(off.results) == {"baseline"}
    for k in set(off.results):
        assert untimed(json.dumps(on.results[k])) == untimed(json.dumps(off.results[k])), k
    assert on_out["cut"].groups == off_out["cut"].groups and on_out["cut"].survivors == off_out["cut"].survivors


def test_logp_with_labels_end_to_end(tmp_path):
    smiles = {
        "4-F": "OC(=O)c1ccc(F)cc1",
        "4-Cl": "OC(=O)c1ccc(Cl)cc1",
        "4-Br": "OC(=O)c1ccc(Br)cc1",
        "4-Me": "Cc1ccc(C(=O)O)cc1",
        "4-OMe": "COc1ccc(C(=O)O)cc1",
        "4-NO2": "OC(=O)c1ccc([N+](=O)[O-])cc1",
    }
    labels = {k: DDE[k] * 0.5 for k in ANALOGUES}  # ddE ranks them perfectly
    plan = make(tmp_path, '\n[baseline]\nlabels = "labels.csv"\nn_boot = 500\n', labels)
    rec, _ = run_plan(plan, measure=fill)
    b = rec.results["baseline"]
    e = b["baselines"]["crippen_logp"]
    assert b["n_labelled"] == 6 and b["unlabelled"] == [] and b["detectable"]["n"] == 6
    for k, s in smiles.items():
        assert e["values"][k] == pytest.approx(logp(s))
    assert e["rho_ddE"] == pytest.approx(1.0) and e["rho_baseline"] < 1.0
    assert e["delta_rho"] == pytest.approx(e["rho_baseline"] - 1.0)
    assert e["verdict_key"] == "ddE_better" or e["verdict_key"] == "indistinguishable"  # n = 6: CI decides
    assert b["verdict"] == e["verdict"] and "underpowered" in b["verdict"]
    json.dumps(rec.results["baseline"])  # serialisable: it goes into run.json


def test_n_too_small_is_in_the_run_record(tmp_path):
    labels = {k: DDE[k] for k in ANALOGUES[:3]}
    plan = make(tmp_path, '\n[baseline]\nlabels = "labels.csv"\n', labels)
    rec, _ = run_plan(plan, measure=fill)
    b = rec.results["baseline"]
    assert b["n_labelled"] == 3 and b["unlabelled"] == sorted(ANALOGUES[3:])
    assert b["verdict"].startswith("n too small to conclude")


def test_a_run_without_labels_still_records_logp(tmp_path):
    rec, _ = run_plan(make(tmp_path, "\n[baseline]\n"), measure=fill)
    b = rec.results["baseline"]
    assert b["verdict"] == core.PHRASES["no_labels"] and b["n_labelled"] == 0
    assert set(b["baselines"]["crippen_logp"]["values"]) == set(ANALOGUES)


def test_mmgbsa_is_skipped_with_the_missing_packages_named(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "find_missing", None, raising=False)
    monkeypatch.setattr(core.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(core.shutil, "which", lambda name: None)
    ok, why = core.mmgbsa_availability()
    assert not ok and "openmm" in why and "openff.toolkit" in why and "sqm" in why
    rec, _ = run_plan(make(tmp_path, "\n[baseline]\nmmgbsa = true\nlogp = false\n"), measure=fill)
    e = rec.results["baseline"]["baselines"]["mmgbsa"]
    assert e["status"] == "skipped" and "not installed" in e["reason"]


def test_mmgbsa_with_packages_but_no_engine_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(bl.HOOK, "mmgbsa_available", lambda: (True, ""))
    rec, _ = run_plan(make(tmp_path, "\n[baseline]\nmmgbsa = true\nlogp = false\n"), measure=fill)
    assert "no in-process MM/GBSA engine" in rec.results["baseline"]["baselines"]["mmgbsa"]["reason"]


def test_mmgbsa_runs_through_an_injected_engine_and_is_compared(tmp_path, monkeypatch):
    monkeypatch.setattr(bl.HOOK, "mmgbsa_available", lambda: (True, ""))
    monkeypatch.setattr(bl, "MMGBSA_ENGINE", lambda plan: {k: DDE[k] * 0.5 for k in ANALOGUES})
    labels = {k: DDE[k] for k in ANALOGUES}
    plan = make(tmp_path, '\n[baseline]\nlabels = "labels.csv"\nmmgbsa = true\nlogp = false\nn_boot = 500\n', labels)
    rec, _ = run_plan(plan, measure=fill)
    e = rec.results["baseline"]["baselines"]["mmgbsa"]
    assert e["status"] == "computed" and e["rho_baseline"] == pytest.approx(1.0) and e["delta_rho"] == pytest.approx(0)


def test_mmgbsa_file_needs_no_packages(tmp_path):
    (tmp_path / "mm.csv").write_text("name,ddG_MM\n" + "".join(f"{k},{DDE[k]}\n" for k in ANALOGUES))
    labels = {k: DDE[k] for k in ANALOGUES}
    plan = make(
        tmp_path, '\n[baseline]\nlabels = "labels.csv"\nmmgbsa_file = "mm.csv"\nlogp = false\nn_boot = 500\n', labels
    )
    rec, _ = run_plan(plan, measure=fill)
    assert rec.results["baseline"]["baselines"]["mmgbsa"]["status"] == "computed"


def test_the_labels_file_is_part_of_the_input_digest(tmp_path):
    a = make(tmp_path, '\n[baseline]\nlabels = "labels.csv"\n', {k: DDE[k] for k in ANALOGUES})
    b = make(tmp_path, '\n[baseline]\nlabels = "labels.csv"\n', {k: -DDE[k] for k in ANALOGUES})
    assert a.inputs["baseline"]["labels"]["sha256"] != b.inputs["baseline"]["labels"]["sha256"]


def test_labels_file_parsing(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text("# comment\nname,value\n a , 1.5\nb\t-2\n\nc 3\n")
    assert bl.read_values(p, "x") == {"a": 1.5, "b": -2.0, "c": 3.0}
    for bad, msg in (
        ("a,1\na,2\n", "appears twice"),
        ("a,1\nb,x\n", "not a number"),
        ("a,nan\n", "not finite"),
        ("\n", "no "),
    ):
        p.write_text(bad)
        with pytest.raises(ValueError, match=msg):
            bl.read_values(p, "x")


@pytest.mark.parametrize(
    "section, msg",
    [
        ("logp = false", "asks for no baseline"),
        ("n_boot = 10", "at least 200"),
        ("logp = 1", "true or false"),
        ("labels = 3", "must be a path"),
        ("bogus = 1", "unknown key"),
    ],
)
def test_config_validation(tmp_path, section, msg):
    p = tmp_path / "c.toml"
    p.write_text(BASE + f"\n[baseline]\n{section}\n")
    with pytest.raises(ConfigError, match=msg):
        validate_config(load_config(p))


def test_the_effect_of_a_fast_logp_is_not_the_cut(tmp_path):
    """ddE ranks 4-NO2 worst and logP would rank it differently: the cut still follows ddE only."""
    off, _ = run_plan(make(tmp_path), measure=fill)
    assert off.results["cut"]["survivors"] == ["4-F", "4-Br"]
    assert not math.isnan(logp("OC(=O)c1ccccc1"))
