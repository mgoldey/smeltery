# PLB experimental ddG benchmark (issue #26)

Experimental relative binding free energies for 15 congeneric series (370 ligands), taken from the Open Force
Field **protein-ligand-benchmark** (PLB) and committed as one machine-readable manifest with provenance.
Nothing here runs quantum chemistry or says anything about smeltery's accuracy: this is the reference data that
issues #27-#29 would be measured against.

## Source, licence, attribution

| | |
|---|---|
| upstream | https://github.com/openforcefield/protein-ligand-benchmark |
| commit | `fd88824f9114244f95a14b485e6d6c96c1de716d` (2024-07-29) |
| retrieved | 2026-10-10 |
| data licence | **CC-BY-4.0** (upstream `LICENSE_DATA`, "Copyright (c) 2020, Open Forcefield Group"); attribution is REQUIRED |
| code licence | MIT (upstream `LICENSE`); no PLB code is used here |
| dataset DOI | 10.5281/zenodo.4813735 (from upstream `CITATION.cff`) |

Redistribution is permitted under CC BY 4.0, so the derived manifest is committed. The attribution text lives in
`licence.attribution` in `manifest.json` (the loader refuses a manifest without it):

> Protein-Ligand Benchmark Dataset for Free Energy Calculations. Copyright (c) 2020, Open Forcefield Group;
> https://github.com/openforcefield/protein-ligand-benchmark (commit fd88824f9114244f95a14b485e6d6c96c1de716d); data
> licensed under CC BY 4.0. Cite: Hahn et al., "Best Practices for Constructing, Preparing, and Evaluating
> Protein-Ligand Binding Affinity Benchmarks", LiveCoMS 4(1), 1497 (2022), doi:10.33011/livecoms.4.1.1497, and the
> dataset, doi:10.5281/zenodo.4813735. Changes: ligand records re-serialised to JSON, SMILES additionally
> canonicalised with RDKit, relative binding free energies, uncertainties and suitability statistics derived. No
> endorsement by the original authors is implied.

Only 15 PLB targets have a data directory at that commit (`targets.yml` also lists bace, jnk1, galectin, ... with
none), and PLB's own README table counts differ from the data, so every count here is from the files.

## Committed versus fetched

* **Committed**: `manifest.json` (about 460 kB): per ligand the name, upstream SMILES (verbatim) and RDKit canonical
  heavy-atom SMILES, raw measurement (type, value, unit, error, DOI/URL sources, PLB comment), derived ddG and sigma;
  per target the suitability and power statistics; for every upstream file its URL, size and sha256.
* **Fetched, not committed**: `protein.pdb` (about 0.5-0.8 MB each) and `ligands.sdf` (up to ~130 kB each), plus
  `ligands.yml`/`target.yml` on request. Reason: repository size. ddG needs only the SMILES and measurements; the
  structures matter to the later docking/QM studies. CC BY would allow committing them.

```bash
SMELTERY_NETWORK=1 uv run python scripts/fetch_plb.py DEST [--targets cdk2 mcl1] [--kinds protein_pdb ligands_sdf]
```

Each file is checked against its pinned sha256 before it is written; a mismatch aborts. Without `SMELTERY_NETWORK=1`
the script refuses to use the network. Run on 2026-10-10 for all 15 targets and all four file kinds (60 files):
every digest matched. The files at this commit are plain files, not git-lfs pointers (the PLB README still says
"git lfs"), so git-lfs is not needed.

## How ddG is derived (per target, against one reference ligand)

```
X            = IC50 or Ki as a molar concentration   (pIC50: X = 10**(-pIC50) M)
ddG_i        = R T ln(X_i / X_ref)                    kcal/mol; negative = binds tighter than the reference
R = 1.987204258640832e-3 kcal/(mol K) (CODATA 2018),  T = 298.15 K
sigma_lnX    = error / value                          (IC50, Ki; first order)
             = ln(10) * error                         (pIC50)
sigma_ddG_i  = R T sqrt(sigma_lnX_i^2 + sigma_lnX_ref^2)      independent errors; 0 for the reference
```

* **IC50 is not Kd.** Cheng-Prusoff is NOT applied; IC50-derived ddG assumes the same substrate concentration and Km
  across the series, which can hold only within one assay. **Ki targets: mcl1, ptp1b, thrombin, tyk2.** pde2 is
  pIC50; the other ten are IC50.
* T = 298.15 K is an assumption; PLB does not record assay temperature.
* **250 of 370 ligands have no reported error.** PLB's README says an unreported error is `null`, but the files use
  `-1` (a p38 comment reads "no error given"). The manifest stores these as `null` (`plb_error_raw` keeps -1) and
  their ddG sigma is `null`; nothing is imputed. Only cdk2, mcl1, p38 (28 of 29), pde2, thrombin and tyk2 carry
  errors; each of those has at least 9 analogues, which is what satisfies "at least 3 series with >= 6 analogues and
  reported uncertainties".
