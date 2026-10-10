"""`VinaProvider`: the first `DockingProvider`, over AutoDock Vina + Meeko."""

from __future__ import annotations

import numpy as np

from ..model import Pose
from .base import DEFAULT_EXHAUSTIVENESS, Box, DockResult
from .united_atom import check_full_pose, check_heavy_atom_count, restore_hydrogens
from .vina_dock import dock_ligand


class VinaProvider:
    """`DockingProvider` over AutoDock Vina + Meeko.

    `receptor` is a PDBQT path (rigid) or a `Receptor` (rigid + flexible
    sidechains); the sidechain coordinates of each pose come back in
    `DockResult.flex_receptor`.

    Returns poses with every hydrogen restored (`restore_hydrogens`, heavy atoms
    pinned at the docked coordinates), after checking each against the input
    molecule: heavy-atom count, per-atom element on the Meeko mapping, and the
    full atom count of what is returned. Those guards RAISE; only a search that
    did not run (no receptor, Vina error) comes back as `DockResult.error`.

    `cpu` defaults to 1, not Vina's 0 (all cores): the tier fans out across
    ligands, and two levels of parallelism oversubscribe the box.
    """

    name = "vina"
    license_id = "MIT OR Apache-2.0"  # this code: smeltery's own (pyproject.toml)
    # Engines (fetched 2026-10-10): AutoDock Vina is Apache-2.0 (https://github.com/ccsb-scripps/AutoDock-Vina);
    # Meeko is LGPL-2.1 per its repository page (https://github.com/forlilab/Meeko). Whether that is SPDX
    # LGPL-2.1-only or -or-later was NOT checked: UNVERIFIED, so the unsuffixed name is kept.
    engine_licenses = {"AutoDock Vina": "Apache-2.0", "Meeko": "LGPL-2.1 (SPDX -only/-or-later UNVERIFIED)"}

    def __init__(
        self,
        exhaustiveness: int = DEFAULT_EXHAUSTIVENESS,
        n_poses: int = 10,
        cpu: int = 1,
    ) -> None:
        self.exhaustiveness = exhaustiveness
        self.n_poses = n_poses
        self.cpu = cpu

    def settings(self) -> dict:
        return {
            "engine": "vina",
            "scoring": "vina",
            "exhaustiveness": self.exhaustiveness,
            "n_poses": self.n_poses,
            "cpu": self.cpu,
        }

    def score_unit(self) -> str:
        return "kcal/mol"  # empirical ranking heuristic, not a binding free energy

    def dock(
        self,
        mol,
        receptor,
        box: Box,
        seed: int,
        exhaustiveness: int | None = None,
    ) -> DockResult:
        from rdkit import Chem

        ex = self.exhaustiveness if exhaustiveness is None else exhaustiveness
        run = dock_ligand(
            mol,
            receptor,
            box.center,
            box.size,
            exhaustiveness=ex,
            n_poses=self.n_poses,
            seed=seed,
            cpu=self.cpu,
        )
        if not run.ok:
            return DockResult(error=run.error)

        fallback_smiles = Chem.MolToSmiles(Chem.RemoveHs(mol))
        poses, scores, flex = [], [], []
        for p in run.poses:
            heavy = [(s, c) for s, c in zip(p.symbols, p.coords_angstrom) if s != "H"]
            check_heavy_atom_count(mol, [s for s, _ in heavy])
            mapping = p.rdkit_index_of_heavy
            # The mapping indexes MEEKO's SMILES, not ours; the two are only
            # correct together. With no mapping there is nothing to mis-index.
            smiles = (p.meeko_smiles if mapping else None) or fallback_smiles
            syms, coords = restore_hydrogens(
                smiles,
                [s for s, _ in heavy],
                [c for _, c in heavy],
                rdkit_index_of_heavy=mapping,
            )
            check_full_pose(mol, syms)
            poses.append(Pose(tuple(syms), np.asarray(coords, dtype=float)))
            scores.append(p.vina_score)
            flex.append(p.flex_pdbqt)
        return DockResult(
            poses=poses,
            scores=scores,
            flex_receptor=flex if any(flex) else [],
        )
