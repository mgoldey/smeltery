# A worked pipeline, end to end: the danuglipron halogen scan

One pipeline file, one command, one record. This page follows the shipped
`danuglipron-halogen` prebuilt from its config to its numbers, says what each
section does and what you change to make it your own, and points at the piece
to copy when you need a step smeltery does not have.

Everything below is in the repository:

| what | where |
|---|---|
| the config | `src/smeltery/prebuilt/danuglipron-halogen/pipeline.toml` |
| its inputs | `7LCJ_pocket.pdb`, `conf_00_cryo_em.xyz`, beside the config |
| the run it produced | `src/smeltery/prebuilt/danuglipron-halogen/run/` |
| the earlier result, imported | `.../imported/` |
| the methods it uses | `docs/methods.md` |

## The question

Danuglipron has four aromatic CH sites. Replace the H at each with F, Cl or
CH3 (12 analogues) and ask which substitution the GLP-1R pocket of 7LCJ prefers.
The measured quantity is ΔΔE_int: how much the pocket's electrostatic field
stabilises an analogue relative to the parent, in kcal/mol, same pose geometry
for both.

## The config, section by section

The file is TOML. Each `[section]` is one method; the registry decides which
sections exist and what each accepts, so `smeltery methods` lists them and a
typo is refused by name before anything runs.

```toml
campaign = "danuglipron-halogen-scan"
parent = "parent"

[candidates]
parent = "C1CO[C@@H]1CN2C3=C(C=CC(=C3)C(=O)O)N=C2CN4CCC(CC4)C5=NC(=CC=C5)OCC6=C(C=C(C=C6)C#N)F"
bnz-C34-F = "N#Cc1ccc(COc2cccc(C3CCN(Cc4nc5ccc(C(=O)O)cc5n4C[C@@H]4CCO4)CC3)n2)c(F)c1F"
# ... 11 more
```

**`[bound_pose]`** — where the parent's geometry comes from. Pose 0 is the
cryo-EM bound conformer as given; poses 1 to 3 are seeded rigid jitters of it
(15°, 0.5 Å). Four poses is what makes a standard error possible.

```toml
[bound_pose]
file = "conf_00_cryo_em.xyz"
n_poses = 4
jitter_deg = 15.0
jitter_ang = 0.5
seed = 20261005
skip_first = 0
```

**`[poses]`** — each analogue's poses, built on the parent's by matching the
common core and embedding only the new substituent. This is what makes the
difference paired: analogue pose *i* and parent pose *i* differ by the
substitution and nothing else.

```toml
[poses]
n_poses = 4
seed = 20261005
jitter_deg = 15.0
jitter_ang = 0.5
mcs_timeout_s = 900     # the 10 s default failed for 9 of 12 on this 74-atom parent
min_core_heavy = 3
relax_unmapped = true
```

**`[pocket]`** — the field. The PDB is charged by `pdb2pqr30 --ff=AMBER`,
giving 6,458 point charges, no distance cutoff.

**`[field_interaction]`** — the measurement: RHF/STO-3G twice per pose, once in
vacuum and once in the field; ΔE_int is the difference. `remedies` is the
ladder tried when an SCF does not converge, rung 0 being the settings above.
No rung may loosen a threshold or add smearing, because that changes the
quantity being measured rather than how it is reached; smeltery refuses such a
rung when it loads the config.

```toml
[field_interaction]
basis = "sto-3g"
energy_conv = 1e-10
density_conv = 1e-8
max_iter = 200
remedies = [
  { diis = "adiis", level_shift = 0.5 },
  { diis = "adiis", level_shift = 1.0, soscf = true, max_iter = 150 },
]
```

**`[charge_sensitivity]`** — the honesty check. Every pose is measured again in
the same pocket charged by `--ff=CHARMM`, and the largest shift it produces
becomes the floor below which no two candidates are called different.

**`[run]`** and **`[cut]`** — the per-SCF time limit, and the ranking: keep the
top 3 if the gaps clear the floor.

## Running it

```bash
# once: the durable-run server
docker run -d -p 7233:7233 -p 8233:8233 temporalio/auto-setup:1.25

# cost first, no SCF, seconds
smeltery run pipeline.toml --out run --plan

# the workers, then the run
smeltery temporal-worker --workers 6 --threads-per-worker 1 &
smeltery run pipeline.toml --out run --temporal
```

`--plan` is worth the seconds: it prints the SCF count and a CPU-hour estimate
from measured calibration before you commit a machine to it.

Each SCF is one Temporal activity in its own child process. Stop the worker,
reboot, start it again and the run picks up from `calcs.jsonl` rather than
recomputing; `docs/temporal-runs.md` has the details.

## What this run cost and produced

156 SCF legs (52 poses × vacuum + AMBER field, the vacuum leg shared with
CHARMM), 38.4 CPU-h on 6 workers, every leg converged. `--plan` predicted
21.6 CPU-h, 1.8× low: the calibration assumes the reference molecule's
iteration count, and these poses took 54 iterations in vacuum against the
reference's 36.

**ΔΔE_int (AMBER, kcal/mol), 4 poses each, and what CHARMM charges do to it:**

