"""Acceptance tests for M2-05 (#12): generators fit the Candidate/pairing model.

1. one pairing implementation, and the self-pair anchor is exactly 0.0 per pose through it;
2. the MCS gate refuses rather than degrades;
3. substitution proposals are keyed by (substituent, SITE).
"""

from types import SimpleNamespace

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import rdFMCS

from smeltery import Candidate, paired_delta
from smeltery.generate import (
    Isomer,
    SubstitutionProposal,
    paired_ddE,
    pairs_from_candidates,
    proposals_by_key,
    propose_substitutions,
    relative_descriptors,
    substituent_scan,
    to_candidates,
)
from smeltery.tiers import NoCommonCoreError, PairedPoses

ACID = "OC(=O)c1ccccc1"
ORTHO, META, PARA = "OC(=O)c1ccccc1F", "OC(=O)c1cccc(F)c1", "OC(=O)c1ccc(F)cc1"


def _canon(smi):
    return Chem.CanonSmiles(smi)


def _energy(symbols, coords):
    """A deterministic function of the geometry that varies with pose (distance to a fixed point)."""
    z = {"H": 1.0, "C": 6.0, "N": 7.0, "O": 8.0, "F": 9.0}
    c = np.array([5.0, 0.0, 0.0])
    return float(sum(z[s] / np.linalg.norm(np.asarray(x) - c) for s, x in zip(symbols, coords)))


def _run_pairing(analogue_smiles, name="analogue", n_poses=4):
    parent, ana = Candidate("parent", ACID), Candidate(name, analogue_smiles)
    tier = PairedPoses(n_poses=n_poses)
    tier.run([parent, ana], {"parent": parent})
    return parent, ana, tier


# ---- criterion 1: exactly one pairing implementation; self-anchor is 0.0 ------


def test_self_pair_anchor_is_exactly_zero_per_pose_through_the_one_pairing():
    parent, twin, tier = _run_pairing(ACID, "twin")
    pairs = pairs_from_candidates(parent, twin, tier.scaffold_maps["twin"])
    assert len(tier.scaffold_maps["twin"]) == len(twin.poses[0].symbols), "self-pair must map every atom"
    res = paired_ddE(pairs, _energy)
    assert res.differences == [0.0] * 4, res.differences  # exact, not approx
    assert [p.scaffold_max_dev for p in pairs] == [0.0] * 4
    # the funnel's own paired_delta, fed the same per-pose energies, agrees
    for c in (parent, twin):
        c.per_pose["e"] = [_energy(p.symbols, p.coords_ang) for p in c.poses]
    assert paired_delta(parent, twin, "e").per_pose == (0.0,) * 4
    # not vacuous: the energy really does vary across poses
    assert np.ptp(parent.per_pose["e"]) > 1e-3


def test_self_pair_negative_control_a_real_analogue_is_not_zero():
    parent, fluoro, tier = _run_pairing(PARA, "4-F")
    res = paired_ddE(pairs_from_candidates(parent, fluoro, tier.scaffold_maps["4-F"]), _energy)
    assert all(d != 0.0 for d in res.differences)


def test_pairing_lives_in_exactly_one_module():
    import pathlib

    import smeltery

    src = pathlib.Path(smeltery.__file__).parent
    mcs_users = sorted(str(f.relative_to(src)) for f in src.rglob("*.py") if "FindMCS(" in f.read_text())
    assert mcs_users == ["tiers.py"]
    assert not any(
        hasattr(__import__("smeltery.generate", fromlist=["x"]), n)
        for n in ("pair_poses_by_scaffold", "relax_substituent")
    )


# ---- criterion 2: the MCS gate refuses, never degrades ------------------------


def test_gate_refuses_a_pair_with_no_common_core():
    parent, nn = Candidate("parent", ACID), Candidate("nn", "N#N")
    with pytest.raises(NoCommonCoreError, match="no common core"):
        PairedPoses(n_poses=2).run([parent, nn], {"parent": parent})
    assert nn.poses == [], "a refused pair must not leave partial poses behind"


def test_gate_refuses_a_core_too_small_to_anchor_a_scaffold():
    parent, eth = Candidate("parent", ACID), Candidate("eth", "CCO")
    with pytest.raises(NoCommonCoreError, match="heavy atom"):
        PairedPoses(n_poses=2).run([parent, eth], {"parent": parent})


def test_gate_refuses_when_the_mcs_search_timed_out(monkeypatch):
    real = rdFMCS.FindMCS
    monkeypatch.setattr(rdFMCS, "FindMCS", lambda *a, **k: SimpleNamespace(canceled=True, smartsString="", numAtoms=0))
    parent, fluoro = Candidate("parent", ACID), Candidate("f", PARA)
    with pytest.raises(NoCommonCoreError, match="timed out"):
        PairedPoses(n_poses=2).run([parent, fluoro], {"parent": parent})
    assert rdFMCS.FindMCS is not real  # the patch was what fired


