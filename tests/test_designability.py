"""`Designability`: the self-pair anchor, drift direction, and what it refuses to claim.

The tier answers "would a chemist make this?", which no other tier here asks. Its one hard invariant
is the self-pair anchor: a candidate paired with ITSELF must give exactly 0.0 for every drift. That
is what catches the only way these numbers could be silently wrong -- a reference mixed up with a
candidate, which would produce plausible-looking nonsense rather than an error.
"""

from __future__ import annotations

import numpy as np
import pytest

from smeltery.model import Candidate, Pose
from smeltery.tiers import Designability

IBUPROFEN = "CC(C)Cc1ccc(cc1)C(C)C(=O)O"
#: The same molecule with a long greasy ester: heavier, greasier, more rotatable bonds. The shape of
#: substituent a fixed-geometry dE_int rewards (more atoms) and a chemist does not.
IBUPROFEN_C8_ESTER = "CC(C)Cc1ccc(cc1)C(C)C(=O)OCCCCCCCC"
DRIFTS = ("d_mw", "d_logp", "d_tpsa", "d_qed", "d_sa", "d_heavy")


def _c(name: str, smiles: str, n_poses: int = 2) -> Candidate:
    pose = Pose(("C",), np.zeros((1, 3)))
    return Candidate(name, smiles, [pose] * n_poses)


def test_self_pair_drift_is_exactly_zero():
    """The anchor. Not approximately zero: the same SMILES must give the same number, bit for bit."""
    parent = _c("parent", IBUPROFEN)
    same = _c("same", IBUPROFEN)
    Designability().run([same], {"parent": parent})
    for q in DRIFTS:
        assert same.per_pose[q] == [0.0, 0.0], f"{q} must be exactly 0.0 for a self-pair"


def test_a_bigger_greasier_analogue_drifts_the_way_a_chemist_would_expect():
    parent = _c("parent", IBUPROFEN)
    ester = _c("ester", IBUPROFEN_C8_ESTER)
    Designability().run([ester], {"parent": parent})
    assert ester.per_pose["d_heavy"][0] == 8  # eight added heavy atoms, counted not estimated
    assert ester.per_pose["d_mw"][0] > 90.0
    assert ester.per_pose["d_logp"][0] > 2.0  # markedly greasier
    assert ester.per_pose["d_tpsa"][0] < 0.1  # an ester hides the acid's polar surface


def test_every_declared_quantity_is_written_once_per_pose():
    """Values are constant across poses by construction; the contract is still one entry per pose."""
    parent = _c("parent", IBUPROFEN)
    cand = _c("a", IBUPROFEN_C8_ESTER, n_poses=3)
    tier = Designability()
    tier.run([cand], {"parent": parent})
    assert set(cand.per_pose) == set(tier.produces())
    for q, values in cand.per_pose.items():
        assert len(values) == 3, q
        assert len(set(values)) == 1, f"{q} must be constant across poses"


def test_a_candidate_with_no_poses_still_gets_one_value():
    """A descriptor tier must not require a pose tier to have run first."""
    parent = Candidate("parent", IBUPROFEN)
    cand = Candidate("a", IBUPROFEN_C8_ESTER)
    tier = Designability()
    tier.run([cand], {"parent": parent})
    assert cand.per_pose["d_heavy"] == [8]


def test_parent_properties_are_recorded_in_settings_after_a_run():
    """A drift is unreadable without the absolute value it came from, so the reference is kept."""
    tier = Designability()
    assert tier.settings()["parent_properties"] is None
    parent = _c("parent", IBUPROFEN)
    tier.run([_c("a", IBUPROFEN)], {"parent": parent})
    props = tier.settings()["parent_properties"]
    assert props["mw"] == pytest.approx(206.28, abs=0.1)  # ibuprofen, 206.29 g/mol
    assert props["heavy_atoms"] == 15


def test_sa_score_is_on_its_documented_scale():
    """1 (easy) to 10 (hard). Ibuprofen is an easy molecule and must land near the bottom."""
    parent = _c("parent", IBUPROFEN)
    cand = _c("a", IBUPROFEN)
    Designability().run([cand], {"parent": parent})
    sa = cand.per_pose["sa_score"][0]
    assert 1.0 <= sa <= 10.0
    assert sa < 4.0, f"ibuprofen should be easy to make, got SA {sa}"


def test_floor_is_none_for_every_quantity_and_unknown_ones_raise():
    """These are descriptors and heuristics, so no floor may be invented for them."""
    tier = Designability()
    for q in tier.produces():
        assert tier.systematic_floor(q) is None
    # Every tier raises a plain KeyError here (`_unknown_quantity`), not a bespoke type.
    with pytest.raises(KeyError, match="does not produce"):
        tier.systematic_floor("binding_affinity")


def test_cost_declines_to_guess():
    tier = Designability()
    cost = tier.estimate_cost([_c("a", IBUPROFEN)])
    assert cost["predicted"] is None and "unmeasured" in cost["basis"].lower()


def test_an_unparseable_smiles_is_an_error_not_a_silent_skip():
    parent = _c("parent", IBUPROFEN)
    with pytest.raises(ValueError, match="unparseable SMILES"):
        Designability().run([_c("bad", "this is not a molecule")], {"parent": parent})


def test_alerts_are_not_computed_here_and_the_settings_say_where_they_live():
    """Duplicating RdkitAlertProvider would give two numbers that could disagree."""
    tier = Designability()
    assert not {"pains", "brenk", "nih"} & set(tier.produces())
    assert "RdkitAlertProvider" in tier.settings()["alerts"]
