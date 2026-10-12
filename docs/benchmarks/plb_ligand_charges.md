# Ligand-side partial charge models on PLB tyk2 (issue #29, ligand side)

Plan, committed before any ligand charge was computed (commit `7623a53`): [plb_ligand_charges_plan.md](plb_ligand_charges_plan.md).
Raw data: `benchmarks/plb/results/tyk2_sto3g_ligand_charges.json` (per-atom Lowdin, Hirshfeld and Gasteiger charges and the pocket-field potential at every atom, 13 ligands x 8 poses);
statistics: `benchmarks/plb/results/tyk2_sto3g_ligand_charges_analysis.json`, recomputed from the first by `tests/test_plb_ligand_charges_results.py`, which also checks that the numbers quoted here equal the numbers in that file.

## Answer first: the PLB `FieldInteraction` ddE uses no ligand charge model

Read from the code (plan section 0): `FieldInteraction` and the PLB run compute `E_RHF(ligand in the pocket point charges) - E_RHF(vacuum)`. The ligand is a wavefunction; only the pocket is point charges.
Ligand Hirshfeld and Lowdin charges exist in `pocket/energy.py::compute_charges` and are returned by `compute_binding_energy` as diagnostics; they enter no energy. **Swapping the ligand charge model changes the tier's ddE by exactly zero, by construction; that is not a measurement.**
So the comparison below is on a **surrogate defined in the plan**, not on the tier: `E_es = sum_i q_i phi(r_i)`, the classical electrostatic energy of a model's atom-centred ligand charges in the same AMBER ff14SB pocket field, and
`ddE_es(L)` = pose mean of `E_es(L) - E_es(parent)`. It has no polarization, no exchange and no repulsion, and is not an estimate of the tier's ddE.

## Answer in four lines

