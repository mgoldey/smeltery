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

Filters run in this order: sanitize, connected, scaffold, forbidden, duplicate, size.

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
molecules that absorbed the carboxylic acid into a ring, so it is not the default. A yield study (scaffold size,
`add_n_nodes`, resamplings, other scaffolds) is the next piece of work. Nothing beyond these counts is claimed.

## Not claimed

The model's pose is one geometry, not a bound pose. The funnel pairs ensembles, so the standard error mixes the
model pose with jittered ones.

## Running the smoke test

`tests/generate/test_sbdd_gpu_smoke.py` is marked `gpu` and skipped unless `SMELTERY_SBDD_PYTHON`,
`SMELTERY_SBDD_DIR`, `SMELTERY_SBDD_CKPT` and `SMELTERY_SBDD_POCKET` are set (`SMELTERY_SBDD_DEVICE` is optional).
It asserts that 4 raw outputs came back and that the provenance records the checkpoint hash and the DiffSBDD
commit; it does not require any sample to be accepted.
