"""Bisect the 7LCJ binding-energy discrepancy: -28.758 (this port) vs the -17.41 issue #11 states.

Runs the vacuum SCF ONCE, then one field SCF per input variation, and prints each
delta_e next to the target. The variations are the inputs that differ between
the ported code and a plausible original run: the pdb2pqr force field, a pocket
truncation radius, and the ligand-overlap filter. Basis and method are held at
the port's def2-svp RHF: the issue's 137.3 s matches this basis on a free machine
(the heavy test records ~2.5 min free, 75 min on 2 niced cores), so a smaller basis
is unlikely to be the explanation.

    OPENBLAS_NUM_THREADS=1 uv run python scripts/bisect_binding_energy.py [--quick]

Needs ferric and `pdb2pqr30` on PATH. ~2.5 min per variation on a free machine.
Nothing here is fitted: a variation that lands near -17.41 is a lead to confirm
against the original run's inputs, not a reason to change the default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from smeltery.model import HARTREE_TO_KCAL
from smeltery.pocket import compute_energy, embed_ligand, load_pocket

DATA = Path(__file__).resolve().parents[1] / "tests" / "data" / "pocket_fixture"
LIGAND = DATA / "conf_00_cryo_em.xyz"
PDB = DATA / "7LCJ_pocket.pdb"
TARGET = -17.41


def centroid(xyz: Path) -> tuple[float, float, float]:
    rows = [line.split() for line in xyz.read_text().splitlines()[2:] if line.strip()]
    n = len(rows)
    return tuple(sum(float(r[i]) for r in rows) / n for i in (1, 2, 3))


def variants(quick: bool):
    c = centroid(LIGAND)
    yield "baseline (AMBER, no cutoff, overlap 1.5 A)", dict(ff="AMBER"), 1.5
    yield "overlap filter 1.0 A", dict(ff="AMBER"), 1.0
    yield "overlap filter 2.5 A", dict(ff="AMBER"), 2.5
    for r in (12.0, 10.0) if quick else (15.0, 12.0, 10.0, 8.0):
        yield f"pocket cutoff {r:g} A about the ligand centroid", dict(ff="AMBER", cutoff_ang=r, center_ang=c), 1.5
    if not quick:
        yield "force field CHARMM", dict(ff="CHARMM"), 1.5
        yield "force field PARSE", dict(ff="PARSE"), 1.5


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="fewer variations")
    args = ap.parse_args()

    e_vac = None
    rows = []
    for label, load_kw, overlap in variants(args.quick):
        field = load_pocket(PDB, **load_kw)
        emb = embed_ligand(LIGAND, pocket=field, basis="def2-svp", overlap_cutoff_angstrom=overlap)
        if e_vac is None:
            e_vac = compute_energy(emb, use_field=False)
            print(f"vacuum SCF: {e_vac.energy:.10f} Eh (converged={e_vac.converged})", flush=True)
        e_f = compute_energy(emb, use_field=True)
        d = (e_f.energy - e_vac.energy) * HARTREE_TO_KCAL
        rows.append((label, len(emb.charges or []), d, e_f.converged))
        print(f"{label:55s} n_q={rows[-1][1]:5d}  dE={d:9.3f}  converged={e_f.converged}", flush=True)

    best = min(rows, key=lambda r: abs(r[2] - TARGET))
    print(f"\nclosest to {TARGET}: {best[0]} (dE={best[2]:.3f}, off by {best[2] - TARGET:+.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