* What PLB's `error` is (SD, SEM, range) is not stated: UNVERIFIED. First-order propagation is crude where
  error/value is large (cdk2 lig_17 is 6.8 +/- 4.8 uM); `n_first_order_error_unreliable` counts error/value >= 0.5.
* Reference ligand: the ligand with the most single-site neighbours (ties: fewest heavy atoms, then name), computed
  by `scripts/build_plb_manifest.py`, not chosen. For cdk2 it is `lig_1h1q`, the unsubstituted anilino parent. The
  ddG between any two ligands is a difference of stored values (`smeltery.benchmark.ddg_between`).
* **Protonation**: SMILES carry explicit formal charges as PLB ships them. PLB documents Schrodinger LigPrep/Epik at
  pH 7.4 for every target (thrombin: pH 7.8) in `preparation/ligand-prep/*.inp`, and PrepWizard/PropKa/Epik at pH 7.4
  for the proteins; that those inputs generated the shipped SMILES is UNVERIFIED. PLB's `netcharge` is the PROTEIN's
  net charge (`protein_net_charge`; null for 7 targets), not the ligand's.
* Every measurement carries source DOIs (a few are patent URLs, recorded as `url`, never as DOIs) and its assay
  type (ic50, ki, pic50). Whether an assay is biochemical or cell-based is not in PLB: UNVERIFIED.

## Suitability for smeltery's method (computed, not asserted)

Computed by `smeltery.benchmark` from the SMILES, stored in the manifest (`targets.*.suitability`), and recomputed by
tests (charge, size and single-site counts for three targets; power counts for all).

* *Single-site*: RDKit MCS (elements, bond order, ring-complete, 2 s timeout) between the reference and the
  analogue; at most one connected unmatched piece on each side, each of at most 8 heavy atoms. An operational
  definition: it does not check that both pieces attach at the same atom. Pairs whose heavy-atom counts differ by
  more than 8 skip the MCS (exact). MCS timeouts, counted as not single-site: cdk8 2, p38 3, syk 1 (the timeout is wall-clock, so on a loaded machine these three counts could differ by a pair or two; the other twelve targets had none).
* Ligand net charge is the RDKit formal charge of the upstream SMILES; it agrees with PLB's own `charge` field
  wherever PLB has one. No ligand has a non-organic element (no metals) and none matched the covalent-warhead SMARTS
  screen (acrylamide/enone, vinyl sulfone, alpha-halocarbonyl, epoxide, isothiocyanate, aldehyde, boronic acid,
  sulfonyl fluoride): a screen, not proof. On the protein side pde2 has Zn and Mg and tnks2 has Zn in the pocket
  file; all are capped (ACE/NME); cdk2 has a phosphothreonine. eg5, syk, thrombin and tnks2 mix ligand charges, so
  some of their ddG includes a charge change.
