from __future__ import annotations

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery.gui.pipeline import Pipeline


def provisional_pose(smiles: str, *, seed: int = 0xF00D) -> tuple[tuple[str, ...], np.ndarray]:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    params = AllChem.ETKDGv3()  # ty: ignore[unresolved-attribute]
    params.randomSeed = seed
    AllChem.EmbedMolecule(mol, params)  # ty: ignore[unresolved-attribute]
    conf = mol.GetConformer()
    symbols = tuple(a.GetSymbol() for a in mol.GetAtoms())
    coords = np.array(
        [
            [conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y, conf.GetAtomPosition(i).z]
            for i in range(mol.GetNumAtoms())
        ],
        dtype=float,
    )
    return symbols, coords


def place_site(pipeline: Pipeline, atom: int, from_atom: int, *, q: float = 1.0, distance: float = 3.0) -> Pipeline:
    if atom == from_atom:
        raise ValueError("atom and from_atom must be distinct")
    stage = pipeline.stage("pocket")
    site = list(stage.params.get("site", []))
    site.append({"q": q, "atom": atom, "from_atom": from_atom, "distance": distance})
    pipeline.set("pocket", site=site)
    return pipeline
