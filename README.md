# smeltery

A tiered screening funnel for biochemical systems that ranks only what it can resolve.

smeltery takes a lead compound and its analogues and runs them through tiers of
increasingly expensive physics. At each tier it keeps only the candidates that are measurably
better. It runs on the [ferric](https://github.com/mgoldey/ferric) quantum-chemistry engine.

## Quickstart

You need Linux x86_64, Python 3.11 or 3.12, and [uv](https://docs.astral.sh/uv/). ferric
installs as a prebuilt wheel from PyPI, so you don't need a compiler.

```bash
git clone https://github.com/mgoldey/smeltery && cd smeltery
uv sync --extra dev
```

**1. Check the install (about 1 s, no SCF).** `--plan` builds the poses and the pocket and
estimates the cost. It runs no quantum chemistry.

```bash
uv run smeltery run examples/mwe_benzoic.toml --plan
```

It should end with `plan OK (no SCF run)` and an `input_digest`.

**2. Run the example (about 7 min on one core).** This runs benzoic acid and two
para-substituted analogues in a two-charge, arginine-like pocket. That's 6 paired poses per
candidate at RHF/STO-3G.

```bash
OPENBLAS_NUM_THREADS=1 uv run smeltery run examples/mwe_benzoic.toml --out out/benzoic
```

```
candidate  formula                paired ddE  unpaired SEM
benzoic    C7H6O2        +0.0000 ± 0.0000 kcal/mol             -
4-F        C7H5FO2       +1.8097 ± 0.0683 kcal/mol        1.2054
4-Cl       C7H5ClO2      +5.0243 ± 0.1228 kcal/mol        1.1959

cut (keep=1, z=2): survivors=['4-F'] | groups: [['4-F'], ['4-Cl']]
```

ΔΔE is each analogue's field interaction energy relative to the parent, on paired poses.
Electron-withdrawing substituents weaken binding to the cationic pocket, so Cl ranks worse
than F, in line with their Hammett σp values. The paired error bars are 10–17× smaller than
the unpaired ones. `out/benzoic/` is a self-contained run directory: the config as it was
run, copies of every input, the run record (`run-*.json`) and a per-SCF log.

**3. Look at it in a GUI (optional).** There are two. Both open in your browser.

- **`smeltery gui`** opens one run in a Marimo app with five tabs:
  - **Results**: the ΔΔE forest plot with tie groups and the cut, plus per-pose lines.
  - **Grid**: candidates, where you record keep/drop decisions with a reason.
  - **Pose**: each analogue pose overlaid on the parent's in Mol\*, with the receptor and
    the field charges.
  - **Compare**: runs side by side, per key.
  - **Pipeline**: a config builder.

  It needs only the `gui` extra, with no Node or build step. The browser does load Mol\* and
  RDKit.js from CDNs.

  ```bash
  uv run --extra gui smeltery gui out/benzoic
  ```

  To look at it before running anything, open a finished run that ships with the repo:
  `uv run --extra gui smeltery gui src/smeltery/prebuilt/benzoic-rhf/run`. Recording a
  decision there writes into the source tree, so copy the run elsewhere if you want to
  record decisions.

  Add `--no-browser` to only print the URL, for example on a remote machine with a forwarded
  port. Add `--desktop` (needs the `desktop` extra) to get a native window instead of a tab.
  More in [docs/gui.md](docs/gui.md).

- **`smeltery bench`** is a workspace for every run under a directory, and the place to build a
  pipeline from nothing. It opens on **Start here**, which offers three ways to start: copy one of
  five working pipelines (each with a finished example run), start from your own parent and
  analogues, or continue from a run you already have. From there you edit each step, choose a PDB
  from your computer for the active site, see what an edit will cost, and run it. In a source
  checkout you have to build its UI once first, which needs Node:

  ```bash
  (cd ui && npm ci && npm run build)
  uv run --extra bench smeltery bench out --open
  ```

  It works on an empty directory, so this is where to start when you have no runs yet:
  `mkdir work && uv run --extra bench smeltery bench work --open`.

**4. Start your own pipeline.**

```bash
uv run smeltery golden init --out myrun.toml --parent lead --parent-smiles "c1ccccc1C(=O)O"
# add analogue SMILES under [candidates] in myrun.toml, then:
uv run smeltery run myrun.toml --plan
uv run smeltery run myrun.toml --out out/myrun
```

[docs/golden-path.md](docs/golden-path.md) goes from there to a shareable bundle.

### Troubleshooting

- **`uv sync` or `uv run` is compiling ferric.** Something is pointing `[tool.uv.sources]` at
  a git commit or a local path. That setup is only for developing against unreleased ferric,
  and it needs Rust, libint2 and OpenBLAS (see ferric's README). Remove it to get the wheel.
- **Not on Linux x86_64.** ferric publishes no wheels for other platforms yet, so you'd have
  to build it from source as above. `scripts/dev-check.sh` runs the tests against a labelled
  ferric stub instead. Tests marked `needs_ferric` are skipped, not failed. It's a smoke check
  and doesn't replace CI.
- **`smeltery bench` exits 2.** The UI isn't built. Run `cd ui && npm ci && npm run build`.
- **`smeltery gui` says "need exactly one run-\*.json".** Point it at the `--out` directory
  of a single run, or at that run's `run-*.json` file directly.
- **Slow or thrashing SCFs.** Set `OPENBLAS_NUM_THREADS=1`. ferric parallelizes with
  Rayon, and BLAS threads on top of that oversubscribe the machine.
- **Exit codes.** `smeltery run` exits 0 on success, including when it prints `UNRANKED`. It
  exits 2 for a bad config, and 3 when a stage fails (for example no common core or an SCF
  that didn't converge); the message names the stage. With `--fail-on-unranked` an `UNRANKED`
  result exits 4 instead (in-process runs and `--combine`, not `--temporal`). `smeltery run`
  refuses (exit 2) an `--out` that already holds a `run-*.json`; pass `--force` to overwrite.

Running the tests and the other checks is covered in [AGENTS.md](AGENTS.md).

## What it does differently

- **Compares differences, never totals.** Total energies scale with molecular size, so
  ranking analogues with different formulas by total energy is meaningless. smeltery ranks
  a *paired* ΔΔE against the parent compound, and refuses to rank totals across formulas.
- **Pairs poses.** Each analogue pose *i* is built on parent pose *i*, so pose-to-pose noise
  cancels.
- **Admits when it can't tell.** If two candidates are within each other's uncertainty, the
  cut reports a tie, or "unranked", instead of an order.
- **Records provenance.** Every run writes a JSON record with the exact ferric commit, the
  settings and an input digest.

## Documentation

| To... | Read |
|---|---|
| Walk one pipeline from its config to its numbers | [docs/worked-example.md](docs/worked-example.md) |
| Go from a target to a shareable result | [docs/golden-path.md](docs/golden-path.md) |
| See what each tier computes, costs and may claim | [docs/tiers/index.md](docs/tiers/index.md) |
| Use the Bench web app | [docs/bench.md](docs/bench.md) |
| Use the Marimo GUI | [docs/gui.md](docs/gui.md) |
| Plug in structure, docking, scoring or potential providers | [docs/providers.md](docs/providers.md) |
| Run long work under Temporal | [docs/temporal-runs.md](docs/temporal-runs.md) |
| Set up optional environments (OpenMM, ML potentials) | [docs/environments.md](docs/environments.md) |
| Check measured limits | [docs/limits.md](docs/limits.md) |
| Look up a term (ddE, floor, tie group, rung) | [docs/glossary.md](docs/glossary.md) |
| Check tool and data licences | [docs/licensing.md](docs/licensing.md) |

## Configs beyond the minimal example

`smeltery run` reads one TOML file. The shipped examples add one section at a time:

- **`[field_dft_interaction]`** measures with KS-DFT instead of RHF (for example
  `functional = "PBE"`; [tier page](docs/tiers/field_dft_interaction.md)). A config selects
  one of the two, never both. D3(BJ) is recorded per pose as `E_d3` and doesn't enter ΔE_int,
  because at a fixed geometry it's the same in and out of the field.
- **`[docking]`** (`examples/mwe_docking.toml`, needs the `docking` and `posebusters` extras)
  docks the parent with Vina and pairs the analogues to its docked poses. `[gates]` runs
  PoseBusters, an MMFF energy and strain record, and an xtb energy record before the SCFs.
  `[poses] relax_unmapped = true` relaxes only the new substituent, which otherwise fails
  PoseBusters at its junction with the copied core.
- **`[structure]`** (`examples/mwe_structure.toml`, same extras plus `pdb2pqr30`) starts
  from a target instead of a hand-prepared receptor. The structure can be a `.pdb` path, a
  PDB id, or an AlphaFold accession with `provider = "afdb"`; the last two need
  `allow_network = true`. smeltery prepares the receptor and centres the docking box on
  `center` or a `reference_ligand`. It cuts the point-charge field at `pocket_cutoff`, which
  is required because truncation is not monotone. Before any SCF it checks that the box,
  the field and the docked pose agree, and a failed check stops the run (exit 3).

  ```bash
  uv run --extra docking --extra posebusters smeltery run examples/mwe_structure.toml --out out
  ```

The config must state `[cut] floor` explicitly while `FieldInteraction`'s systematic floor is
unmeasured, and the run record notes this. The gates (parity, PoseBusters, and MMFF strain
when `[gates] max_strain_kcal` is set) either pass every candidate or stop the run. Only the
paired-ΔΔE cut removes candidates. `results.funnel` in the record gives each stage's
candidates in and out.

## Long runs: Temporal and the dashboard

For long work, Temporal is the system of record. smeltery keeps no process table or status
store of its own, so a task's status, timings and exit code outlive the worker that ran it. A
task runs at most once, because an arbitrary shell command isn't safe to re-run
automatically. A task whose worker died is reported as failed, and resubmitting it is your
decision. This needs the `temporal` extra and a reachable Temporal server (`--temporal-host`,
default `127.0.0.1:7233`), for example `docker run temporalio/temporal:latest server start-dev`.

```bash
uv run --extra temporal smeltery task-worker &             # executes submitted tasks
uv run --extra temporal smeltery dashboard out/ --port 8123 # runs, task status, task submission
```

If Temporal is down, the run list still works, and task operations return 503 or raise
`TemporalUnavailable` rather than quietly falling back. More in
[docs/temporal-runs.md](docs/temporal-runs.md).

## Correctness checks

`tests/test_anchors.py` checks the trivial limits, where the machinery must do nothing:

- A molecule paired with itself gives a ΔΔE of exactly 0.0 on every pose. The test also
  checks that the field is non-zero, so it can't pass vacuously.
- An empty field gives exactly zero interaction.
- An analogue's core coordinates are copied verbatim from the parent pose.
- smeltery's Å→Bohr conversion is exactly the one ferric uses, so point charges and atoms
  share one frame.

Each check was mutation-tested: breaking the pose copy, ranking a total energy, or passing
charges in Å each makes a test fail.

## Status

This is an early working example. The tier protocol, the paired-ΔΔE funnel, the
resolution-aware cut, the run record, the golden path, Bench, the dashboard and the Temporal
task layer all work end to end.

- Vina docking supports flexible receptor sidechains. Scores are an empirical ranking
  heuristic, not binding free energies.
- A force-field tier exists: MMFF in core, and OpenMM + OpenFF in an optional conda
  environment.
- `smeltery.tiers.Qmmm` is a single-point QM/MM tier with a real covalent cut. It needs
  ferric ≥ 0.1.0rc7 and has no geometry optimization yet.
- Only one stage is ranked (`FieldInteraction`), because its systematic floor is still
  unmeasured.

Measured limits, with sizes and sources: [docs/limits.md](docs/limits.md).

## License

Licensed under either of [MIT](https://github.com/mgoldey/smeltery/blob/main/LICENSE-MIT) or [Apache-2.0](https://github.com/mgoldey/smeltery/blob/main/LICENSE-APACHE), at your option.
