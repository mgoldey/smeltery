# smeltery

A tiered screening funnel for biochemical systems that ranks only what it can resolve.

smeltery pushes candidate molecules (substitution analogues of a lead, for
example) through successive tiers of increasingly expensive physics. At each
tier it keeps the ones that are measurably better. It runs on the
[ferric](https://github.com/mgoldey/ferric) quantum-chemistry engine.

## What it does differently

- **Compares differences, never totals.** Total energies scale with molecular
  size, so ranking analogues of different formula by them is meaningless.
  smeltery ranks a *paired* ΔΔE against the parent compound instead, and
  refuses to rank totals across formulas.
- **Pairs poses.** Analogue pose *i* is built on parent pose *i*, so
  pose-to-pose noise cancels. In the example below this cuts the
  uncertainty 10–17×.
- **Admits when it can't tell.** If two candidates are inside each other's
  uncertainty, the cut reports a tie, or "unranked", instead of an order.
- **Records provenance.** Every run writes a JSON record with the exact ferric
  commit (read from the installed package's metadata), settings and an input
  digest.

## Quickstart

Requires Python 3.11–3.12, [uv](https://docs.astral.sh/uv/), and ferric's build
prerequisites (Rust, libint2, OpenBLAS; see ferric's README). uv builds ferric
from source at a pinned commit.

```bash
uv sync --extra dev
OPENBLAS_NUM_THREADS=1 uv run pytest -q
OPENBLAS_NUM_THREADS=1 uv run python examples/mwe_benzoic.py
```

No ferric (e.g. a cloud sandbox where it won't build)? `scripts/dev-check.sh` runs
the tests in a venv without it: a labelled stub is installed and tests marked
`needs_ferric` are skipped, not failed. It is a smoke check, not a substitute for
CI, and prints passed / skipped-needs-ferric / failed (non-zero on any failure).

## Providers

Structure, scoring, docking, property and potential providers plug in through the
`smeltery.providers` entry-point group and are checked by a shared conformance suite;
see [docs/providers.md](docs/providers.md). Tool and data licences, with the date each was read and which
are excluded: [docs/licensing.md](docs/licensing.md).

## Tiers

Each tier has a page stating what it computes, its settings, its measured cost, its gates, its
systematic floor and what it is not licensed to claim: [docs/tiers/index.md](docs/tiers/index.md).

## Example

`examples/mwe_benzoic.py` places benzoic acid and two para-substituted
analogues in a two-charge, arginine-like pocket. It builds six paired poses
and computes each pose's field interaction energy,
ΔE_int = E(in field) − E(vacuum), with RHF/STO-3G:

```
candidate  formula                paired ddE  unpaired SEM
benzoic    C7H6O2        +0.0000 ± 0.0000 kcal/mol             -
4-F        C7H5FO2       +1.8097 ± 0.0683 kcal/mol        1.2054
4-Cl       C7H5ClO2      +5.0243 ± 0.1228 kcal/mol        1.1959

cut (keep=1, z=2): survivors=['4-F'] | groups: [['4-F'], ['4-Cl']]
```

Electron-withdrawing substituents weaken binding to the cationic pocket, and
Cl > F follows their Hammett σp values.

### The same run from a config

`smeltery run` drives the same stages from a TOML file (`examples/mwe_benzoic.toml`):

```bash
uv run smeltery run examples/mwe_benzoic.toml --plan   # poses, pocket, parity, digest; no SCF
OPENBLAS_NUM_THREADS=1 uv run smeltery run examples/mwe_benzoic.toml
```

It prints the same numbers as the script above, lists tie groups, prints `UNRANKED`
(exit 0) when nothing can be ordered, and writes a run record under `out/`. A bad
config exits 2; a failed stage (no common core, odd electron count, SCF not
converged) exits 3 and names the stage. The config must state `[cut] floor`
explicitly while `FieldInteraction`'s systematic floor is unmeasured, and the record
says so. Measured 2026-10-10 on one pinned core (`taskset -c`), box load about 12:
6 min 55 s wall, 403 s CPU, 181 MB peak (ferric `b22183b`). This is a slice of the
campaign path: structure and prep are not wired in.

With a `[docking]` section the parent is docked with Vina and the analogues are paired to its docked poses
(`examples/mwe_docking.toml`, needs the `docking` and `posebusters` extras); `[gates]` runs PoseBusters, an MMFF
energy and strain record, and an xtb energy record before the SCFs. Analogue poses built by pairing fail PoseBusters
at the junction between the copied core and the new substituent; `[poses] relax_unmapped = true` relaxes only the
substituent and leaves every core coordinate exactly the parent's.

### Starting from a target

A `[structure]` section replaces the hand-prepared receptor, box centre and pocket file
(`examples/mwe_structure.toml`, committed fixtures only, same extras plus `pdb2pqr30`):

```bash
uv run --extra docking --extra posebusters smeltery run examples/mwe_structure.toml --out out
```

The structure comes from a `StructureProvider` (a `.pdb` path; a PDB id or, with `provider = "afdb"`, a UniProt
accession, both only with `allow_network = true`). The run writes a protein-only receptor PDB and its Meeko PDBQT
to `out/structure/`, docks the parent in a box centred on `center = [x, y, z]` or on `reference_ligand = "RES"` (the
centroid of that HETATM residue's heavy atoms), and cuts the point-charge field at `pocket_cutoff` around the same
centre. The cutoff is required: truncation is not monotone, so there is no default. The record names both prepared
files by sha256, says whether the structure is EXPERIMENTAL or PREDICTED (a prediction carries its licence,
attribution and the mean pLDDT around the pocket), lists what was dropped (all HETATM by residue name, other
altlocs, unselected chains) and the frame checks: box centre near the receptor, field covering the box faces (and
whether it covers the corners), PDBQT atoms coinciding with the receptor PDB, no receptor atoms inside the box lost
to Meeko, the docked parent inside the box. A failed check is a stage error (exit 3).

The table and the record (`results.funnel`) give each stage's candidates in and out. Gates (parity, PoseBusters,
and MMFF strain when `[gates] max_strain_kcal` is set) either pass everyone or stop the run loudly; MMFF and xtb
without a threshold are recorded, not filters. Only the paired-ddE cut removes candidates, and it counts analogues
(the parent is the reference); an unranked cut has no survivor count. There is one ranked stage: the only wired
ranker, `FieldInteraction`, has an unmeasured systematic floor, and a second rung would need a second
unmeasured floor stated in the config, so it is not offered.

## Correctness checks

`tests/test_anchors.py` checks the trivial limits, where the machinery must do
nothing:

- A molecule paired with itself gives a ΔΔE of exactly 0.0 on every pose. The
  test also checks that the field is non-zero, so this check can't pass
  vacuously.
- An empty field gives exactly zero interaction.
- An analogue's core coordinates are copied verbatim from the parent pose.
- smeltery's Å→Bohr conversion is exactly the one ferric uses, so point
  charges and atoms share one frame.

Each check was mutation-tested: breaking the pose copy, ranking a total
energy, or passing charges in Å each makes a test fail.

## Status

This is an early working example. The tier protocol, the paired-ΔΔE funnel,
the resolution-aware cut and the run record work end to end. Vina docking works with a
flexible ligand and, optionally, flexible receptor sidechains
(`prepare_target(pdb, workdir, flex_residues=["A:45"])` also picks the box from the
co-crystal ligand or pocket residues; `ensemble_score` pools a pose ensemble's
scores). Scores are an empirical ranking heuristic, not binding free energies.
A force-field tier (MMFF in core; OpenMM + OpenFF in an optional conda environment, see docs/environments.md) exists; real receptor pockets are next.
`smeltery.tiers.Qmmm` is a single-point QM/MM tier
with a real covalent cut (scaled-position link H, boundary-charge scheme default Z1, refusals before the SCF);
it needs ferric >= v0.1.0rc7 (QM-MM Lennard-Jones follows the bond list across the cut, ferric PR #336) and
skips/refuses on older builds. Its parameter source is caller-supplied (`MmParameters`); no geometry optimization yet.

## License

Licensed under either of [MIT](https://github.com/mgoldey/smeltery/blob/main/LICENSE-MIT) or [Apache-2.0](https://github.com/mgoldey/smeltery/blob/main/LICENSE-APACHE), at your option.
