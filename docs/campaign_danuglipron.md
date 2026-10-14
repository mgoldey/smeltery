# The danuglipron campaign through smeltery: what was asked, what was measured, what is refused

Written 2026-10-13. Verdicts are short, dated and provisional; the tables are the deliverable. Every number below carries its system, method and n, and is
asserted by `tests/test_campaign_danuglipron.py` against the committed run records (`experiments/danuglipron/out/`, `benchmarks/`) or, where only prose
exists, against the frozen log `experiments/danuglipron/RESULTS.md`, which the table marks as such. Nothing here is retyped from memory.

## The answer

1. **No candidate is recommended, and none is licensed.** The pocket-fit metric was refuted by its own negative control: at n = 100 poses per candidate the
   control `NC2-decyano` (a pharmacophore contact deleted) scored 18.51 kcal/mol BETTER than the parent against a 2-sigma bar of 13.46, so NC2 resolved on the
   wrong side. More sampling made the refutation stronger, not weaker. The one candidate that looked best (`H1c-azetidine-for-piperidine`, 33.74 kcal/mol better) is
   a hypothesis, confounded with molecular size and pose error, and the campaign does not recommend it.
2. **The liability is dose-dependent GI intolerability, which this repo treats as on-target** (a GLP-1R-class effect), so substituting atoms is not obviously the lever.
   That is a premise taken from the programme's public record and from issue #35, **not a finding of this work**: no experiment here tests it
   (`docs/limits.md`, row U2: a substitution objective where the liability is the target's own function is not supported).
3. **What the funnel adds is what it refuses to say.** `docs/limits.md` lists the measured limits (each with a magnitude, a system and a source) and the unmeasured
   ones; the one end-to-end re-run of the campaign's halogen scan did not complete (below), so no per-analogue ddE from the migrated tiers exists.

## What was asked

`experiments/danuglipron/PLAN.md`: reduce the toxicity liability of danuglipron (PF-06882961, Pfizer's oral GLP-1R agonist, discontinued April 2025) while keeping
active-site fit, by ranking designed analogues with cheap tiers first (RDKit alerts, GFN2-xTB, a pocket point-charge interaction, docking).

## Structures (corrected)

| use | PDB entry | what it is (RCSB, checked 2026-10-13) |
|---|---|---|
| structure of danuglipron bound to GLP-1R | **6X1A** | "Non peptide agonist PF-06882961, bound to GLP-1R", 2.5 A (released 2020-09-09; primary citation Mol Cell, "Differential GLP-1R Binding and Activation by Peptide and Non-peptide Agonists") |
| the pocket every smeltery and campaign number uses | **7LCJ** | "PF 06882961 bound to the GLP-1R:Gs complex", 2.82 A cryo-EM, ligand UK4 (released 2021-01-20) |

6X1A is the entry to cite for the danuglipron-bound structure and the discovery series; 7LCJ is only the pocket the point-charge field and the docking box come from
(6458 AMBER charges, `tests/data/pocket_fixture/7LCJ_pocket.pdb`). This page does not assert which of the two a given paper cites.

## What was measured

"Source" is a committed record (asserted from the JSON) or the frozen prose log (asserted as quoted text). All campaign numbers are GFN2-xTB unless stated.

| id | measurement | value | system, method, n | source |
|---|---|---|---|---|
| M1 | structural alerts | 0 hits in each of six catalogs; MW 555.6; cLogP 4.89 | danuglipron, RDKit FilterCatalog (Brenk, PAINS, NIH, Glaxo, Dundee, BMS) plus Lipinski/Veber, offline; n = 1 molecule | RESULTS.md (prose) |
| M2 | strain of the bound conformer | **2.28 kcal/mol** above the free minimum, rank 3 of 20, spread 10.87 | danuglipron, GFN2-xTB vacuum relaxations, n = 20 conformers | `out/arm_a_free.json` |
| M5 | negative control, parent vs NC2-decyano | fit -126.07 (parent) vs -144.57 (NC2); gap -18.51, 2-sigma bar 13.46; **gate failed** | 7LCJ pocket, 6458 AMBER charges, GFN2-xTB in-field minus vacuum, aligned poses (parent 91, NC2 100 scored of 100 requested) | `out/fit_and_rank.json` |
| M5 | known-inactive control NC1-methyl-ester | -22.33, worse than parent by 103.74 (the control that behaved) | same, n = 100 scored | `out/fit_and_rank.json` |
| M5 | what the metric responds to | charge-dominated: anion-vs-neutral -109.5 against a 41.4 kcal/mol spread among anions; size-correlated r = +0.490 | same | RESULTS.md (prose) |
| M7 | best generated conformer vs the bound pose | 2.23 A (docking bar 2.0 A); the other 19 are 2.70 to 3.70 A | 20 committed conformers, scaffold RMSD, 41 heavy atoms | RESULTS.md (prose) |
| M9 | redocking (pose search) | **0.95 A** best-of-20, top-ranked also 0.95 A | AutoDock Vina 1.2.7, 7LCJ box, exhaustiveness 32, 20 poses, one seed (the seed, not the effort, moves it: 0.75 to 1.24 A over 3 seeds) | `out/redock_validation.json`; band RESULTS.md (M11) |
| M13 | "select the docked pose" vs averaging | selected pose is biased +27.43 kcal/mol against the 15-pose mean; no detectable rank association (Spearman -0.261, p 0.35, n = 15; cannot see below |rho| 0.514) | danuglipron in 7LCJ, 15 docked poses | RESULTS.md (prose) |
| M17 | pairing a substitution to the parent's pose | SELF anchor +0.004 kcal/mol (relaxed); Cl-for-H rho 0.399, SEM ratio 1.21x | paracetamol-like parent, 34 atoms, 24 ETKDG poses, MMFF, no pocket (provisional) | RESULTS.md (prose) |

## Five routes closed (and the sixth that was not)

| route | status (RESULTS.md, 2026-09-19) |
|---|---|
| more poses (M4/M5) | closed: per-pose sd flat in n |
| relax in the field (M6) | closed: a real 15% effect, about 3 orders short |
| a real pose search (M12) | closed: 1%, 32.5x short |
| select one pose (M13) | closed: 7 to 10x worse than averaging |
| a different scorer (M14) | closed: none available is less pose-sensitive |
| **pair the substitution to the parent's pose (M17)** | **open, provisional**: the five closures all used independently embedded ensembles |

The migrated tiers (`PairedPoses`, `FieldInteraction`) are the pocket test of the sixth route. Their measured results are below, and none of them clears the bar for a ranking.

## Four self-inflicted errors, and what caught each

1. Selection bias (parent scored at 1 pose, analogues at the best of 6): caught by the negative controls.
2. Wrong ionization state (everything neutral, the potency anchor is an anion): caught by following up the control failure with a direct probe.
3. Wrong noise statistic (a pose range, which grows with n, instead of the SEM): caught by a convergence probe.
4. Never validated the input geometries (M7): caught only when the generated conformers were aligned to the bound pose, a one-line check never run.

## What the migrated funnel measured, and what it refuses to claim

All smeltery numbers: RHF/STO-3G in an AMBER ff14SB point-charge field, ferric 0.1.0rc7, the series at the n shown (`docs/limits.md` has every row with its source).

| quantity | value | system, n | source |
|---|---|---|---|
| unpaired ddE pose-ensemble noise floor | 4.07 kcal/mol | danuglipron in 7LCJ, n = 100 poses | `smeltery.pocket.DDE_NOISE_FLOOR_KCAL_MOL`; limits row L2 |
| ddE against experimental ddG (Spearman) | tyk2 +0.252 (permutation p 0.43); cdk2 +0.690 (p 0.0694) | PLB tyk2 n = 12 analogues, 8 poses; cdk2 n = 8, 5 poses | `benchmarks/plb/results/{tyk2,cdk2}_sto3g_analysis.json`; limits L8 |
| halogen scan, end to end | **not completed**: 7 SCFs failed on jittered poses, all F or Cl analogues; every CH3 SCF and the parent converged; no paired ddE exists | danuglipron halogen analogues, 7LCJ field of 6458 charges, 2 jittered poses planned | `docs/benchmarks/danuglipron_halogen.md`; issue #6 |

**The funnel refuses:** a ranking finer than the declared floor ("unranked", not guessed); any claim that ddE tracks experiment (not shown on either series; the series disagree with each other); any
statement about screening, solvation or ionization beyond the measured rows L10, L14 and L16; any candidate recommendation from a metric whose control failed. The
`limits.md` forbidden-claims column is the authoritative list.

## What would change this

The NC2 control resolving on the right side under a pose-averaged-by-construction estimator, or a pocket-based paired measurement (the sixth route) that tracks experiment on a series large
enough to see it (critical |rho| about 0.59 at n = 12). Neither exists today.
