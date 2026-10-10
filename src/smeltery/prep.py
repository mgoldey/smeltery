"""Structure-first preparation for `smeltery run`: a target in, a receptor, a box and a pocket field out.

`prepare_target_from_structure` is the `[structure]` stage of the CLI. It

1. obtains the structure through a `StructureProvider` (a local .pdb, a PDB id, or an AlphaFold DB
   accession) and keeps its EXPERIMENTAL / PREDICTED label, licence and attribution;
2. writes a protein-only receptor PDB (ATOM records of the first model, altloc blank or A, optionally
   some chains; every HETATM is dropped and counted by residue name) and the Vina PDBQT made from it
   (`smeltery.docking.prepare_receptor`, i.e. Meeko);
3. fixes the docking box centre from a STATED source: explicit coordinates, or the centroid of a named
   reference ligand's heavy atoms in the input structure;
4. loads the pocket point-charge field around that centre with an EXPLICIT cutoff (`load_pocket`;
   truncation is not monotone, so there is no default).

Everything lives in ONE coordinate frame, the input structure's, because the receptor PDB, the PDBQT
and the pocket PDB are all derived from the same atoms. That is checked here rather than assumed:

- the box centre must lie near the receptor (`MAX_CENTRE_GAP_ANG`, a sanity bound, not a measurement);
- the field must cover the box faces (cutoff >= the largest box half-side) and the record says whether
  it covers the corners too;
- every heavy atom of the PDBQT must coincide (0.01 A) with a heavy atom of the receptor PDB, and no
  receptor atom that Meeko dropped may lie inside the box (`prepare_receptor` drops bad residues);
- the docked parent must end up inside the box (checked by the CLI).

The artefacts are written under `out_dir/structure/` and identified by sha256 in the returned record.
Downloaded structure text is data: it is only parsed here, never executed.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .pocket import load_pocket
from .providers.structure import AfdbProvider, PdbProvider, StructureResult

#: Sanity bound: a box centre farther than this from every receptor heavy atom is almost surely in the
#: wrong frame (or off the protein). A heuristic, not a measured threshold.
MAX_CENTRE_GAP_ANG = 8.0
#: PDBQT coordinates are written to 3 decimals; a heavy atom farther than this from its PDB atom is not it.
FRAME_TOL_ANG = 0.01
WATER = {"HOH", "WAT", "DOD", "TIP", "TIP3"}
_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


class PrepError(RuntimeError):
    """The structure could not be prepared or failed a frame check (a stage error)."""


class PrepConfigError(ValueError):
    """The `[structure]` section names something that does not exist or is inconsistent (a config error)."""


@dataclass
class PreparedTarget:
    receptor_pdb: Path
    receptor_pdbqt: Path
    center: tuple[float, float, float]
    field: list  # PocketField
    record: dict = field(default_factory=dict)  # what goes into the run's inputs
    summary: str = ""


def _atoms(text: str):
    """(record, name, resname, chain, resnum, altloc, element, xyz, line) of the FIRST model's ATOM/HETATM."""
    for line in text.splitlines():
        if line.startswith("ENDMDL"):
            return
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError as e:
            raise PrepError(f"bad coordinates in PDB line: {line!r}") from e
        name = line[12:16].strip()
        el = (line[76:78].strip() or name.lstrip("0123456789")[:1]).upper()
        yield (
            line[:6].strip(),
            name,
            line[17:20].strip(),
            line[21].strip(),
            line[22:26].strip(),
            line[16].strip(),
            el,
            xyz,
            line,
        )


def clean_receptor(text: str, chains: list[str] | None) -> tuple[str, dict]:
    """Protein-only PDB text and what was dropped. Altloc must be blank or 'A'; HETATM is always dropped."""
    keep, dropped, n_alt = [], Counter(), 0
    for rec, _name, res, chain, _num, alt, _el, _xyz, line in _atoms(text):
        if rec == "HETATM":
            dropped[res] += 1
            continue
        if chains is not None and chain not in chains:
            continue
        if alt not in ("", "A"):
            n_alt += 1
            continue
        keep.append(line[:16] + " " + line[17:])
    if not keep:
        raise PrepError("the structure has no protein ATOM records (after the chain / altloc selection)")
    info = {
        "chains": None if chains is None else list(chains),
        "model": 1,
        "altloc": "blank or A",
        "n_altloc_dropped": n_alt,
        "hetatm_dropped": dict(sorted(dropped.items())),
        "n_protein_atoms": len(keep),
    }
    return "\n".join([*keep, "END", ""]), info


