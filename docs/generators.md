# Structure-based generator: ScaffoldGrowth

## What it is

`smeltery.generate.ScaffoldGrowth` regrows the part of a parent molecule outside a fixed scaffold, inside a
receptor pocket, with DiffSBDD's inpainting. The raw output is repaired, filtered and returned as
`Candidate`s. Samples it declines are kept as `Rejection`s with a reason, so a caller can count what was lost.

```python
from smeltery.generate import ScaffoldGrowth
from smeltery.tiers import PairedPoses

gen = ScaffoldGrowth(
    python="/path/to/venv-sbdd/bin/python",
    diffsbdd_dir="/path/to/DiffSBDD",
    checkpoint="/path/to/DiffSBDD/checkpoints/crossdocked_fullatom_cond.ckpt",
    n_samples=16,
    add_n_nodes=9,
    device="0",
    pose_source="model",
)
result = gen.generate(parent, scaffold_indices, "pocket.pdb", seed=1)

# With pose_source="model" the analogues carry the generator's geometry. See "Pairing the model's poses" below
# for what this recipe was and was not measured to do.
paired = PairedPoses(parent_poses_given=True, analogue_poses_given=True)
# ... pass [parent, *result.candidates] through `paired`

# Record the settings and the run's provenance (checkpoint hash, DiffSBDD commit, wall time, device).
inputs = {"generator": gen.settings(), "generation": result.provenance}  # into RunRecord.inputs
```

## The external environment

DiffSBDD does not run in smeltery's environment. The recipe verified on 2026-10-10 on this machine (two GTX 1080s,
CUDA 13 driver):

1. `uv venv --python 3.10 <dir>`
2. `uv pip install --python <dir>/bin/python "torch==2.0.1" --index-url https://download.pytorch.org/whl/cu118`
3. `uv pip install --python <dir>/bin/python numpy==1.23.5 scipy==1.10.1 pytorch-lightning==1.8.4 torchmetrics==0.11.4 rdkit==2022.9.5 biopython==1.79 networkx pandas==1.5.3 seaborn imageio pyyaml tqdm wandb==0.13.1 protobuf==3.20.3 matplotlib openbabel-wheel`
4. `uv pip install --python <dir>/bin/python torch-scatter==2.1.1 -f https://data.pyg.org/whl/torch-2.0.0+cu118.html`

Clone `https://github.com/arneschneuing/DiffSBDD` (verified at commit `5d0d38d`) and download
`crossdocked_fullatom_cond.ckpt` from Zenodo record 8183747. The checkpoint is a pickle and loads code: load it
only in that environment, from that source. Pascal GPUs run torch's `sm_60` kernels.

The runner patches DiffSBDD at runtime and never edits the checkout, so the recorded `diffsbdd_dirty` is false for
an untouched clone. Untracked files such as `checkpoints/` do not count as dirty.

## Settings and what they cost

The defaults (50 timesteps, 20 resamplings) are `inpaint.py`'s. A 16-sample batch took 213 s on one GTX 1080,
including model load. The runner passes `CUDA_VISIBLE_DEVICES` from `device`.

## Rejection reasons

Repairs (all off by default, see "Repairing over-valent output") run before sanitization. Filters run in this order: sanitize, connected, scaffold, forbidden, duplicate, size.

| reason | meaning |
|---|---|
| `scaffold_atom_mismatch` | a scaffold atom could not be found in the output, or the fit was too loose |
| `unsanitizable` | RDKit could not sanitize the repaired molecule, or the record was unreadable |
| `disconnected` | more than one fragment |
| `scaffold_lost` | the scaffold is not a substructure of the result |
| `forbidden_substructure` | matched a forbidden SMARTS; `detail` is its label |
| `duplicate` | the same canonical SMILES was already returned |
| `size` | outside the heavy-atom limits |
| `atom_order` | the pose could not be put in the candidate SMILES's atom order |

The default forbidden list holds one pattern, `peroxide`: `[OX2][OX2]`. Replace it through
`FilterConfig(forbidden_smarts={...})`.

## Repairing over-valent output

The model assigns bonds from distances and never sees valences, so most of its output fails sanitization because one
atom, usually an O or N, has a bond too many. Three optional `FilterConfig` fields repair that; all default to off, so
the counts under "Measured yield" are for no repair:

- `salvage_bonds=N`: for each over-valent atom, drop its longest bond that is not a scaffold bond, at most N times
  per sample. The scaffold's own bonds are never dropped.
