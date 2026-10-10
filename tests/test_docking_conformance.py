"""VinaProvider against the shared conformance suite (#25), engine checks included.

Needs the `docking` extra (vina, meeko); skips with the install command without it, and CI's
`docking` job fails if this skips. The static checks (licence, unit, settings) also run without
the engine in `test_provider_conformance.py`.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from smeltery.docking import Box, VinaProvider
from smeltery.providers.conformance import DockingCase, check_names, run_check

_MISSING = [m for m in ("vina", "meeko", "rdkit") if importlib.util.find_spec(m) is None]
pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=f"needs the docking extra: pip install 'smeltery[docking]' ({', '.join(_MISSING)} missing)",
)

DATA = pathlib.Path(__file__).parent / "data"
BOX = Box((0.4, -0.1, -0.1), (22.0, 16.0, 16.0))  # covers the whole pentapeptide, as in test_docking_real_vina


def _case(**kw) -> DockingCase:
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    assert AllChem.EmbedMolecule(mol, randomSeed=0xF00D) == 0
    AllChem.MMFFOptimizeMolecule(mol)
    return DockingCase(
        lambda: VinaProvider(n_poses=3),
        mol,
        DATA / "pocket_pep5.pdbqt",
        BOX,
        unanswerable_receptor=DATA / "does_not_exist.pdbqt",
        score_upper_bound=0.0,  # a pocket of real residues must give a binding (negative) score
        **kw,
    )


@pytest.mark.parametrize("check", check_names("docking"))
def test_vina_provider_conforms(check):
    run_check("docking", check, _case())


def test_the_docking_anchor_can_fail():
    class Unsorted(VinaProvider):
        def dock(self, *a, **k):
            r = super().dock(*a, **k)
            r.scores.reverse()
            r.poses.reverse()
            return r

    class Fabricates(VinaProvider):
        def dock(self, mol, receptor, box, seed, exhaustiveness=None):
            return super().dock(mol, DATA / "pocket_pep5.pdbqt", box, seed, exhaustiveness)  # ignores the receptor

    class Drifts(VinaProvider):
        calls = 0

        def dock(self, *a, **k):
            r = super().dock(*a, **k)
            type(self).calls += 1
            r.scores[:] = [x + type(self).calls * 1e-3 for x in r.scores]  # same seed, different answer
            return r

    class WrongAtoms(VinaProvider):
        def dock(self, *a, **k):
            from smeltery.model import Pose

            r = super().dock(*a, **k)
            r.poses[:] = [Pose(p.symbols[::-1], p.coords_ang) for p in r.poses]  # H first: not the input's order
            return r

    c = _case()
    for cls, check, text in (
        (Unsorted, "anchor", "best"),
        (Fabricates, "none_not_fabricated", "impossible"),
        (Drifts, "anchor", "not reproducible"),
        (WrongAtoms, "anchor", "atoms differ"),
    ):
        bad = DockingCase(
            lambda cls=cls: cls(n_poses=3), c.mol, c.receptor, c.box, c.unanswerable_receptor, c.seed, 0.0
        )
        with pytest.raises(AssertionError, match=text):
            run_check("docking", check, bad)
