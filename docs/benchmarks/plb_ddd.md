# smeltery's paired ddE with a distance-dependent dielectric on the pocket field, tyk2 (issue #28)

Plan, committed before any production SCF: [plb_ddd_plan.md](plb_ddd_plan.md). Runner and analysis: `scripts/run_plb_ddE.py` (`--ddd MODEL=SLOPE`), `scripts/analyse_plb.py`,
`smeltery.plb_measure.distance_dielectric_field` and `distance_dielectric_analysis`. Results: `benchmarks/plb/results/tyk2_sto3g_ddd.json` (per-pose SCF energies, the merged gas-phase model
folded in bit for bit) and `tyk2_sto3g_ddd_analysis.json`. Gas-phase comparison: the merged run ([plb_tyk2.md](plb_tyk2.md)), on the same four poses.

## Answer in three lines

1. **A nearest-ligand-atom distance-dependent charge scaling leaves the rank agreement of ddE with experiment unchanged at the level this study can detect.** tyk2, 12 analogues, poses 0 to 3:
   rho +0.280 unscreened; +0.238 (p 0.4575, CI [-0.40, +0.76]) with eps = d and +0.210 (p 0.5151, CI [-0.44, +0.79]) with eps = 4d. Delta-rho -0.042 (paired bootstrap CI [-0.33, +0.26]) and
   -0.070 (CI [-0.42, +0.26]); both inside the pre-declared 0.15 threshold. Neither form tracks experiment at the level detectable (critical |rho| at n = 12 is about 0.59).
2. **It rescales and reshuffles a little, it does not reorder:** the ddE ranking correlates +0.979 (eps = d) and +0.916 (eps = 4d) with the unscreened one; mean dE_int goes from -18.81 to -7.11
   (x0.378) and -1.62 kcal/mol (x0.086); the spread of ddE against ddG narrows (sd ratio 2.42 to 1.00 and 0.24); ddE changes sign for 2 and 4 of 12 ligands (near zero), dE_int sign for 0 of 52 values. The
   same mechanical-rescale behaviour as the uniform-eps lane (L10).
3. **Decision for the maintainer, not taken here:** no screening option is added to `FieldInteraction`, no default is published, `systematic_floor` is untouched.

## What ran

tyk2, 13 ligands, poses 0 to 3, two treatments, 104 SCFs (RHF/STO-3G, `energy_conv` 1e-10, ferric 0.1.0rc7 from PyPI), 3.51 CPU-h (cap 5.0), no failures, none retried. The field is the merged
run's AMBER ff14SB field (PQR rebuilt with `plb_pocket_charges.py`; its sha256 equals the one recorded in the merged run, `1eae440d...`); each pocket charge is divided by max(1, s d) with d its
distance to the nearest atom of the scored ligand pose, s = 1 or 4 per Angstrom. Vacuum SCFs and the unscreened model are the merged run's, digest-checked (`assemble --base ... --same-field-as-base`).
Closest ligand-charge distance over the four poses: 1.04 A (so no charge was inside the eps = 1 floor for s = 1, and the floor binds only for s = 4 below 0.25 A, which never occurs).

## Results

| model | s (1/A) | rho | p (perm., 2-sided) | rho 95% CI | Delta-rho vs unscreened | Delta-rho 95% CI (paired) | rho with unscreened ddE | mean dE_int | ddE sign flips | rank change | tracks |
|---|---:|---:|---:|---|---:|---|---:|---:|---:|---|---|
| amber14 (unscreened) | - | +0.280 | - | - | - | - | - | -18.81 | - | - | - |
| amber14_ddd1 | 1 | +0.238 | 0.4575 | [-0.40, +0.76] | -0.042 | [-0.33, +0.26] | +0.979 | -7.11 | 2/12 | unchanged at the level this study can detect | does not track at the level detectable |
| amber14_ddd4 | 4 | +0.210 | 0.5151 | [-0.44, +0.79] | -0.070 | [-0.42, +0.26] | +0.916 | -1.62 | 4/12 | unchanged at the level this study can detect | does not track at the level detectable |

## What this does and does not show

* It is a heuristic charge scaling. There is no polarisation of the dielectric by the ligand (no reaction field), the nearest-atom distance is a stand-in for the electron-charge distance, and ligand and
  parent are screened by slightly different fields. It does not test COSMO, Poisson-Boltzmann or generalised-Born screening, or a PCM cavity that includes the pocket; those remain unmeasured (docs/limits.md U1).
* One series (tyk2, n = 12), four poses, one force field: a rank change below a few tenths cannot be established, and the two slopes are the only ones tried (none added after seeing results).
* It says nothing about absolute binding energies: dE_int is a single-geometry RHF/STO-3G interaction energy, without dispersion, entropy or relaxation.

## Deviations from the plan

None. (Branch note: the runner's tests and the mutation checks made while writing it are in `tests/test_plb_ddd.py`: the farthest-atom distance, the missing floor, a worker that ignores the slope and a
family that does not exclude distance-dielectric models each made a test fail and were reverted.)
