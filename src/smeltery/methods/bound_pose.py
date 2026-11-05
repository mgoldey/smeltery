"""`bound_pose`: the parent's poses from a bound conformer (an experimental structure's ligand), not ETKDG or docking.

`[bound_pose] file` is an XYZ file of the parent as bound (heavy atoms from the structure, hydrogens placed by any
tool), in the field's frame. Pose 0 is that conformer, relabelled to the atom order of the parent SMILES with
hydrogens added and otherwise unaltered; poses 1.. are seeded rigid jitters of it (`jitter_deg` rotation about the
centroid, `jitter_ang` shift), mimicking the spread of docked poses. The analogues are then paired to these poses by
`[poses]` exactly as to docked ones (`PairedPoses(parent_poses_given=True)`: the MCS core copied from parent pose i).

`skip_first` builds `n_poses + skip_first` poses and drops the first `skip_first` before pairing, so `n_poses` poses
are kept. The parent's kept poses are those the danuglipron example (`examples/danuglipron_halogen.py
--skip-first-poses`) kept; its analogues' substituent atoms are not, because the example dropped AFTER pairing and the
pairing seeds pose i's fresh substituent embedding with `seed + i` (the mapped core is an exact copy either way).
With `skip_first = 0` the poses are the example's, atom for atom.

The `file` is a file knob: `smeltery run` stages it under `OUT/inputs/` and the input digest carries its sha256.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds

from ..model import Candidate, Pose
from ..tiers import PairedPoses, _mol_with_h, _rigid_jitter
from . import MethodSpec, register


@dataclass(frozen=True)
class BoundPoseKnobs:
    """The `[bound_pose]` knobs. `file` and `seed` have no default: a run states which conformer and which jitters."""

    file: str
    seed: int
    n_poses: int = 6
    jitter_deg: float = 15.0
    jitter_ang: float = 0.5
    skip_first: int = 0


def bound_pose_in_smiles_order(xyz_path: Path, smiles: str) -> Pose:
    """The conformer in `xyz_path`, relabelled to the atom order of `AddHs(MolFromSmiles(smiles))`.

    Heavy atoms by skeleton substructure match on the connectivity perceived from the file; each hydrogen goes to a
    slot of the heavy atom it sits on. Hydrogens of one heavy atom are interchangeable, so slot order within a heavy
    atom is immaterial. Coordinates are copied, never altered.
    """
    pmol = _mol_with_h(smiles)
    xmol = Chem.MolFromXYZFile(str(xyz_path))
    if xmol is None:
        raise ValueError(f"{xyz_path}: not a readable XYZ file")
    rdDetermineBonds.DetermineConnectivity(xmol)
    xyz = np.array([list(xmol.GetConformer().GetAtomPosition(i)) for i in range(xmol.GetNumAtoms())])

    def skeleton(m):
        r = Chem.RWMol(Chem.RemoveHs(m, sanitize=False))
        for b in r.GetBonds():
            b.SetBondType(Chem.BondType.SINGLE)
            b.SetIsAromatic(False)
        for a in r.GetAtoms():
            a.SetIsAromatic(False)
            a.SetFormalCharge(0)
        return r.GetMol()

    sx, sp = skeleton(xmol), skeleton(pmol)
    match = sp.GetSubstructMatch(sx)  # xyz heavy-atom order index -> pmol heavy-atom index
    if len(match) != sx.GetNumAtoms():
        raise RuntimeError("the bound conformer does not match the parent's heavy-atom skeleton")
    xheavy = [a.GetIdx() for a in xmol.GetAtoms() if a.GetAtomicNum() > 1]
    out = np.full((pmol.GetNumAtoms(), 3), np.nan)
    for xi, pi in zip(xheavy, match, strict=True):
        out[pi] = xyz[xi]
    xh_by_heavy: dict[int, list[int]] = {}
    for a in xmol.GetAtoms():
        if a.GetAtomicNum() == 1:
            nb = [n.GetIdx() for n in a.GetNeighbors() if n.GetAtomicNum() > 1]
            if len(nb) != 1:
                raise RuntimeError("hydrogen without exactly one heavy neighbour in the bound conformer")
            xh_by_heavy.setdefault(nb[0], []).append(a.GetIdx())
    x2p = dict(zip(xheavy, match, strict=True))
    for xi, hs in xh_by_heavy.items():
        slots = [n.GetIdx() for n in pmol.GetAtomWithIdx(x2p[xi]).GetNeighbors() if n.GetAtomicNum() == 1]
        if len(slots) != len(hs):
            raise RuntimeError("hydrogen count differs between the bound conformer and the parent SMILES")
        for slot, h in zip(slots, hs, strict=True):
            out[slot] = xyz[h]
    if np.isnan(out).any():
        raise RuntimeError("some parent atoms received no coordinates from the bound conformer")
    return Pose(tuple(a.GetSymbol() for a in pmol.GetAtoms()), out)


def bound_pose_ensemble(bound: Pose, n_poses: int, seed: int, jitter_deg: float, jitter_ang: float) -> list[Pose]:
    """`n_poses` poses: the bound conformer itself, then `n_poses - 1` seeded rigid jitters of it (one rng stream)."""
    rng = np.random.default_rng(seed)
    base = bound.coords_ang
    coords = [base.copy()] + [_rigid_jitter(base, rng, jitter_deg, jitter_ang) for _ in range(n_poses - 1)]
    return [Pose(bound.symbols, x) for x in coords]


class BoundPosePairedPoses(PairedPoses):
    """`PairedPoses` with the parent ensemble built on a bound conformer (`bound_pose_ensemble`).

    Parent pose 0 is the bound conformer; poses 1.. are seeded rigid jitters of it (`seed`, `jitter_deg`,
    `jitter_ang`). Analogue pairing is `PairedPoses._paired`, unchanged: mapped atoms are exact copies of the parent
    pose, so pairing a molecule with itself copies everything. `smeltery run` reaches the same poses through
    `[bound_pose]` (the parent) and `[poses]` (the pairing).
    """

    def __init__(self, bound: Pose, **kw):
        super().__init__(**kw)
        self.bound = bound

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        parent = ctx["parent"]
        pmol = _mol_with_h(parent.smiles)
        if tuple(a.GetSymbol() for a in pmol.GetAtoms()) != self.bound.symbols:
            raise ValueError("bound pose atom order does not match the parent SMILES")
        parent.poses = bound_pose_ensemble(self.bound, self.n_poses, self.seed, self.jitter_deg, self.jitter_ang)
        coords = [p.coords_ang for p in parent.poses]
        for cand in candidates:
            if cand is not parent:
                cand.poses = self._paired(pmol, coords, cand)


def _resolve(cfg: dict, name: str) -> Path:
    """A path from the config: absolute as given, otherwise relative to the config file's directory (as `smeltery
    run` resolves every input)."""
    p = Path(name)
    return p if p.is_absolute() else Path(cfg.get("_dir", ".")) / p


class BoundPose:
    """The `pose_source` hook: `[bound_pose]` gives the parent's poses; `smeltery run` pairs the analogues to them."""

    def parent_poses(self, cfg: dict, parent: Candidate, ctx: dict) -> list[Pose]:
        k = BoundPoseKnobs(**cfg["bound_pose"])
        path = _resolve(cfg, k.file)
        if not path.is_file():
            raise FileNotFoundError(f"[bound_pose] file {k.file!r}: no such file ({path})")
        bound = bound_pose_in_smiles_order(path, parent.smiles)
        poses = bound_pose_ensemble(bound, k.n_poses + k.skip_first, k.seed, k.jitter_deg, k.jitter_ang)
        return poses[k.skip_first :]


def _validate(cfg: dict) -> None:
    """`[bound_pose]`: a table of its knobs with `file` and `seed` set, beside no `[docking]` (nor `[structure]`, which
    docks); the counts are whole numbers and the jitters non-negative."""
    from .. import cli
    from . import registry

    if "bound_pose" not in cfg:
        return
    cli._validate_section(registry()["bound_pose"], cfg)  # a table, its knobs only, not beside [docking]
    s = cfg["bound_pose"]
    if "structure" in cfg:
        raise cli.ConfigError("[bound_pose] gives the parent's poses; [structure] docks the parent: keep one")
    for key in ("file", "seed"):
        if key not in s:
            raise cli.ConfigError(f"[bound_pose] needs {key!r}")
    if not isinstance(s["file"], str) or not s["file"]:
        raise cli.ConfigError("[bound_pose] file must be a path (a string)")

    def whole(key: str, low: int) -> None:
        v = s.get(key, low)
        if not isinstance(v, int) or isinstance(v, bool) or v < low:
            raise cli.ConfigError(f"[bound_pose] {key} must be an integer >= {low}, got {v!r}")

    whole("seed", 0)
    whole("n_poses", 1)
    whole("skip_first", 0)
    for key in ("jitter_deg", "jitter_ang"):
        v = s.get(key, 0.0)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            raise cli.ConfigError(f"[bound_pose] {key} must be a number >= 0, got {v!r}")


def register_methods() -> None:
    from ..gui.catalog import Param

    register(
        MethodSpec(
            "bound_pose",
            "Bound conformer (parent poses)",
            "geometry",
            "search",
            step="poses",
            slot="pose_source",
            section="bound_pose",
            knobs=BoundPoseKnobs,
            file_knobs=("file",),
            order=25,
            exclusive=("docking",),
            provides=("parent_poses",),
            validate=_validate,
            hook=BoundPose(),
            cost_model=lambda: "cannot say: no recorded timing (reads one file and applies rigid jitters; no search)",
            summary="The parent's poses from a bound conformer: pose 0 as bound, poses 1.. seeded rigid jitters of it.",
            params=(
                Param("file", "path", required=True, help="XYZ of the bound parent in the field's frame, with H"),
                Param("seed", "int", required=True, help="seed of the rigid jitters; a new seed draws new poses"),
                Param("n_poses", "int", 6, help="parent poses kept: the bound conformer plus n_poses - 1 jitters"),
                Param("jitter_deg", "float", 15.0, help="largest random rotation (deg) of a jittered pose"),
                Param("jitter_ang", "float", 0.5, help="largest random shift (A) of a jittered pose"),
                Param("skip_first", "int", 0, help="leading poses built and then dropped before pairing"),
            ),
        )
    )
