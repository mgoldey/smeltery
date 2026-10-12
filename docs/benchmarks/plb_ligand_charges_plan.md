# Plan: ligand-side partial charge models on the PLB tyk2 poses (issue #29, ligand side)

Status: **PLAN, committed before any ligand charge was computed.** Anything done differently is listed under "Deviations" in the report
(`docs/benchmarks/plb_ligand_charges.md`). Issue #29 asks for a comparison of ligand-side charge models (Lowdin, Hirshfeld, one external).
Pocket-side comparisons are done ([plb_cdk2.md](plb_cdk2.md), [plb_tyk2_charmm.md](plb_tyk2_charmm.md)); this plan does not touch them.

## 0. The code path, established from the code (read, not assumed)

* **The PLB `FieldInteraction` ddE uses no ligand partial charge model at all.** `FieldInteraction` (`src/smeltery/tiers.py`) and the PLB run
  (`scripts/run_plb_ddE.py`, `run_task`) compute `E_RHF(ligand in the pocket point charges) - E_RHF(vacuum)`: the ligand is a QM wavefunction
  (`ferric.run_rhf(mol, basis, point_charges=...)`); only the pocket is classical charges. No ligand charge is read, fitted or assigned anywhere on that path.
* **Where ligand charges do exist in the pocket package**: `pocket/energy.py::compute_charges` returns Hirshfeld and Lowdin charges from a converged density;
  `pocket/binding_energy.py::compute_binding_energy` calls it in vacuum and in the field and returns them as `charges_vacuum` / `charges_field`. They are **reported
  diagnostics; they enter no energy and no ddE** (`delta_e_kcal_mol = e_field - e_vacuum`, nothing else). The only test (`tests/test_active_site.py`) checks the two keys exist.
* **Elsewhere in smeltery** a ligand charge model does enter an energy: the force-field tier (`tiers.py`) assigns AM1-BCC to the ligand once per SMILES (pose-independent).
  That is a different tier, a different charge model, and not a PLB measurement here; it is not part of this comparison.
* **Consequence.** A "ligand charge model" cannot change the `FieldInteraction` ddE, so the induced shift of that ddE from swapping the ligand charge model is **exactly zero by
  construction**, not a measured zero. Any kcal/mol number below belongs to a **surrogate** defined here, not to the tier. The report must say so first.

## 1. What is compared (the surrogate, defined now)