1. **Three models, n = 12 analogues, 8 poses, identical geometries** (the stored tyk2 run's, sha256-checked; vacuum RHF/STO-3G density, ferric 0.1.0rc7; Gasteiger is topological):

   | pair | ddE_es sign flips | pair-order flips | Spearman [95% bootstrap CI] | Kendall | shift floor (range, kcal/mol) | shift sd (kcal/mol) | verdict (pre-declared) |
   |---|---:|---:|---:|---:|---:|---:|---|
   | Lowdin vs Hirshfeld | 0/12 | 0/66 | +1.000 [+1.00, +1.00] | +1.000 | 1.58 | 0.39 | agree |
   | Lowdin vs Gasteiger | 0/12 | 12/66 | +0.832 [+0.39, +0.97] | +0.636 | 6.73 | 1.81 | between |
   | Hirshfeld vs Gasteiger | 0/12 | 12/66 | +0.832 [+0.39, +0.97] | +0.636 | 6.87 | 1.93 | between |

2. **The two QM-density models rank the 12 analogues identically; the external topological model reorders 12 of 66 pairs** (Spearman +0.832, between the pre-declared "agree" and "disagree" bands, so no verdict word).
   No pair has a sign flip. The bootstrap CI of a rank correlation of exactly 1 is degenerate (every resample of 12 identical rankings gives 1).
3. **The induced shift in ddE_es** (kcal/mol, range over the 12 analogues, comparable with the pocket-side `charge_sensitivity` floors 0.67 tyk2 and 1.26 cdk2): 1.58 Lowdin to Hirshfeld; 6.73 and 6.87 to Gasteiger. These are ranges of surrogate values and exist only for a classical-ligand-charge energy that smeltery does not have.
   The raw (unpaired) E_es shift is -0.30 +/- 0.46 (Lowdin to Hirshfeld), -2.64 +/- 1.86 (Lowdin to Gasteiger) and -2.34 +/- 1.99 (Hirshfeld to Gasteiger) kcal/mol (mean +/- sd over 13 ligands x 8 poses); pairing removes the shared part.
4. **No default floor is published** and none can be: see "What this does not support".

## Descriptive checks (not interpreted beyond the numbers)

* **Against the tier's own stored QM ddE** (AMBER, the same 8 poses, same 12 analogues): Spearman of ddE_es with the QM ddE is +0.755 (Lowdin), +0.755 (Hirshfeld), +0.783 (Gasteiger), each with 6 of 12 signs the same. A classical-charge picture does not reproduce the wavefunction's ddE; its agreement with it is not resolved from chance at n = 12 and was not tested.
* **Pose noise**: mean paired SEM of ddE_es over the 8 poses is 0.15 (Lowdin), 0.14 (Hirshfeld), 0.22 (Gasteiger) kcal/mol; the Lowdin-Hirshfeld floor (1.58) and the Gasteiger floors (about 6.8) are larger than that.
* **Carbonyl polarity** (the known single-exponential Hirshfeld failure on C-O): with the rc7 default `proatom="scf"` all 208 of 208 carbonyl (ligand, pose) cases have q(C) > 0 and q(O) < 0 under Hirshfeld, and under Lowdin and Gasteiger. No inversion seen here; the `"slater"` proatom was not run, and ferric's Hirshfeld row stays graded Smoke.
* **Charge sums** equal the formal charge (0) to better than 1e-12 e for all three models.

## What was run

* 104 vacuum RHF/STO-3G SCFs (13 ligands x 8 poses, `energy_conv` 1e-10, `density_conv` 1e-8), each followed by `ferric.lowdin_charges` and `ferric.hirshfeld_charges(proatom="scf")`; all 104 converged, none dropped. RDKit Gasteiger charges from the SDF (hydrogens explicit, atom order = the SCF's).
* Availability: PyPI `ferric==0.1.0rc7` (build `cddfd1aa...`) in a clean venv has `hirshfeld_charges`, `lowdin_charges`, `mulliken_charges`, `chelpg_charges`, `resp_charges`. Nothing was substituted.
* Cost: 4.48 CPU-h for the 104 SCFs plus the 0.10 CPU-h pilot (2 SCFs, discarded), 4.6 CPU-h in all against the 6 CPU-h cap; 4 workers at `nice -n 10`, one thread each; the machine was shared and loaded, which affects wall time, not CPU time. Nothing is left running.
* The geometry check: every ligand's `pose_geometry_sha256` equals the stored run's; the PQR sha256 equals the stored AMBER PQR's.

## What this does not support

* **A default systematic floor for the charge-dependent tiers.** #29's criteria ask for a floor in kcal/mol derived from a measurement, committed as a constant, with a test that the tiers default to it. Not met, and not invented: the tier measured (`FieldInteraction`) has no ligand charge model, so these ranges are not its floor;
  the two pocket-side floors (0.67 and 1.26) disagree by about 2x and are lower bounds for one model pair; the surrogate is not the tier. `FieldInteraction.systematic_floor` still returns `None` (tested).
* Anything about the force-field tier's AM1-BCC ligand charges (a charge-dependent tier that does exist; not measured here), about field-polarized charges (charges are from the vacuum density), about cdk2 or other targets, about RESP (ferric's `resp_charges` is a single-stage fit and an external RESP tool was not installed), or about which model is closer to the truth.
* That the Lowdin/Hirshfeld agreement is general: both are population analyses of the same STO-3G density on one series of single-site analogues of one scaffold, so they share the basis and the density. Gasteiger differing from both is partly "topological vs density-derived", not only "external".
* Any statement about the tier's ddE ranking against experiment (unchanged: `plb_tyk2.md`).

## Deviations from the plan

* None to the design, the statistics or the verdict thresholds. The run used `--max-cpu-hours 5.4` (the plan said 5.5); it did not reach the cap. The pilot figures in the plan are as stated there.

## Decisions for the maintainer

1. The tier has no ligand charge model, so #29's ligand-side item is answered as "not applicable to `FieldInteraction`" plus a surrogate. Should #29 close on that, or stay open for a path where ligand charges do enter an energy (the force-field tier's AM1-BCC against another model, or a classical-charge scoring path)?
2. A pocket-side default floor still needs a third target or more charge-model pairs; the data in hand give 0.67 and 1.26 and no basis to choose.
3. If a classical-ligand-charge path is ever added, this page says its model choice moves the paired surrogate by about 1.6 kcal/mol between the two density models and about 6.8 kcal/mol to a topological one.