- `trim_stray_fragments=True`: a sample that falls into pieces (often because a salvaged bond was the only link to a
  grown group) keeps the piece that holds the scaffold instead of being rejected `disconnected`. The grown atoms in
  the discarded pieces are lost.
- `scaffold_orders=True`: the scaffold's bond orders are forced onto the scaffold's bonds the model placed. Alone this
  sanitizes 0 of 16; it only makes sense with `salvage_bonds`, which removes the bonds that crowd the scaffold.

`provenance["repairs"]` counts the accepted candidates that used them (`salvaged`, `bonds_dropped`, `trimmed`). A
repaired molecule is the model's geometry with some of its bonds removed and some orders overruled, so it is a
proposal whose chemistry is chosen partly by these rules. "Validating the repaired molecules" below measures what that
costs: the repairs add real yield, but about half of what they add fails PoseBusters' ligand-only geometry checks.

## Sampling until a target is met

`target_accepted` and `max_batches` work together. `max_batches` defaults to 1, so setting `target_accepted` alone
changes nothing. With both set, batches of `n_samples` run (seeds `seed`, `seed + 1`, ...) and sampling stops as soon
as the number of accepted candidates reaches the target or `max_batches` batches have run. The last batch may
overshoot the target. Duplicates are detected across batches.

## Pairing the model's poses

With `pose_source="model"` each candidate carries the model's geometry as its only pose, in the atom order of
`Chem.AddHs(Chem.MolFromSmiles(candidate.smiles))`. The recipe in the example above
(`PairedPoses(parent_poses_given=True, analogue_poses_given=True)`) works on small cores such as the benzoic acid
pair in `tests/test_paired_given_poses.py`. It was refused on the only real candidate the generator produced (the
41-heavy-atom parent's one accepted sample):

- The maximum-common-substructure search times out at the default `mcs_timeout_s=10` and was still cancelled at
  60 s: it reaches 54 atoms on the 71-atom parent and 76-atom candidate (hydrogens included) and never proves
  maximality.
- Were the search bypassed, the given-pose check would also refuse it: the worst mapped heavy atom sits 2.22 Å from
  the parent's pose 0 (median 0.055 Å, 2 of 32 over the 0.5 Å limit) because the search picks a
  symmetry-degenerate assignment.

Both refusals are correct. `tests/generate/test_sbdd_model_pose_pairing.py` pins the first. `pose_source="embed"`
has the same search cost, because `PairedPoses` runs the same MCS search for it. The follow-up is a `PairedPoses`
option that takes the known scaffold mapping (the generator already knows which candidate atoms are the scaffold)
instead of searching.

## Measured yield

One scaffold (32 of 41 heavy atoms fixed, 9 added), 16 samples, one GTX 1080: 12 unsanitizable, 3 lost the
scaffold, 1 accepted, and that one is chemically implausible (a phosphonate ester on the carboxyl). At the training
step count with one resampling, 0 of 16 were usable.

A second, independent data point from the GPU smoke test (4 samples, `pose_source="model"`, seed 1):

```
SMOKE n_raw=4 accepted=0 rejected={'scaffold_lost': 1, 'unsanitizable': 3} wall=74.534s device=NVIDIA GeForce GTX 1080 free_mib=4678
```

That run accepted no candidate, in line with the yield above. This setup is a poor proposer on that scaffold. `scaffold_match="topology"` admits 4 of the 16, but they include
molecules that absorbed the carboxylic acid into a ring, so it is not the default. The yield study below widens this to 15 cells.

## Yield study

15 cells on the same parent (danuglipron in 7LCJ, 41 heavy atoms), 32 samples each (480 raw), 50 timesteps, 20
resamplings, two GTX 1080s, about 6.7 minutes per cell. Five scaffolds regrow 3, 4, 9, 17 and 17 atoms, each at
`add_n_nodes` equal to that count, 4 more, and unset. Accepted out of 32, no repair:

| regrown | true count | +4 | unset |
|---|---|---|---|
| carboxylic acid (3) | 5 | 1 | 1 |
| oxetane (4) | 8 | 3 | 2 |
| cyanofluorobenzyl head (9) | 2 | 0 | 3 |
| piperidine to oxetane (17) | 9 | 2 | 12 |
| head to piperidine (17) | 0 | 0 | 1 |

49 of 480 (10%). By `add_n_nodes`: true count 24 of 160, unset 19, true count + 4 only 6. With the size unset the
model often returns the same molecule over and over (up to 25 of 32 duplicates). Rejections: 189 unsanitizable (112
of them an O with too many bonds, 48 an N), 121 lost the scaffold, 63 duplicates, 58 disconnected.

The same 480 raw samples re-scored with the repairs above (no new sampling):

| repairs | accepted of 480 |
|---|---|
| none | 49 |
| `salvage_bonds=4` | 62 |
| `salvage_bonds=4`, `trim_stray_fragments` | 96 |
| `salvage_bonds=4`, `trim_stray_fragments`, `scaffold_orders` | 334 (384 with `salvage_bonds=8`; 7x) |

On the 16-sample fixture the last combination accepts 13. The accepted molecules still sanitize, hold the exact
scaffold, and have no bond more than 1.3 times its covalent-radii sum, but some carry odd groups (an azo ester, an
aminal), and nothing here says they bind. Pair the repairs with `forbidden_smarts` and a tier that scores them. Not
varied: timesteps and resamplings, a stated attachment atom, other parents.

## Validating the repaired molecules

The bond-length check above is weak, so the molecules were checked further. The study was regenerated with the same
seeds (49 of 480 accepted without repair, per-cell counts within one of the first run). Everything here is one parent
and one pocket, with small samples; none of it is a benchmark.

**PoseBusters on the model's own pose, ligand-only checks** (a check that hung for 120 s counts as neither pass nor
fail):

