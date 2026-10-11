# Assay-to-assay variability (ChEMBL)

Issue #26 asks for the experimental floor: how far do independent measurements of the *same compound* disagree?
PLB (`benchmarks/plb/`) cannot say, because no compound appears twice in it. ChEMBL can: the same compound is
often measured by different groups. `scripts/chembl_variability.py` computes it; `results.json` is its output.

## Result

For each PLB target, ChEMBL binding-assay activities of the target's own PLB measurement type (IC50 or Ki), exact
values in nM, grouped by compound; a compound measured in two or more different publications (documents) gives
independent estimates. Pooled within-compound SD of pChEMBL, converted with RT ln 10 = 1.3643 kcal/mol per log
unit at 298.15 K.

| PLB target | ChEMBL target | type | compounds in 2+ papers | pairs | identical pairs | pooled SD (log10) | pooled SD (kcal/mol) |
|---|---|---|---|---|---|---|---|
| cdk2 | CHEMBL301 | IC50 | 38 | 138 | 1 | 0.668 | 0.91 |
| cdk8 | CHEMBL5719 | IC50 | 21 | 46 | 1 | 0.542 | 0.74 |
| cmet | CHEMBL3717 | IC50 | 72 | 946 | 2 | 0.761 | 1.04 |
| eg5 | CHEMBL4581 | IC50 | 26 | 65 | 0 | 0.400 | 0.55 |
| hif2a | CHEMBL1744522 | IC50 | 24 | 26 | 1 | 0.310 | 0.42 |
| mcl1 | CHEMBL4361 | Ki | 76 | 155 | 1 | 0.629 | 0.86 |
| p38 | CHEMBL260 | IC50 | 100 | 621 | 13 | 0.678 | 0.92 |
| pde2 | CHEMBL2652 | IC50 | 42 | 82 | 1 | 0.497 | 0.68 |
| pfkfb3 | CHEMBL2331053 | IC50 | 3 | 3 | 0 | 0.844 | 1.15 |
| ptp1b | CHEMBL335 | Ki | 39 | 46 | 0 | 0.340 | 0.46 |
| shp2 | CHEMBL3864 | IC50 | 83 | 214 | 1 | 0.660 | 0.90 |
| syk | CHEMBL2599 | IC50 | 67 | 159 | 3 | 0.733 | 1.00 |
| thrombin | CHEMBL204 | Ki | 68 | 97 | 0 | 0.464 | 0.63 |
| tnks2 | CHEMBL6154 | IC50 | 24 | 115 | 0 | 0.836 | 1.14 |
| tyk2 | CHEMBL3553 | Ki | 12 | 14 | 0 | 0.625 | 0.85 |
| **all of the above, pooled** | | | 695 | 2727 | 24 | **0.647** | **0.88** |

A difference of two independent measurements has SD sqrt(2) x that: **1.25 kcal/mol**.

Retrieved 2026-10-10 from ChEMBL release ChEMBL_37 (2026-05-01).

## What this bounds and what it does not

* It is **between-publication** variability: different groups, protocols, enzyme constructs, substrate and ATP
  concentrations. It is the right floor for asking whether a *computed* number can be validated against an
  *arbitrary* measurement of the same compound.
* It is **not** the noise inside one publication. Each PLB series comes from one paper (or a few), where the
  *relative* values between its compounds are tighter than this (a shared assay cancels much of it). Do not read
  1.25 kcal/mol as the uncertainty of a PLB ddG; PLB's own reported errors (median 0.08 kcal/mol on cdk2) are a
  different, smaller quantity whose meaning PLB does not state.
* IC50 depends on assay conditions (substrate and enzyme concentration), and the data show the expected direction
  without being uniform: pooled over all 15 targets the spread is 0.698 log units for IC50 (1,234 degrees of freedom)
  and 0.528 for Ki (282). Of the 9 targets with both types, Ki is lower in 7 and higher in cdk2 (6 Ki compounds) and
  eg5 (11), where the Ki samples are small. The table above deliberately takes each target's own type only.
* `documents` are a proxy for independent experiments: two documents may report the same measurement (a review
  re-reporting a paper). Pairs that agree to within 0.005 log units are counted ("identical pairs": 24 of 2,727),
  and `results.json` also gives the pooled SD excluding compounds whose documents all agree; the difference is small.
  Replicates *inside* one document are collapsed to one value and IC50 is never compared with Ki.
* ChEMBL's curation (units, relations, duplicates) is taken as given, apart from the filters in `results.json`
  (`provenance.filters`).

## Data and licence

ChEMBL is licensed CC BY-SA 3.0 (https://chembl.gitbook.io/chembl-interface-documentation/about). Only aggregates are
committed, with the sha256 of the (compound, document, type, value) tuples each target used (`inputs_sha256`), so a
re-run can be compared without redistributing the records. Attribution: ChEMBL, EMBL-EBI,
https://www.ebi.ac.uk/chembl/ (Zdrazil et al., Nucleic Acids Res. 2024).

Re-run: `SMELTERY_NETWORK=1 python scripts/chembl_variability.py benchmarks/variability/results.json`; table:
`python scripts/chembl_variability.py benchmarks/variability/results.json --report`.