For model m, pose k, ligand L, with the same AMBER ff14SB pocket point charges Q_j used by the stored tyk2 run (1013 charges, cutoff 15 A, PQR sha256 `1eae440d...` checked):

    E_es(m, L, k) = sum_i q_i(m, L, k) * phi_k(r_i),     phi(r) = sum_j Q_j / |r - R_j|      (kcal/mol, Coulomb constant from the repo's bohr and Hartree constants)
    ddE_es(m, L)  = mean over poses k of [ E_es(m, L, k) - E_es(m, parent, k) ]                 (paired by pose, as the tier's ddE is)

This is the classical first-order electrostatic energy of the ligand's atom-centred charges in the same field. It is the quantity in which a ligand charge model would enter if
smeltery had a classical-charge scoring path (it has none). It carries no polarization and no exchange/repulsion; it is NOT an estimate of the tier's ddE and is not compared
to experiment. It is also compared, descriptively, with the tier's own stored QM ddE (AMBER, the same poses) to show how far a classical-charge picture sits from the wavefunction.

## 2. Data and models

* **Geometries**: the stored tyk2 run's: 13 ligands (12 analogues + parent `lig_ejm_42`), **8 poses** (seed 1, 15 deg, 0.5 A, shared pivot). Regenerated with the same code and
  **refused unless every ligand's `pose_geometry_sha256` equals the stored one**. n = **12 analogues** throughout. Net charge 0 for all.
* **Lowdin**: `ferric.lowdin_charges` on the converged vacuum RHF/STO-3G density of each (ligand, pose). The project's CM5 baseline per the maintainers' notes.
* **Hirshfeld**: `ferric.hirshfeld_charges(..., proatom="scf")`, the rc7 default (free-atom SCF proatoms in the molecule's basis). **Availability checked** on a clean venv with PyPI
  `ferric==0.1.0rc7` (build sha `cddfd1aa...`): `hirshfeld_charges(mol, basis_set, result, proatom="scf")`, `lowdin_charges`, `mulliken_charges`, `chelpg_charges`, `resp_charges` all exist.
  The `"slater"` single-exponential proatom (documented in rc7 as qualitative, 0.23-0.72 e from "scf") is **not used**; the ferric Hirshfeld row stays graded Smoke and is reported as such.
  A descriptive sanity check is pre-declared: the fraction of (ligand, pose, carbonyl C=O) with q(C) > 0 and q(O) < 0 under each model, because a single-exponential Hirshfeld proatom is known to invert C-O.
* **External**: **Gasteiger-Marsili via RDKit** (`rdPartialCharges.ComputeGasteigerCharges`, RDKit 2026.x, BSD-3-Clause, `docs/licensing.md` row `rdkit-code`, already a dependency; no licence issue).
  Gasteiger is topological: the same charges at every pose. Its charges sum to the formal charge (checked). RESP is **not** run: `ferric.resp_charges` is a single-stage ESP fit (not multi-conformer RESP, per its own
  docstring) and would be one more ferric-internal model rather than an external one; an external RESP tool (e.g. via AmberTools) was not installed. Not substituted silently: absent.
* **Which density**: the **vacuum** density at each pose. Charges re-derived from the field-polarized density (`charges_field` in `compute_binding_energy`) are **not** computed (it would double the SCF cost); a limitation stated, not assumed away.
* **SCF**: RHF/STO-3G, `energy_conv` 1e-10, `density_conv` 1e-8 (the stored run's settings), ferric 0.1.0rc7 from PyPI.

## 3. Statistics, fixed now

Per model: ddE_es(m, L) for the 12 analogues (pose mean and paired SEM over the 8 poses). For each of the three pairs (Lowdin-Hirshfeld, Lowdin-Gasteiger, Hirshfeld-Gasteiger), n = 12:

* ddE_es **sign flips** (count, of 12), **pair-order flips** (of 66), **Spearman** (with a 95% bootstrap CI over ligands, 10,000 resamples, seed fixed in the script), Kendall tau-b;
* the **induced shift** shift(L) = ddE_es(b, L) - ddE_es(a, L), kcal/mol: its range (the `gates.charge_sensitivity` floor, so the number is comparable with the pocket-side floors 1.26 and 0.67), its sd, and the per-ligand values.
  Also the raw (unpaired) E_es shift, mean and sd across ligand-poses, so what pairing cancels is visible.
* Against the tier's stored QM ddE (AMBER, the same 8 poses): Spearman and sign agreement of each model's ddE_es. Descriptive.
* Context only: the shift floor against the pose-noise mean paired SEM of ddE_es, and against the pocket-side floors 0.67 (tyk2) and 1.26 (cdk2).
* Checks printed, not interpreted: the maximum |sum of charges - net charge| per model; the carbonyl polarity fraction above.

### Verdict language, fixed before the data

Per pair, using the same thresholds the tyk2 pocket plan used for "agree" (Spearman >= 0.90, sign flips <= 1 of 12, pair-order flips <= 6 of 66):

* **Agree** (all three met): "on these 12 analogues and 8 poses the two ligand charge models give the same surrogate ranking; shifts of X kcal/mol (range)". Nothing about the tier follows, because the tier uses no ligand charge model.
* **Disagree** (any one of: Spearman < 0.70, sign flips >= 3 of 12, pair-order flips >= 17 of 66): "the ligand charge model changes the surrogate's ranking; any classical-ligand-charge scoring in smeltery would need a stated model and this range of shift".
* **Between** (neither): reported with the numbers and no verdict word.

The earlier smeltery study (two ligand charge models, 4 of 12 signs flipped, Spearman +0.664; Hirshfeld vs Lowdin +0.671) is context; it is not pooled and not a prior on these numbers.

## 4. What this plan will not produce

* **No default systematic floor.** #29's acceptance criteria ask for a floor in kcal/mol for the charge-dependent tiers, committed as a constant with a test that those tiers default to it. This measurement cannot support one:
  (a) the tier measured, `FieldInteraction`, has no ligand charge model, so a ligand-side range is not that tier's floor; (b) the two pocket-side floors already disagree (0.67 vs 1.26, outside the factor-2 band) and are lower bounds on one model pair;
  (c) the surrogate is not the tier's ddE. **Whatever the numbers are, no constant is added, `FieldInteraction.systematic_floor` is unchanged, and the report says so.** A limits row records the measured number with its source and its non-claim.
* Nothing about the force-field tier's AM1-BCC charges, about field-polarized charges, about other targets (cdk2 is not run), or about which model is closer to the truth.

## 5. Budget (declared)

* At most 4 workers at `nice -n 10`, one thread each; cap **6 CPU-h in all**, pilot included.
* **Pilot** (disclosed): 2 SCFs with charges, `lig_ejm_42` and `lig_ejm_48`, pose 0, printed CPU seconds only: **145 s and 202 s** (the SCF 131 s and 182 s; Lowdin plus Hirshfeld about 14 s and 20 s). The charges were not printed; the pilot output is discarded and not reused.
* Projection: 13 ligands x 8 poses = 104 tasks at about 145 s on average = about 4.2 CPU-h plus 0.1 pilot, about 4.4 CPU-h, inside the cap with no shrinking. The run cap is set to 5.5 CPU-h; if it is reached, the common pose prefix that every ligand finished is analysed
  (poses finish in pose-major order, so which poses finished is decided by the cap, never by a result); fewer than 4 common poses means no analysis is reported.
* **What has been seen**: the manifest; the stored tyk2 QM ddE and its statistics; the pocket-side floors; the earlier study's summary numbers above. No ligand charge from the 13 ligands has been looked at.

## 6. Tests (written with the analysis, before the production file is assembled)

Synthetic-data tests of the Coulomb arithmetic and the pair statistics; a test that the committed results recompute (sum of q*phi, ddE_es, flips, Spearman) from the committed per-atom charges and potentials; a test that quoted numbers in the report and in `docs/limits.md` equal the analysis file;
a test that `FieldInteraction.systematic_floor` is unchanged (no default introduced); mutation checks recorded in the test docstring.
