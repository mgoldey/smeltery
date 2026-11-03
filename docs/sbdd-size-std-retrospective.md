# `size_std` and growth size: two sweeps, and why the first one's answer was unusable

A record of work done 2026-10-28/29 on PocketXMol's scaffold-growth sampling. The result is negative:
`size_std` does not move lipophilicity, and the growth-size dependence that the first sweep appeared
to find does not survive a design that can actually measure it. This is written down so the next
person does not re-run it, and because the way the first answer failed is more instructive than the
answer.

None of this code is in smeltery. It lives in `/home/matt/qc/external/gen-compare`. Paths at the end.

## Why anyone cared

The strongest result in the generator comparison is a property gap, not an energy gap: **41.4% of
PocketXMol's accepted molecules exceed logP 5.6 against DiffSBDD's 3.4%** (+38 pp, p < 1e-4, the only
finding in that study with an interval far from zero). Greasy molecules are a developability problem,
so the obvious next question is which sampling knob moves it.

`size_std` is the std of PocketXMol's target heavy-atom count (`variable_mol_size.num_atoms_distri`).
A first 2-cell sweep suggested width buys diversity where growth is small and does nothing where it
is large, which made it look like both a logP lever and one that had to scale with growth size.

## Sweep 1: 15 cells x std {0,1,2,3} x 3 seeds = 180 runs

Clean execution, 180/180, zero failures, ~2h50 on two GPUs. It found a `std x n_grow` interaction at
**p = 0.0385** and, applying a selection rule fixed in writing before any output was read
(`RESCORE_PLAN.md`), selected `std=3` for the large-growth half and `std=2` — production's setting —
for the small-growth half.

**That selection could not be acted on, and the reason is a design flaw, not a statistical one.**

In the 15-cell design, `n_grow` is `41 - len(scaffold)`: a property of which cell, not a knob. All six
"large growth" cells have `n_grow = 17` and are exactly the `s17_*` and `s19_*` cells; the nine "small"
ones are everything else. **A median split on `n_grow` separates two scaffolds from three.** Any
interaction it detects is equally well explained by "those two scaffolds behave differently", and no
reanalysis of that data can separate the two.

Two further things were wrong with reading it as a result:

* **At the cell level it is not resolved.** The aggregate 39.4% vs 42.2% over logP 5.6 is a 2.8 pp gap
  assembled from per-cell swings of ±10 pp in *both* directions (−0.9, +6.3, +5.2, −4.8, −10.4, −7.0).
  Cluster-robust at G=6: −2.06 pp, CI [−7.42, +3.30], p = 0.451. Sign test 4/6, p = 0.69.
* **The interaction p came from a per-molecule regression** over 4130 molecules clustered in 15 cells.
  The unit of inference is the cell, and there are 15 of them.

No rescore was run. `dE_int` would have cost ~4.5 h to compare a setting whose selection was confounded.

## Sweep 2: the ladder — vary `n_grow` within one growth site

To break the confound, `n_grow` has to vary with the scaffold's growth *site* held fixed. That means
new cells: deeper cuts at the same place. Four designs were built and discarded before one was valid,
and the constraints are worth stating because three of them are invisible until late:

1. **`build_scaffold` indexes canonical SMILES order, not SDF order.** Checking connectivity in SDF
   order reports four of the five original scaffolds as disconnected, which is false. Indices taken
   from an SDF describe a different set of atoms and fail much later, as rejected samples.
2. **The removed fragment and the remainder must both be connected.**
3. **The cut must sever exactly one acyclic bond.** This is the binding constraint and it was invisible:
   a two-bond cut through an aromatic ring passed every check, passed `build_scaffold`, and
   **generated a full batch of molecules on the GPU** — then scored 0/9 accepted with 8
   `scaffold_lost`, against 23/33 and 28/33 for original cells on the identical code path. Cause:
   `build_scaffold` kekulizes before cutting, clearing aromatic flags, so the partial ring sanitizes
   and comes back reinterpreted as an open chain. **This is now refused in smeltery** —
   `build_scaffold` raises on a severed aromatic ring bond (`sbdd_post.py`), with a test both ways.
   It was caught only because the smoke test was scored against an original cell rather than merely
   inspected.

Enumerating every single-acyclic-bond cut of this parent gives the complete feasible set of growth
sizes — **3, 4, 5, 9, 10, 11, 17, 18, 23, 24** — with essentially one option each. A within-site
ladder exists at two places only:

| site | `n_grow` | bonds cut |
|---|---|---|
| head | 9, 10, 11, 17 | 5-6, 6-7, 7-8, 12-13 (nested, same chain) |
| tail | 17, 18 | 17-18, 16-17 |

`s3acid` and `s4oxet` **cannot be laddered at all**: short substituents, 3 and 4-5 atoms, with no valid
larger fragment containing them. So the honest design is 6 cells, not the 25 a 5x5 grid implies — a
property of this parent, which is a mostly-rigid fused-ring system with few cut points, not of the
search. `head_g9`, `head_g17` and `tail_g17` reproduce the `s9head`, `s17` and `s19` scaffolds exactly,
asserted against `cells_p1_all.json`.

