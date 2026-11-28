"""smeltery.floor_measure: per-system charge-model floors, pooling, and the refusal to call a default measured."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from smeltery.cli import main
from smeltery.floor import ABSOLUTE
from smeltery.floor_measure import (
    FloorEvidenceError,
    SystemFloor,
    measure_file,
    pool,
    provisional_default,
    system_floor,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "plb" / "results"
CDK2, TYK2 = RESULTS / "cdk2_sto3g.json", RESULTS / "tyk2_sto3g_charmm36.json"
stored = pytest.mark.skipif(not (CDK2.exists() and TYK2.exists()), reason="stored PLB results not present")


def synth(shifts, n_poses=4, name="s"):
    """parent + len(shifts) analogues; model b = model a + shift[i] on analogue i (parent unshifted)."""
    rng = np.random.default_rng(0)
    a = rng.normal(-20, 3, size=(len(shifts) + 1, n_poses))
    b = a.copy()
    b[1:] += np.asarray(shifts)[:, None]
    ligands = ["parent"] + [f"l{i}" for i in range(len(shifts))]
    return system_floor(name, "parent", ligands, a, b, ("m1", "m2"), source="synthetic")


def test_floor_is_range_of_shifts_and_reference_is_unshifted():
    s = synth([0.1, 0.5, -0.2, 0.3])
    assert s.floor == pytest.approx(0.7)
    assert s.n_analogues == 4 and s.n_poses == 4
    assert s.shift_sd == pytest.approx(np.std([0.1, 0.5, -0.2, 0.3], ddof=1))
    assert s.max_shift_pose_sem == pytest.approx(0.0, abs=1e-12)  # constant shift: no pose noise in it


def test_identical_models_give_zero_floor():
    assert synth([0.0, 0.0, 0.0]).floor == 0.0


def test_a_common_shift_cancels():
    assert synth([2.0, 2.0, 2.0]).floor == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("bad", ["shape", "nan", "reference", "few"])
def test_bad_inputs_refused(bad):
    a = np.zeros((4, 3))
    lig = ["p", "a", "b", "c"]
    with pytest.raises(FloorEvidenceError):
        if bad == "shape":
            system_floor("x", "p", lig, a, np.zeros((4, 2)), ("m", "n"), source="t")
        elif bad == "nan":
            system_floor("x", "p", lig, a, np.full((4, 3), np.nan), ("m", "n"), source="t")
        elif bad == "reference":
            system_floor("x", "zzz", lig, a, a, ("m", "n"), source="t")
        else:
            system_floor("x", "p", ["p", "a"], np.zeros((2, 3)), np.zeros((2, 3)), ("m", "n"), source="t")


def test_pool_statistics_and_duplicate_refusal():
    s1, s2 = synth([0.0, 1.0, 0.5], name="one"), synth([0.0, 0.4, 0.2, 0.1], name="two")
    p = pool([s1, s2])
    assert p.max_floor == pytest.approx(1.0) and p.min_floor == pytest.approx(0.4)
    assert p.spread_ratio == pytest.approx(2.5)
    df = [2, 3]
    assert p.pooled_shift_sd == pytest.approx(np.sqrt((df[0] * s1.shift_sd**2 + df[1] * s2.shift_sd**2) / 5))
    with pytest.raises(FloorEvidenceError, match="twice"):
        pool([s1, s1])
    assert p.to_dict()["kind"] == ABSOLUTE and "LOWER bound" in " ".join(p.to_dict()["caveats"])


def test_provisional_refused_below_minimum_systems():
    p = pool([synth([0.0, 1.0, 0.5], name="one"), synth([0.0, 0.9, 0.5], name="two")])
    with pytest.raises(FloorEvidenceError, match="at least 3"):
        provisional_default(p)


def test_provisional_refused_when_systems_disagree():
    p = pool([synth([0, 1.0], name="a"), synth([0, 0.2], name="b"), synth([0, 0.5], name="c")])
    with pytest.raises(FloorEvidenceError, match="disagree"):
        provisional_default(p)


def test_provisional_default_is_labelled_and_never_measured():
    p = pool([synth([0, 1.0], name="a"), synth([0, 0.8], name="b"), synth([0, 0.9], name="c")])
    prov = provisional_default(p)
    assert prov.measured is False and prov.value == pytest.approx(1.0)
    assert prov.floor.source.startswith("PROVISIONAL, NOT MEASURED")
    for name in "abc":
        assert name in prov.floor.source
    d = prov.to_dict()
    assert d["measured"] is False and len(d["evidence"]["systems"]) == 3
    with pytest.raises(AttributeError):
        prov.measured = True  # type: ignore[misc]


def test_tier_floor_stays_unmeasured():
    from smeltery.tiers import FieldInteraction

    assert FieldInteraction().systematic_floor("dE_int") is None


@stored
def test_stored_results_reproduce_limits_rows_l6_and_l11():
    c, t = measure_file(CDK2), measure_file(TYK2)
    assert c.floor == pytest.approx(1.2568478831670, abs=1e-6) and (c.n_sign_flips, c.n_pair_order_flips) == (0, 1)
    assert t.floor == pytest.approx(0.6666532779271885, abs=1e-9) and (t.n_sign_flips, t.n_pair_order_flips) == (0, 0)
    assert isinstance(c, SystemFloor) and c.source_sha256 and c.models == ("amber14", "charmm36")
    p = pool([c, t])
    assert p.spread_ratio == pytest.approx(1.885, abs=0.01)
    with pytest.raises(FloorEvidenceError, match="2 system"):  # two systems cannot make a default
        provisional_default(p)


@stored
def test_measure_file_refuses_unknown_model():
    with pytest.raises(FloorEvidenceError, match="not a gas-phase"):
        measure_file(CDK2, ("amber14", "nonesuch"))


@stored
def test_cli_measure_writes_evidence_and_refuses_provisional(tmp_path, capsys):
    out = tmp_path / "ev.json"
    assert main(["floor", "measure", str(CDK2), str(TYK2), "--json", str(out)]) == 0
    rec = json.loads(out.read_text())
    assert rec["n_systems"] == 2 and "provisional_default" not in rec
    assert main(["floor", "measure", str(CDK2), str(TYK2), "--provisional", "--json", str(out)]) == 3
    rec = json.loads(out.read_text())
    assert rec["provisional_default"] is None and "at least 3" in rec["provisional_refused"]
    assert "no provisional default" in capsys.readouterr().err


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert main(["floor", "measure", str(tmp_path / "nope.json")]) == 2
    assert "smeltery floor:" in capsys.readouterr().err