def reference_ligand_centroid(text: str, ref: str) -> tuple[tuple[float, float, float], dict]:
    """Centroid of the heavy atoms of HETATM residue `RES[:CHAIN[:RESNUM]]` in the input structure."""
    parts = ref.split(":")
    if not 1 <= len(parts) <= 3 or not parts[0]:
        raise PrepConfigError(f"reference_ligand {ref!r} must look like RES, RES:CHAIN or RES:CHAIN:RESNUM")
    resname, chain, num = parts[0], (parts[1] if len(parts) > 1 else None), (parts[2] if len(parts) > 2 else None)
    inst: dict[tuple[str, str], list] = {}
    for rec, _n, res, ch, rn, alt, el, xyz, _l in _atoms(text):
        if rec != "HETATM" or res != resname or res in WATER or el == "H" or alt not in ("", "A"):
            continue
        if (chain is None or ch == chain) and (num is None or rn == num):
            inst.setdefault((ch, rn), []).append(xyz)
    if not inst:
        raise PrepConfigError(f"reference_ligand {ref!r}: no such HETATM residue in the structure")
    if len(inst) > 1:
        raise PrepConfigError(
            f"reference_ligand {ref!r} matches several residues {sorted(inst)}; name one as RES:CHAIN:RESNUM"
        )
    ((ch, rn), pts), *_ = inst.items()
    c = np.mean(np.asarray(pts, dtype=float), axis=0)
    return (float(c[0]), float(c[1]), float(c[2])), {"residue": f"{resname}:{ch}:{rn}", "n_heavy_atoms": len(pts)}


def _heavy_xyz_pdb(text: str) -> np.ndarray:
    return np.asarray([xyz for rec, _n, _r, _c, _u, _a, el, xyz, _l in _atoms(text) if el != "H"], dtype=float)


def _heavy_xyz_pdbqt(text: str) -> np.ndarray:
    out = []
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and line[77:79].strip() not in ("H", "HD"):
            out.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
    return np.asarray(out, dtype=float)


