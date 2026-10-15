# Halogen scan: AURORA, TRAH and single ladder rungs on the unconverged SCFs (issue #6, ferric#418)

Plans, committed before any run: `danuglipron_halogen_accel_plan.md` and `danuglipron_halogen_ladder_probe_plan.md`. Runners: `examples/danuglipron_halogen_accel.py`, `examples/danuglipron_halogen_ladder_probe.py`. Records: `benchmarks/danuglipron_halogen/results/halogen_accel_results.json`, `halogen_ladder_probe.json`.

**Answer: none of the three routes converged a single SCF within its CPU limit, and the reason is not the SCF: five of the six failed poses have the halogen 3.3 to 8.2 Å from the carbon it is bonded to. The failures come from the poses, not from ferric. See "Cause found" below; the accelerator results are kept as measured, but they were run on broken geometries.**

## Cause found (after the runs above, from a 16-atom fragment)

The accelerator and ladder runs were all on the full 74-atom molecule in 6,458 charges, where every job ran into its CPU limit and said nothing about why. A small case did: the difluoro-benzonitrile ring of `bnz-C34-F`, cut at its CH2-O bond, H-capped, at its stored pose-0 position (16 atoms; scratch scripts kept verbatim as `benchmarks/danuglipron_halogen/fragment_check/*.py.txt`, record `halogen_fragment_check.json`).

* **Stored geometry:** RHF/STO-3G in **vacuum (no charges)** does not converge in 200 iterations. With the nearest 10 to 4,000 charges it converges only after 78 to 159 iterations. A 16-atom STO-3G SCF should take about 18.
* **Cause:** the stored halogen sits 8.22 Å from its carbon. Checking the six stored poses: longest bond per pose is 2.14 (`pyr-C28-F`), 3.3 (`pyr-C28-Cl`), 4.68 (`bzim-C9-F`), 7.05 (`bnz-C34-Cl`), 5.75 (`bnz-C36-F`) and 8.22 Å (`bnz-C34-F`). An F or Cl atom that far from the molecule is an isolated open-shell atom, which a closed-shell RHF cannot converge.
* **Origin:** `docs/tiers/paired_poses.md` documents it: with the default `relax_unmapped=False` the substituent comes from a fresh embedding rigidly aligned onto the core, so the junction is not physical, and the halogen scan example does not turn relaxation on.
* **Check:** moving that one F to 1.35 Å from its carbon (along the ring-centre direction, nothing else changed) makes the fragment converge in **18 to 19 iterations in every field tried, including all 6,458 charges.**

**What this overturns.** The premise of ferric#410 (RHF fails to converge for F/Cl analogues in a point-charge field) is wrong: the failures are smeltery's poses. The amendment A2 threshold change, run 3's results, and this study's accelerator conclusions were all measured on geometries with a broken halogen junction. The one reused SCF with dE_int +106.4 kcal/mol is now likely such a pose that converged anyway; the run-3 energies are not safe to use. Whether the CH3 and parent cases are affected was not checked (the six stored poses are the ones checked), and the converged F/Cl poses were not rechecked.

**What it does not show.** That a repaired-pose scan converges: only the fragment was repaired, and by moving one atom by hand, not by `relax_unmapped=True`. The next step is a new plan: rebuild the halogen poses with `relax_unmapped=True`, check every pose's bond lengths before any SCF, and rerun. `relax_unmapped` changes the substituent geometry and hence the ddE, so that is a deliberate change of treatment.

## Result

| Route | Cases | Converged | Limit per job | Note |
|---|---|---|---|---|
| AURORA (`aurora=True`) | 6 (4 DIIS-failed, 2 DIIS-converged at 1e-6) | 0 of 6 | 5,000 CPU-s | killed at the limit; DIIS had converged `pyr-C28-F` and `pyr-C28-Cl` in 2,490 and 2,945 s |
| TRAH (`trah_trigger=1e-2`) | 2 (`pyr-C28-F`, `pyr-C28-Cl`) | 0 of 2 | 5,000 CPU-s | the other 4 cases were **not run**: the 12 CPU-h cap allowed two jobs |
| ladder rung 1 (ADIIS) | 1 (`bnz-C34-Cl`) | 0 of 1 | 2,500 CPU-s | truncated test |
| ladder rung 2 (ADIIS + shift 0.5) | 1 | 0 of 1 | 2,500 CPU-s | truncated test |
| ladder rung 3 (ADIIS + shift 1.0 + SOSCF) | 1 | 0 of 1 | 2,500 CPU-s | truncated test |

CPU spent: 11.11 CPU-h on AURORA/TRAH and 2.08 on the probe, 13.19 together. The accel study's cap was 12 CPU-h; the probe was run on top of it at the project owner's request. The scan's running total moves from 29.0 to about 42.2 CPU-h, against the 30 CPU-h of the earlier plans.

## What this does and does not show

* **Does show:** on the two cases DIIS did converge, AURORA and TRAH were slower, not faster: both were killed at 5,000 CPU-s where DIIS needed about 2,500. They are no help on these SCFs as run.
* **Does not show** that the accelerators would help on sound geometries (they were run on broken ones). Every job was killed by the CPU limit, so there is **no iteration count and no convergence trace**. The four DIIS-failed cases never saw TRAH.
* **The ladder probe is a truncated test.** 2,500 CPU-s is roughly 50 iterations, short of rungs 2 and 3's caps (60 and 80), and Python cannot warm-start each rung from the last one's density as the real ladder does, so the three probes are single rungs from the MINAO guess. Its outcome is **inconclusive**, not a negative for the ladder. No ferric issue is filed on this evidence, as the probe plan fixed.
* **Deviation:** the dev ferric wheel's default `max_iter` is 200; the plan assumed rc7's 100. The CPU limit (about 100 iterations at roughly 50 CPU-s each) bound first, so it probably did not change the outcome, but it is not shown.
* **Build:** unreleased ferric `0.1.0.dev0`, Wheels run 38630999922 on `773081a`, in a separate environment from smeltery's rc7 pin. The poses are the frozen ones rebuilt in the project environment (all six matched the cached DIIS records).

## What would decide it

Rebuild the poses with `relax_unmapped=True` and a bond-length check, then rerun the scan under a new plan. Accelerators and ferric's ladder are not the next step: they were chasing a symptom.
