"""Tiers: each one reads candidates and adds per-pose results.

A tier implements `run(candidates, ctx)` and `estimate_cost(candidates)`, and
declares what it writes: `produces()` maps each `Candidate.per_pose` key to its
unit, and `systematic_floor(quantity)` is the tier's own systematic error in that
unit, or None while unmeasured (the funnel then refuses to cut on it). It
reports in its native settings; nothing here hides a knob behind a "generic
energy" interface. Two tiers ship in the MWE:

* `PairedPoses`: builds the parent's pose ensemble and, for each analogue,
  pose i built on the parent's pose i, which is what makes ΔΔE pairable.
* `FieldInteraction`: ΔE_int = E(in field) − E(vacuum) at the same geometry,
  with ferric RHF. It is a difference, so it compares across formulas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, rdFMCS
from rdkit.Chem.rdMolAlign import AlignMol

from .model import HARTREE_TO_KCAL, Candidate, PointCharge, Pose


class Tier(Protocol):
    name: str

    def run(self, candidates: list[Candidate], ctx: dict) -> None: ...

    def estimate_cost(self, candidates: list[Candidate]) -> dict: ...

    def settings(self) -> dict: ...

    def produces(self) -> dict[str, str]:
        """`Candidate.per_pose` keys this tier writes, mapped to their unit."""
        ...

    def systematic_floor(self, quantity: str) -> float | None:
        """Systematic error of `quantity` in its declared unit; None if unmeasured."""
        ...


class UndeclaredQuantityError(ValueError):
    """Raised when a tier writes a `per_pose` key it did not declare in `produces()`."""


def run_checked(tier: Tier, candidates: list[Candidate], ctx: dict) -> None:
    """Run `tier`, then fail if it added or replaced a `per_pose` key it didn't declare."""
    before = {id(c): dict(c.per_pose) for c in candidates}
    tier.run(candidates, ctx)
    declared = set(tier.produces())
    for c in candidates:
        old = before[id(c)]
        written = {k for k, v in c.per_pose.items() if k not in old or old[k] is not v}
        undeclared = sorted(written - declared)
        if undeclared:
            raise UndeclaredQuantityError(
                f"tier {tier.name!r} wrote undeclared per_pose key(s) {undeclared} on "
                f"{c.name!r}; declared: {sorted(declared)}"
            )


def _unknown_quantity(tier: Tier, quantity: str) -> KeyError:
    return KeyError(f"tier {tier.name!r} does not produce {quantity!r}; produces {sorted(tier.produces())}")


def _mol_with_h(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"unparseable SMILES: {smiles!r}")
    return Chem.AddHs(mol)


def _pose_from_conf(mol: Chem.Mol, conf_id: int) -> Pose:
    conf = mol.GetConformer(conf_id)
    xyz = np.array([list(conf.GetAtomPosition(i)) for i in range(mol.GetNumAtoms())])
    return Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), xyz)


def _rigid_jitter(xyz: np.ndarray, rng: np.random.Generator, max_deg: float, max_shift: float) -> np.ndarray:
    """Rotate about the centroid by a random axis/angle and translate."""
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    theta = np.deg2rad(rng.uniform(-max_deg, max_deg))
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rot = np.eye(3) + np.sin(theta) * k + (1 - np.cos(theta)) * (k @ k)
    c = xyz.mean(axis=0)
    return (xyz - c) @ rot.T + c + rng.uniform(-max_shift, max_shift, size=3)


