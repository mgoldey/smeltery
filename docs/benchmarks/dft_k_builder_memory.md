# Does ferric's K builder cut DFT memory? Measured on a 714-bf anion

Prompted by issue #22: the indomethacin anion (40 atoms, aug-cc-pVDZ, 714 bf) was SIGKILLed at 15.5 GB
resident on a 23 GB box, and the refusal in `tiers.py` advised only "use a smaller basis, or raise
memory_budget_gb". ferric has a third knob the advice did not mention, so this measures whether it helps.

Measured 2026-11-09, Linux, 23 GB RAM, 2 cores pinned (`taskset -c 0,1`), `OMP_NUM_THREADS=1`,
`OPENBLAS_NUM_THREADS=1`. ferric 0.1.0rc10. Peak RSS from `ru_maxrss`, B3LYP, `max_iter=2`,
`memory_budget_gb=18`. Molecule: the indomethacin carboxylate, GFN2-xtb geometry at charge −1.

## What ferric offers

`ferric.run_dft` takes **`k_builder`**, and probing an invalid value lists the options:

```
RuntimeError: unknown k_builder 'bogus': valid options are 'direct', 'link', 'cosx'
```

* `direct` — recompute the integrals each Fock build; no stored K tensor.
* `link` — screened (linear-exchange) build.
* `cosx` — chain-of-spheres exchange.

A **global** budget exists as `FERRIC_MEM_BUDGET_GB`, with a per-call `memory_budget_gb` override. Both are
honoured: setting either to 0.001 GB changes ferric's plan and triggers its own spill warnings.

## The headline result: the K builder is NOT the memory lever here

cc-pVDZ (429 bf), where every builder finishes:

| k_builder | peak RSS | wall |
|---|---|---|
| default (stored RI-K) | 7.88 GB | 274 s |
| `direct` | **7.85 GB** | 213 s |

**A 0.03 GB difference — within noise.** `direct` was 22% faster, which is a real (if incidental) win, but it
did not reduce peak memory.

On aug-cc-pVDZ, `direct` was killed by hand at 13.2 GB resident with the box down to 3 GB free, after ferric
reported `resident memory 15.58 GB exceeds 110% of the 10.82 GB budget`. Note the provenance: that 15.58 GB is
**ferric's own report, not a `ru_maxrss` figure** — the run never reached the line that prints peak RSS. It is
the same number the default build hit before being SIGKILLed, so `direct` did not avoid the wall, but it is a
run that was stopped rather than one that completed, and it is recorded that way.

So the earlier hypothesis — that the stored exchange tensor was the problem — is **wrong for this system**.

## Where the memory actually goes

Phase-by-phase RSS on aug-cc-pVDZ (714 bf):

| phase | peak RSS |
|---|---|
| import ferric | 0.23 GB |
| molecule (40 atoms) | 0.24 GB |
| basis loaded | 0.28 GB |
| `AoIntegrals` constructed | 0.28 GB |
| `overlap()` (714×714) | 0.28 GB |
| `hcore()` | 0.28 GB |

Setup is **0.28 GB**. The 15.5 GB arrives inside the SCF, and ferric's warning label `[RHF setup]` is where it
*reported*, not where it allocated — a detail that cost an hour of looking in the wrong place.

Accounting for the rest, at 714 bf:

* RI 3-index tensor (naux ≈ 3·nbf, packed): **≈ 4.1 GB** — real, but under a third of the peak.
* KS integration grid: **182,299 points** (`ferric.dft_grid_point_count`). A blocked grid × nbf AO value
  block is ≈ 0.97 GB, and ≈ 3.9 GB with the three gradient components.
* The remainder is SCF working set: Fock/density history for DIIS, scratch for the J/K contractions.

Since `direct` removes the stored K tensor and peak RSS does not move, the binding constraint is the **sum** of
grid-block, RI-J and DIIS working set, not any single tensor.

## What the grid knobs buy

| setting | grid points | vs default |
|---|---|---|
| default | 182,299 | 1.00× |
| `grid_radial=50` | 122,525 | **0.67×** |
| `grid_radial=50, grid_angular=194` | 191,133 | 1.05× |
| `grid_weight_cut=1e-12` | 178,931 | 0.98× |

At best a 33% cut from `grid_radial`, and a coarser grid changes the energies, so on a measurement it needs its
own pre-declared plan — the same objection as shrinking the basis. `grid_prune` rejects a bool in this build
(it wants the resolver's name, not `True`).

## Conclusion for issue #22

The indomethacin anion at aug-cc-pVDZ does not fit this machine, and **no builder flag changes that**. The
honest statement stands: the drug-sized carboxylate is blocked on hardware (or on a smaller basis, which is a
different measurement), not on a ferric option nobody passed.

What did change: `FieldDftInteraction` now **has** a `k_builder` field, plumbed to `run_dft` and recorded in
`settings()`, and the over-budget refusal names it. That is worth having even though it did not rescue this
case — `direct` was 22% faster here, and a genuinely K-bound system (more diffuse functions, fewer grid
points) is exactly where it would pay. It is off by default, so existing runs are unchanged.

## `cosx` is slower here, not faster

`cosx` was killed by a 500 s limit on the same cc-pVDZ case that `default` finished in 274 s and `direct` in
213 s, so its wall time is **> 500 s**: at least 2.3× the default, on a molecule this size. Chain-of-spheres
pays off when the exchange build dominates; here it does not, which is the same conclusion the RSS numbers
reach from the other direction.

That bound is all this document claims. A re-run with a 1800 s limit, on two other cores while the test suite
held the first two, produced no output at all, so **`cosx` has no completed measurement here** — no wall time
and no peak RSS. It is a lower bound from a killed run, not a number.

That matters for the advice in `tiers.py`, which lists `cosx` first as "cheapest": cheapest in **memory**, not
in time, and on this system neither. The advice is still worth having for a K-bound case, but nobody should
read it as a free win.

## Not measured

`link`. No GPU. One molecule, one geometry, one machine, `max_iter=2` — these are memory and setup-cost
numbers, not converged energies, and nothing here is a statement about accuracy. The cc-pVDZ and aug-cc-pVDZ
runs are separate systems for this purpose: only the aug-cc-pVDZ case is the one issue #22 actually needs.
