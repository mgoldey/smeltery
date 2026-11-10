# AIMNet2 and carboxylates: the follow-up measured (issue #22)

Plan, written before any number: `docs/benchmarks/aimnet2_carboxylate_plan.md`. The drug-sized molecule was named
in commit `3175bc6`, before its energy was computed. Records: `benchmarks/aimnet2/carboxylate_probe.json` (the full
attempt) and `benchmarks/aimnet2/carboxylate_truncated.json` (the plan's truncated fallback).
Script: `examples/aimnet2_carboxylate_probe.py`.

Measured 2026-11-09, Linux, CPU, float64 (model and external modules). `aimnet` 0.2.6, model `aimnet2` (wB97M-D3),
ensemble member 0, torch 2.15.0+cu132, ferric 0.1.0rc10, xtb 6.7.1. Reference: ferric B3LYP/aug-cc-pVDZ single
points on GFN2-xtb geometries at the right charge. Single geometries, no error bars.

**This is a newer software stack than `aimnet2_anion.md` used** (`aimnet` 0.2.0, torch 2.9.1). Before computing
anything new, the recorded acetate energy at charge −1 was reproduced: −143495.21725 here against the recorded
−143495.21497, a difference of **0.002 kcal/mol**, three orders of magnitude below the 5.0 bar.

## Answer

**Carboxylates are NOT entered. The question is not established.** `MEASURED_CHARGE_STATES` stays {0, +1} and the
danuglipron anion is still refused by `evaluate`.

Every error that completed is inside the 5.0 bar — but the plan requires the drug-sized carboxylate and the
danuglipron anion, and **neither completed**. The plan's rule is that a single miss keeps carboxylates out; an
unmeasured required species is not a pass either, so the conclusion is "not established", not "met".

## Pair errors, kcal/mol

`[E_model(anion) − E_model(acid)] − [E_DFT(anion) − E_DFT(acid)]`, bar 5.0.

| pair | heavy atoms | model | DFT | error | within bar |
|---|---|---|---|---|---|
| formic acid / formate | 3 | 353.91 | 349.22 | **+4.68** | yes |
| acetic acid / acetate | 4 | 356.99 | 353.20 | +3.79 | yes |
| propanoic acid / propanoate | 5 | 357.66 | 353.64 | +4.02 | yes |
| benzoic acid / benzoate | 9 | 349.18 | 346.29 | +2.89 | yes |
| truncated danuglipron fragment | 14 | 353.10 | 350.04 | +3.06 | yes |
| **indomethacin** (drug-sized) | 25 | — | **not completed** | — | — |
| **danuglipron** | 41 | — | **not completed** | — | — |

## Isodesmic errors against acetic acid, kcal/mol

`carboxylate + acetic acid → acid + acetate`. These cancel the functional offset better than the bare pair, and
they are markedly tighter: every one is inside 1 kcal/mol.

| carboxylate | DFT | AIMNet2 | error |
|---|---|---|---|
| formate | 3.97 | 3.08 | -0.89 |
| propanoate | -0.44 | -0.67 | -0.23 |
| benzoate | 6.91 | 7.81 | +0.90 |
| truncated danuglipron fragment | 3.15 | 3.89 | +0.73 |

## Why the two required species did not complete

Both failed on **memory, not the plan's CPU budget**. `RLIMIT_CPU` raises SIGXCPU (rc −24); these died with
SIGKILL (rc −9) on a 23 GB box:

* **indomethacin anion**: 5803 s wall, killed with ferric reporting `resident memory 15.52 GB exceeds 110% of the
  11.70 GB budget`.
* **danuglipron** and its anion: 1522 s and 1404 s wall, both killed; ferric had already fallen back to Split-RI-J
  because the RI-J tensor did not fit the budget.

A retry of the indomethacin anion at a 16 GB budget reached **13.2 GB resident** and was climbing, with the box
down to 6 GB available, and was killed deliberately rather than risk the kernel OOM killer taking other processes
with it. The 70-atom danuglipron pair needs roughly 48 min per leg by the repo's own nbf^2.71 scaling — more than
this machine's memory allows at aug-cc-pVDZ, not merely more time.

**So the drug-sized question is open for want of hardware, not for want of agreement.** It needs a box with more
RAM, or a smaller basis (which would change the reference and so needs its own plan).

## Forces against finite difference (plan item 4)

Relative error of the provider's analytic forces against a finite difference of its **own** energy, float64 CPU,
bar `FORCE_FD_RELATIVE_BAR` = 1e-4. Computed on the AIMNet2 side only, so these are unaffected by the DFT failures.

| species | step 1e-4 | step 1e-3 | step 1e-2 |
|---|---|---|---|
| indomethacin anion | 1.0e-3 | **1.2e-4** | 5.6e-4 |
| danuglipron anion | 1.3e-3 | **1.5e-4** | 6.3e-4 |

**Still not met**, at 1.2e-4 against the 1e-4 bar — but closer than the 1.6e-4 recorded earlier on smaller
species. The bar was not loosened. As the plan says, if a carboxylate state were ever entered on this evidence it
would be for energies only, with `relax` restricted accordingly.

## What this does and does not say

It says AIMNet2's carboxylate deprotonation energies agree with B3LYP/aug-cc-pVDZ to better than 5 kcal/mol on
**five** carboxylates from 3 to 14 heavy atoms, and to better than 1 kcal/mol isodesmically.

It does not say the model is valid for the danuglipron ligand. The two largest species — the only ones that probe
drug-sized behaviour — were never measured. Formic acid at +4.68 also sits 0.3 kcal/mol inside the bar on a single
geometry with no error bars, which is thin regardless of the arithmetic. And `supports_external_charges = False`
still holds: whatever the energies do in vacuum, this model cannot see a pocket.

The earlier rule and the recorded +7.3 methoxide miss are unchanged.