def _nearest(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance from each row of `a` to the nearest row of `b` (chunked brute force)."""
    out = np.empty(len(a))
    for i in range(0, len(a), 512):
        d = np.linalg.norm(a[i : i + 512, None, :] - b[None, :, :], axis=2)
        out[i : i + 512] = d.min(axis=1)
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def obtain_structure(spec: str, provider: str, base: Path, allow_network: bool, fetch=None) -> StructureResult:
    """The structure for `spec`. A local file never needs the network; an id or accession needs `allow_network`."""
    if provider == "afdb":
        if not allow_network:
            raise PrepConfigError(
                "[structure] provider 'afdb' fetches from AlphaFold DB: set allow_network = true to opt in"
            )
        prov = AfdbProvider(fetch) if fetch else AfdbProvider()
        arg = spec
    else:
        p = Path(spec) if Path(spec).is_absolute() else base / spec
        if p.is_file():
            prov, arg = PdbProvider(), str(p)
        elif p.suffix.lower() == ".pdb" or not _PDB_ID.match(spec):
            raise PrepConfigError(f"[structure] spec {str(p)!r} is not an existing .pdb file or a PDB id")
        elif not allow_network:
            raise PrepConfigError(
                f"[structure] spec {spec!r} is a PDB id, which is fetched from RCSB: set allow_network = true to opt in"
            )
        else:
            prov, arg = (PdbProvider(fetch) if fetch else PdbProvider()), spec
    result = prov.predict(arg)
    if result is None:
        raise PrepError(f"stage 'structure' failed: {prov.last_error}")
    return result


@contextlib.contextmanager
def _tool_path():
    """Meeko's CLI is a console script next to the interpreter; make it findable when the venv is not activated."""
    if shutil.which("mk_prepare_receptor.py") is not None:
        yield
        return
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + old
    try:
        yield
    finally:
        os.environ["PATH"] = old


def prepare_target_from_structure(
    scfg: dict,
    result: StructureResult,
    box_size: tuple[float, float, float],
    out_dir: Path,
) -> PreparedTarget:
    """Write the receptor PDB / PDBQT under `out_dir/structure`, check the frame, load the field."""
    from importlib.metadata import PackageNotFoundError, version

    from .docking import prepare_receptor  # the docking extra

    sdir = Path(out_dir) / "structure"
    sdir.mkdir(parents=True, exist_ok=True)
    for stale in sdir.glob("receptor*"):
        stale.unlink()
    text = result.text
    center_cfg = scfg.get("center")
    if center_cfg is not None:
        center, centre_rec = tuple(float(x) for x in center_cfg), {"mode": "explicit"}
    else:
        center, extra = reference_ligand_centroid(text, scfg["reference_ligand"])
        centre_rec = {"mode": "reference_ligand", "reference_ligand": scfg["reference_ligand"], **extra}
    centre_rec["xyz"] = list(center)

    pdb_text, selection = clean_receptor(text, scfg.get("chains"))
    receptor_pdb = sdir / "receptor.pdb"
    receptor_pdb.write_text(pdb_text)
    heavy = _heavy_xyz_pdb(pdb_text)
    c = np.asarray(center)

    gap = float(np.linalg.norm(heavy - c, axis=1).min())
    if gap > MAX_CENTRE_GAP_ANG:
        raise PrepError(
            f"stage 'structure' failed: the box centre {tuple(round(x, 2) for x in center)} is {gap:.1f} A from the "
            f"nearest receptor heavy atom (> {MAX_CENTRE_GAP_ANG} A): the centre is not in the receptor's frame"
        )
    cutoff = float(scfg["pocket_cutoff"])
    half = np.asarray(box_size, dtype=float) / 2
    if cutoff < half.max():
        raise PrepError(
            f"stage 'structure' failed: pocket_cutoff {cutoff} A is smaller than the largest box half-side "
            f"{half.max():g} A, so the field would not even cover the box faces the ligand can reach"
        )
    covers_corners = bool(cutoff >= float(np.linalg.norm(half)))

    with _tool_path():
        produced = prepare_receptor(receptor_pdb, sdir / "receptor.pdbqt")
    receptor_pdbqt = Path(produced)  # a rigid receptor: a bare path
    qt = _heavy_xyz_pdbqt(receptor_pdbqt.read_text())
    if not len(qt):
        raise PrepError("stage 'structure' failed: the prepared PDBQT has no heavy atoms")
    off = float(_nearest(qt, heavy).max())
    if off > FRAME_TOL_ANG:
        raise PrepError(
            f"stage 'structure' failed: a PDBQT heavy atom is {off:.3f} A from any receptor PDB atom "
            f"(> {FRAME_TOL_ANG} A): the docking receptor and the pocket are not in one frame"
        )
    missing = heavy[_nearest(heavy, qt) > FRAME_TOL_ANG]  # receptor atoms Meeko dropped (--allow_bad_res)
    in_box = int((np.abs(missing - c) <= half).all(axis=1).sum()) if len(missing) else 0
    if in_box:
        raise PrepError(
            f"stage 'structure' failed: Meeko dropped {in_box} receptor atom(s) inside the docking box "
            f"(of {len(missing)} dropped); docking would see a pocket with holes"
        )

    pocket = load_pocket(receptor_pdb, cutoff_ang=cutoff, center_ang=center)
    if not len(pocket):
        raise PrepError(f"stage 'structure' failed: no pocket charges within {cutoff} A of {center}")

    record: dict = {
        "spec": Path(scfg["spec"]).name if Path(scfg["spec"]).suffix.lower() == ".pdb" else scfg["spec"],
        "provider": scfg.get("provider", "pdb"),
        "allow_network": bool(scfg.get("allow_network", False)),
        "structure": result.to_dict(),
        "kind": result.kind,
        "selection": selection,
        "center": centre_rec,
        "pocket_cutoff_ang": cutoff,
        "frame": {
            "centre_to_nearest_receptor_atom_ang": round(gap, 3),
            "pdbqt_heavy_atoms_vs_pdb_max_dev_ang": round(off, 4),
            "receptor_atoms_dropped_by_meeko": int(len(missing)),
            "dropped_inside_box": in_box,
            "field_covers_box_faces": True,
            "field_covers_box_corners": covers_corners,
        },
        "artefacts": {"receptor.pdb": _sha(receptor_pdb), "receptor.pdbqt": _sha(receptor_pdbqt)},
    }
    for pkg in ("meeko",):
        with contextlib.suppress(PackageNotFoundError):
            record.setdefault("tool_versions", {})[pkg] = version(pkg)
    if result.kind == "PREDICTED":
        near = {
            n
            for _rec, name, _r, _ch, n, _a, _e, xyz, _l in _atoms(pdb_text)
            if name == "CA" and np.linalg.norm(np.asarray(xyz) - c) <= cutoff
        }
        vals = [v for n, v in (result.plddt or ()) if str(n) in near]
        record["pocket_plddt"] = (
            {"n_residues": len(vals), "mean": float(np.mean(vals)), "min": float(np.min(vals))} if vals else None
        )
    summary = (
        f"structure {result.kind} ({result.source}); centre {tuple(round(x, 2) for x in center)} "
        f"({centre_rec['mode']}); receptor {receptor_pdbqt.name} sha256 {record['artefacts']['receptor.pdbqt'][:12]}; "
        f"pocket {len(pocket)}/{pocket.provenance['n_total']} charges within {cutoff:g} A"
    )
    return PreparedTarget(receptor_pdb, receptor_pdbqt, center, pocket, record, summary)