* **Power.** smeltery's paired ddE has a measured noise floor of **4.07 kcal/mol** (`DDE_NOISE_FLOOR_KCAL_MOL`). A
  pair can be resolved only if its true difference is at least that, so the last three columns count experimental
  differences >= the floor and >= 2x the floor (the funnel's z=2 cut). This is the BEST CASE: it assumes smeltery's
  ddE is on the same scale as experimental ddG (slope 1). Whether it is (issue #27 cites a 4.5-5x overshoot of
  interaction energies and asks whether pairing cancels it) is not measured here. The manifest also stores a
  labelled hypothetical count for a 4.5x scale; it is a scenario, not a result.

| target | PDB | assay | ligands | reference (computed) | single-site / analogues | ligand net charge(s) | heavy atoms | exp. ddG range (kcal/mol) | ligands with reported error | median sigma(ddG) | analogues >= floor vs ref | pairs >= floor / all pairs | pairs >= 2x floor |
|---|---|---|---:|---|---:|---|---|---:|---:|---:|---:|---:|---:|
| cdk2 | 1H1Q | ic50 | 10 | `lig_1h1q` | 9 / 9 | 0 | 24-28 | 2.76 | 10 | 0.08 | 0 | 0 / 45 | 0 |
| cdk8 | 5HNB | ic50 | 31 | `lig_19` | 21 / 30 | 0 | 17-31 | 5.70 | 0 | n/a | 1 | 20 / 465 | 0 |
| cmet | 4R1Y | ic50 | 5 | `lig_CHEMBL3402744_300_4` | 3 / 4 | 0 | 27-37 | 3.68 | 0 | n/a | 0 | 0 / 10 | 0 |
| eg5 | 3L9H | ic50 | 27 | `lig_CHEMBL1089056` | 6 / 26 | 0, 1 | 21-35 | 3.49 | 0 | n/a | 0 | 0 / 351 | 0 |
| hif2a | 5TBM | ic50 | 37 | `lig_163` | 8 / 36 | 0 | 22-29 | 4.26 | 0 | n/a | 0 | 2 / 666 | 0 |
| mcl1 | 4HW3 | ki | 25 | `lig_33` | 12 / 24 | -1 | 22-27 | 4.16 | 25 | 0.20 | 0 | 1 / 300 | 0 |
| p38 | 3FLY | ic50 | 29 | `lig_p38a_3fln` | 20 / 28 | 0 | 23-34 | 3.78 | 28 | 0.28 | 0 | 0 / 406 | 0 |
| pde2 | 6EZF | pic50 | 21 | `lig_48271249` | 14 / 20 | 0 | 28-33 | 3.18 | 21 | 0.29 | 0 | 0 / 210 | 0 |
| pfkfb3 | 6HVI | ic50 | 32 | `lig_43` | 20 / 31 | 0 | 27-36 | 3.52 | 0 | n/a | 0 | 0 / 496 | 0 |
| ptp1b | 2QBS | ki | 22 | `lig_23475` | 13 / 21 | -2 | 21-37 | 5.14 | 0 | n/a | 0 | 9 / 231 | 0 |
| shp2 | 5EHR | ic50 | 24 | `lig_SHP099-1` | 11 / 23 | 1 | 21-25 | 4.32 | 0 | n/a | 1 | 3 / 276 | 0 |
| syk | 4PV0 | ic50 | 44 | `lig_CHEMBL3265006` | 17 / 43 | -1, 0 | 25-42 | 4.13 | 0 | n/a | 0 | 1 / 946 | 0 |
| thrombin | 2ZFF | ki | 23 | `lig_1b` | 22 / 22 | 0, 1 | 26-30 | 5.84 | 23 | 0.49 | 0 | 13 / 253 | 0 |
| tnks2 | 4UI5 | ic50 | 27 | `lig_5d` | 15 / 26 | 0, 1 | 17-26 | 4.29 | 0 | n/a | 0 | 3 / 351 | 0 |
| tyk2 | 4GIH | ki | 13 | `lig_ejm_42` | 12 / 12 | 0 | 21-25 | 3.45 | 13 | 0.25 | 0 | 0 / 78 | 0 |

Reading it: **no target can resolve its series.** At slope 1 the largest experimental ddG range in the set is
5.84 kcal/mol (thrombin) and no pair anywhere reaches 2x the floor. Only cdk8 and shp2 have even one analogue that
differs from its reference by the floor. cdk2's whole 2.76 kcal/mol span, and all 45 of its pairs, sit below the
floor, so a faithful result there is "unranked". The series with the most resolvable pairs are cdk8 (20 of 465, no
reported errors) and thrombin (13 of 253, with the largest reported sigma). Resolving a series needs a ddE noise
floor well under 4 kcal/mol or a ddE several times larger than ddG; this data cannot say which.

## Assay-to-assay variability

* **Within PLB: not measurable.** No compound (heavy-atom isomeric SMILES) appears twice at this commit, within or
  across targets, so there is no same-compound repeat to quantify variability from. The issue's criterion cannot be
  met from this data; this is a negative result, not an omission. (tyk2 mixes two papers, with different compounds.)
* **Literature.** Kramer, Kalliokoski, Gedeck and Vulpetti, J. Med. Chem. 2012, 55, 5165 (doi 10.1021/jm300131x), on
  independent public Ki pairs from ChEMBL: mean error 0.44, standard deviation 0.54, median error 0.34 pKi units
  (taken from the abstract via Europe PMC on 2026-10-10; the full text was not read). At 298.15 K one pKi unit is
  1.364 kcal/mol, so 0.54 pKi is about 0.74 kcal/mol. Whether the abstract's figures describe one measurement or a
  difference of two is UNVERIFIED. Either way the order of magnitude, about 0.5-1 kcal/mol, is far below smeltery's
  4.07, so the limit on this benchmark is smeltery's noise, not the experiment's. Ki only; no IC50 figure is claimed.

## Loader and tests

`smeltery.benchmark.load_benchmark()` returns typed dataclasses and raises `BenchmarkError` on a missing or blank
provenance, licence or attribution field, a malformed commit or sha256, an unknown unit or type, a non-positive
value, a missing source, an absent `error` key, a stored ddG or sigma that does not recompute from the raw
measurement, or a reference that is not a ligand of the target. `tests/test_benchmark.py` covers each case, the ddG
arithmetic against an independently written formula, the attribution text, and the fetcher with a fake downloader
(the live test runs only with `SMELTERY_NETWORK=1`).

## Regenerating

`scripts/build_plb_manifest.py` is a one-off needing PyYAML, deliberately not a smeltery dependency or extra
(`uv run --with pyyaml python scripts/build_plb_manifest.py <PLB checkout> benchmarks/plb/manifest.json`). It reads a
checkout of PLB at the pinned commit (untrusted data) and takes tens of minutes on a loaded machine because of the
all-pairs MCS.
