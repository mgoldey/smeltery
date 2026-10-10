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

`Gfn2` (GFN2-xTB through the external `xtb` binary) is a GATE, not a ranker.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, rdFMCS
from rdkit.Chem.rdMolAlign import AlignMol

from .cost import rhf_calibration, sto3g_basis_functions_strict
from .gates import require_passing_poses
from .model import ANGSTROM_TO_BOHR, HARTREE_TO_KCAL, Candidate, PointCharge, Pose


class NoCommonCoreError(RuntimeError):
    """Raised when the MCS gate refuses a pair instead of returning a degraded mapping."""


class Tier(Protocol):
    name: str

    def run(self, candidates: list[Candidate], ctx: dict) -> None: ...

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        """{quantity, unit, predicted, basis}; `predicted` is None when unmeasured, never a guess."""
        ...

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
    a seeded rigid jitter to mimic the spread of docked poses. With
    `parent_poses_given=True` the parent's existing `poses` (for example from
    `Docking`) are used as they are, unjittered: `n_poses`, `jitter_deg` and
    `jitter_ang` then play no part, and the pose count is `len(parent.poses)`.

    Analogue pose i: every atom the maximum common substructure maps onto
    the parent (hydrogens included) is COPIED EXACTLY from parent pose i.
    Only unmapped atoms (the substituent) come from a fresh embedding that
    has been aligned onto the mapped core. Pairing an analogue with ITSELF
    therefore maps every atom, copies every coordinate, and gives a
    per-pose ΔΔE of exactly 0.0. That is the self-pair anchor.

    The copied core and the freshly embedded substituent meet at a junction whose
    bond lengths and angles are not those of any force field, and PoseBusters
    rejects such poses (measured on docked and on ETKDG parents alike: bond_lengths,
    bond_angles, internal_energy). `relax_unmapped=True` relaxes ONLY the unmapped
    atoms with MMFF94, the mapped atoms held fixed, so every mapped coordinate is
    still an exact copy of the parent's; the pose is refused (RuntimeError) if MMFF
    cannot type the molecule or does not converge. Off by default: it changes the
    substituent's geometry and hence the ΔΔE.

    The MCS gate REFUSES rather than degrades: a pair with no common core, a
    core below `min_core_heavy` heavy atoms, a search that hit its timeout, or
    a molecule paired with an identical one whose core misses atoms, raises
    `NoCommonCoreError`. A partial mapping would leave most atoms to a free
    re-embedding, which is a different quantity from a paired difference (a
    campaign saw self-MCS collapse to 19 of 41 atoms on poses whose bonds were
    perceived from coordinates). Topology here always comes from SMILES.
    """

    n_poses: int = 6
    seed: int = 20261005
    jitter_deg: float = 15.0
    jitter_ang: float = 0.5
    min_core_heavy: int = 3
    mcs_timeout_s: int = 10
    parent_poses_given: bool = False
    relax_unmapped: bool = False
    name: str = "paired_poses"
    #: Per analogue name: (analogue atom, parent atom) pairs copied exactly. Read
    #: by `smeltery.generate.pairs_from_candidates`; reset on every `run`.
    scaffold_maps: dict[str, list[tuple[int, int]]] = field(default_factory=dict, repr=False, compare=False)

    def settings(self) -> dict:
        return {
            k: getattr(self, k)
            for k in (
                "n_poses",
                "seed",
                "jitter_deg",
                "jitter_ang",
                "min_core_heavy",
                "mcs_timeout_s",
                "parent_poses_given",
                "relax_unmapped",
            )
        }

    def produces(self) -> dict[str, str]:
        return {}  # writes poses, no per_pose quantity

    def systematic_floor(self, quantity: str) -> float | None:
        raise _unknown_quantity(self, quantity)

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        return {
            "quantity": "wall_time",
            "unit": "s",
            "predicted": None,
            "basis": f"unmeasured: no recorded timing for {len(candidates) * self.n_poses} RDKit embeddings",
        }

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        parent = ctx["parent"]
        pmol = _mol_with_h(parent.smiles)
        psym = tuple(a.GetSymbol() for a in pmol.GetAtoms())
        if self.parent_poses_given:
            if not parent.poses:
                raise ValueError(f"{parent.name}: parent_poses_given is set but the parent has no poses")
            for i, pose in enumerate(parent.poses):
                if tuple(pose.symbols) != psym:
                    raise ValueError(f"{parent.name} pose {i}: atom order does not match the SMILES (with H added)")
            parent_poses = [np.array(p.coords_ang, dtype=float) for p in parent.poses]
        else:
            cids = list(AllChem.EmbedMultipleConfs(pmol, numConfs=self.n_poses, randomSeed=self.seed))
            if len(cids) < self.n_poses:
                raise RuntimeError(f"{parent.name}: embedded only {len(cids)} of {self.n_poses} poses")
            AllChem.MMFFOptimizeMoleculeConfs(pmol)
            rng = np.random.default_rng(self.seed)
            parent_poses: list[np.ndarray] = []
            for cid in cids:
                AlignMol(pmol, pmol, prbCid=cid, refCid=cids[0])
                xyz = _pose_from_conf(pmol, cid).coords_ang
                parent_poses.append(
                    xyz if cid == cids[0] else _rigid_jitter(xyz, rng, self.jitter_deg, self.jitter_ang)
                )
            parent.poses = [Pose(psym, x) for x in parent_poses]
        self.scaffold_maps = {}

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
            timeout=self.mcs_timeout_s,
        )
        if mcs.canceled:
            raise NoCommonCoreError(f"{cand.name}: MCS search timed out; an unfinished core is not a core")
        core = Chem.MolFromSmarts(mcs.smartsString) if mcs.smartsString else None
        p_idx = pmol.GetSubstructMatch(core) if core is not None else ()
        a_idx = amol.GetSubstructMatch(core) if core is not None else ()
        if not p_idx or not a_idx:
            raise NoCommonCoreError(f"{cand.name}: no common core with the parent")
        mapping = list(zip(a_idx, p_idx, strict=True))  # (analogue atom, parent atom)
        n_heavy = sum(1 for a, _ in mapping if amol.GetAtomWithIdx(a).GetAtomicNum() > 1)
        if n_heavy < self.min_core_heavy:
            raise NoCommonCoreError(
                f"{cand.name}: common core has {n_heavy} heavy atom(s), fewer than "
                f"min_core_heavy={self.min_core_heavy}; refusing a degraded mapping"
            )
        if (
            Chem.MolToSmiles(Chem.RemoveHs(amol)) == Chem.MolToSmiles(Chem.RemoveHs(pmol))
            and len(mapping) != amol.GetNumAtoms()
        ):
            raise NoCommonCoreError(
                f"{cand.name}: identical to the parent but the core maps {len(mapping)} of "
                f"{amol.GetNumAtoms()} atoms; refusing a degraded self-mapping"
            )
        self.scaffold_maps[cand.name] = mapping
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
            if self.relax_unmapped and len(mapping) < amol.GetNumAtoms():
                xyz = self._relax_unmapped(amol, xyz, [a for a, _ in mapping], f"{cand.name} pose {i}")
            poses.append(Pose(sym, xyz))
        return poses

    def _relax_unmapped(self, amol: Chem.Mol, xyz: np.ndarray, mapped: list[int], label: str) -> np.ndarray:
        """MMFF94 minimization with every mapped atom fixed: only the substituent moves."""
        from rdkit.Chem import rdForceFieldHelpers as ffh

        mol = Chem.Mol(amol)
        conf = Chem.Conformer(mol.GetNumAtoms())
        for k, (x, y, z) in enumerate(xyz):
            conf.SetAtomPosition(k, (float(x), float(y), float(z)))
        mol.RemoveAllConformers()
        mol.AddConformer(conf, assignId=True)
        props = ffh.MMFFGetMoleculeProperties(mol)
        if props is None:
            raise RuntimeError(f"{label}: MMFF94 cannot type this molecule, so the substituent cannot be relaxed")
        ff = ffh.MMFFGetMoleculeForceField(mol, props)
        for a in mapped:
            ff.AddFixedPoint(a)
        if ff.Minimize(maxIts=5000) != 0:
            raise RuntimeError(f"{label}: the constrained MMFF94 relaxation did not converge")
        out = np.array(mol.GetConformer().GetPositions(), dtype=float)
        out[mapped] = xyz[mapped]  # the fixed atoms did not move; this makes "exact copy" hold bit for bit
        return out


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
    field_provenance: dict | None = None  # set from a `PocketField` in `run`: file digest, pdb2pqr30 version, cutoff

    def settings(self) -> dict:
        return {
            "method": "RHF",
            "basis": self.basis,
            "energy_conv": self.energy_conv,
            "density_conv": self.density_conv,
            "engine": "ferric",
            "field": self.field_provenance,
        }

    def produces(self) -> dict[str, str]:
        return {"dE_int": "kcal/mol"}

    def systematic_floor(self, quantity: str) -> float | None:
        if quantity != "dE_int":
            raise _unknown_quantity(self, quantity)
        return None  # charge-model / basis sensitivity not yet measured

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        """Measured reference time scaled by nbf^p (see `smeltery.cost`); None outside what was measured."""
        quantity, unit = "wall_time", "s"

        def none(why: str) -> dict:
            return {"quantity": quantity, "unit": unit, "predicted": None, "basis": f"unmeasured: {why}"}

        cal = rhf_calibration()
        if self.basis != cal.basis:
            return none(f"calibrated for {cal.basis} only, tier is {self.basis}")
        if not candidates or any(not c.poses for c in candidates):
            return none("poses not built yet, so the SCF count is unknown")
        total = 0.0
        for c in candidates:
            for pose in c.poses:
                nbf = sto3g_basis_functions_strict(list(pose.symbols))
                if nbf is None:
                    return none(f"{c.name}: element outside the STO-3G table")
                total += cal.predicted_seconds_per_pose(nbf)
        n = sum(len(c.poses) for c in candidates)
        return {
            "quantity": quantity,
            "unit": unit,
            "predicted": total,
            "basis": f"{n} poses x (vacuum + field RHF) at {cal.basis}; measured {cal.reference_name} "
            f"({cal.reference_nbf} bf) {cal.reference_seconds:.3g} s scaled by nbf^{cal.exponent:.2f} "
            f"(fit on calibration set), reference iteration count assumed; {cal.machine}",
        }

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        for cand in candidates:
            require_passing_poses(cand, ctx)
        import ferric

        field: list[PointCharge] = ctx["field"]
        self.field_provenance = getattr(field, "provenance", None)
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


# ---------------------------------------------------------------- surface ESP


class SurfaceEspUnavailableError(RuntimeError):
    """The installed ferric has no `esp_on_surface` binding."""


@dataclass(frozen=True)
class SurfaceEspResult:
    """ESP on a Lebedev vdW surface. `points_ang` (M, 3) in Å; `esp` (M,) in Hartree/e.

    `n_buried` is the number of surface points dropped for lying inside a
    neighbour's scaled vdW sphere, so M excludes them.
    """

    points_ang: np.ndarray
    esp: np.ndarray
    n_buried: int


def surface_esp(pose: Pose, result, basis_set, vdw_scale: float = 1.4, n_angular: int = 110) -> SurfaceEspResult:
    """Per-point ESP outside `pose`'s vdW surface from a converged ferric `result`.

    The array-valued feature source; `SurfaceEsp` reduces it to scalars for
    `Candidate.per_pose`. Raises `SurfaceEspUnavailableError` on a ferric build
    without the binding rather than falling back to anything.
    """
    import ferric

    if not hasattr(ferric, "esp_on_surface"):
        raise SurfaceEspUnavailableError(
            "this ferric build has no esp_on_surface (added in mgoldey/ferric#359, commit b22183b). "
            "Install ferric at or after that commit."
        )
    mol = ferric.Molecule.from_xyz_string(pose.to_xyz("surface esp"))
    points, esp, n_buried = ferric.esp_on_surface(mol, basis_set, result, vdw_scale=vdw_scale, n_angular=n_angular)
    return SurfaceEspResult(np.asarray(points) / ANGSTROM_TO_BOHR, np.asarray(esp), int(n_buried))


@dataclass
class SurfaceEsp:
    """Surface-ESP descriptor tier: one RHF per pose, then the ESP on its vdW surface.

    Writes scalar summaries to `per_pose` (min and max ESP over the retained
    surface points, and the buried-point count). The full per-point arrays are
    kept in `surfaces[(candidate name, pose index)]` for use as ML features.
    A descriptor, not a ranker: no systematic floor has been measured.
    """

    basis: str = "sto-3g"
    vdw_scale: float = 1.4
    n_angular: int = 110
    energy_conv: float = 1e-10
    density_conv: float = 1e-8
    name: str = "surface_esp"
    surfaces: dict[tuple[str, int], SurfaceEspResult] = field(default_factory=dict)

    def settings(self) -> dict:
        return {
            "method": "RHF",
            "basis": self.basis,
            "vdw_scale": self.vdw_scale,
            "n_angular": self.n_angular,
            "energy_conv": self.energy_conv,
            "density_conv": self.density_conv,
            "engine": "ferric",
        }

    def produces(self) -> dict[str, str]:
        return {"esp_surface_min": "Eh/e", "esp_surface_max": "Eh/e", "n_buried": "count"}

    def systematic_floor(self, quantity: str) -> float | None:
        if quantity not in self.produces():
            raise _unknown_quantity(self, quantity)
        return None  # basis / surface-definition sensitivity not yet measured

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        return {
            "quantity": "wall_time",
            "unit": "s",
            "predicted": None,
            "basis": "unmeasured: one RHF plus a surface ESP evaluation per pose; not calibrated",
        }

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        for cand in candidates:
            require_passing_poses(cand, ctx)
        import ferric

        bs = ferric.BasisSet.bundled(self.basis)
        for cand in candidates:
            lo, hi, buried = [], [], []
            for i, pose in enumerate(cand.poses):
                mol = ferric.Molecule.from_xyz_string(pose.to_xyz(f"{cand.name} pose {i}"))
                res = ferric.run_rhf(mol, bs, energy_conv=self.energy_conv, density_conv=self.density_conv)
                if not res.converged:
                    raise RuntimeError(f"{cand.name} pose {i}: SCF did not converge")
                s = surface_esp(pose, res, bs, self.vdw_scale, self.n_angular)
                if s.esp.size == 0:
                    raise RuntimeError(f"{cand.name} pose {i}: every surface point is buried")
                self.surfaces[(cand.name, i)] = s
                lo.append(float(s.esp.min()))
                hi.append(float(s.esp.max()))
                buried.append(float(s.n_buried))
            cand.per_pose["esp_surface_min"] = lo
            cand.per_pose["esp_surface_max"] = hi
            cand.per_pose["n_buried"] = buried


# ---------------------------------------------------------------- GFN2-xTB

XTB = "xtb"
# Measured xtb-vs-DFT mean absolute error; the floor may not be reported below it.
XTB_VS_DFT_MAE_KCAL = 0.825
_XTB_ENERGY_RE = re.compile(r"TOTAL ENERGY\s+(-?\d+\.\d+)\s+Eh")


class XtbUnavailableError(RuntimeError):
    """Raised when the `xtb` binary is not on PATH."""


XTB_MISSING_MESSAGE = (
    "the `xtb` binary is not on PATH. xtb is a pinned external binary, not a Python "
    "dependency (tblite has no external point charges): install xtb 6.7.x and put it "
    "on PATH, or set XTB_PREFIX to its install prefix"
)


def xtb_path() -> str | None:
    return shutil.which(XTB)


def _xtb_env() -> dict[str, str]:
    """Environment for xtb: libxtb/parameter paths, and every thread pool forced to 1.

    Parallelism is across processes (one xtb per pose); libxtb is not thread-safe.
    """
    prefix = Path(os.environ.get("XTB_PREFIX", Path.home() / ".local"))
    env = dict(os.environ)
    libdirs = [str(prefix / "lib" / "x86_64-linux-gnu"), str(prefix / "lib")]
    if env.get("LD_LIBRARY_PATH"):
        libdirs.append(env["LD_LIBRARY_PATH"])
    env["LD_LIBRARY_PATH"] = ":".join(libdirs)
    env.setdefault("XTBPATH", str(prefix / "share" / "xtb"))
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        env[var] = "1"
    return env


def xtb_version() -> str | None:
    """Version reported by `xtb --version`, or None if xtb is absent."""
    if xtb_path() is None:
        return None
    out = subprocess.run([XTB, "--version"], capture_output=True, text=True, env=_xtb_env())
    text = out.stdout + out.stderr
    m = re.search(r"xtb version\s+(\S+)", text)
    return m.group(1) if m else (text.strip() or None)


def xtb_singlepoint(symbols, coords_ang, charge: int = 0, point_charges_bohr=(), timeout: float = 600.0) -> float:
    """GFN2-xTB total energy in Hartree. `point_charges_bohr` rows are (q, x, y, z), x y z in BOHR.

    xtb reads the `pcharge` file in its working directory, in Bohr. A file passed
    through `--input` is silently ignored, so it is written to the work dir instead.
    Raises on any failure; never returns a placeholder energy.
    """
    if xtb_path() is None:
        raise XtbUnavailableError(XTB_MISSING_MESSAGE)
    if len(symbols) != len(coords_ang):
        raise ValueError(f"{len(symbols)} symbols but {len(coords_ang)} coordinates")
    with tempfile.TemporaryDirectory(prefix="smeltery-xtb-") as wd_name:
        wd = Path(wd_name)
        lines = [str(len(symbols)), ""]
        lines += [f"{s:<3s} {x:16.10f} {y:16.10f} {z:16.10f}" for s, (x, y, z) in zip(symbols, coords_ang, strict=True)]
        (wd / "mol.xyz").write_text("\n".join(lines) + "\n")
        rows = list(point_charges_bohr)
        if rows:
            body = [str(len(rows))] + [f"{q:18.10f} {x:18.10f} {y:18.10f} {z:18.10f}" for q, x, y, z in rows]
            (wd / "pcharge").write_text("\n".join(body) + "\n")
        proc = subprocess.run(
            [XTB, "mol.xyz", "--gfn", "2", "--chrg", str(charge)],
            cwd=wd,
            env=_xtb_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    m = _XTB_ENERGY_RE.search(proc.stdout)
    if proc.returncode != 0 or m is None:
        raise RuntimeError(f"xtb exited {proc.returncode} without a TOTAL ENERGY: {proc.stderr.strip()[:300]}")
    return float(m.group(1))


def _net_charge(smiles: str) -> int:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"unparseable SMILES: {smiles!r}")
    return sum(a.GetFormalCharge() for a in mol.GetAtoms())


@dataclass
class Gfn2:
    """GFN2-xTB total energy per pose, in the pocket's point-charge field. A GATE, not a RANKER.

    Use it to reject wrong ionization states and gross failures. Do NOT use it to
    order conformers or analogues that are close in energy: over a 3 kcal/mol span
    xtb-vs-DFT Spearman was 0.011 (n=20), i.e. no ordering information. Its
    measured xtb-vs-DFT MAE is 0.825 kcal/mol, which `systematic_floor` reports.

    xtb is driven as a subprocess (threads forced to 1) and reads point charges in
    BOHR; `PointCharge` is Angstrom and is converted once, via `as_ferric_bohr`.
    The net charge comes from the SMILES formal charges. With no field, the value
    is the vacuum energy.
    """

    name: str = "gfn2"
    field_provenance: dict | None = None

    def settings(self) -> dict:
        return {
            "method": "GFN2-xTB",
            "engine": "xtb subprocess, threads=1",
            "role": "gate, not ranker",
            "xtb_path": xtb_path(),
            "xtb_version": xtb_version(),
            "point_charge_unit": "bohr",
            "field": self.field_provenance,
        }

    def produces(self) -> dict[str, str]:
        return {"E_gfn2": "kcal/mol"}  # total energy: a gate on ionization state / failure, NOT a ranker

    def systematic_floor(self, quantity: str) -> float | None:
        if quantity != "E_gfn2":
            raise _unknown_quantity(self, quantity)
        return XTB_VS_DFT_MAE_KCAL

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        return {"xtb_runs": sum(len(c.poses) for c in candidates), "kind": "xtb subprocess, ~0.1-1 s each"}

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        for cand in candidates:
            require_passing_poses(cand, ctx)
        field = ctx.get("field", [])
        self.field_provenance = getattr(field, "provenance", None)
        charges = [c.as_ferric_bohr() for c in field]  # Bohr
        for cand in candidates:
            q = _net_charge(cand.smiles)
            cand.per_pose["E_gfn2"] = [
                xtb_singlepoint(p.symbols, p.coords_ang, q, charges) * HARTREE_TO_KCAL for p in cand.poses
            ]


# ---------------------------------------------------------------- force field


class ForceFieldTypingError(RuntimeError):
    """MMFF has no parameters for this molecule; no energy is fabricated."""


@dataclass
class ForceField:
    """MMFF94 energies per pose (RDKit, core): at the pose as given, and after relaxing a COPY.

    `E_mmff` is the MMFF energy at the incoming geometry; `E_mmff_relaxed` is the
    energy after minimizing a copy, so their difference is the pose's strain. The
    tier NEVER writes coordinates back: a quantum tier after it scores the pose it
    was handed, not a force-field-relaxed one (the ferric PR #325 defect). A
    non-converged minimization raises rather than reporting a half-relaxed energy.

    A descriptor and a gate, not a ranker: MMFF energies are not comparable
    across formulas and no systematic floor has been measured. Only `path="mmff"`
    exists; an OpenMM path (issue #18) has no parameter source decided yet.
    """

    path: str = "mmff"
    variant: str = "MMFF94"
    max_iters: int = 2000
    name: str = "forcefield"

    def __post_init__(self) -> None:
        if self.path != "mmff":
            raise ValueError(f"unsupported force-field path {self.path!r}: only 'mmff' is implemented (issue #18)")
        if self.variant not in ("MMFF94", "MMFF94s"):
            raise ValueError(f"unknown MMFF variant {self.variant!r}; use 'MMFF94' or 'MMFF94s'")

    def settings(self) -> dict:
        import rdkit

        return {"path": self.path, "variant": self.variant, "max_iters": self.max_iters, "rdkit": rdkit.__version__}

    def produces(self) -> dict[str, str]:
        return {"E_mmff": "kcal/mol", "E_mmff_relaxed": "kcal/mol"}

    def systematic_floor(self, quantity: str) -> float | None:
        if quantity not in self.produces():
            raise _unknown_quantity(self, quantity)
        return None  # not measured

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        return {
            "quantity": "wall_time",
            "unit": "s",
            "predicted": None,
            "basis": "unmeasured as a tier: see docs/environments.md for the one-molecule timing",
        }

    def _energies(self, cand: Candidate, pose: Pose, i: int) -> tuple[float, float]:
        from rdkit.Chem import rdForceFieldHelpers as ffh

        mol = _mol_with_h(cand.smiles)
        if tuple(a.GetSymbol() for a in mol.GetAtoms()) != tuple(pose.symbols):
            raise ValueError(f"{cand.name} pose {i}: atom order does not match the SMILES (with H added)")
        conf = Chem.Conformer(mol.GetNumAtoms())
        for k, xyz in enumerate(pose.coords_ang):
            conf.SetAtomPosition(k, [float(v) for v in xyz])
        mol.RemoveAllConformers()
        mol.AddConformer(conf)  # a private copy: `pose.coords_ang` is only read
        props = ffh.MMFFGetMoleculeProperties(mol, mmffVariant=self.variant)
        if props is None:
            raise ForceFieldTypingError(f"{cand.name} pose {i}: {self.variant} cannot type this molecule")
        ff = ffh.MMFFGetMoleculeForceField(mol, props)
        e_pose = ff.CalcEnergy()
        if ff.Minimize(maxIts=self.max_iters) != 0:
            raise RuntimeError(f"{cand.name} pose {i}: {self.variant} did not converge in {self.max_iters} iterations")
        return e_pose, ff.CalcEnergy()

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        for cand in candidates:
            require_passing_poses(cand, ctx)
        for cand in candidates:
            pairs = [self._energies(cand, p, i) for i, p in enumerate(cand.poses)]
            cand.per_pose["E_mmff"] = [a for a, _ in pairs]
            cand.per_pose["E_mmff_relaxed"] = [b for _, b in pairs]


# ---------------------------------------------------------------- QM/MM with a covalent cut

#: Scaled-position link-H factor for a cut C-C single bond, 1.09 A / 1.53 A. This is ferric's own
#: `DEFAULT_LINK_SCALE` (ferric-scf qmmm.rs); any other cut bond type must pass `link_scale` explicitly.
CC_LINK_SCALE = 1.09 / 1.53

#: Refuse when the link H lies closer than this to an embedding charge. NOT a measured boundary: it is
#: the midpoint of the two distances in mgoldey/smeltery#19 (a `keep` scheme with a charge 0.443 A from
#: the link H made an optimization DIVERGE; delete-host at 1.305 A converged in 6 steps). Those numbers
#: come from the issue text. A re-run on the Gly3 fixture (tests/data/gly3_ff14sb.json, STO-3G, 40
#: steps) did NOT reproduce a divergence at 0.433 A (keep), 0.90 A (rcd) or 1.47 A (delete-host): all
#: three descended without a step increase; none converged in 40 steps. So the true boundary is
#: system-dependent or unobserved here; this value is a conservative placeholder, and it is a knob.
MIN_LINK_CHARGE_DISTANCE_ANG = (0.443 + 1.305) / 2

BOUNDARY_SCHEMES = ("keep", "delete-host", "rc", "rcd")


class QmmmUnavailableError(RuntimeError):
    """The installed ferric lacks a QM/MM capability this tier needs; nothing is computed."""


class QmmmBoundaryError(ValueError):
    """A QM/MM partition was refused before any SCF: unsafe or ill-defined boundary."""


@dataclass(frozen=True)
class MmParameters:
    """Explicit AMBER-form force-field data for a whole structure, in atom order.

    ferric's `MmTopology` assigns no parameters of its own, so every number here is caller-supplied
    and its source must ride along in `provenance`. Units: e, Angstrom, kcal/mol (the units of
    `ferric.MmTopology.from_amber_units`). `bonds` is also the covalent graph the tier uses to find
    cut bonds, and the graph ferric derives its 1-2/1-3/1-4 exclusions from: one list, so the two
    cannot disagree.
    """

    symbols: tuple[str, ...]
    charges: tuple[float, ...]
    sigmas_angstrom: tuple[float, ...]
    epsilons_kcal: tuple[float, ...]
    bonds: tuple[tuple[int, int, float, float], ...]
    angles: tuple[tuple[int, int, int, float, float], ...] = ()
    torsions: tuple[tuple[int, int, int, int, int, float, float], ...] = ()
    provenance: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        n = len(self.symbols)
        for name in ("charges", "sigmas_angstrom", "epsilons_kcal"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"MmParameters.{name} has {len(getattr(self, name))} entries for {n} atoms")
        for b in self.bonds:
            if not (0 <= b[0] < n and 0 <= b[1] < n and b[0] != b[1]):
                raise ValueError(f"MmParameters bond {b[:2]} is out of range for {n} atoms")
        if not self.provenance:
            raise ValueError("MmParameters needs a non-empty provenance: where did these numbers come from?")

    @classmethod
    def from_json(cls, path: str | Path) -> MmParameters:
        """Read the tests/data/gly3_ff14sb.json layout (atoms[{element,q,sigma_ang,eps_kcal}], bonds, ...).

        Provenance is the file's own `openmm_version`/`forcefield` keys plus the file's SHA-256.
        """
        import hashlib
        import json

        raw = Path(path).read_bytes()
        d = json.loads(raw)
        atoms = d["atoms"]
        return cls(
            symbols=tuple(a["element"] for a in atoms),
            charges=tuple(float(a["q"]) for a in atoms),
            sigmas_angstrom=tuple(float(a["sigma_ang"]) for a in atoms),
            epsilons_kcal=tuple(float(a["eps_kcal"]) for a in atoms),
            bonds=tuple((int(b[0]), int(b[1]), float(b[2]), float(b[3])) for b in d["bonds"]),
            angles=tuple((int(a[0]), int(a[1]), int(a[2]), float(a[3]), float(a[4])) for a in d.get("angles", [])),
            torsions=tuple(
                (int(t[0]), int(t[1]), int(t[2]), int(t[3]), int(t[4]), float(t[5]), float(t[6]))
                for t in d.get("torsions", [])
            ),
            provenance={
                "file": Path(path).name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "openmm_version": d.get("openmm_version"),
                "forcefield": d.get("forcefield"),
            },
        )

    def topology(self, ferric, bonds=None):
        """A `ferric.MmTopology`. `bonds` overrides the bond list (tests use it to omit a cut bond)."""
        return ferric.MmTopology.from_amber_units(
            list(self.charges),
            list(self.sigmas_angstrom),
            list(self.epsilons_kcal),
            [tuple(b) for b in (self.bonds if bonds is None else bonds)],
            [tuple(a) for a in self.angles],
            [tuple(t) for t in self.torsions],
        )


def _qmmm_api_missing(ferric) -> list[str]:
    missing = [n for n in ("QmmmSystem", "MmTopology", "run_qmmm") if not hasattr(ferric, n)]
    if missing:
        return missing
    return [
        f"QmmmSystem.{m}"
        for m in ("with_link_atoms", "with_boundary_charges", "min_link_to_charge_distance", "qm_molecule")
        if not hasattr(ferric.QmmmSystem, m)
    ]


_CUT_LJ_PROBE: dict[int, tuple[bool, float]] = {}


def cut_lj_exclusions_probe(ferric=None) -> tuple[bool, float]:
    """Does this ferric exclude 1-2/1-3 QM-MM Lennard-Jones pairs across a bonded cut? (ferric #319)

    Behavioural, not version-based: an H2 QM region with one MM atom bonded to it, all sigma 3.4 A
    (carbon-like). Both QM-MM pairs are 1-2 or 1-3 through the bond list, so a ferric that excludes
    them reports LJ = 0 exactly; one that does not reports thousands of kcal/mol. Returns
    `(supported, lj_kcal_per_mol)`. Cached per module object. Costs one two-electron SCF.
    """
    if ferric is None:
        import ferric as ferric_mod

        ferric = ferric_mod
    key = id(ferric)
    if key in _CUT_LJ_PROBE:
        return _CUT_LJ_PROBE[key]
    xyz = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.74), (0.0, 0.0, 2.24)]
    system = ferric.QmmmSystem(["H", "H", "H"], xyz, [0.0, 0.0, 0.0], qm_indices=[0, 1], charge=0)
    top = ferric.MmTopology.from_amber_units(
        [0.0] * 3, [3.4] * 3, [0.1] * 3, [(0, 1, 300.0, 0.74), (1, 2, 300.0, 1.5)], [], []
    )
    res = ferric.run_qmmm(system, "sto-3g", mm_topology=top)
    lj = float(res.mm_energy["lj"]) * HARTREE_TO_KCAL
    out = (abs(lj) < 1e-9, lj)
    _CUT_LJ_PROBE[key] = out
    return out


@dataclass
class Qmmm:
    """Single-point QM/MM (RHF in a fixed-charge field + AMBER-form MM) with a real covalent cut.

    The candidate's poses are conformers of the WHOLE structure (ligand and pocket residues, in the
    atom order of `params`); `qm_indices` picks the QM region by explicit atom index. Every bond in
    `params.bonds` with exactly one QM end is a cut bond. The tier never cuts silently:

    * each cut bond is capped with a scaled-position link H (`link_scale`; default C-C only, any other
      cut bond type needs an explicit scale), and the MM host's charge is treated by `boundary_scheme`
      (default "delete-host", Z1);
    * the partition is refused BEFORE any SCF if the link H lies within
      `min_link_charge_distance_ang` of an embedding charge, if the QM region has an odd electron
      count, or if the installed ferric lacks the link-atom API or does not exclude 1-2/1-3 (and scale
      1-4) QM-MM Lennard-Jones across the cut (ferric #319, fixed by ferric PR #336);
    * `E_mm` is the force field's MM-MM energy plus QM-MM Lennard-Jones (QM-MM Coulomb is in the
      embedding, so it is in `E_qm_embedded`).

    Writes `E_qmmm = E_qm_embedded + E_mm`, all kcal/mol, at the geometry given; it never writes
    coordinates back. It is a single point: no geometry optimization. It is a total energy of a
    partitioned system and depends on the partition, so no systematic floor is claimed.
    `diagnostics[(candidate name, pose index)]` holds the boundary measurements (min link-charge
    distance in Angstrom, cut bonds, MM components).
    """

    params: MmParameters
    qm_indices: tuple[int, ...]
    qm_charge: int
    basis: str = "sto-3g"
    boundary_scheme: str = "delete-host"
    link_scale: float | None = None
    min_link_charge_distance_ang: float = MIN_LINK_CHARGE_DISTANCE_ANG
    energy_conv: float = 1e-10
    density_conv: float = 1e-8
    name: str = "qmmm"
    diagnostics: dict[tuple[str, int], dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        n = len(self.params.symbols)
        qm = tuple(int(i) for i in self.qm_indices)
        if not qm or len(set(qm)) != len(qm) or any(not 0 <= i < n for i in qm):
            raise ValueError(f"qm_indices must be unique atom indices in [0, {n}); got {self.qm_indices!r}")
        self.qm_indices = qm
        if self.boundary_scheme not in BOUNDARY_SCHEMES:
            raise ValueError(f"unknown boundary_scheme {self.boundary_scheme!r}; use one of {BOUNDARY_SCHEMES}")
        if self.link_scale is not None and not 0.0 < self.link_scale < 1.0:
            raise ValueError(f"link_scale must be in (0, 1), got {self.link_scale}")

    # -- partition ---------------------------------------------------------------------------

    def cut_bonds(self) -> list[tuple[int, int]]:
        """Bonds with exactly one QM end, as (qm atom, mm atom)."""
        qm = set(self.qm_indices)
        return [(b[0], b[1]) if b[0] in qm else (b[1], b[0]) for b in self.params.bonds if (b[0] in qm) != (b[1] in qm)]

    def resolved_link_scale(self) -> float | None:
        """The link scale used: None with no cut; `link_scale` if given; CC_LINK_SCALE for all-C-C cuts; else refuse."""
        cuts = self.cut_bonds()
        if not cuts:
            return None
        if self.link_scale is not None:
            return self.link_scale
        syms = self.params.symbols
        odd = [(a, b) for a, b in cuts if (syms[a], syms[b]) != ("C", "C")]
        if odd:
            raise QmmmBoundaryError(
                f"cut bond(s) {[(f'{syms[a]}{a}', f'{syms[b]}{b}') for a, b in odd]} are not C-C: the default link "
                f"scale ({CC_LINK_SCALE:.4f} = 1.09/1.53) is for a cut C-C bond only; pass link_scale explicitly"
            )
        return CC_LINK_SCALE

    def _require_engine(self, ferric) -> None:
        missing = _qmmm_api_missing(ferric)
        if missing:
            raise QmmmUnavailableError(
                f"this ferric build lacks {missing} (QM/MM link atoms, boundary charges and MmTopology are in "
                "mgoldey/ferric PR #1; the cut-pair LJ exclusions are ferric PR #336, fixing issue #319, in "
                "v0.1.0rc7 and later). Install a ferric at or after v0.1.0rc7."
            )
        if self.cut_bonds():
            ok, lj = cut_lj_exclusions_probe(ferric)
            if not ok:
                raise QmmmUnavailableError(
                    "refusing to cut a covalent bond: this ferric applies QM-MM Lennard-Jones across the bonded cut "
                    f"pair without 1-2/1-3 exclusion (probe LJ = {lj:.1f} kcal/mol, expected 0). That is "
                    "mgoldey/ferric#319, fixed by ferric PR #336 (commit 0251d40, in v0.1.0rc7). Install a ferric "
                    "at or after v0.1.0rc7."
                )

    def _system(self, ferric, pose: Pose, label: str):
        p = self.params
        if tuple(pose.symbols) != p.symbols:
            raise ValueError(f"{label}: pose atoms do not match the MmParameters atom order")
        xyz = [tuple(float(v) for v in row) for row in pose.coords_ang]  # copies; the pose is only read
        sysm = ferric.QmmmSystem(
            list(p.symbols), xyz, list(p.charges), qm_indices=list(self.qm_indices), charge=self.qm_charge
        )
        scale = self.resolved_link_scale()
        bonds = [(b[0], b[1]) for b in p.bonds]
        if scale is not None:
            sysm = sysm.with_link_atoms(bonds, scale)
        sysm = sysm.with_boundary_charges(bonds, self.boundary_scheme)
        if scale is not None:
            d = sysm.min_link_to_charge_distance()  # Angstrom (ferric.pyi); None when there is no charge
            if d is not None and d < self.min_link_charge_distance_ang:
                raise QmmmBoundaryError(
                    f"{label}: link H is {d:.3f} A from the nearest embedding charge, under the "
                    f"{self.min_link_charge_distance_ang:.3f} A threshold (boundary scheme {self.boundary_scheme!r}); "
                    "an unscreened charge this close over-polarizes the QM density (smeltery#19). Move the cut, "
                    "or use a boundary scheme that removes the nearby charge"
                )
        nelec = sysm.qm_molecule().nelec()
        if nelec % 2:
            raise QmmmBoundaryError(
                f"{label}: QM region plus link atoms has {nelec} electrons; RHF needs an even count"
            )
        return sysm

    # -- Tier protocol -------------------------------------------------------------------------

    def settings(self) -> dict:
        cuts = self.cut_bonds()
        return {
            "method": "RHF",
            "basis": self.basis,
            "energy_conv": self.energy_conv,
            "density_conv": self.density_conv,
            "engine": "ferric",
            "qm_indices": list(self.qm_indices),
            "qm_charge": self.qm_charge,
            "cut_bonds": cuts,
            "boundary_scheme": self.boundary_scheme,
            "boundary_charge_treatment": {
                "keep": "none (host charge left in place)",
                "delete-host": "Z1: host (M1) charge zeroed",
                "rc": "RC: host charge moved to M1-M2 bond midpoints",
                "rcd": "RCD: RC with the group dipole preserved",
            }[self.boundary_scheme],
            "link_atom": "scaled-position H" if cuts else None,
            "link_scale": self.link_scale if self.link_scale is not None else (CC_LINK_SCALE if cuts else None),
            "min_link_charge_distance_ang": self.min_link_charge_distance_ang,
            "mm_force_field": dict(self.params.provenance),
            "mm_terms": "MM-MM bonded, LJ and Coulomb plus QM-MM LJ (1-2/1-3 excluded, 1-4 scaled by the topology)",
        }

    def produces(self) -> dict[str, str]:
        return {"E_qmmm": "kcal/mol", "E_qm_embedded": "kcal/mol", "E_mm": "kcal/mol"}

    def systematic_floor(self, quantity: str) -> float | None:
        if quantity not in self.produces():
            raise _unknown_quantity(self, quantity)
        return None  # boundary / basis / force-field sensitivity not yet measured

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        return {
            "quantity": "wall_time",
            "unit": "s",
            "predicted": None,
            "basis": "unmeasured: one embedded RHF per pose; the RHF calibration does not cover QM/MM",
        }

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        for cand in candidates:
            require_passing_poses(cand, ctx)
        import ferric

        self._require_engine(ferric)
        top = self.params.topology(ferric)
        # Build and gate EVERY partition before the first SCF, so a refusal costs no compute.
        systems = [
            [self._system(ferric, pose, f"{cand.name} pose {i}") for i, pose in enumerate(cand.poses)]
            for cand in candidates
        ]
        for cand, per_cand in zip(candidates, systems, strict=True):
            e_tot, e_qm, e_mm = [], [], []
            for i, sysm in enumerate(per_cand):
                res = ferric.run_qmmm(
                    sysm,
                    self.basis,
                    energy_conv=self.energy_conv,
                    density_conv=self.density_conv,
                    mm_topology=top,
                )
                if not res.converged:
                    raise RuntimeError(f"{cand.name} pose {i}: SCF did not converge")
                mm = {k: float(v) * HARTREE_TO_KCAL for k, v in res.mm_energy.items()}
                e_qm.append(float(res.energy) * HARTREE_TO_KCAL)
                e_mm.append(mm["total"])
                e_tot.append(e_qm[-1] + e_mm[-1])
                self.diagnostics[(cand.name, i)] = {
                    "min_link_charge_distance_ang": sysm.min_link_to_charge_distance(),
                    "cut_bonds": self.cut_bonds(),
                    "mm_components_kcal": mm,
                }
            cand.per_pose["E_qmmm"] = e_tot
            cand.per_pose["E_qm_embedded"] = e_qm
            cand.per_pose["E_mm"] = e_mm
