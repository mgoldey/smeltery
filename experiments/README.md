# `experiments/` — campaigns, not libraries

## Provenance: moved from ferric (issue #13)

This directory was moved from `mgoldey/ferric` (`experiments/`, plus the part of `tools/` the
campaign imports) at ferric commit `922a3b9b6203e10fedbb4e143ac24f0352e55230`. It is a **frozen record**: the code that produced
the numbers in `danuglipron/RESULTS.md`, kept as it ran, so those numbers stay reproducible
and do not drift when `src/smeltery` changes. Git history was not carried over; ferric's history
holds it.

What is here and what was changed:

- `danuglipron/frozen_tools/`: the 38 modules of ferric's `tools/` that the campaign imports
  (transitive closure), with their tests. Imports `tools.X` became
  `experiments.danuglipron.frozen_tools.X`; apart from the path changes in the next bullet, nothing else
  in the code was edited.
  These are **not** `smeltery.*`: several smeltery ports differ on purpose (for example
  `dock_ligand`'s default `exhaustiveness` is 32 here and 4 in `smeltery.docking`, and pocket
  charges are Bohr tuples here and Angstrom `PointCharge`s there), so repointing a driver
  changes its numbers. Rebasing the drivers on smeltery is the work of issues #6 and #35.
- Repo-relative data paths now point into `danuglipron/data/` (the committed conformer ensemble,
  `7LCJ_pocket.pdb`, and three small molecules); one test's repo-root depth (`parents[K]`) grew by 2.
- **Not moved**, because they test ferric itself or its docs, not this code: the tests that read
  ferric's `site/` pages (`test_answer_table_is_live`, `test_cost_claims_are_sourced`,
  `test_golden_path_smoke`, `test_whole_chain_smoke`, `test_qmmm_page_is_current`), the audit of
  ferric's `tools/` source (`test_xyz_roundtrip_is_the_same_molecule`), two tests that read ferric's
  Rust source (`test_python_and_rust_element_heuristics_agree`,
  `test_the_qm_mm_dispersion_comment_does_not_claim_missing_code`), and the tests of modules that
  are not moved (`mm_topology`, `solvate`, `viz`, `tox.__main__`, `campaign.jsonl`,
  `morph.paired`/`topology`, `pipeline.cost`, `active_site.pose_relaxation`).
- Run: `uv run --extra dev --extra experiments python -m pytest experiments`.
  The drivers (`run_*.py`) are compute campaigns (docking, xtb, DFT; tens of minutes each) and are
  not run by the tests; they were not re-run in the move.
- Lint excludes this directory (`extend-exclude` in `pyproject.toml`): restyling would defeat the freeze.

This directory holds **specific scientific investigations**. Each subdirectory is
one campaign: its hypotheses, its drivers, its results, and its evidence.

## The boundary

| | `src/smeltery/` | `experiments/` |
|---|---|---|
| contains | reusable machinery | one campaign's hypotheses and findings |
| knows about | molecules in general | *this* molecule, *this* target |
| lifetime | as long as it is useful | permanent record of what was measured |
| when it changes | to fix or extend a capability | when new measurements land |

A rule that keeps the split honest: **`src/smeltery/` must not import from
`experiments/`**, and must not contain a named molecule, target, or hypothesis.
The reverse direction is expected — a campaign imports the machinery it needs.

`src/smeltery/` docstrings *do* cite campaign measurements ("measured 2026-08-29 on the
danuglipron ensemble: ..."), and that is deliberate. A library rule justified by
a real observation is far more useful than an unattributed assertion, and the
citation is provenance, not a dependency.

## Layout of a campaign

```
experiments/<name>/
  PLAN.md       pre-registered design: hypotheses, arms, exactness anchors, and
                the artifact hypothesis stated BEFORE measuring. Not updated
                with results -- rewriting a prediction after seeing the outcome
                is what a pre-registration exists to prevent.
  RESULTS.md    measurements, kept separate from interpretation. Read first.
  README.md     how to re-run it, and how to read its gates.
  design.py     this campaign's hypothesis set (structures, constraints).
  run_*.py      the drivers.
  tests/        tests of THIS campaign's designs, not of the machinery.
  out/          gitignored scratch; evidence promoted with `git add -f`.
```

## Current campaigns

- **`danuglipron/`** — can conformers or structural changes to danuglipron
  (PF-06882961) reduce toxicity liability while keeping GLP-1R active-site fit?
  Reached a definitive **negative**: candidate pose generation failed (no
  generated conformer reaches the bound pose within 2.0 Å), so no fit ranking is
  licensed. The strain result is independent of that and stands. See its
  `RESULTS.md`.
