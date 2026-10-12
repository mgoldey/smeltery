"""Post-processing of raw scaffold-growth output: match the scaffold, repair its bonds, filter, build candidates.

Pure RDKit and numpy: no torch, no GPU. The raw output comes from a model that sees atom types and
coordinates but never bond orders or valences, so two things hold of it (measured, see
docs/superpowers/specs/2026-10-10-sbdd-scaffold-growth-design.md):

* the atoms of each output are NOT in the order they were given (DiffSBDD's `batch_to_list` sorts them
  with an unstable `argsort`), so the scaffold is found by position, never by index;
* bonds are assigned from distances, so scaffold bonds go missing and spurious ones appear, and new atoms
  are often placed beside atoms that are already saturated in the scaffold.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from rdkit import Chem

from ..model import Pose

MATCH_TOL_ANG = 0.5  # a scaffold atom's output atom must lie within this of its input position
FIT_TOL_ANG = 0.3  # and the rigid fit of the whole scaffold must leave at most this rmsd


class RepairError(ValueError):
    """The output cannot be matched to the scaffold."""


@dataclass(frozen=True)
class ScaffoldSpec:
    """The fixed part of the parent: its graph, its coordinates, and the parent it was cut from."""

    parent: Chem.Mol  # heavy atoms, the bound conformer
    scaffold: Chem.Mol  # sanitized; atoms in `indices` order; the parent's coordinates
    kekule: Chem.Mol  # the same graph with Kekule bond orders and no aromatic flags
    indices: tuple[int, ...]  # heavy-atom indices into the parent SMILES

    @property
    def coords(self) -> np.ndarray:
        return np.asarray(self.scaffold.GetConformer().GetPositions(), dtype=float)


def _with_coords(mol: Chem.Mol, xyz: np.ndarray) -> Chem.Mol:
    out = Chem.Mol(mol)
    conf = Chem.Conformer(out.GetNumAtoms())
    for i, p in enumerate(xyz):
        conf.SetAtomPosition(i, (float(p[0]), float(p[1]), float(p[2])))
    out.RemoveAllConformers()
    out.AddConformer(conf, assignId=True)
    return out


def build_scaffold(smiles: str, indices: Sequence[int], pose: Pose) -> ScaffoldSpec:
    """Cut `indices` (heavy atoms of `smiles`, in SMILES order) out of the parent at its pose `pose`."""
    parent = Chem.MolFromSmiles(smiles)
    if parent is None:
        raise ValueError(f"unparseable SMILES: {smiles!r}")
    n = parent.GetNumAtoms()
    idx = tuple(int(i) for i in indices)
    if not idx or len(set(idx)) != len(idx):
        raise ValueError("scaffold must be a non-empty list of distinct heavy-atom indices")
    if min(idx) < 0 or max(idx) >= n:
        raise ValueError(f"scaffold index out of range for a molecule with {n} heavy atoms: {idx}")
    if len(idx) >= n:
        raise ValueError("scaffold covers every heavy atom; there is nothing to grow")
    if tuple(pose.symbols[:n]) != tuple(a.GetSymbol() for a in parent.GetAtoms()):
        raise ValueError("the parent pose does not start with the heavy atoms of the parent SMILES, in order")
    parent = _with_coords(parent, np.asarray(pose.coords_ang[:n], dtype=float))
    kek_parent = Chem.Mol(parent)
    Chem.Kekulize(kek_parent, clearAromaticFlags=True)
    where = {a: k for k, a in enumerate(idx)}
    rw = Chem.RWMol()
    for a in idx:
        src = kek_parent.GetAtomWithIdx(a)
        atom = Chem.Atom(src.GetAtomicNum())
        atom.SetFormalCharge(src.GetFormalCharge())
        rw.AddAtom(atom)
    for b in kek_parent.GetBonds():
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        if i in where and j in where:
            rw.AddBond(where[i], where[j], b.GetBondType())
    xyz = parent.GetConformer().GetPositions()
    kekule = _with_coords(rw.GetMol(), xyz[list(idx)])
    if len(Chem.GetMolFrags(kekule)) != 1:
        raise ValueError("the scaffold atoms are not one connected piece of the parent")
    scaffold = Chem.Mol(kekule)
    Chem.SanitizeMol(scaffold)
    return ScaffoldSpec(parent, scaffold, kekule, idx)


def _match_scaffold(pos: np.ndarray, symbols: Sequence[str], spec: ScaffoldSpec) -> list[int] | None:
    """Scaffold atom k -> the output atom nearest its input position; None unless unique and an element match."""
    sx = spec.coords
    out = []
    for k in range(len(sx)):
        d = np.linalg.norm(pos - sx[k], axis=1)
        j = int(d.argmin())
        if d[j] > MATCH_TOL_ANG or symbols[j] != spec.scaffold.GetAtomWithIdx(k).GetSymbol():
            return None
        out.append(j)
    return out if len(set(out)) == len(out) else None


def _kabsch(moving: np.ndarray, fixed: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Proper rotation `rot` and `shift` with `moving @ rot.T + shift` closest to `fixed`, and the rmsd left."""
    mc, fc = moving.mean(axis=0), fixed.mean(axis=0)
    u, _, vt = np.linalg.svd((moving - mc).T @ (fixed - fc))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rot = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    shift = fc - rot @ mc
    rmsd = float(np.sqrt((((moving @ rot.T + shift) - fixed) ** 2).sum(axis=1).mean()))
    return rot, shift, rmsd


