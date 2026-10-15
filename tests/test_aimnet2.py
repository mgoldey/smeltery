"""The AIMNet2 PotentialProvider (issue #22 (b)) and its committed measurement.

Tests that need the `aimnet2` extra (aimnet, torch, and the model weights) skip with a reason when it is not
installed. The flag, missing-backend and record tests run everywhere. Numbers: docs/benchmarks/aimnet2_anion.md.
"""

import json
import re
import sys
from importlib import util
from pathlib import Path

import numpy as np
import pytest

from smeltery import PointCharge
from smeltery.model import Pose
from smeltery.providers import PocketContextError, PotentialProvider, UnsupportedChargeStateError, evaluate
from smeltery.providers.aimnet2 import MEASURED_CHARGE_STATES, Aimnet2
from smeltery.structure import MissingBackend

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "benchmarks" / "aimnet2" / "charge_probe.json"
DOC = ROOT / "docs" / "benchmarks" / "aimnet2_anion.md"
BAR = 5.0

HAVE = all(util.find_spec(m) is not None for m in ("aimnet", "torch"))
needs_aimnet = pytest.mark.skipif(not HAVE, reason="aimnet2 extra not installed: pip install 'smeltery[aimnet2]'")

SYMBOLS = ("C", "C", "O", "O", "H", "H", "H")  # acetate, a fixed non-equilibrium geometry (Angstrom)
XYZ = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.5, 0.0, 0.0],
        [2.1, 1.05, 0.0],
        [2.1, -1.15, 0.2],
        [-0.4, 1.0, 0.0],
        [-0.4, -0.5, 0.9],
        [-0.4, -0.5, -0.9],
    ]
)
POSE = Pose(SYMBOLS, XYZ)
CHARGES = [PointCharge(-0.5, (4.0, 0.0, 0.0))]


def test_the_flags_are_readable_without_the_backend_and_exclude_the_anion():
    assert Aimnet2.supports_external_charges is False
    assert Aimnet2.license_id == "MIT"
    assert Aimnet2.supported_charge_states == MEASURED_CHARGE_STATES == frozenset({0, 1})
    assert -1 not in Aimnet2.supported_charge_states  # methoxide pair missed the bar: see the measurement doc


def test_a_missing_backend_names_the_extra(monkeypatch):
    for mod in ("aimnet", "aimnet.calculators"):
        monkeypatch.setitem(sys.modules, mod, None)
    with pytest.raises(MissingBackend, match=r"smeltery\[aimnet2\]"):
        Aimnet2()


def test_the_committed_record_obeys_the_plan_rule_and_the_flag_matches():
    # The plan's rule: a charge state is supported only if every pair and isodesmic error involving it is within BAR.
    rec = json.loads(RECORD.read_text())
    assert rec["bar_kcal_mol"] == BAR
    species = rec["species"]
    ok: dict[int, bool] = {0: abs(rec["pairs"]["dimethyl ether - ethanol"]["error"]) <= BAR}
    for name, pair in rec["pairs"].items():
        charged = name.split(" - ")[0]
        q = species[charged]["charge"]
        if q != 0:
            ok[q] = ok.get(q, True) and abs(pair["error"]) <= BAR
    for rxn, v in rec["isodesmic"].items():
        for part in re.split(r" \+ | -> ", rxn):
            q = species[part]["charge"]
            if q != 0:
                ok[q] = ok.get(q, True) and abs(v["error"]) <= BAR
    assert {q for q, good in ok.items() if good} == set(MEASURED_CHARGE_STATES)


def test_the_doc_quotes_the_recorded_numbers():
    rec = json.loads(RECORD.read_text())
    text = DOC.read_text()
    for name, pair in rec["pairs"].items():
        assert f"{pair['error']:+.1f}" in text or f"{pair['error']:+.2f}" in text, name
    for rxn, v in rec["isodesmic"].items():
        assert f"{v['error']:+.1f}" in text, rxn
    assert rec["charge_used"]["differs"] is True
    fd = rec["force_fd"]["relative_error"]
    assert all(v > rec["force_fd"]["bar"] for v in fd.values())  # the force bar is recorded as not met
    assert "not met" in text


@needs_aimnet
def test_it_is_a_potential_provider_and_records_versions():
    p = Aimnet2()
    assert isinstance(p, PotentialProvider)
    s = p.settings()
    assert s["aimnet"] and s["torch"] and s["license_id"] == "MIT" and s["supported_charge_states"] == [0, 1]


@needs_aimnet
def test_in_pocket_use_raises_and_the_anion_is_refused():
    p = Aimnet2()
    with pytest.raises(PocketContextError, match="vacuum result"):
        evaluate(p, POSE, CHARGES)
    with pytest.raises(PocketContextError):
        p.energy_and_forces(POSE, CHARGES)
    with pytest.raises(UnsupportedChargeStateError):
        evaluate(p, POSE, None, -1)
    assert evaluate(p, POSE, None, 0).energy < 0


@needs_aimnet
def test_the_charge_input_changes_the_energy_unlike_ani2x():
    p = Aimnet2(supported_charge_states=frozenset({-1, 0}))
    e_anion = p.energy_and_forces(POSE, charge=-1).energy
    e_neutral = p.energy_and_forces(POSE, charge=0).energy
    assert abs(e_anion - e_neutral) > 1.0  # kcal/mol; recorded difference for acetate coordinates is ~121
