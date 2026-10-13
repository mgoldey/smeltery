# Plan: a single-structure MM/GBSA baseline against the PLB experimental ddG on cdk2 and tyk2 (issue #27)

Status: **PLAN, written and committed before any MM/GBSA energy of the cdk2 or tyk2 series was computed.** Anything done
differently from this plan is listed under "Deviations" in the report (`docs/benchmarks/plb_mmgbsa.md`) with the reason.
Context: smeltery's paired RHF/STO-3G ddE did not clearly track experiment on cdk2 (n = 8, rho +0.69, p 0.069) or tyk2
(n = 12, rho +0.25, p 0.43), and Crippen logP does as well on cdk2 and not on tyk2 ([plb_cdk2.md](plb_cdk2.md),
[plb_tyk2.md](plb_tyk2.md)). On congeneric series the standard cheap physics baseline is MM/GBSA, and smeltery has none, so
those two results cannot say whether ddE is better or worse than the standard cheap method. This plan measures that baseline.

## 0. Feasibility, established before this plan (what was tried; no series energy was computed)

* **Environment.** The optional conda environment of `docs/environments.md` ("OpenMM force-field path") exists on this machine
  (`/tmp/ff18/ffenv`, built 2026-10-10 from `environment-openff.yml`): openmm 8.6.1, openmmforcefields 0.16.0, openff-toolkit 0.18.0,
  ambertools 26.0 (`antechamber`, `sqm` on PATH once the environment's `bin` is first), rdkit 2026.03.2. No new environment is built
  and no new smeltery dependency or extra is added: the tool is a script that runs only in that environment.
* **API checks run on ethanol only** (a molecule outside the series): GAFF2 (`gaff-2.11`, `GAFFTemplateGenerator`) and OpenFF Sage 2.2.1
  (`SMIRNOFFTemplateGenerator`) both build a System together with `amber14-all.xml` and `implicit/obc2.xml`; the OBC2 force then comes out
  of OpenMM's `GBSAOBC2Force` script, which assigns radii and screening by element and bonding, so any ligand atom gets GB parameters from
  the ligand's own NonbondedForce charge. Both full proteins build with `amber14-all.xml` once the unsupported residues are handled:
  tyk2 needs only the crystal waters removed (its ACE/NME caps have templates); cdk2 (two chains, 9034 atoms: CDK2 and a cyclin
  fragment) also needs the phosphothreonine TPO160, which `amber14-all.xml` has no template for, deleted.
* **Licences (read from conda package metadata on 2026-10-13, so INFERRED, not from the licence files): OpenMM LGPL-3.0-or-later,
  openmmforcefields MIT, openff-toolkit MIT, openff-forcefields (Sage) CC-BY-4.0, AmberTools GPL-3.0-or-later AND LGPL-3.0-or-later AND
  BSD-3-Clause AND MIT (sqm and antechamber are AmberTools components). No OpenEye or other restricted-use toolkit is involved (AM1-BCC is
  computed by AmberTools' sqm, not OpenEye's QUACPAC). `docs/licensing.md` has no rows for these tools (its exclusion rule is about
  structure-prediction tools and `tests/test_licensing.py` requires a fetched primary quote per row); no row is added because the primary
  texts were not fetched here. The consequence is stated, not hidden: GPL components are used as external command-line programs
  (`sqm`/`antechamber`) and as a conda environment, never linked into or redistributed with smeltery.
* **Pilot disclosure.** Before this plan was committed nothing of the series was run. The pilot after the plan commits (one cdk2 parent
  minimisation and three energy evaluations, to time the cost) prints timings only; its energies are not printed.

## 1. What is seen, and what this study may not do

* Seen: the ddE results, the Crippen logP results and the experimental ddG of both series (public). Not seen: any MM energy of any series ligand.
* **No knob is tuned after seeing agreement with experiment.** One protocol (section 3) and **one** pre-declared sensitivity variant (section 4).
  If a ligand fails (a parameterisation error, a non-converged minimisation), it is reported as it is; the rule for that is in section 3.6.
* cdk2 and tyk2 are analysed separately and never pooled.

## 2. Data

* PLB commit `fd88824f9114244f95a14b485e6d6c96c1de716d` (CC BY 4.0), files fetched with `scripts/fetch_plb.py` and sha256-checked against
  the manifest (the run script re-checks and refuses a ligand whose SDF structure differs from the manifest SMILES).
* **cdk2** (PDB 1H1Q): parent `lig_1h1q`, the **same 8 analogues as the ddE study** (`lig_1h1r, lig_1oi9, lig_1oiu, lig_1oiy, lig_20, lig_21, lig_22, lig_26`).
  **`lig_17` (3-bromo) is excluded from the primary and paired analyses** because ddE could not be computed for it (no STO-3G bromine shells), and the paired
  comparison needs the same ligands on both sides. MM does not need the element table, so **it is run anyway and reported in ONE pre-declared descriptive row:
  MM/GBSA on n = 9 (rho, permutation p, bootstrap CI only; no comparison with ddE, which does not exist for it).** Caveat declared now: OpenMM's `mbondi2`
  table has no bromine, so its GB radius is the 1.5 A default; that row is weaker than the others on that count.
* **tyk2** (PDB 4GIH): parent `lig_ejm_42`, the same 12 analogues.
* **Geometry.** The **PLB pose of each ligand, unmoved, in the protein's frame** (one snapshot per ligand). This is the geometry from which the
  ddE jittered poses were built (a shared rigid transform of the PLB pose), so the two methods score the same ligands in the same binding mode; it is not
  one of the jittered poses (the ddE study dropped its unmoved pose 0 for cdk2). The provenance of the PLB poses (crystal or modelled analogue) is not
  re-verified here. Not docking, no pose search, no ensemble.
* Experimental ddG = `R T ln(X_i / X_ref)` from the committed manifest (cdk2 IC50, tyk2 Ki), as in the ddE studies.

## 3. Protocol (the primary; every choice fixed now)

3.1 **Receptor.** PLB `protein.pdb`, protonation exactly as shipped, all chains kept. Deleted: crystal waters (as in the ddE field), and for cdk2 the TPO160
    residue (no `amber14-all.xml` template; refused unless every atom lies more than 10 A from every atom of the parent ligand, distance recorded).
    ACE/NME caps are kept where `amber14-all.xml` templates them. Any other residue with no template is a refusal, never skipped. Protein force field:
    **AMBER ff14SB (`amber14-all.xml`)**, the same family as the ddE field.

3.2 **Ligand.** The SDF structure with its explicit hydrogens and formal charge as shipped. **Charges: AM1-BCC computed by AmberTools' `sqm` through
    openff-toolkit, from the PLB pose conformer of that ligand** (so charges are pose-specific, not conformer-averaged). **Ligand force field: GAFF2
    (`gaff-2.11`) through `openmmforcefields.GAFFTemplateGenerator`**, supplied with those charges (a check refuses the run if the System's ligand charges differ from them by more than 1e-6 e).

3.3 **Complex.** Protein + ligand in one System, `NoCutoff`, no constraints. **Implicit solvent: OBC2 (Onufriev-Bashford-Case, Amber igb 5 analogue)**
    from OpenMM's `implicit/obc2.xml`: mbondi2-type radii, solute dielectric 1, solvent dielectric 78.5, no salt, and OpenMM's ACE surface-area term at its
    default (2.25936 kJ/mol/nm^2). This is OpenMM's GB and SA, not Amber's LCPO with gamma 0.0072; the difference is declared and not varied.

3.4 **Minimisation (one, of the complex, in GB).** Protein heavy atoms are held by a harmonic restraint to their input positions with k = 10 kcal/mol/A^2 (4184
    kJ/mol/nm^2); ligand atoms and protein hydrogens are free. OpenMM `LocalEnergyMinimizer`, CPU platform (single precision), rounds of 1000 iterations until the
    RMS force of the total (restraints included) is at most 1 kJ/mol/nm or 5000 iterations have been used. The restraint is used only for minimisation and is
    in none of the reported energies.

3.5 **Energies and the score.** On the minimised coordinates, with the Reference platform (double precision), no restraint:
    `E(C)` for the complex, `E(R)` for the receptor atoms alone and `E(L)` for the ligand alone, **the same snapshot** (the single-trajectory approximation),
    each as MM energy (bonded + vdW + Coulomb) plus the GB + SA solvation energy. The score
    **dG_MMGBSA(L) = E(C) - E(R) - E(L)** (kcal/mol, kJ divided by 4.184). The bonded part of dG is zero by construction on one snapshot; it is computed and recorded as a check
    (|bonded term| below 1e-6 kcal/mol, else the run is refused). The calculated relative score of an analogue is **ddG_MM(L) = dG_MMGBSA(L) - dG_MMGBSA(parent)**,
    the quantity compared with the experimental ddG.
    * **Ligand strain.** Not included: the ligand's conformation is the one in the complex, in both the complex and the ligand-alone term (so its internal energy
      cancels), with no re-minimisation of the free ligand. The same holds for receptor reorganisation. This is the usual single-trajectory simplification and is a known source of error.
    * **Entropy and explicit water are omitted.** No ensemble: one minimised snapshot per ligand. This is a deliberately cheap, low-sampling MM/GBSA; results here say nothing
      about MM/GBSA averaged over molecular dynamics snapshots, which is what most published MM/GBSA numbers are.
    * Reported decomposition (descriptive, not an endpoint): vdW, Coulomb, GB + SA (the OpenMM GB force contains both; they are not separated) and the sum.

3.6 **Failures.** A ligand that cannot be parameterised stops the run for that ligand and is reported as missing (n then drops, stated). A minimisation that reaches the
    5000-iteration cap without meeting the tolerance is **kept**, flagged, and a sensitivity row drops the flagged ligands (descriptive).

3.7 **Cost.** 4 worker processes at `nice -n 10`, one OpenMM CPU thread each; the cap is **6 CPU-h** (the sum of the per-task process CPU seconds). If the pilot projects more than 6 CPU-h for
    both variants on both series, the run stops and the report says which part was not run (the primary on cdk2, then tyk2, then the variant).

## 4. The one pre-declared sensitivity variant

**S: the ligand force field is OpenFF Sage 2.2.1 (`openff_unconstrained-2.2.1.offxml` through `SMIRNOFFTemplateGenerator`), the same AM1-BCC charges, everything else identical.** (Sage is the
force field smeltery's `ForceField(path="openmm")` already uses.) It is reported next to the primary and never replaces it; it is not used to pick a winner.
No other variant (no GBn2, no minimisation change, no other charge method, no pose change) is run.

## 5. Statistics, fixed now

Primary endpoint, per series, separately: **Spearman rho of ddG_MM against the experimental ddG over the analogues** (cdk2 n = 8, tyk2 n = 12), the order cdk2 then tyk2.
Computed with the code the ddE studies used (`smeltery.plb_measure`): exact permutation p (n <= 9) or 200,000 seeded shuffles (n = 12), two-sided on |rho|; Kendall tau-b; 95% percentile
bootstrap CI over ligands, 10,000 resamples, seed 20261010; OLS slope, sd ratio and sign agreement are printed, **not interpreted** (as in the ddE studies, only when rank tracking is shown).

For the same ligands: the stored ddE rho (AMBER ff14SB field, the ddE studies' primary: per-ligand pose means from `*_sto3g_analysis.json`) and the Crippen logP rho.

* **Paired comparison with ddE: Delta-rho = rho_MM - rho_ddE**, 95% percentile bootstrap CI from the same ligand resamples for both correlations (`paired_delta_spearman_ci`, 10,000, seed 20261010).
* The same Delta-rho against Crippen logP (informational).
* Spearman of ddG_MM with ddE and with logP (is MM/GBSA just size?), and the rho of each MM component (descriptive).
* The detectable effect is stated before interpreting any correlation: smallest significant |rho| 0.738 (n = 8) and 0.587 (n = 12); about 40% / 68% power at true r = 0.7.

## 6. Language for each outcome, fixed in advance

Per series, for MM/GBSA alone (the `tracks_label` rule of the tyk2 plan): **"tracks (weakly)"** iff rho > 0 and p < 0.05 and the bootstrap CI excludes 0 and tau-b > 0;
**"anti-correlated"** iff rho < 0 and p < 0.05; otherwise **"does not track at the level this n can detect (no evidence either way)"**.

Comparison with ddE per series, from the Delta-rho CI: lower bound > 0, **"MM/GBSA ranks this series better than ddE (95% CI excludes 0)"**; upper bound < 0, **"ddE ranks it better"**;
otherwise **"cannot be distinguished at this n"**, and the sign of the point estimate is not interpreted. At n = 8 and n = 12 an interval that wide is the expected outcome and is reported as such.

Joint reading, stated only if both series give the same label: MM/GBSA tracks on both and ddE does not, "ddE has not been shown to beat the standard cheap baseline"; neither tracks on either, "these
series cannot rank ddE against the standard cheap baseline in either direction"; the labels differ between series, "the two series disagree, as the literature's series dependence of MM/GBSA would predict; no pooled statement".

What no outcome supports (written before the data): pairwise ranking (experiment resolves no pair at the 4.07 kcal/mol floor); absolute binding free energies; any statement about MM/GBSA with MD sampling, entropy or
explicit waters; a ranking of the two methods from the point estimates of two correlations; a comparison with literature R values (different sets, different sampling).

## 7. Files and tests

`scripts/plb_mmgbsa.py` (run and assemble; the conda environment only), `scripts/analyse_plb_mmgbsa.py` (analysis; needs only numpy, RDKit via `smeltery.plb_measure`), results under `benchmarks/plb/results/` (`*_mmgbsa.json`,
`*_mmgbsa_analysis.json`), `tests/test_plb_mmgbsa.py` (statistics on synthetic data with mutation checks, the committed results recomputed with floats to rel 1e-12, the report's quoted numbers; the OpenMM energy tests skip without
the environment), report `docs/benchmarks/plb_mmgbsa.md`, a `docs/limits.md` row (number and existing source). No series name in `src/`. No new dependency or extra.

## Amendment log

(Amendments are appended here with the date and the reason, never edited into the sections above.)

* **A1, 2026-10-13, before any series energy.** Section 3.2 says the AM1-BCC charges are computed "from the PLB pose conformer". Reading what antechamber
  writes (`sqm.in` for `-c bcc` on a test molecule outside the series: "Run semi-empirical minimization", no `maxcyc` line) shows sqm **minimises the geometry at
  the AM1 level first**, so the charges are those of the AM1-optimised conformer started from the PLB pose, not of the PLB geometry itself. Nothing else changes
  (same method, same code path as `ForceField(path="openmm")`); the sentence in 3.2 is to be read this way.
* **A2, 2026-10-13, before any series energy was seen. Two cost-driven changes to sections 3.1 and 3.3, made after a pilot that was stopped by me, not by a result.** The pilot
  (cdk2 parent, GAFF2, the protocol as committed) ran 12 CPU-minutes without finishing minimisation and was killed; its energies were never printed or read, and its output directory was deleted.
  A timing test on the tyk2 protein alone (no ligand, nothing of the series scored) then measured one energy-and-force evaluation of the whole 4652-atom receptor at **4.2 s** with the xml's
  CustomGBForce on one CPU thread, so the full-protein protocol (9034 atoms for cdk2, thousands of evaluations per minimisation) would cost tens of CPU-hours against the 6 CPU-h cap.
  1. **GB force.** Section 3.3 said OBC2 "from OpenMM's `implicit/obc2.xml`". Instead the same model is built with OpenMM's **native `GBSAOBCForce`** (OBC type II, Amber igb 5; alpha 1.0, beta 0.8, gamma 4.85)
     with the intrinsic radii (mbondi2) and the OBC screening parameters by element taken from OpenMM's own tables (`openmm.app.internal.customgbforces`), the charges from the NonbondedForce,
     solute dielectric 1, solvent dielectric 78.5, and the force's default ACE surface-area term (the same 2.25936 kJ/mol/nm^2). Measured on the tyk2 receptor alone, same coordinates,
     CPU platform: total energy -36969.98 kJ/mol (native) against -36970.03 kJ/mol (xml CustomGBForce), a difference of 1.3e-6 relative, and 0.63 s per evaluation against 4.2 s.
  2. **Receptor truncation.** Section 3.1 said all chains kept. Instead the receptor is **every residue with any atom within 10 A of any atom of any PLB ligand pose of that series** (the union over all the series' ligands,
     including cdk2's `lig_17`, so one receptor serves every ligand and the paired differences share it), whole residues, crystal waters dropped, protonation as shipped, `ignoreExternalBonds` so cut residues are
     templated as in-chain, no capping or neutralisation of the cut ends (declared limitation; its effect on a difference of two ligands in the same receptor is not measured). A residue inside the shell with no
     template is an error. The explicit TPO160 deletion and its guard are therefore dropped from the protocol (TPO160 lies about 17 A from the parent and outside the shell; the run records which non-standard residues, if any, stay).
     The 10 A radius was fixed by cost (the shell holds a small fraction of the atoms) and was not varied.
  Everything else in sections 3 to 6 is unchanged, including the restrained minimisation (protein heavy atoms, k = 10 kcal/mol/A^2), the tolerance, the single snapshot and the statistics.
* **A3, 2026-10-13, before any series result was read.** After A2 the same pilot (cdk2 parent, GAFF2) finished in **45 CPU-s** (1133 receptor atoms in 69 residues, GAFF2 charges used exactly), so the
  full run is about 0.5 CPU-h for 46 tasks, far inside the cap. Its minimisation stopped at the 5000-iteration budget with a total RMS force of **1.26 kJ/mol/nm** against the declared tolerance of 1.0: the CPU platform is single precision, so
  1.0 is below its noise floor and would flag essentially every ligand as "not converged", which would make the flag meaningless. The RMS-force tolerance of section 3.4 is therefore **5 kJ/mol/nm** (half OpenMM's default of 10); the
  5000-iteration budget and everything else are unchanged. Only the minimisation diagnostics (iterations, RMS force, receptor size, charge check, bonded-term check, timing) of this one parent pilot were read; **its energies were not**, and its output was deleted.

