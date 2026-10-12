# What smeltery cannot do

Measured limits of the paired-ΔΔE funnel, each with its size, the system it was measured on, and where the measurement lives.
`tests/test_limits.py` fails if a measured row has no number or no existing source. Verdicts are provisional: each rests on one
system or one model pair, and says so.

## Measured limits

| ID | Limit | Magnitude | Measured on | Source | Claim it forbids |
|---|---|---|---|---|---|
| L1 | Independent experiments disagree on the same compound | 0.88 kcal/mol pooled SD per measurement; 1.25 kcal/mol for a difference of two | 695 compounds in 2+ ChEMBL publications across the 15 PLB targets (ChEMBL_37) | `benchmarks/variability/results.json` (issue #26) | That a computed ΔΔG is wrong, or right, by less than about 1.25 kcal/mol against a between-publication experimental difference |
| L2 | Pose-ensemble noise of the unpaired ddE | 4.07 kcal/mol | danuglipron in 7LCJ, n = 100 poses | `src/smeltery/pocket/binding_energy.py` (`DDE_NOISE_FLOOR_KCAL_MOL`), `experiments/danuglipron/RESULTS.md` | Ranking analogues whose unpaired ddE differs by less than this |
| L3 | Pose noise of the paired ddE | 2.07 kcal/mol mean paired SEM (3.34 unpaired, so pairing helps 1.6x) | PLB cdk2, 8 analogues, 5 jittered poses, RHF/STO-3G, AMBER ff14SB field | `benchmarks/plb/results/cdk2_sto3g_analysis.json`, `docs/benchmarks/plb_cdk2.md` (issue #27) | Pairwise ranking of cdk2 analogues: experiment resolves no pair at the L2 floor |
| L4 | Detectable rank correlation at this series size | smallest significant Spearman rho 0.738; about 40% power at true r = 0.7 | PLB cdk2, n = 8 | `docs/benchmarks/plb_cdk2.md` (issue #27) | Reading ρ = +0.69 (p = 0.069) as either tracking or not tracking |
| L5 | A trivial descriptor does as well as ddE | Crippen logP ρ = +0.79 against ddE ρ = +0.69 | PLB cdk2, n = 8 | `docs/benchmarks/plb_cdk2.md` (issue #27) | That ddE adds rank information beyond lipophilicity |
| L6 | Pocket charge model changes ddE | 1.26 kcal/mol sensitivity floor (lower bound); 0 of 8 sign flips, 1 of 28 pair-order flips | PLB cdk2, AMBER ff14SB vs CHARMM36, identical geometries | `benchmarks/plb/results/cdk2_sto3g_analysis.json` (issue #29) | A published default systematic floor for `FieldInteraction`, or any statement about other targets or charge models |
| L7 | Experimental ddG range is narrower than the noise | 2.8 kcal/mol whole span, 0.85 kcal/mol sd | PLB cdk2 | `benchmarks/plb/manifest.json`, `docs/benchmarks/plb_cdk2.md` | Validating ranking on cdk2 at all with pose noise at the L2 level |
| L8 | Detectable rank correlation at the tyk2 series size | smallest significant Spearman rho 0.587; about 68% power at true r = 0.7, 33% at r = 0.5 | PLB tyk2, n = 12 analogues | `benchmarks/plb/results/tyk2_sto3g_analysis.json`, `docs/benchmarks/plb_tyk2.md` (issue #27) | Reading ρ = +0.25 (p = 0.43) as proof that ddE does not track: a true r = 0.5 is missed two times in three |
| L9 | Pose noise of the paired ddE at 8 poses | 0.38 kcal/mol mean paired SEM (1.46 unpaired, so pairing helps 3.8x); ranking reliability 0.97 | PLB tyk2, 12 analogues, 8 jittered poses, RHF/STO-3G, AMBER ff14SB field | `benchmarks/plb/results/tyk2_sto3g_analysis.json`, `docs/benchmarks/plb_tyk2.md` (issue #27) | That the missing rank correlation on tyk2 is pose-sampling noise |
| L10 | Uniform dielectric scaling of the bare field is a rescale | fitted slope falls to 0.94, 0.91, 0.90 of the first-order 1/eps at eps = 2, 4, 8; Spearman with experiment moves by at most 0.11 | PLB tyk2, 12 analogues, 4 poses, charges divided by eps | `benchmarks/plb/results/tyk2_sto3g_analysis.json`, `docs/benchmarks/plb_tyk2.md` (issue #28) | That a uniform eps brings ddE closer to experiment, or tests whether screening is the missing term |
| L11 | Pocket charge model changes ddE on a second system | 0.67 kcal/mol sensitivity floor (lower bound; cdk2 1.26, so the two floors disagree by about 2x); 0 of 12 sign flips, 0 of 66 pair-order flips, Spearman between models +1.000 | PLB tyk2, AMBER ff14SB vs CHARMM36, identical geometries and stored AMBER energies, 12 analogues, 8 poses | `benchmarks/plb/results/tyk2_sto3g_charmm36_analysis.json`, `docs/benchmarks/plb_tyk2_charmm.md` (issue #29) | A published default systematic floor for `FieldInteraction` (two systems, one model pair, floors that differ by about 2x), or any statement about ligand-side charge models |
| L12 | Ligand charge model changes a classical-charge surrogate of ddE (the PLB `FieldInteraction` ddE uses no ligand charge model, so its shift is 0 by construction) | surrogate ddE_es shift floors 1.58 kcal/mol (Lowdin vs Hirshfeld, Spearman +1.000), 6.73 (Lowdin vs Gasteiger) and 6.87 (Hirshfeld vs Gasteiger), Spearman +0.832 for both Gasteiger pairs; 0 of 12 sign flips and 12 of 66 pair-order flips for each Gasteiger pair | PLB tyk2, 12 analogues, 8 poses, vacuum RHF/STO-3G charges, AMBER ff14SB field | `benchmarks/plb/results/tyk2_sto3g_ligand_charges_analysis.json`, `docs/benchmarks/plb_ligand_charges.md` (issue #29) | A default systematic floor for any charge-dependent tier, any ligand-charge sensitivity of `FieldInteraction`, or any statement about the force-field tier's AM1-BCC charges |

## Not yet measured

| ID | Limit | Status | Tracked in | Claim it forbids until measured |
|---|---|---|---|---|
| U1 | Missing ligand-pocket dielectric screening | UNMEASURED for distance-dependent screening, PCM and COSMO (uniform eps is L10) | issue #28 | That the bare point-charge field's ddE is quantitatively comparable to experiment |
| U2 | On-target liabilities | UNMEASURED, target-specific | issue #35 | A substitution objective where the liability is the target's own function |