| set | molecules | pass | fail | timeout |
|---|---|---|---|---|
| unrepaired | 49 | 40 (82%) | 9 | 0 |
| repaired, also in the unrepaired set | 28 | 22 | 5 | 1 |
| repaired only | 354 | 145 (44% of 333 conclusive) | 188 | 21 |

The repaired-only failures are mostly `internal_steric_clash` and `internal_energy`, then `bond_angles`: dropped
bonds and forced orders leave crowded, strained geometry that sanitization does not see. The repairs still yield 145
geometry-passing molecules against 40 without them. The receptor-aware PoseBusters check is not reported as a
comparison, because the two groups were run on different subsets of cells.

**`PairedPoses`** (`parent_poses_given=True`, `relax_unmapped=True`, `mcs_timeout_s=300`) on 20 repaired-only molecules
that passed the ligand-only check, and 10 unrepaired controls. The exact MCS search did not finish within 300 s for 9
of 20 and 6 of 10, so about half could not be paired on drug-sized molecules; raising `mcs_timeout_s` is the lever.
Of the paired poses, 8 of 11 repaired-only and 3 of 4 unrepaired passed the ligand-only check again. The failures are
`bond_lengths` at the copied-core and new-substituent junction, even with `relax_unmapped`.

**ferric RHF/STO-3G `FieldInteraction`** in the 7LCJ pocket field, pose 0, paired against the parent (dE_int −16.64
kcal/mol), on the 8 repaired-only and 3 unrepaired molecules that paired and passed:

| set | converged | ddE (kcal/mol) |
|---|---|---|
| repaired only | 8 of 8 | +6.4, +2.8, −8.8, +1.4, +2.9, +3.5, +2.5, +1.1 |
| unrepaired | 2 of 3 (one SCF did not converge) | −6.0, +4.5 |

The two groups cover the same range. This cannot show that the repairs are harmless: the samples are tiny, each is
filtered by three earlier cuts (8 of 354), and dE_int is a fixed-geometry field interaction that does not contain
internal strain. The molecules also differ chemically from the parent, so a ddE mixes substituent chemistry with
geometry artefacts. Differences of 1 to 3 kcal/mol are within the pocket-charge sensitivity floors in `limits.md`.

**What to take from it.** The repairs are a yield tool with a quality cost: roughly half of the molecules they add are
strained, and half of all candidates cannot be paired without a longer MCS budget. Keep them off by default, turn them
on when yield is the limit, and run a geometry gate (PoseBusters ligand-only) before any quantum tier.

## Not claimed

The model's pose is one geometry, not a bound pose. The funnel pairs ensembles, so the standard error mixes the
model pose with jittered ones.

## Running the smoke test

`tests/generate/test_sbdd_gpu_smoke.py` is marked `gpu` and skipped unless `SMELTERY_SBDD_PYTHON`,
`SMELTERY_SBDD_DIR`, `SMELTERY_SBDD_CKPT` and `SMELTERY_SBDD_POCKET` are set (`SMELTERY_SBDD_DEVICE` is optional).
It asserts that 4 raw outputs came back and that the provenance records the checkpoint hash and the DiffSBDD
commit; it does not require any sample to be accepted.
