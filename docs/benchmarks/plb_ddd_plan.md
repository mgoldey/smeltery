# Plan: does a distance-dependent dielectric on the pocket field change the rank agreement of smeltery's paired ddE with experiment? (issue #28)

Status: **PLAN, committed before any production SCF.** It extends the tyk2 uniform-eps lane ([plb_tyk2.md](plb_tyk2.md), L10) by changing ONE
thing: each pocket charge is divided by a dielectric that grows with its distance from the scored ligand. Same ligands, poses, AMBER ff14SB field,
RHF/STO-3G, statistics code and the 0.15 shift threshold as the PCM lane ([plb_pcm_plan.md](plb_pcm_plan.md)). Deviations are listed in the report
(`docs/benchmarks/plb_ddd.md`).

## 0. What is known, what has been seen

* Uniform eps = 2, 4, 8 is a mechanical rescale (slope falls as 1/eps; rho moves by at most 0.11). IEF-PCM with a ligand-only cavity leaves rho
  unchanged (change +0.014). Both say nothing about screening that depends on where a charge sits relative to the ligand. That is this study.
* No distance-dependent-dielectric SCF has been run and no such energy seen. The only cost datum is the merged tyk2 field SCFs: about 100 CPU-s each.

## 1. The treatment, exactly

For ligand i at pose k, every pocket charge q_j (the same 1013-charge AMBER field, 15 A cutoff, same PQR sha256 as the merged tyk2 run) is replaced by
q_j / eps(d_j), where d_j is the distance in Angstrom from charge j to the NEAREST ATOM of that ligand pose (all atoms) and

* **ddd1**: eps(d) = max(1, d)       (the classical "eps = r" form)
* **ddd4**: eps(d) = max(1, 4 d)     (the "eps = 4r" form)

Positions are untouched. dE_int(ddd; i, k) = E_RHF(ligand in the scaled field) - E_RHF(vacuum), the vacuum SCFs being the merged ones (digest-checked
`assemble --base`). ddE is the mean over poses of the paired difference to the parent, as before.

**What this is not.** A heuristic charge scaling. It does not model the dielectric polarising in response to the ligand (no reaction field), it is not a
Poisson-Boltzmann or generalised-Born treatment, and the nearest-atom distance is a stand-in for the electron-charge distance. Because d_j depends on the
ligand's own atoms, ligand and parent are screened by slightly different fields; that is part of the treatment. Whatever the outcome the report says
"a nearest-ligand-atom distance-dependent charge scaling was tested", not "pocket screening was tested". COSMO and a cavity that includes the pocket
remain unmeasured (docs/limits.md U1).

## 2. Run

tyk2 only (n = 12 analogues, 13 ligands), poses **0, 1, 2, 3** (the poses the gas and uniform-eps models share), both treatments: 2 x 13 x 4 = 104 field
SCFs, about 2.9 CPU-h. CPU cap 5.0 CPU-h; a failed SCF is not retried and its pose is dropped for every ligand and model (the merged convention). No
other series, slope, floor or basis is run, and none is added after seeing results. cdk2 is not run.

## 3. Endpoint and labels (fixed now)

Primary: **Delta-rho = rho(ddE_ddd, ddG) - rho(ddE_amber14, ddG)** on the same four poses, with the paired bootstrap 95% CI over ligands (10,000, seed 20261010,
the PCM lane's code). Labels from `rank_change_label`: improves / worsens (|Delta-rho| >= 0.15 and CI excludes 0); "shift of at least 0.15 not
distinguishable from zero"; otherwise "unchanged at the level this study can detect". Also reported per treatment: rho, permutation p, CI, tau-b, slope and
sd ratio, mean dE_int and its ratio to the unscreened value, ddE and dE_int sign flips, the `tracks` label (the tyk2 plan's Q1 criteria). Critical |rho|
at n = 12 is 0.587 (L8), so a true correlation below about 0.6 is invisible and a rank change below a few tenths cannot be established.

## 4. Not claimed in advance

No claim that ddE agrees with experiment, none about other series or force fields, none about solvation. A null result closes this lane for the
nearest-atom form only. A new `docs/limits.md` row is added with the measured numbers and the committed results file as its source.
