# ruff: noqa: E501
"""Ligand-side charge-model surrogate (issue #29): the arithmetic, on synthetic data. No quantum chemistry.

The committed production file is tested in test_plb_ligand_charges_results.py.

Mutation checks done by hand when this file was written (each made at least one test below fail, then reverted):
dropping the Coulomb constant, using q instead of q*phi, counting the parent among the analogues, comparing pair order with
`>` instead of `<` on the product, swapping the agree thresholds, and making `FieldInteraction.systematic_floor` return a number.
"""

from __future__ import annotations

import numpy as np
import pytest

from smeltery import plb_ligand_charges as lc
from smeltery.model import HARTREE_TO_KCAL
from smeltery.tiers import FieldInteraction


def test_coulomb_constant_is_the_textbook_value():
    assert lc.COULOMB_KCAL_ANG == pytest.approx(332.0637, abs=2e-4)  # kcal A / (mol e^2)


def test_field_potential_one_charge_at_one_angstrom():
    phi = lc.field_potential([[0.0, 0.0, 0.0]], [1.0], [[1.0, 0.0, 0.0]])
    assert phi[0] == pytest.approx(lc.COULOMB_KCAL_ANG)
    assert lc.field_potential([[0.0, 0.0, 0.0]], [1.0], [[2.0, 0.0, 0.0]])[0] == pytest.approx(lc.COULOMB_KCAL_ANG / 2)


def test_field_potential_refuses_coincident_charge():
    with pytest.raises(ValueError):
        lc.field_potential([[0.0, 0.0, 0.0]], [1.0], [[0.0, 0.0, 0.0]])


def test_es_energy_is_q_dot_phi_and_checks_shape():
    assert lc.es_energy([1.0, -0.5], [10.0, 4.0]) == pytest.approx(8.0)
    with pytest.raises(ValueError):
        lc.es_energy([1.0], [1.0, 2.0])


def test_dipole_in_a_uniform_like_field_matches_hand_value():
    # +1/-1 charges 1 A apart along x, one pocket charge Q = 1 at x = 3: phi = k/(3-x)
    xyz = [[0.0, 0, 0], [1.0, 0, 0]]
    phi = lc.field_potential(xyz, [1.0], [[3.0, 0.0, 0.0]])
    e = lc.es_energy([1.0, -1.0], phi)
    assert e == pytest.approx(lc.COULOMB_KCAL_ANG * (1 / 3 - 1 / 2))


def test_paired_ddE_excludes_parent_and_pairs_by_pose():
    names = ["p", "a", "b"]
    E = np.array([[1.0, 2.0, 3.0], [2.0, 4.0, 6.0], [1.0, 2.0, 3.0]])
    out = lc.paired_ddE_es(E, names, "p")
    assert set(out) == {"a", "b"}
    assert out["a"]["mean"] == pytest.approx(2.0)  # (1 + 2 + 3) / 3
    assert out["b"]["mean"] == 0.0 and out["b"]["sem"] == 0.0
    assert out["a"]["sem"] == pytest.approx(1.0 / np.sqrt(3))  # sd of [1,2,3] is 1


def test_pair_comparison_counts_flips_and_floor():
    a = {"x": 1.0, "y": 2.0, "z": -1.0, "w": 3.0}
    b = {"x": -1.0, "y": 2.5, "z": -2.0, "w": 2.0}  # x flips sign; order of (x,y),(x,w),(x,z) etc.
    pc = lc.pair_comparison({"a": a, "b": b}, "a", "b")
    assert pc["n"] == 4 and pc["n_pairs"] == 6
    assert pc["n_sign_flips"] == 1
    # pairs: (w,x) a:+ b:+ ; (w,y) a:+ b:- flip ; (w,z) ++ ; (x,y) -- ; (x,z) a:+ b:+ ; (y,z) ++
    assert pc["n_pair_order_flips"] == 1
    d = {n: b[n] - a[n] for n in a}
    assert pc["floor_kcal_mol"] == pytest.approx(max(d.values()) - min(d.values()))


def test_identical_models_agree_exactly():
    v = {"m": {"a": 1.0, "b": 2.0, "c": -3.0}}
    v["n"] = dict(v["m"])
    pc = lc.pair_comparison(v, "m", "n")
    assert (pc["n_sign_flips"], pc["n_pair_order_flips"], pc["floor_kcal_mol"]) == (0, 0, 0.0)
    assert pc["spearman"] == pytest.approx(1.0)
    assert lc.verdict(pc) == "agree"


def test_verdict_thresholds_are_the_plans():
    base = {"spearman": 0.95, "n_sign_flips": 1, "n_pair_order_flips": 6}
    assert lc.verdict(base) == "agree"
    assert lc.verdict({**base, "spearman": 0.89}) == "between"
    assert lc.verdict({**base, "n_sign_flips": 2}) == "between"
    assert lc.verdict({**base, "n_pair_order_flips": 7}) == "between"
    assert lc.verdict({**base, "spearman": 0.69}) == "disagree"
    assert lc.verdict({**base, "n_sign_flips": 3}) == "disagree"
    assert lc.verdict({**base, "n_pair_order_flips": 17}) == "disagree"
    assert (lc.AGREE, lc.DISAGREE) == (
        {"spearman_min": 0.90, "sign_flips_max": 1, "pair_flips_max": 6},
        {"spearman_below": 0.70, "sign_flips_min": 3, "pair_flips_min": 17},
    )


def test_pair_comparison_refuses_mismatched_ligands():
    with pytest.raises(ValueError):
        lc.pair_comparison({"a": {"x": 1.0, "y": 2.0}, "b": {"x": 1.0, "z": 2.0}}, "a", "b")


def test_analyse_record_on_a_tiny_synthetic_record():
    names = ["p", "a", "b", "c"]
    npose = 2
    rng = np.random.default_rng(0)
    phi = {n: [list(rng.normal(size=3)) for _ in range(npose)] for n in names}
    base = {n: [list(rng.normal(size=3)) for _ in range(npose)] for n in names}
    rec = {
        "ligands": names,
        "reference": "p",
        "n_poses": npose,
        "models": ["m1", "m2", "m3"],
        "phi_kcal_mol_per_e": phi,
        "charges": {"m1": base, "m2": {n: [[1.01 * v for v in q] for q in base[n]] for n in names}, "m3": base},
        "carbonyl_atom_pairs": {n: [[0, 1]] for n in names},
        "inputs": {"net_charge": {n: 0 for n in names}},
    }
    stored = {"results": {"dE_int_kcal_mol": {"amber14": {n: [1.0 * i, 2.0 * i] for i, n in enumerate(names)}}}}
    an = lc.analyse_record(rec, stored)
    assert an["n_analogues"] == 3 and an["n_poses"] == 2
    assert set(an["pairs"]) == {"m1|m2", "m1|m3", "m2|m3"}
    assert an["pairs"]["m1|m3"]["floor_kcal_mol"] == 0.0  # identical charges
    assert an["pairs"]["m1|m2"]["floor_kcal_mol"] > 0.0
    assert an["qm_ddE_stored_amber_kcal_mol"]["a"] == pytest.approx(1.5)  # mean(1, 2)


def test_field_interaction_still_has_no_floor_and_no_ligand_charge_model():
    """The plan's non-claim: no default floor is published and the tier has no ligand-charge setting."""
    assert FieldInteraction().systematic_floor("dE_int") is None
    assert not any("charge" in k.lower() for k in FieldInteraction().settings() if k != "field")
    assert HARTREE_TO_KCAL > 0
