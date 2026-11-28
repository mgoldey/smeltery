# Measure the FieldInteraction floor, and publish a default only if evidence supports one

Date: 2026-11-28. Status: built (tooling); no default published.

## Problem

`FieldInteraction.systematic_floor` is None, so every config sets `[cut] floor` by hand. Two lower bounds exist: L6
(cdk2, 1.26 kcal/mol) and L11 (tyk2, 0.67), AMBER ff14SB vs CHARMM36 on identical geometries. They differ 1.89x.

## Audit of what can be measured offline

| Need | Finding |
|---|---|
| Stored per-pose dE_int under two charge models | cdk2 (8 analogues, 5 poses) and tyk2 (12, 8) in `benchmarks/plb/results/`. Nothing else. |
| A third system | Would need a PLB protein and ligand set (fetch needs `SMELTERY_NETWORK=1`; only `tests/data/plb/mini_cdk2.pdb`, a fixture, is local) and RHF/STO-3G SCFs in ferric (cdk2 pose 0 of one ligand took 470 CPU s without converging). Not cheap on 1-2 cores; not done. |

## Decisions

1. `smeltery.floor_measure.system_floor` takes `(n_ligands, n_poses)` energies under two models and returns the same
   floor `gates.charge_sensitivity` reports on the paired ddE (range of per-analogue shifts), plus shift SD, sign and
   pair-order flips, and the largest pose SEM of the shift. `measure_file` validates a PLB results file first.
   Solvent and distance-dielectric models are refused as the compared pair.
2. `pool` keeps every system visible and reports max, median, min, spread ratio and a df-weighted pooled shift SD. It
   never replaces a disagreement with one average. The same system twice is refused.
3. `provisional_default` is policy: at least 3 systems and max/min <= 1.5. The value is the max per-system floor. The
   thresholds are decisions, not measurements, and are in the returned `rule`. The result is a `ProvisionalFloor` with
   `measured` False (not settable) whose `Floor.source` begins `PROVISIONAL, NOT MEASURED` and lists each system.
4. With two systems it refuses. The tier's `systematic_floor` stays None and is not wired to the provisional value;
   the config's explicit `[cut] floor` remains the only route, so a run record cannot call it a tier measurement.
5. Docs: no new L-row (nothing new was measured; L6 and L11 are reproduced). New U3 row records what is missing.

## Tests

Synthetic arrays (range, common-mode cancellation, refusals), pooling arithmetic, provisional refusals and labelling,
tier floor still None, reproduction of L6/L11 from the stored files, CLI exit codes.