@dataclass
class PairedPoses:
    """Pose ensemble for the parent, plus paired poses for each analogue.

    Parent: `n_poses` ETKDG conformers, aligned onto conformer 0, each given
    a seeded rigid jitter to mimic the spread of docked poses.

    Analogue pose i: every atom the maximum common substructure maps onto
    the parent (hydrogens included) is COPIED EXACTLY from parent pose i.
    Only unmapped atoms (the substituent) come from a fresh embedding that
    has been aligned onto the mapped core. Pairing an analogue with ITSELF
    therefore maps every atom, copies every coordinate, and gives a
    per-pose ΔΔE of exactly 0.0. That is the self-pair anchor.
    """

    n_poses: int = 6
    seed: int = 20261005
    jitter_deg: float = 15.0
    jitter_ang: float = 0.5
    name: str = "paired_poses"

    def settings(self) -> dict:
        return {k: getattr(self, k) for k in ("n_poses", "seed", "jitter_deg", "jitter_ang")}

    def produces(self) -> dict[str, str]:
        return {}  # writes poses, no per_pose quantity

    def systematic_floor(self, quantity: str) -> float | None:
        raise _unknown_quantity(self, quantity)

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        return {"embeddings": len(candidates) * self.n_poses, "kind": "rdkit, milliseconds"}

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        parent = ctx["parent"]
        pmol = _mol_with_h(parent.smiles)
        cids = list(AllChem.EmbedMultipleConfs(pmol, numConfs=self.n_poses, randomSeed=self.seed))
        if len(cids) < self.n_poses:
            raise RuntimeError(f"{parent.name}: embedded only {len(cids)} of {self.n_poses} poses")
        AllChem.MMFFOptimizeMoleculeConfs(pmol)
        rng = np.random.default_rng(self.seed)
        parent_poses: list[np.ndarray] = []
        for cid in cids:
            AlignMol(pmol, pmol, prbCid=cid, refCid=cids[0])
            xyz = _pose_from_conf(pmol, cid).coords_ang
            parent_poses.append(xyz if cid == cids[0] else _rigid_jitter(xyz, rng, self.jitter_deg, self.jitter_ang))
        psym = tuple(a.GetSymbol() for a in pmol.GetAtoms())
        parent.poses = [Pose(psym, x) for x in parent_poses]

        for cand in candidates:
            if cand is parent:
                continue
            cand.poses = self._paired(pmol, parent_poses, cand)

    def _paired(self, pmol: Chem.Mol, parent_poses: list[np.ndarray], cand: Candidate) -> list[Pose]:
        amol = _mol_with_h(cand.smiles)
        mcs = rdFMCS.FindMCS(
            [pmol, amol],
            atomCompare=rdFMCS.AtomCompare.CompareElements,
            bondCompare=rdFMCS.BondCompare.CompareOrder,
            ringMatchesRingOnly=True,
            completeRingsOnly=True,
            timeout=10,
        )
        core = Chem.MolFromSmarts(mcs.smartsString)
        p_idx = pmol.GetSubstructMatch(core)
        a_idx = amol.GetSubstructMatch(core)
        if not p_idx or not a_idx:
            raise RuntimeError(f"{cand.name}: no common core with the parent")
        mapping = list(zip(a_idx, p_idx, strict=True))  # (analogue atom, parent atom)
        sym = tuple(a.GetSymbol() for a in amol.GetAtoms())
        poses = []
        for i, pxyz in enumerate(parent_poses):
            if len(mapping) == amol.GetNumAtoms():
                xyz = np.zeros((amol.GetNumAtoms(), 3))
            else:
                cid = AllChem.EmbedMolecule(amol, randomSeed=self.seed + i)
                if cid < 0:
                    raise RuntimeError(f"{cand.name}: could not embed pose {i}")
                ref = Chem.Mol(pmol)
                ref.RemoveAllConformers()
                conf = Chem.Conformer(pmol.GetNumAtoms())
                for j, (x, y, z) in enumerate(pxyz):
                    conf.SetAtomPosition(j, (float(x), float(y), float(z)))
                ref.AddConformer(conf, assignId=True)
                AlignMol(amol, ref, atomMap=mapping)
                xyz = _pose_from_conf(amol, 0).coords_ang.copy()
            for a, p in mapping:  # exact copy of every mapped atom
                xyz[a] = pxyz[p]
            poses.append(Pose(sym, xyz))
        return poses


@dataclass
class FieldInteraction:
    """ΔE_int = E_RHF(in point-charge field) − E_RHF(vacuum), per pose, in kcal/mol.

    Both energies are at the same geometry, so the difference is the field's
    interaction energy including the electronic polarization it induces. Unlike
    a total energy, it is comparable between molecules of different formula.
    """

    basis: str = "sto-3g"
    energy_conv: float = 1e-10
    density_conv: float = 1e-8
    name: str = "field_interaction"

    def settings(self) -> dict:
        return {"method": "RHF", "basis": self.basis, "energy_conv": self.energy_conv,
                "density_conv": self.density_conv, "engine": "ferric"}

    def produces(self) -> dict[str, str]:
        return {"dE_int": "kcal/mol"}

    def systematic_floor(self, quantity: str) -> float | None:
        if quantity != "dE_int":
            raise _unknown_quantity(self, quantity)
        return None  # charge-model / basis sensitivity not yet measured

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        n = sum(len(c.poses) for c in candidates)
        return {"scf_runs": 2 * n, "kind": "ferric RHF, ~0.1-1 s each at STO-3G for <20 atoms"}

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        import ferric

        field: list[PointCharge] = ctx["field"]
        charges = [c.as_ferric_bohr() for c in field]
        bs = ferric.BasisSet.bundled(self.basis)
        for cand in candidates:
            vals = []
            for i, pose in enumerate(cand.poses):
                mol = ferric.Molecule.from_xyz_string(pose.to_xyz(f"{cand.name} pose {i}"))
                kw = {"energy_conv": self.energy_conv, "density_conv": self.density_conv}
                vac = ferric.run_rhf(mol, bs, **kw)
                fld = ferric.run_rhf(mol, bs, point_charges=charges, **kw)
                if not (vac.converged and fld.converged):
                    raise RuntimeError(f"{cand.name} pose {i}: SCF did not converge")
                vals.append((fld.energy - vac.energy) * HARTREE_TO_KCAL)
            cand.per_pose["dE_int"] = vals
