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
A force-field tier and real receptor pockets are next.

## License

Licensed under either of [MIT](https://github.com/mgoldey/smeltery/blob/main/LICENSE-MIT) or [Apache-2.0](https://github.com/mgoldey/smeltery/blob/main/LICENSE-APACHE), at your option.
