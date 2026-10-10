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

Observed on one run (16 samples, 32-atom fixed scaffold, 9 atoms added, single GTX 1080,
213 s wall including model load): all 16 kept the scaffold's coordinates (max 0.12 Å) and
elements; 11 sanitized; 7 were one connected fragment; only 4 contained the scaffold's
graph. Every one of the 16 missing scaffold bonds was a C–O single bond of 1.37–1.50 Å,
dropped by DiffSBDD's distance-based bond builder. This is one run and one scaffold; the
numbers motivate the design and are not a benchmark.

Stages, in order, each recorded in `Rejection.reason`:

1. **Graft scaffold bonds (repair, always on).** For each scaffold atom, find the output
   atom at the same position (nearest, within a tolerance, one-to-one), check the
   element matches, then add any scaffold bond that is missing and set the scaffold's
   bond orders. An element mismatch or a non-unique match rejects the sample
   (`scaffold_atom_mismatch`). Repair is not a filter and has no switch.
2. **Sanitize (required).** An output that does not sanitize cannot yield a SMILES, so
   it is rejected (`unsanitizable`). This stage cannot be disabled.
3. **Optional filters**, each a `ScaffoldGrowth` argument, applied in this order and as
   far upstream as possible, before any pose, tier or expensive step:
   - `require_connected` (default on): one fragment (`disconnected`).
   - `require_scaffold` (default on): the scaffold is a substructure of the result under
     the original bond orders (`scaffold_lost`). `PairedPoses` needs a common core, so
     turning this off is allowed but the caller owns the consequence.
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
- **`"model"`**: each candidate also carries pose 0, built from the model's geometry:
  1. scaffold atoms are overwritten with the parent pose's exact coordinates;
  2. the new atoms keep the model's coordinates;
  3. hydrogens are added with `Chem.AddHs(addCoords=True)` and only the hydrogens are
     relaxed (heavy atoms held), so no heavy-atom coordinate changes.

`PairedPoses` gains `analogue_poses_given: bool = False`. When set, a candidate that
already has poses keeps its pose 0 and remaining poses come from the existing
jittered-ensemble path; pose count stays `len(parent.poses)`. The default behavior is
unchanged, and the self-pair anchor test is extended to cover the new path.

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
settings, `pose_source`, wall time, and counts per rejection reason. `record.py` embeds
the result in the run record alongside the ferric identity. The runner reports a dirty
DiffSBDD checkout rather than hiding it; the local one has a one-line `batch_to_list`
device patch; the runner must either avoid needing it or apply the equivalent fix and
record that it did (see Risks).

## Error handling

- Run-level failure (interpreter missing, import error, CUDA error, runner crash):
  `GeneratorError`, with the stderr tail and the exit code.
- Sample-level failure: a `Rejection`, never an exception.
- Misconfiguration (scaffold index out of range, scaffold not a connected subgraph of
  the parent, missing pocket file, checkpoint path absent): `ValueError` before any
  subprocess starts.
- GPU contention: the runner reports free memory at start and fails fast with a clear
  message if the batch cannot fit; `batch_size` is configurable. A shared GPU is a
  known condition on this machine (another process held about 3 GB per card).

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
- `MIN_TESTS` in `.github/workflows/test.yml` is raised to the real collected count.

## Risks and open items

- **Local DiffSBDD patch.** The clone has a one-line device fix in `utils.batch_to_list`
  that `inpaint.py` needs on GPU. The runner should avoid depending on it by calling the
  sampling API directly and moving tensors itself; if that proves not to be possible, the
  runner applies the equivalent fix at import time and records it in the report. To be
  settled in the implementation plan.
- **Model quality.** The 7 of 16 connected and 11 of 16 sanitizable rates were measured
  once, on one scaffold. The filters make output usable; whether the proposals are good
  is a separate measurement (the head-to-head against `substituent_scan`, a later spec).
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
2. On the fixture SDF, graft recovers the scaffold in every sample whose only defect is
   missing scaffold bonds, and rejects none of those.
3. With every optional filter off, only `scaffold_atom_mismatch` and `unsanitizable`
   rejections remain.
4. `pose_source="model"` candidates pass the atom-order check and the exact-core check,
   and the self-pair anchor holds with `analogue_poses_given=True`.
5. The run record carries the checkpoint SHA-256 and the DiffSBDD commit.
6. The full suite passes with no GPU present, and `MIN_TESTS` matches the collected count.
7. One GPU smoke run on this machine produces at least one accepted candidate, with the
   wall time and rejection counts printed.