def repair(raw: Chem.Mol, spec: ScaffoldSpec) -> tuple[Chem.Mol, list[int]]:
    """Fit the output onto the scaffold and make the scaffold's graph exactly the scaffold's.

    Adds scaffold bonds the model missed (with the scaffold's order), deletes bonds between two scaffold atoms
    that the scaffold does not have, and takes scaffold atoms' formal charges from the scaffold. Bonds the model
    placed anywhere else, and the orders of scaffold bonds it did place, are left alone: the model never saw bond
    orders, but forcing the scaffold's onto atoms the model crowded makes every one of them over-valent (0 of 16
    sanitized when measured), so valid output is rejected later, honestly, by sanitization.
    """
    pos = np.asarray(raw.GetConformer().GetPositions(), dtype=float)
    mapping = _match_scaffold(pos, [a.GetSymbol() for a in raw.GetAtoms()], spec)
    if mapping is None:
        raise RepairError(f"a scaffold atom has no unique same-element output atom within {MATCH_TOL_ANG} A")
    rot, shift, rmsd = _kabsch(pos[mapping], spec.coords)
    if rmsd > FIT_TOL_ANG:
        raise RepairError(f"the scaffold fits with rmsd {rmsd:.2f} A, above {FIT_TOL_ANG} A")
    rw = Chem.RWMol(raw)
    inside = set(mapping)
    want = {frozenset((mapping[b.GetBeginAtomIdx()], mapping[b.GetEndAtomIdx()])) for b in spec.kekule.GetBonds()}
    for b in list(rw.GetBonds()):
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        if i in inside and j in inside and frozenset((i, j)) not in want:
            rw.RemoveBond(i, j)
    for b in spec.kekule.GetBonds():
        i, j = mapping[b.GetBeginAtomIdx()], mapping[b.GetEndAtomIdx()]
        if rw.GetBondBetweenAtoms(i, j) is None:
            rw.AddBond(i, j, b.GetBondType())
    for k, j in enumerate(mapping):
        rw.GetAtomWithIdx(j).SetFormalCharge(spec.kekule.GetAtomWithIdx(k).GetFormalCharge())
    return _with_coords(rw.GetMol(), pos @ rot.T + shift), mapping


def smiles_and_pose(mol: Chem.Mol) -> tuple[str, Pose]:
    """Canonical SMILES of `mol` (heavy atoms, with a conformer) and a pose in the atom order of
    `Chem.AddHs(Chem.MolFromSmiles(smiles))`, the order `PairedPoses` and every tier read poses in.

    Raises ValueError if the order cannot be established or checked; never returns a pose in another order.
    """
    smiles = Chem.MolToSmiles(mol)
    order = [int(x) for x in re.findall(r"\d+", mol.GetProp("_smilesAtomOutputOrder"))]
    canon = Chem.MolFromSmiles(smiles)
    if canon is None or canon.GetNumAtoms() != mol.GetNumAtoms() or len(order) != mol.GetNumAtoms():
        raise ValueError(f"cannot rebuild {smiles!r} in SMILES atom order")
    if [mol.GetAtomWithIdx(o).GetSymbol() for o in order] != [a.GetSymbol() for a in canon.GetAtoms()]:
        raise ValueError(f"the atom order of {smiles!r} does not match the molecule it came from")
    xyz = np.asarray(mol.GetConformer().GetPositions(), dtype=float)[order]
    withh = Chem.AddHs(_with_coords(canon, xyz), addCoords=True)
    pose = Pose(
        tuple(a.GetSymbol() for a in withh.GetAtoms()),
        np.asarray(withh.GetConformer().GetPositions(), dtype=float),
    )
    reference = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if pose.symbols != tuple(a.GetSymbol() for a in reference.GetAtoms()):
        raise ValueError(f"the pose of {smiles!r} is not in the atom order of its SMILES with hydrogens added")
    if not np.array_equal(pose.coords_ang[: len(order)], xyz):
        raise ValueError(f"adding hydrogens moved a heavy atom of {smiles!r}")
    return smiles, pose
