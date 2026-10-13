"""Valence salvage, fragment trimming and forced scaffold bond orders, on synthetic molecules and the fixture."""

from collections import Counter

import numpy as np
import pytest
from rdkit import Chem

from smeltery.generate.sbdd_post import FilterConfig, process_samples, salvage_valence


def _crowded_oxygen(lengths=(1.4, 1.5, 2.1)):
    """An O with three single bonds, to carbons at the given distances; atoms 1-3 are carbons."""
    mol = Chem.RWMol()
    for symbol in "OCCC":
        mol.AddAtom(Chem.Atom(symbol))
    for k in (1, 2, 3):
        mol.AddBond(0, k, Chem.BondType.SINGLE)
    conf = Chem.Conformer(4)
    conf.SetAtomPosition(0, (0.0, 0.0, 0.0))
    for k, length in enumerate(lengths, start=1):
        angle = 2 * np.pi * k / 3
        conf.SetAtomPosition(k, (length * np.cos(angle), length * np.sin(angle), 0.0))
    mol.AddConformer(conf)
    return mol.GetMol()


def test_the_longest_bond_of_an_over_valent_atom_is_the_one_dropped():
    out, dropped = salvage_valence(_crowded_oxygen(), protected=set(), max_drops=2)
    assert dropped == 1
    assert out.GetBondBetweenAtoms(0, 3) is None  # the 2.1 A one
    assert out.GetBondBetweenAtoms(0, 1) is not None and out.GetBondBetweenAtoms(0, 2) is not None
    Chem.SanitizeMol(out)  # salvage returns a molecule that sanitizes


def test_a_protected_bond_is_never_dropped_even_if_it_is_the_longest():
    out, dropped = salvage_valence(_crowded_oxygen(), protected={frozenset((0, 3))}, max_drops=2)
    assert dropped == 1
    assert out.GetBondBetweenAtoms(0, 3) is not None and out.GetBondBetweenAtoms(0, 2) is None  # next longest


def test_nothing_is_dropped_when_every_bond_is_protected_or_the_budget_is_spent():
    every = {frozenset((0, k)) for k in (1, 2, 3)}
    out, dropped = salvage_valence(_crowded_oxygen(), protected=every, max_drops=5)
    assert dropped == 0 and out.GetNumBonds() == 3
    out, dropped = salvage_valence(_crowded_oxygen(), protected=set(), max_drops=0)
    assert dropped == 0 and out.GetNumBonds() == 3
    with pytest.raises(Chem.AtomValenceException):
        Chem.SanitizeMol(out)  # left for the caller to reject, not hidden


def test_a_valid_molecule_comes_back_untouched():
    mol = Chem.MolFromSmiles("COC")
    conf = Chem.Conformer(3)
    mol.AddConformer(conf)
    out, dropped = salvage_valence(mol, protected=set(), max_drops=3)
    assert dropped == 0 and out.GetNumBonds() == 2


def test_salvage_bonds_cannot_be_negative():
    with pytest.raises(ValueError, match="salvage_bonds"):
        FilterConfig(salvage_bonds=-1)


def _run(raw_samples, scaffold_spec, **kw):
    stats: dict[str, int] = {}
    candidates, rejected = process_samples(raw_samples, scaffold_spec, FilterConfig(**kw), stats=stats)
    return candidates, dict(Counter(r.reason for r in rejected)), stats


def test_every_repair_is_off_by_default(raw_samples, scaffold_spec):
    candidates, reasons, stats = _run(raw_samples, scaffold_spec)
    assert len(candidates) == 1 and reasons == {"unsanitizable": 12, "scaffold_lost": 3} and stats == {}


def test_salvage_alone_turns_unsanitizable_output_into_disconnected_output(raw_samples, scaffold_spec):
    candidates, reasons, stats = _run(raw_samples, scaffold_spec, salvage_bonds=4)
    assert len(candidates) == 1 and reasons == {"disconnected": 11, "scaffold_lost": 4} and stats == {}


def test_salvage_with_trimming_keeps_the_piece_that_holds_the_scaffold(raw_samples, scaffold_spec):
    candidates, reasons, stats = _run(raw_samples, scaffold_spec, salvage_bonds=4, trim_stray_fragments=True)
    assert len(candidates) == 4 and reasons == {"scaffold_lost": 12}
    assert stats == {"salvaged": 3, "bonds_dropped": 6, "trimmed": 3}


def test_forcing_the_scaffold_orders_on_top_of_both_keeps_the_scaffold_in_most_of_the_fixture(
    raw_samples, scaffold_spec
):
    candidates, reasons, stats = _run(
        raw_samples, scaffold_spec, salvage_bonds=4, trim_stray_fragments=True, scaffold_orders=True
    )
    assert len(candidates) == 13 and reasons == {"unsanitizable": 2, "forbidden_substructure": 1}
    assert stats == {"salvaged": 13, "bonds_dropped": 32, "trimmed": 10}
    for cand in candidates:
        mol = Chem.MolFromSmiles(cand.smiles)
        assert mol is not None and len(Chem.GetMolFrags(mol)) == 1
        assert mol.HasSubstructMatch(scaffold_spec.scaffold)  # the exact scaffold, bond orders included


def test_forcing_the_scaffold_orders_without_salvage_does_not_help(raw_samples, scaffold_spec):
    candidates, reasons, _ = _run(raw_samples, scaffold_spec, scaffold_orders=True)
    assert candidates == [] and reasons == {"unsanitizable": 16}  # the 0-of-16 that ruled this policy out alone