def test_gate_refuses_a_degraded_self_mapping(monkeypatch):
    """The campaign failure: a molecule paired with ITSELF whose MCS covers only part of it."""
    partial = SimpleNamespace(canceled=False, smartsString="c1ccccc1", numAtoms=6)
    monkeypatch.setattr(rdFMCS, "FindMCS", lambda *a, **k: partial)
    parent, twin = Candidate("parent", ACID), Candidate("twin", ACID)
    with pytest.raises(NoCommonCoreError, match="degraded self-mapping"):
        PairedPoses(n_poses=2).run([parent, twin], {"parent": parent})


def test_gate_negative_control_a_legitimate_analogue_passes():
    _, fluoro, tier = _run_pairing(PARA, "4-F", n_poses=2)
    assert len(fluoro.poses) == 2
    assert sum(a != "H" for a in fluoro.poses[0].symbols) == 10  # all heavy atoms present
    assert len(tier.scaffold_maps["4-F"]) == len(fluoro.poses[0].symbols) - 1  # all but the swapped substituent H... F


# ---- criterion 3: proposals are keyed by (substituent, SITE) ---------------------


def test_ortho_meta_para_give_different_keys():
    props = [p for p in propose_substitutions(ACID, {"F": "F"}) if not p.is_parent]
    assert len(props) == 3, "5 aryl CH sites collapse to 3 symmetry classes"
    assert len({p.key for p in props}) == 3
    assert {p.label for p in props} == {"F"}, "keyed by substituent alone these would all collide"
    assert {p.smiles for p in props} == {_canon(ORTHO), _canon(META), _canon(PARA)}
    assert len({p.site for p in props}) == 3
    assert len(proposals_by_key(props)) == 3


def test_descriptor_tuples_are_site_blind_but_keys_are_not():
    """The note on #12: a descriptor per substituent cannot see WHERE a group goes."""
    d = {s: relative_descriptors(_canon(s), ACID) for s in (ORTHO, META, PARA)}
    # equal up to float summation order (different atom order, same molecule class)
    assert d[ORTHO] == pytest.approx(d[META], abs=1e-9) and d[META] == pytest.approx(d[PARA], abs=1e-9)
    assert d[PARA] != relative_descriptors(_canon("OC(=O)c1ccc(Cl)cc1"), ACID), "vacuity guard: F and Cl differ"
    keys = {p.smiles: p.key for p in propose_substitutions(ACID, {"F": "F"}) if not p.is_parent}
    assert len(set(keys.values())) == 3


def test_substituent_only_keying_is_detectably_wrong():
    """Negative control: collapsing to the substituent alone loses two of three analogues."""
    props = propose_substitutions(ACID, {"F": "F", "Cl": "Cl"})
    assert len({p.label for p in props if not p.is_parent}) == 2
    assert len({p.key for p in props if not p.is_parent}) == 6


def test_a_repeated_key_is_refused_not_overwritten():
    props = propose_substitutions(ACID, {"F": "F"})
    with pytest.raises(ValueError, match="duplicate proposal key"):
        proposals_by_key(props + [props[1]])


def test_symmetry_equivalent_sites_share_a_key():
    iso = substituent_scan("c1ccccc1", {"F": "F"})
    assert len(iso) == 1 and iso[0].key[0] == "F"
    ortho_sites = [i for i in substituent_scan(ACID, {"F": "F"}) if i.canonical == _canon(ORTHO)]
    assert len(ortho_sites) == 1, "the two ortho CH are one site"


def test_site_keys_do_not_depend_on_how_the_parent_is_spelled():
    a = {p.smiles: p.key for p in propose_substitutions("OC(=O)c1ccccc1", {"F": "F"})}
    b = {p.smiles: p.key for p in propose_substitutions("c1ccccc1C(=O)O", {"F": "F"})}
    assert a == b


def test_only_substitutional_isomers_have_a_key():
    with pytest.raises(ValueError, match="no \\(substituent, site\\) key"):
        Isomer("c1ccccc1", "parent", "none", "c1ccccc1").key  # noqa: B018 -- the access itself raises


def test_keyed_proposals_flow_through_the_pairing_tier_as_candidates():
    """The generators fit the Candidate model: ortho/meta/para each pair with the parent."""
    props = propose_substitutions(ACID, {"F": "F"})
    cands = to_candidates(props)
    assert cands[0].name == "parent" and len({c.name for c in cands}) == 4
    tier = PairedPoses(n_poses=2)
    tier.run(cands, {"parent": cands[0]})
    for c in cands[1:]:
        assert len(c.poses) == 2
        res = paired_ddE(pairs_from_candidates(cands[0], c, tier.scaffold_maps[c.name]), _energy)
        assert all(p.scaffold_max_dev == 0.0 for p in pairs_from_candidates(cands[0], c, tier.scaffold_maps[c.name]))
        assert res.n_pairs == 2
    with pytest.raises(ValueError, match="start with the parent"):
        to_candidates(props[1:])


def test_substitution_proposal_default_site_is_none_for_the_parent_row():
    parent = propose_substitutions(ACID, {})[0]
    assert parent.key == ("parent", None)
    assert isinstance(parent, SubstitutionProposal)