72 runs, 72/72, zero failures, ~1h30 on two GPUs.

## What sweep 2 found: nothing

`std x n_grow` with `site` blocked and `n_grow` continuous — no median split anywhere:

| outcome | coef | cluster-robust p | wild bootstrap p |
|---|---|---|---|
| logP shift | −0.0023 | 0.634 | **0.683** |
| P(logP > 5.6) | −0.0009 | 0.625 | **0.624** |
| accepted/run | −0.120, CI [−0.242, +0.003] | 0.055 | **0.198** |

Main effect of `std` on the logP tail: **−0.62 pp per unit, CI [−2.04, +0.80], p = 0.41.**

**Trust the bootstrap.** At G = 4-6 clusters the cluster sandwich is severely downward-biased, the same
problem seen at G = 11 in the generator comparison. On the head site alone the two disagree wildly —
logP interaction p = 0.0166 cluster-robust against **p = 0.81** by wild cluster bootstrap-t
(Rademacher, null imposed). Reporting only the cluster-robust number would have "confirmed" the
interaction.

A leverage check shows why the head-site fit is meaningless:

```
all four rungs (9,10,11,17):   coef -0.0092    (p < 1e-4 cluster-robust)
dropping head_g17:             coef +0.0343    (p < 1e-4 cluster-robust)
```

**One cell determines the slope's sign.** The per-rung slopes of `d_logp` on `std` across
`n_grow` 9 -> 10 -> 11 -> 17 are **−0.040, −0.002, +0.034, −0.083**: no trend, alternating signs. It is
a straight line fit through three clustered points and one distant one.

## What does move logP

The scaffold, and at single-atom resolution. Two cells at the same site, one atom apart:

| cell | grown | % over logP 5.6, std 0 -> 3 |
|---|---|---|
| `tail_g17` | 17 | **70.1, 52.0, 58.9, 54.8** |
| `tail_g18` | 18 | **15.2, 17.4, 18.7, 18.4** |

**One more atom removed swings the tail ~40 pp** — more than `size_std` moves it across its whole 0-3
range in any cell. Where you cut dominates how wide you sample. `std=2` stands, not because it was
confirmed but because nothing displaced it.

## A second finding: PocketXMol's `--seed` does not reproduce a run

`repos/PocketXMol/scripts/sample_use.py:173`:

```python
seed = config.sample.seed + np.sum([ord(s) for s in args.outdir] + [ord(s) for s in args.config_task])
```

The **output directory and config path are hashed into the seed**. Two runs of one cell differing only
in output directory shared **1 molecule of 17 distinct**, with PocketXMol's own recorded
`complete_seed` at 17266 vs 17268 — `ord('b') - ord('a')` — while both logged `seed: 100`. A
`sweep_full` / `sweep_ladder` pair with byte-identical `scaffold.sdf` (same md5) and `task.yml`
identical but for the path gave 13466 vs 13598 and overlapped by 2 of 33. GPU nondeterminism and
device effects were ruled out by re-running on one GPU.

`sweep_full.sh` documented "seed 100 reproduces the original cells exactly and acts as a control on the
whole pipeline". **That was false** and is corrected in both sweep scripts and the analyzer. The three
seeds are still genuinely independent draws, so seed-spread error bars and `select_std.py`'s
eligibility tolerance are unaffected — but a seed is not a reproducible label, and "re-run seed 100 and
compare" is not a pipeline control. To reproduce a run, keep the output directory and config path
identical.

## What to do instead

* **Treat the growth site as the lever, not the width.** The 40 pp swing between `tail_g17` and
  `tail_g18` is the largest within-generator effect in this study. If the goal is less greasy output,
  choose where to cut.
* **Do not re-run `size_std`** on this parent. Two sweeps, 252 runs, 5837 accepted molecules; every
  interval includes zero.
* **A different scaffold series could still show growth-size dependence.** This is one parent with few
  cut points, and the ladder exists at two sites only. That is a scope limit, not evidence against.
* **Design before power.** Sweep 1 was executed flawlessly and answered a question nobody could use.
  Adding seeds to it would not have helped; the factor was confounded with the blocking variable from
  the start.

## Paths

| What | Where |
|---|---|
| Ladder cell construction, with the three validity constraints asserted | `external/gen-compare/make_ladder_cells.py` |
| Ladder sweep driver (6 cells x 4 std x 3 seeds, resumable, two GPUs) | `external/gen-compare/sweep_ladder.sh` |
| Ladder analysis: site blocked, `n_grow` continuous, wild cluster bootstrap | `external/gen-compare/sweep_ladder_analyze.py` |
| Generated cells | `external/gen-compare/ladder_cells.json` |
| First sweep driver and analysis | `external/gen-compare/sweep_full.sh`, `sweep_full_analyze.py` |
| The pre-declared selection rule, and its execution | `external/gen-compare/RESCORE_PLAN.md`, `select_std.py` |
| PocketXMol adapter (`--cells` selects a cell file) | `external/gen-compare/pxm_adapt.py` |
| Sweep outputs | `external/gen-compare/sweep_full/`, `sweep_ladder/` |
| Results | `external/gen-compare/sweep_full.json`, `sweep_ladder.json`, `select_std.json` |
