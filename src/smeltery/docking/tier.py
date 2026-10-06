"""`Docking`: a funnel tier that proposes poses by search, via any `DockingProvider`.

It fits the existing `Tier` contract: `produces()` declares the per-pose key it
writes (`dock_score`) and its unit, `systematic_floor` is None until the
engine's systematic error is measured (so the funnel refuses to cut on it), and
`settings()` reports the provider's native settings plus the seeds and the
exhaustiveness actually used.

Seeds are the budget lever: each seed is an independent ETKDG embedding of the
ligand AND an independent search seed, and the poses of all seeds are pooled.
Measured on a campaign target (ferric RESULTS.md M11), 8x more exhaustiveness
moved the mean redock RMSD less than the seed-to-seed SEM, so the default is
exhaustiveness 4 and effort is added by adding seeds.

`ctx` keys: `receptor` (what the provider takes: a PDBQT path for Vina) and
`box` (a `Box`). A missing key is an explained error, not a KeyError.
"""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import AllChem

from ..model import Candidate
from .base import DEFAULT_EXHAUSTIVENESS, Box, DockingProvider


class DockingError(RuntimeError):
    """A candidate could not be docked (bad ligand, missing context, search failed)."""


class Docking:
    name = "docking"
    QUANTITY = "dock_score"

    def __init__(
        self,
        provider: DockingProvider,
        seeds: tuple[int, ...] = (0xF00D,),
        exhaustiveness: int = DEFAULT_EXHAUSTIVENESS,
    ) -> None:
        if not seeds:
            raise ValueError("Docking needs at least one seed")
        self.provider = provider
        self.seeds = tuple(seeds)
        self.exhaustiveness = exhaustiveness

    def settings(self) -> dict:
        return {
            "provider": self.provider.name,
            "provider_settings": self.provider.settings(),
            "exhaustiveness": self.exhaustiveness,
            "seeds": list(self.seeds),
            "score_unit": self.provider.score_unit(),
        }

    def produces(self) -> dict[str, str]:
        return {self.QUANTITY: self.provider.score_unit()}

    def systematic_floor(self, quantity: str) -> float | None:
        if quantity != self.QUANTITY:
            raise KeyError(
                f"tier {self.name!r} does not produce {quantity!r}; produces {sorted(self.produces())}"
            )
        return None  # an empirical score: its systematic error is not measured

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        return {
            "quantity": "wall_time", "unit": "s", "predicted": None,
            "basis": f"unmeasured: no timing recorded in smeltery for {self.provider.name} "
                     f"at exhaustiveness {self.exhaustiveness} x {len(self.seeds)} seeds",
        }

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        missing = [k for k in ("receptor", "box") if k not in ctx]
        if missing:
            raise DockingError(f"docking needs ctx{missing}: a prepared receptor and a Box")
        box: Box = ctx["box"]
        for cand in candidates:
            poses, scores = [], []
            errors = []
            for seed in self.seeds:
                mol = self._embed(cand, seed)
                res = self.provider.dock(mol, ctx["receptor"], box, seed, self.exhaustiveness)
                if not res.ok:
                    errors.append(f"seed {seed}: {res.error or 'no poses'}")
                    continue
                poses += res.poses
                scores += res.scores
            if not poses:
                raise DockingError(f"{cand.name}: " + "; ".join(errors))
            order = sorted(range(len(poses)), key=lambda i: scores[i])
            cand.poses = [poses[i] for i in order]
            cand.per_pose[self.QUANTITY] = [float(scores[i]) for i in order]

    @staticmethod
    def _embed(cand: Candidate, seed: int):
        parsed = Chem.MolFromSmiles(cand.smiles)
        if parsed is None:
            raise DockingError(f"{cand.name}: unparseable SMILES {cand.smiles!r}")
        if len(Chem.GetMolFrags(parsed)) > 1:
            raise DockingError(f"{cand.name}: not a single connected molecule")
        mol = Chem.AddHs(parsed)
        params = AllChem.ETKDGv3()
        params.randomSeed = seed
        params.useSmallRingTorsions = True
        if AllChem.EmbedMolecule(mol, params) != 0:
            raise DockingError(f"{cand.name}: ETKDG could not embed at seed {seed}")
        AllChem.MMFFOptimizeMolecule(mol)
        return mol
