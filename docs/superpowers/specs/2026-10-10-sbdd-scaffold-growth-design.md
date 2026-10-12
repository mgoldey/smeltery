# Scaffold-growth candidate generator (DiffSBDD) — design

Status: draft for review. Date: 2026-10-10. Branch: `worktree-generative-sbdd` (from `origin/main`).

## Purpose

Add a **structure-based generative proposer** in front of the funnel: given a parent
molecule, a fixed scaffold (a subset of the parent's atoms) and a receptor pocket, a
pretrained pocket-conditioned diffusion model (DiffSBDD, `inpaint.py`) regrows the rest
of the molecule in the pocket. The generator returns `Candidate`s that the existing
tiers (`PairedPoses`, docking, `FieldInteraction`, `cut`) consume unchanged.

Success looks like: a call that returns connected, sanitized candidates that contain
the scaffold, each with the reason-coded rejection of everything that was not returned,
a run record that says exactly which checkpoint and settings produced them, and a
measured comparison of the model's geometry against smeltery's own embedding.

## Scope

In scope:

1. A `CandidateGenerator` protocol in `smeltery.generate`.
2. `ScaffoldGrowth`, the DiffSBDD-backed implementation, run in a **separate Python
   environment** through a subprocess boundary.
3. Post-processing that repairs and filters raw model output (pure RDKit, no GPU).
4. A switchable pose path: `pose_source="embed"` (SMILES only) and `"model"` (use the
   model's geometry), plus the `PairedPoses` option the second needs.
5. Provenance in the run record.

Out of scope (each gets its own spec): calibrating the docking tier's floors; the
elite-regrowth loop that drives generation from interaction energy and affinity;
DiffInt or any second model; fine-tuning or training; de novo (no-parent) generation.

## Constraints

- **Environment split.** DiffSBDD's tested stack is Python 3.10, torch 2.0.1+cu118,
  Lightning 1.8.4, RDKit 2022.09.5. smeltery requires Python 3.11–3.12 and RDKit
  ≥ 2024.3. They cannot share an interpreter, so torch never enters smeltery's
  dependencies and no extra is added for it. The external environment is configuration.
- **Library genericity.** `generate/` contains no molecule. No campaign name appears in
  executable code (`tests/test_migration_rules.py`); fixtures are named neutrally.
- **Core coordinates are exact.** `PairedPoses` copies mapped core atoms from the parent
  pose; the self-pair anchor (ΔΔE = 0.0 exactly) must survive the model-pose path.
- **Atom order is never assumed.** See "Atom-order invariant".
- **Failures are recorded, not fatal.** One bad sample must not abort a batch.
- **Licensing.** DiffSBDD is MIT; its CrossDocked checkpoint is from Zenodo record
  8183747. The checkpoint is a pickle and loads code; it is loaded only inside the
  external environment. smeltery records its SHA-256 and never loads it.

## Architecture

All new code is under `src/smeltery/generate/`.

| File | Role | Imports |
|---|---|---|
| `base.py` | `CandidateGenerator` protocol, `GenerationResult`, `Rejection` | stdlib, `..model` |
| `sbdd.py` | `ScaffoldGrowth`: writes inputs, runs the subprocess, calls post-processing | stdlib, rdkit (lazy) |
| `sbdd_post.py` | Repair, filter, dedupe, build `Candidate`s | rdkit |
| `_sbdd_runner.py` | Executed **by the external interpreter**; wraps DiffSBDD `inpaint` | stdlib, torch, DiffSBDD only. Imports nothing from smeltery |

`_sbdd_runner.py` is shipped as package data and run by path
(`<python> _sbdd_runner.py <request.json>`), so it needs nothing installed beside it
except the DiffSBDD checkout. It is the only file that touches torch.

### Protocol

```python
class CandidateGenerator(Protocol):
    name: str
    def settings(self) -> dict: ...           # recorded in the run record
    def generate(self, parent: Candidate, scaffold: Sequence[int], pocket: PathLike,
                 *, seed: int) -> GenerationResult: ...

@dataclass
class GenerationResult:
    candidates: list[Candidate]
    rejected: list[Rejection]       # one per raw output not returned, with a reason code
    provenance: dict                # checkpoint sha256, upstream commit, device, wall time, seed
```

`scaffold` is a sequence of heavy-atom indices into the parent's SMILES atom order.
`Rejection.reason` is one of a closed set of codes (below) so callers can count by reason.

## Data flow

1. `ScaffoldGrowth.generate` checks the parent has poses (the bound pose is the
   reference ligand and fixes the coordinates of the scaffold). It writes to a temp dir:
   `parent.sdf` (heavy atoms, the parent's pose 0), `scaffold.sdf` (the scaffold atoms
   at their parent coordinates), the pocket path, and `request.json` (checkpoint,
   `n_samples`, `add_n_nodes`, `device`, `seed`, timesteps, resamplings).
2. It runs `<external python> _sbdd_runner.py request.json` with `CUDA_VISIBLE_DEVICES`
   set from config. The runner writes `grown.sdf` and `report.json` (wall time,
   checkpoint SHA-256, DiffSBDD commit, torch/CUDA versions, exception text if any).
3. `sbdd_post` reads `grown.sdf` **unsanitized**, repairs, filters and builds candidates.
4. `GenerationResult` is returned; nothing is written outside the temp dir unless
   `keep_workdir` is set.

A nonzero runner exit, a missing `grown.sdf`, or a corrupt report raises
`GeneratorError` with the runner's stderr tail. This is a failure of the run, not of a
sample.

## Post-processing

Measured on one scaffold (16 samples, 32-atom fixed scaffold of a 41-heavy-atom parent,
9 atoms added, one GTX 1080, 213 s wall including model load; `--timesteps 50
--resamplings 20`, the `inpaint.py` defaults). Raw output, no repair: every scaffold atom
is within 0.12 Å of its input position and has the right element, but the atoms of each
output are **not in the input order** (DiffSBDD's `batch_to_list` uses an unstable
`argsort`), so scaffold atoms must be found by position. 11 of 16 sanitize, 7 are one
fragment, 4 contain the scaffold's graph (the dropped bonds are C–O single bonds of
1.37–1.50 Å that the distance-based bond builder missed).

What repair can and cannot do, measured on the same 16:

| repair policy | sanitize | connected | contain scaffold (exact bond orders) |
|---|---|---|---|
| force the scaffold's bond orders onto the model's atoms | 0 of 16 | – | – |
| add missing scaffold bonds, drop extra scaffold–scaffold bonds, keep the model's orders | 4 of 16 | 4 | **1 of 16** |

Forcing the scaffold's orders fails because the model places new atoms next to atoms that
are already saturated in the scaffold (a carboxyl oxygen with a neighbour at 1.34 Å, a
piperidine nitrogen with a fourth neighbour): the model never sees bond orders or
valences. The 1 survivor is chemically implausible (a phosphonate ester on the carboxyl).
A second run at the training step count (`--timesteps 500 --resamplings 1`) gave 0 of 16
usable. With bond orders ignored in the scaffold check, 4 of 16 pass, but they include
molecules that absorbed the carboxylic acid into a ring, so that check is too lenient to
be the default. **On this scaffold and settings the model is a poor proposer; the wrapper
must report this, not hide it.** One run, one scaffold; a yield study (scaffold size,
`add_n_nodes`, resamplings, other scaffolds) is a separate piece of work.

Stages, in order, each recorded in `Rejection.reason`:

1. **Match and repair (always on).** Find, for each scaffold atom, the output atom nearest
   its input position (one-to-one, within 0.5 Å, same element). A failure rejects the
   sample (`scaffold_atom_mismatch`). Fit the output rigidly onto the scaffold
   coordinates (Kabsch; residual above 0.3 Å rejects as `scaffold_atom_mismatch`), so the
   scaffold atoms sit exactly where the parent has them. Add any scaffold bond the model
   missed, with the scaffold's bond order, and delete any bond between two scaffold atoms
   that the scaffold does not have. Bonds the model did place are never re-ordered.
   Formal charges on scaffold atoms come from the scaffold. Repair is not a filter and
   has no switch.
2. **Sanitize (required).** An output that does not sanitize cannot yield a SMILES, so
   it is rejected (`unsanitizable`). This stage cannot be disabled.
3. **Optional filters**, each a `ScaffoldGrowth` argument, applied in this order and as
   far upstream as possible, before any pose, tier or expensive step:
   - `require_connected` (default on): one fragment (`disconnected`).
   - `scaffold_match` (default `"exact"`; `"topology"` ignores bond orders; `"off"`): the
     scaffold is a substructure of the result (`scaffold_lost`). `"exact"` can reject a
     product whose partially cut aromatic ring is perceived differently; `"topology"`
     is more lenient and admits molecules that rearranged the scaffold's chemistry.
     `PairedPoses` needs a common core, so `"off"` is allowed but the caller owns the
     consequence.
   - `forbidden_smarts` (default: a short documented list, peroxide O–O first; pass `()`
     to disable): matches reject with `forbidden_substructure:<label>`.
   - `deduplicate` (default on): canonical SMILES with stereo (`duplicate`).
   - `max_heavy_atoms` / `min_heavy_atoms` (default off).
4. **Sampling budget.** `target_accepted` (default off) with `max_batches`: keep
   sampling in batches until that many pass the filters or the budget is spent, so a
   56%-rejection rate does not silently shrink a run. When off, one batch is run.

Every filter is independent and individually switchable. The rejection list is complete:
`len(candidates) + len(rejected)` equals the number of raw outputs.

## Pose source

`pose_source` is a `ScaffoldGrowth` argument.

- **`"embed"`**: returned candidates have `smiles` only and no poses. `PairedPoses`
  works exactly as today. The model acts as a proposer.
- **`"model"`**: each candidate also carries one pose, built from the model's geometry:
  1. the output is fitted rigidly onto the scaffold (stage 1; rmsd at most 0.3 Å, and
     no more than about 0.1 Å per atom on the measured fixture), so the scaffold's heavy
     atoms sit close to the parent's coordinates and the grown atoms keep their position
     relative to the scaffold;
  2. heavy atoms are put in the candidate SMILES's atom order (below);
  3. hydrogens are added with `Chem.AddHs(addCoords=True)`; there is no relaxation, so
     no heavy-atom coordinate changes.

`PairedPoses` gains `analogue_poses_given: bool = False` (a ninth key in `settings()` and
on `docs/tiers/paired_poses.md`). When set and a candidate already has poses, its first
pose is pose 0 of the pair and poses 1..n-1 come from the existing path, so the pose
count stays `len(parent.poses)`. The given pose is checked, not trusted: its symbols must
equal those of the candidate SMILES with hydrogens added, and each heavy atom the
common-substructure search maps onto the parent must lie within 0.5 Å of the parent's
pose-0 position, otherwise `ValueError` (a sanity check against a mis-mapped core, such
as a flipped ring, not a test of exactness). Every mapped atom, hydrogens included, is
then overwritten with the parent's pose-0 coordinate, so the core is an exact copy as in
the default path. A given pose is not relaxed, whatever `relax_unmapped` says. The default behavior is unchanged, and the self-pair
anchor is extended to the new path.

### Atom-order invariant

`PairedPoses` and the tiers read poses in the atom order of `AddHs(MolFromSmiles(smiles))`.
Model output arrives in the model's order, which is not the SMILES order, and a silently
scrambled order produces the right atoms in the wrong places with no error (the lesson
recorded in `docking/united_atom.py` for Meeko). Therefore: the candidate's SMILES is
the canonical SMILES of the repaired molecule; its heavy atoms are renumbered to that
SMILES's output order; hydrogens are added after renumbering; and a test asserts
`tuple(pose.symbols)` equals the symbols of `AddHs(MolFromSmiles(candidate.smiles))`
and that heavy-atom coordinates are unchanged by the renumbering (within 1e-9 Å) for
every fixture molecule. A pose that fails the check is rejected (`atom_order`).

## Provenance and the run record

`GenerationResult.provenance` and `ScaffoldGrowth.settings()` carry: checkpoint path and
SHA-256, DiffSBDD git commit (read by the runner from the checkout, `dirty` flag
included), torch and CUDA versions, device name, seed, sampling parameters, all filter
settings, `pose_source`, wall time, and counts per rejection reason. `record.py` is not
changed: the caller puts `generator.settings()` and `result.provenance` into
`RunRecord.inputs` (they are plain JSON-able dicts), where they enter the input digest
alongside the ferric identity. The runner reports a dirty DiffSBDD checkout rather than
hiding it, and lists the runtime patches it applied (see Risks).

## Error handling

- Run-level failure (interpreter missing, import error, CUDA error, runner crash):
  `GeneratorError`, with the stderr tail and the exit code.
- Sample-level failure: a `Rejection`, never an exception.
- Misconfiguration (scaffold index out of range, scaffold not a connected subgraph of
  the parent, missing pocket file, checkpoint path absent): `ValueError` before any
  subprocess starts.
- GPU contention: the runner records free GPU memory in its report, and a CUDA
  out-of-memory error becomes a `GeneratorError` that says so; `n_samples` per run is
  configurable. A shared GPU is a known condition on this machine (another process held
  about 3 GB per card).

## Testing

No test needs a GPU, torch, or the external environment, except the one marked smoke.

- **Post-processing** on fixture SDFs checked in under `tests/data/` and named
  neutrally: the raw output of the run above (16 molecules, generated from the scaffold
  and parent that ship beside it), plus hand-made cases for each rejection reason. Every
  stage has a test that it fires on a violation and does not fire on a clean molecule;
  graft is mutation-tested (remove the graft and the 12 scaffold-lost fixtures fail).
- **Atom-order invariant** (above), on every fixture.
- **Anchors:** the self-pair stays exactly 0.0 with `analogue_poses_given=True`; scaffold
  coordinates in model poses equal the parent's bit for bit; an analogue whose given pose
  has a wrong atom order is refused.
- **Runner contract** with a fake runner script that writes a canned `grown.sdf` and
  `report.json`; covers nonzero exit, missing output and corrupt report.
- **Boundary tests:** `tests/test_migration_rules.py` still passes; the new modules name
  no campaign target.
- **GPU smoke** (`@pytest.mark.gpu`, skipped unless `SMELTERY_SBDD_PYTHON` and a
  checkpoint are set): one tiny run on the bundled example complex from the DiffSBDD
  repository.
- The tier docs tests (`tests/test_tier_docs.py`) keep passing: `docs/tiers/paired_poses.md`
  gains the ninth settings row. CI has no test-count gate to update.

## Risks and open items

- **DiffSBDD needs two runtime fixes.** `inpaint.py` fails on GPU in `utils.batch_to_list`
  (a CPU/CUDA index error), and `prepare_substructure` reads a module global `model` that
  exists only when the file runs as a script. The runner applies both at import time
  (wraps `utils.batch_to_list`; sets `inpaint.model`) and lists them under `patches` in
  its report. The local clone's edit to `utils.py` is reverted so the checkout is clean
  and the commit hash in the report is the upstream one.
- **Model quality is poor on the scaffold measured.** 1 of 16 samples survived repair and
  the exact scaffold check, and that one is chemically implausible (see Post-processing).
  The wrapper, filters and accounting are model-independent and worth having either way,
  but this scaffold-growth setup does not yet yield usable analogues. Whether a smaller
  fixed scaffold, a stated attachment atom, other `add_n_nodes` or resampling values, or
  another model helps is the next piece of work (a yield study), and the head-to-head
  against `substituent_scan` comes after it. Each spec of that work starts from the
  rejection counts this wrapper reports.
- **One pose per molecule.** The model gives one geometry. The funnel pairs ensembles,
  so the model path yields n = 1 model pose plus the jittered ones, and the SEM then
  mixes the two kinds. Whether that is acceptable is exactly what the switchable design
  lets us measure.
- **Pocket cropping.** DiffSBDD crops the pocket around the reference ligand itself; the
  full 407-residue receptor file works but its cropping radius is DiffSBDD's, not ours.
- **Checkpoint scope.** The CrossDocked checkpoint is trained on drug-like ligands from
  PDB complexes; behaviour on large macrocyclic or peptidic scaffolds is unmeasured.
- **Unverified.** Whether grafting the scaffold's bond orders can create valence errors
  at the junction with grown atoms is not yet tested; such samples are rejected as
  `unsanitizable`, and their rate is reported.

## Acceptance criteria

1. `ScaffoldGrowth(...).generate(...)` returns a `GenerationResult` where
   `len(candidates) + len(rejected)` equals the raw output count, on the fixture run and
   on a fake-runner run.
2. On the fixture SDF (16 raw outputs) with default settings: 1 accepted, 12
   `unsanitizable`, 3 `scaffold_lost`, none of any other reason (measured, then pinned).
   With `scaffold_match="topology"`: 4 accepted.
3. With every optional filter off, only `scaffold_atom_mismatch` and `unsanitizable`
   rejections remain, and 4 of the 16 fixture outputs are returned.
4. `pose_source="model"` candidates pass the atom-order check and the exact-core check,
   and the self-pair anchor holds with `analogue_poses_given=True`.
5. The run record carries the checkpoint SHA-256 and the DiffSBDD commit.
6. The full suite passes with no GPU present, and ruff check and format are clean.
7. One GPU smoke run on this machine completes, and its wall time, accepted count and
   rejection counts are printed. It is not required to accept any candidate: on the
   measured scaffold the expected yield is about 1 in 16, and a run that accepts none
   is a result, not a failure.
