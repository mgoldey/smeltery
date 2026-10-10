"""PLB protein.pdb -> a PQR of per-atom force-field charges, assigned by OpenMM, plus a provenance JSON.

pdb2pqr30 cannot template the PLB proteins (ACE/NME caps, phosphothreonine TPO), so charges come straight from the
force field's own residue templates through OpenMM. OpenMM is an OPTIONAL one-off dependency of this tool
(`pip install openmm==8.6.1`, or `uv sync --extra pocket-charges`); nothing under src/smeltery imports it.

    python scripts/plb_pocket_charges.py PROTEIN.pdb OUT.pqr --ff amber14 \\
        --center 5.26 44.24 51.23 --cutoff 15 --delete-residues ACE,NME,TPO --drop-waters

Every preparation decision is written to OUT.pqr.json (and printed): the input and output sha256, OpenMM version, the
force-field XML files with their sha256, which residues were deleted and how far each lies from the field centre,
how many waters were dropped, the histidine templates that matched, the net charge written and the net charge of the
cropped field. Decisions the tool refuses to make silently:

* A residue named in --delete-residues must lie farther than --cutoff + --margin from --center (the field is the
  sphere of that radius around the centre), otherwise the deletion would remove charges the field uses: the tool
  FAILS (exit 2) and writes nothing.
* Waters are dropped only when --drop-waters is given; they may lie inside the cutoff (a crystal water in the pocket
  does), which is recorded, not hidden.
* Any other residue the force field has no template for (a metal, a cofactor, an ion) is a failure, never skipped.

`--ff amber14` is AMBER ff14SB (amber14-all.xml); `--ff charmm36` is CHARMM36 (charmm36.xml). The CHARMM36 files
in OpenMM express ACE/NME as patches on a neighbouring residue, so the caps are deleted (and must be outside the
cutoff, which the guard checks) rather than renamed atom by atom.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

FORCE_FIELDS: dict[str, tuple[str, ...]] = {
    "amber14": ("amber14-all.xml", "amber14/tip3p.xml"),
    "charmm36": ("charmm36.xml",),
}
WATER_NAMES = frozenset({"HOH", "WAT", "TIP3", "SOL"})


class PrepError(RuntimeError):
    """A preparation decision that must not be made silently."""


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_deleted_outside_cutoff(
    deleted: dict[str, np.ndarray], center_ang, cutoff_ang: float, margin_ang: float
) -> dict[str, float]:
    """Minimum atom-to-centre distance per deleted residue; raise `PrepError` if any lies within cutoff + margin.

    `deleted` maps a residue label to its atoms' coordinates (n, 3) in Angstrom. The field is every charge within
    `cutoff_ang` of `center_ang`, so a deleted residue with an atom inside that sphere would have contributed.
    """
    c = np.asarray(center_ang, dtype=float)
    dist = {k: float(np.linalg.norm(np.asarray(xyz, dtype=float) - c, axis=1).min()) for k, xyz in deleted.items()}
    inside = {k: d for k, d in dist.items() if d <= cutoff_ang + margin_ang}
    if inside:
        detail = ", ".join(f"{k} at {d:.2f} A" for k, d in sorted(inside.items()))
        raise PrepError(
            f"refusing to delete residue(s) inside the field sphere (cutoff {cutoff_ang} A + margin {margin_ang} A "
            f"around {tuple(float(v) for v in c)}): {detail}. Deleting them would silently remove charges the "
            "field uses; handle them explicitly (a template, a different force field) instead."
        )
    return dist


def format_pqr_line(serial: int, name: str, resname: str, chain: str, resid: str, xyz, q: float) -> str:
    x, y, z = xyz
    head = f"ATOM  {serial:5d} {name:<4s} {resname:>3s} {chain:1s}{resid:>4s}    "
    return f"{head}{x:8.3f}{y:8.3f}{z:8.3f} {q:8.4f} {0.0:7.4f}\n"


def prepare(
    pdb_path: Path,
    out_pqr: Path,
    *,
    ff: str,
    center_ang,
    cutoff_ang: float,
    delete_residues: tuple[str, ...],
    drop_waters: bool,
    margin_ang: float = 1.0,
) -> dict:
    """Assign charges and write `out_pqr`; return the provenance dict (also written beside it)."""
    try:
        import openmm
        from openmm import NonbondedForce, app, unit
    except ImportError as e:
        raise PrepError("OpenMM is needed: `pip install openmm==8.6.1` (an optional, one-off dependency)") from e
    if ff not in FORCE_FIELDS:
        raise PrepError(f"unknown --ff {ff!r}; choose from {sorted(FORCE_FIELDS)}")
    pdb = app.PDBFile(str(pdb_path))
    mod = app.Modeller(pdb.topology, pdb.positions)
    pos = np.asarray(mod.positions.value_in_unit(unit.angstrom))
    index = {a.index: i for i, a in enumerate(mod.topology.atoms())}

    def label(r) -> str:
        return f"{r.name}:{r.chain.id}:{r.id}"

    to_delete, deleted_xyz, waters = [], {}, []
    for r in mod.topology.residues():
        if r.name in delete_residues:
            to_delete.append(r)
            deleted_xyz[label(r)] = np.array([pos[index[a.index]] for a in r.atoms()])
        elif drop_waters and r.name in WATER_NAMES:
            to_delete.append(r)
            waters.append(r)
    missing = [n for n in delete_residues if n not in {r.name for r in to_delete}]
    if missing:
        raise PrepError(f"--delete-residues names residue(s) not in the file: {missing}")
    distances = check_deleted_outside_cutoff(deleted_xyz, center_ang, cutoff_ang, margin_ang)
    c = np.asarray(center_ang, dtype=float)
    water_dist = {
        label(r): float(np.linalg.norm(np.array([pos[index[a.index]] for a in r.atoms()]) - c, axis=1).min())
        for r in waters
    }
    mod.delete(to_delete)

    data_dir = Path(openmm.app.__file__).parent / "data"
    paths = [data_dir / f for f in FORCE_FIELDS[ff]]
    forcefield = app.ForceField(*FORCE_FIELDS[ff])
    try:
        templates = forcefield.getMatchingTemplates(mod.topology, ignoreExternalBonds=True)
        system = forcefield.createSystem(
            mod.topology, nonbondedMethod=app.NoCutoff, constraints=None, ignoreExternalBonds=True
        )
    except ValueError as e:
        raise PrepError(
            f"the force field {ff} has no template for a residue that remains after the declared deletions: {e}. "
            "Metals, cofactors and ions must be handled or refused explicitly, never skipped."
        ) from e
    nb = next(f for f in system.getForces() if isinstance(f, NonbondedForce))
    xyz = np.asarray(mod.positions.value_in_unit(unit.angstrom))
    q = np.array([nb.getParticleParameters(i)[0].value_in_unit(unit.elementary_charge) for i in range(len(xyz))])
    his = {}
    for r, t in zip(mod.topology.residues(), templates, strict=True):
        if r.name in {"HIS", "HID", "HIE", "HIP", "HSD", "HSE", "HSP"}:
            his[t.name] = his.get(t.name, 0) + 1
    in_sphere = np.linalg.norm(xyz - c, axis=1) <= cutoff_ang
    lines = []
    for i, a in enumerate(mod.topology.atoms()):
        lines.append(format_pqr_line(i + 1, a.name, a.residue.name, a.residue.chain.id, a.residue.id, xyz[i], q[i]))
    out_pqr.write_text("".join(lines))
    prov = {
        "tool": "scripts/plb_pocket_charges.py",
        "input": pdb_path.name,
        "input_sha256": sha256_file(pdb_path),
        "output": out_pqr.name,
        "output_sha256": sha256_file(out_pqr),
        "openmm_version": openmm.__version__,
        "force_field": ff,
        "force_field_files": {str(p.relative_to(data_dir)): sha256_file(p) for p in paths},
        "center_ang": [float(v) for v in c],
        "cutoff_ang": cutoff_ang,
        "margin_ang": margin_ang,
        "deleted_residues_min_distance_to_center_ang": distances,
        "waters_dropped": bool(drop_waters),
        "waters_dropped_min_distance_to_center_ang": water_dist,
        "n_waters_dropped_inside_cutoff": sum(1 for d in water_dist.values() if d <= cutoff_ang),
        "protonation": "as given by the input file (PLB: PrepWizard/PropKa/Epik pH 7.4); NO protonation is changed",
        "histidine_templates_matched": his,
        "n_atoms_written": int(len(xyz)),
        "net_charge_written_e": float(q.sum()),
        "n_atoms_in_cutoff_sphere": int(in_sphere.sum()),
        "net_charge_in_cutoff_sphere_e": float(q[in_sphere].sum()),
        "ignore_external_bonds": True,
    }
    Path(str(out_pqr) + ".json").write_text(json.dumps(prov, indent=2, sort_keys=True) + "\n")
    return prov


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdb", type=Path)
    ap.add_argument("out_pqr", type=Path)
    ap.add_argument("--ff", required=True, choices=sorted(FORCE_FIELDS))
    ap.add_argument("--center", required=True, type=float, nargs=3, metavar=("X", "Y", "Z"))
    ap.add_argument("--cutoff", required=True, type=float, help="field radius in Angstrom, around --center")
    ap.add_argument("--delete-residues", default="", help="comma-separated residue names to delete (guarded)")
    ap.add_argument("--drop-waters", action="store_true")
    ap.add_argument("--margin", type=float, default=1.0)
    a = ap.parse_args(argv)
    try:
        prov = prepare(
            a.pdb,
            a.out_pqr,
            ff=a.ff,
            center_ang=a.center,
            cutoff_ang=a.cutoff,
            delete_residues=tuple(s for s in a.delete_residues.split(",") if s),
            drop_waters=a.drop_waters,
            margin_ang=a.margin,
        )
    except PrepError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(json.dumps(prov, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
