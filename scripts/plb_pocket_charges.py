"""protein.pdb -> a PQR of per-atom force-field charges, assigned by OpenMM, plus a provenance JSON.

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

TWO OPTIONAL EXTENSIONS, both off by default so the PLB behaviour above is byte-identical without them.

`--lj OUT.lj.json` additionally writes Lennard-Jones sigma and epsilon per atom, in atom order. The same
`NonbondedForce.getParticleParameters(i)` call that yields the charge returns `(charge, sigma, epsilon)`; this
script read only `[0]` for its first two years. They go in a sidecar JSON, not extra PQR columns, because PQR
carries charge and *radius* and has no well-depth field -- and because `smeltery.pocket.loader` parses this PQR and
must keep working unchanged. LJ matters wherever a pose can sit INSIDE the protein: a bare point charge has no Pauli
repulsion, so a ligand atom in a charge site is attracted into a bottomless Coulomb well instead of being pushed
out (measured: a converged SCF returning dE_int = -1122.94 kcal/mol against a cell median near -18).

`--prep` repairs an unprepped crystal pocket before typing, via pdbfixer (a further optional dependency:
`uv sync --extra pocket-prep`). Five defects, each needing a different tool; all five appeared in the first real
pocket tried (7LCJ):

  * no hydrogens anywhere                 -> addMissingHydrogens(pH)
  * truncated side chains                 -> pdbfixer findMissingAtoms/addMissingAtoms
  * a missing terminal atom (no OXT)      -> pdbfixer missingTerminals; nothing in openmm.app.Modeller does this
  * a peptide chain break                 -> ignoreExternalBonds=True (already used below)
  * CYS matching both CYM and CYX         -> explicit residueTemplates from SG-SG geometry

Gaps found by `findMissingResidues` are deliberately NOT built: `missingResidues` is cleared, so no loop is
modelled. This tool produces a typeable, repulsive environment, never invented density. Cysteines are assigned
CYX when an SG-SG pair is within --ss-dist, otherwise neutral CYS -- never CYM, because a deprotonated thiolate is
not assumed without evidence. `--prep` is refused for charmm36, whose CYS/disulfide templates are not these names.
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


#: An SG-SG pair at or below this is a disulfide. Observed in 7LCJ: 2.024-2.035 A for the four real pairs,
#: and the next closest non-bonded SG-SG pair is far outside it, so the threshold is not near a decision boundary.
SS_DIST_ANG = 2.5


def assign_cysteines(topology, positions_ang, ss_dist_ang: float = SS_DIST_ANG) -> tuple[dict, list, list]:
    """`residueTemplates` for every CYS: CYX when disulfide-bonded, else neutral CYS.

    Returns `(residue_templates, disulfide_pairs, free_residue_indices)`. CYM (deprotonated thiolate) is never
    assigned: it is a protonation claim, and this tool does not invent protonation states. Pairing is by SG-SG
    distance rather than by the topology's own bond list so that the decision and its evidence are both reported.
    """
    sg = [(r, a.index) for r in topology.residues() if r.name == "CYS" for a in r.atoms() if a.name == "SG"]
    xyz = np.asarray(positions_ang, dtype=float)
    pairs, bonded = [], set()
    for i in range(len(sg)):
        for j in range(i + 1, len(sg)):
            d = float(np.linalg.norm(xyz[sg[i][1]] - xyz[sg[j][1]]))
            if d <= ss_dist_ang:
                pairs.append((sg[i][0].index, sg[j][0].index, round(d, 3)))
                bonded.update((sg[i][0].index, sg[j][0].index))
    templates = {r: ("CYX" if r.index in bonded else "CYS") for r, _ in sg}
    free = sorted(r.index for r, _ in sg if r.index not in bonded)
    return templates, pairs, free


#: Every residue name a histidine can carry, across AMBER (HID/HIE/HIP) and CHARMM (HSD/HSE/HSP).
HIS_NAMES = frozenset({"HIS", "HID", "HIE", "HIP", "HSD", "HSE", "HSP"})


def histidine_census_from_hydrogens(topology) -> dict[str, int]:
    """Histidine protonation counted from the hydrogens present, not from matched templates.

    A histidine's state is fully determined by which ring nitrogens carry a hydrogen: HD1 only is the
    delta tautomer (HID), HE2 only the epsilon tautomer (HIE), and both the protonated cation (HIP).
    Reported under the AMBER names whatever the input calls the residue, so the key set is stable.

    This exists because `ForceField.getMatchingTemplates` accepts no `residueTemplates` override and so
    raises on the CYM/CYX ambiguity that `--prep` resolves -- i.e. exactly for pockets with a disulfide.
    Counting hydrogens needs no templates and works in both paths.
    """
    counts: dict[str, int] = {}
    for r in topology.residues():
        if r.name not in HIS_NAMES:
            continue
        names = {a.name for a in r.atoms()}
        hd1, he2 = "HD1" in names, "HE2" in names
        state = "HIP" if hd1 and he2 else "HID" if hd1 else "HIE" if he2 else "HIS(no ring H found)"
        counts[state] = counts.get(state, 0) + 1
    return counts


def fix_unprepped_pocket(
    pdb_path: Path, ph: float, declared_deletions: tuple[str, ...] = ()
) -> tuple[object, object, dict]:
    """A crystal pocket through pdbfixer: side chains, terminals and hydrogens, but NO modelled loops.

    Returns `(topology, positions, report)`. `report` records what was missing, so a prep that silently
    changed the structure cannot pass unnoticed.

    `declared_deletions` are residue names the caller has already named for deletion (and which
    `prepare` checks against the field sphere). A nonstandard residue among them is left alone here
    rather than refused, since it is being removed anyway; any OTHER nonstandard residue is a hard
    failure, because pdbfixer's own remedy is to substitute the standard parent -- TPO becomes THR,
    quietly losing a phosphate.
    """
    try:
        import pdbfixer
    except ImportError as e:
        raise PrepError(
            "--prep needs pdbfixer: `uv sync --extra pocket-prep` (or `pip install pdbfixer`). It is pure Python "
            "and needs only numpy and openmm>=8.2, both already required here."
        ) from e
    import importlib.metadata

    try:  # pdbfixer exposes no __version__ attribute; the installed distribution does
        version = importlib.metadata.version("pdbfixer")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - installed-but-unpackaged checkout
        version = "unknown"
    fixer = pdbfixer.PDBFixer(filename=str(pdb_path))
    fixer.findMissingResidues()
    gaps = {str(k): len(v) for k, v in fixer.missingResidues.items()}
    # Cleared deliberately: a built loop is fabricated coordinates presented as structure. The goal of this
    # prep is a typeable, repulsive environment, and an unmodelled gap is handled by ignoreExternalBonds.
    fixer.missingResidues = {}
    fixer.findNonstandardResidues()
    # pdbfixer would happily REPLACE a nonstandard residue with its standard parent (TPO -> THR),
    # silently discarding a phosphate. That is refused here. A residue the caller has already named
    # for deletion is exempt: it is leaving anyway, under the distance guard in `prepare`.
    undeclared = [(r, n) for r, n in fixer.nonstandardResidues if r.name not in declared_deletions]
    if undeclared:
        found = ", ".join(f"{r.name}->{n}" for r, n in undeclared)
        raise PrepError(
            f"nonstandard residue(s) present: {found}. Handle them explicitly (--delete-residues, with the "
            "distance guard) or choose a force field that templates them; this tool does not replace them silently."
        )
    # Never call replaceNonstandardResidues(): nothing is substituted, only repaired or deleted.
    fixer.findMissingAtoms()
    report = {
        "pdbfixer_version": version,
        "unmodelled_gaps_residues_per_chain": gaps,
        "loops_modelled": False,
        "residues_missing_heavy_atoms": len(fixer.missingAtoms),
        "residues_missing_terminal_atoms": len(fixer.missingTerminals),
        "hydrogens_added_at_ph": ph,
    }
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(ph)
    return fixer.topology, fixer.positions, report


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
    lj_path: Path | None = None,
    prep: bool = False,
    ph: float = 7.0,
    ss_dist_ang: float = SS_DIST_ANG,
) -> dict:
    """Assign charges and write `out_pqr`; return the provenance dict (also written beside it)."""
    # Argument checks come BEFORE the import: they are about the request, not the environment, so a
    # bad --ff must be refused the same way whether or not OpenMM happens to be installed. (Ordering
    # them the other way made `test_prep_is_refused_for_charmm36` pass locally, where OpenMM is
    # present, and fail in CI, where it is not -- the test was asserting the environment.)
    if ff not in FORCE_FIELDS:
        raise PrepError(f"unknown --ff {ff!r}; choose from {sorted(FORCE_FIELDS)}")
    if prep and ff != "amber14":
        raise PrepError(
            f"--prep assigns the AMBER cysteine templates CYX/CYS and is verified for amber14 only, not {ff!r}"
        )
    try:
        import openmm
        from openmm import NonbondedForce, app, unit
    except ImportError as e:
        raise PrepError("OpenMM is needed: `pip install openmm==8.6.1` (an optional, one-off dependency)") from e
    if lj_path is not None and ff != "amber14":
        # OpenMM's charmm36.xml carries PLACEHOLDERS in its NonbondedForce (epsilon 0, sigma 1 nm for every
        # type) and puts the real Lennard-Jones in a separate <LennardJonesForce> block, which becomes a
        # CustomNonbondedForce with tabulated NBFIX coefficients. Reading getParticleParameters() there yields
        # eps=0 for every atom -- a field with no Pauli repulsion, which is the exact failure --lj exists to
        # prevent. NBFIX pairs are not representable as per-atom sigma/epsilon either, so this refuses rather
        # than writing a plausible-looking file of zeros.
        raise PrepError(
            f"--lj reads per-atom sigma/epsilon from the NonbondedForce, which holds real values for amber14 "
            f"only, not {ff!r}: charmm36 keeps its Lennard-Jones in a separate LennardJonesForce block (with "
            "NBFIX pairs that are not per-atom quantities), so every epsilon here would be a placeholder zero."
        )
    prep_report: dict = {}
    if prep:
        top, pos_q, prep_report = fix_unprepped_pocket(pdb_path, ph, delete_residues)
        mod = app.Modeller(top, pos_q)
    else:
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
    # With --prep, CYS matches both CYM and CYX and OpenMM refuses the ambiguity; resolve it from SG-SG geometry.
    res_templates: dict = {}
    if prep:
        cys_xyz = np.asarray(mod.positions.value_in_unit(unit.angstrom))
        res_templates, ss_pairs, ss_free = assign_cysteines(mod.topology, cys_xyz, ss_dist_ang)
        prep_report |= {
            "disulfides_sg_sg_ang": ss_pairs,
            "n_cys_disulfide_CYX": 2 * len(ss_pairs),
            "free_cys_residue_indices": ss_free,
            "cys_assignment": "CYX when an SG-SG pair is within ss_dist_ang, else neutral CYS; CYM never assigned",
            "ss_dist_ang": ss_dist_ang,
        }
    try:
        # getMatchingTemplates takes no residueTemplates override, so with --prep it raises on the very CYM/CYX
        # ambiguity resolved above. It only feeds the histidine census, so it is skipped rather than allowed to
        # fail the run; createSystem below is what must succeed.
        try:
            templates = forcefield.getMatchingTemplates(mod.topology, ignoreExternalBonds=True)
        except Exception:
            if not prep:
                raise
            templates = None
        system = forcefield.createSystem(
            mod.topology,
            nonbondedMethod=app.NoCutoff,
            constraints=None,
            ignoreExternalBonds=True,
            residueTemplates=res_templates,
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
    if templates is not None:
        for r, t in zip(mod.topology.residues(), templates, strict=True):
            if r.name in HIS_NAMES:
                his[t.name] = his.get(t.name, 0) + 1
    else:
        # The template census is unavailable (see the getMatchingTemplates note above). Fall back to reading
        # the protonation off the hydrogens actually present, which is template-independent and gives the same
        # answer. Reporting `{}` here instead would be indistinguishable from "this pocket has no histidines".
        his = histidine_census_from_hydrogens(mod.topology)
    in_sphere = np.linalg.norm(xyz - c, axis=1) <= cutoff_ang
    lines = []
    for i, a in enumerate(mod.topology.atoms()):
        lines.append(format_pqr_line(i + 1, a.name, a.residue.name, a.residue.chain.id, a.residue.id, xyz[i], q[i]))
    out_pqr.write_text("".join(lines))

    lj_summary: dict = {}
    if lj_path is not None:
        # The SAME getParticleParameters call that gave the charges: (charge, sigma, epsilon). Written in atom
        # order, matching the PQR line order above, so a consumer can zip the two without a mapping.
        sigma_ang = np.array([nb.getParticleParameters(i)[1].value_in_unit(unit.angstrom) for i in range(len(xyz))])
        eps_kcal = np.array(
            [nb.getParticleParameters(i)[2].value_in_unit(unit.kilocalorie_per_mole) for i in range(len(xyz))]
        )
        # Polar hydrogens legitimately carry no LJ in AMBER (eps exactly 0): reported, not treated as an error.
        zero = [i for i, e in enumerate(eps_kcal) if e == 0.0]
        atoms = list(mod.topology.atoms())
        zero_non_h = [i for i in zero if atoms[i].element is None or atoms[i].element.symbol != "H"]
        lj_summary = {
            "n_atoms": int(len(xyz)),
            "n_epsilon_zero": len(zero),
            "n_epsilon_zero_not_hydrogen": len(zero_non_h),
            "sigma_ang_min": float(sigma_ang.min()),
            "sigma_ang_max": float(sigma_ang.max()),
            "epsilon_kcal_min": float(eps_kcal.min()),
            "epsilon_kcal_max": float(eps_kcal.max()),
        }
        Path(lj_path).write_text(
            json.dumps(
                {
                    "units": {"sigma": "angstrom", "epsilon": "kcal/mol", "charge": "e"},
                    "order": "atom order of the companion PQR, one entry per ATOM line",
                    "pqr": out_pqr.name,
                    "pqr_sha256": sha256_file(out_pqr),
                    "force_field": ff,
                    "summary": lj_summary,
                    "atoms": [
                        {
                            "name": a.name,
                            "res": a.residue.name,
                            "resid": a.residue.id,
                            "chain": a.residue.chain.id,
                            "element": a.element.symbol if a.element is not None else None,
                            "q": float(q[i]),
                            "sigma_ang": float(sigma_ang[i]),
                            "eps_kcal": float(eps_kcal[i]),
                        }
                        for i, a in enumerate(mod.topology.atoms())
                    ],
                },
                indent=2,
            )
            + "\n"
        )
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
        "protonation": (
            f"hydrogens ADDED by pdbfixer at pH {ph} (--prep); heavy-atom protonation states as given by the input"
            if prep
            else "as given by the input file (PLB: PrepWizard/PropKa/Epik pH 7.4); NO protonation is changed"
        ),
        "histidine_templates_matched": his,
        "n_atoms_written": int(len(xyz)),
        "net_charge_written_e": float(q.sum()),
        "n_atoms_in_cutoff_sphere": int(in_sphere.sum()),
        "net_charge_in_cutoff_sphere_e": float(q[in_sphere].sum()),
        "ignore_external_bonds": True,
    }
    if prep:
        prov["prep"] = prep_report
    else:
        prov["prep"] = False
    if lj_path is not None:
        prov["lj_output"] = Path(lj_path).name
        prov["lj_output_sha256"] = sha256_file(lj_path)
        prov["lj_summary"] = lj_summary
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
    ap.add_argument("--lj", type=Path, default=None, metavar="OUT.lj.json", help="also write LJ sigma/epsilon per atom")
    ap.add_argument(
        "--prep", action="store_true", help="repair an unprepped crystal pocket first (pdbfixer; amber14 only)"
    )
    ap.add_argument("--ph", type=float, default=7.0, help="pH for --prep hydrogen addition (default 7.0)")
    ap.add_argument(
        "--ss-dist", type=float, default=SS_DIST_ANG, help=f"SG-SG disulfide cutoff (default {SS_DIST_ANG} A)"
    )
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
            lj_path=a.lj,
            prep=a.prep,
            ph=a.ph,
            ss_dist_ang=a.ss_dist,
        )
    except PrepError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(json.dumps(prov, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