| analogue | mean | paired SEM | unpaired SEM | CHARMM shift |
|---|---|---|---|---|
| `bnz-C34-F` | −4.11 | 0.76 | 3.68 | −0.01 |
| `bzim-C9-Cl` | −2.76 | 0.36 | 3.63 | −0.13 |
| `bzim-C9-F` | −1.71 | 0.50 | 3.74 | −0.04 |
| `pyr-C28-CH3` | −0.91 | 0.66 | 3.54 | −0.14 |
| `bnz-C36-CH3` | −0.35 | 0.08 | 3.34 | −0.30 |
| `bnz-C36-F` | +0.83 | 0.13 | 3.33 | +0.07 |
| `pyr-C28-F` | +1.09 | 0.19 | 3.25 | +0.22 |
| `bnz-C36-Cl` | +1.43 | 0.26 | 3.27 | +0.27 |
| `bzim-C9-CH3` | +1.56 | 2.22 | 2.42 | −0.18 |
| `pyr-C28-Cl` | +2.19 | 0.47 | 3.07 | +0.43 |
| `bnz-C34-CH3` | +2.36 | 1.37 | 3.48 | +0.22 |
| `bnz-C34-Cl` | +5.58 | 5.20 | 5.95 | −0.10 |

Reading it:

* **Pairing earns its keep.** The paired SEM is below the unpaired SEM for
  12 of 12 analogues, by 5× to 40× for the well-behaved ones. Comparing
  absolute energies across molecules would drown every effect measured here.
* **The parent is not inert.** Its own ΔE_int is −17.4, −18.4, −14.5 and
  −25.7 kcal/mol across the four poses, so the field is doing something; and
  parent-against-itself is exactly 0.
* **The charge model barely matters.** No analogue moves more than
  0.43 kcal/mol between AMBER and CHARMM, under the 0.73 kcal/mol floor that
  sensitivity itself sets. The ranking is not an artefact of the charge set.
* **The cut is UNRANKED.** The twelve land in a single tie group, so no gap near
  the keep-3 boundary clears the floor. The widest spreads come from one pose
  disagreeing with the other three: `bnz-C34-Cl` (SEM 5.20 on a mean of 5.58),
  `bzim-C9-CH3` (2.22 on 1.56, the one analogue whose standard error exceeds its
  own mean) and `bnz-C34-CH3` (1.37 on 2.36). With four poses this is the honest answer:
  `bnz-C34-F` is the most stabilised of the twelve, and the twelve are not
  separable at this pose count.
* **The direction is consistent, though.** F at `bnz-C34` and Cl or F at
  `bzim-C9` are stabilising on every one of their four poses; Cl and CH3 at
  `bnz-C34` are destabilising on the two poses that agree. More poses, not a
  different method, is what would turn this into a ranking.

### One SCF lied, and what came of it

`pyr-C28-Cl` pose 3 did not converge in vacuum at rung 0. Rung 1 reported
convergence after 3 iterations, 0.82 Ha above an energy rung 0 had already
reached, which gave that pose a ΔΔE of −510 kcal/mol and made the whole cut
meaningless.

Every SCF iterate is the energy of a real wavefunction, so the minimum cannot
lie above one already reached: that "convergence" was arithmetically impossible,
not merely suspect. It was a ferric bug in ADIIS with a level shift, fixed
upstream in `455b8fbad`; on the installed build it fired on every molecule we
tried it on.

Two things changed as a result. smeltery now refuses any rung whose converged
energy sits above one the same leg already reached, records it as not converged
with the reason, and climbs; and that leg was rerun on the fixed ferric, where
it converged in 13 iterations to 7 × 10⁻⁹ Ha of rung 0's energy. The run record
keeps both builds, so the mixed-build provenance is visible rather than
flattened.

The general lesson for a campaign of this size: run each remedy rung once on one
real molecule before committing hours to it. A rung that converges in 3
iterations where rung 0 took 200 is not good news.

## Extending it

Every section above is a registered method, and a new one is a module in
`src/smeltery/methods/` with a `register_methods()` function. Nothing central
changes: the catalog, validation, the GUI's editor, the input digest and the
cost model all derive from the registration. `docs/methods.md` has the protocol
for each slot; the pieces added for this campaign are the ones to copy.

| you want | copy | which does |
|---|---|---|
| poses from somewhere else (a different conformer source, a docking program, an MD snapshot) | `methods/bound_pose.py` | a `pose_source`: hands the pipeline the parent's poses |
| another field (a different charge set, a QM/MM embedding, a solvent model) | the `[pocket]` registration in `methods/builtin.py` | a `field_source` |
| a different measurement (DFT, a different basis, a binding free energy) | `FieldDftInteraction` in `tiers.py` | a `measure`, which the registry wires to `[field_interaction]`'s slot |
| a check on the numbers (another charge model, another basis, a control) | `methods/charge_sensitivity.py` | an `analysis`, optionally re-measuring under extra fields |

A registration carries the knobs it accepts, which tier it maps to, what it
needs from earlier steps and what it provides to later ones. That is what lets
the validator reject `[bound_pose]` next to `[docking]` (two pose sources) with
a sentence naming both, before any SCF starts.

## The shape of the output

```
run/
  run-ccb0c68648fe.json   the record: ddE, SEMs, the cut, provenance, timings
  calcs.jsonl             one line per SCF: energy, iterations, seconds, rung, build
  config.toml             the config as run, with inputs rewritten to digests
  inputs/<sha256>/...     every input file, by content hash
  poses/<candidate>.sdf   the exact geometries measured
```

`calcs.jsonl` is the audit trail: 158 lines here for 156 legs, the two extra
being the refused rung and the rerun. Every line names the ferric and smeltery
build that produced it, so a number can always be traced to the code that made
it.

Open the record in the GUI with `smeltery bench` and the funnel, the per-pose
values and the pocket are all clickable; `docs/bench.md` covers that.
