# AIMNet2 and the carboxylate anion (issue #22 (b) and (c))

Plan, written before any number: `docs/benchmarks/aimnet2_anion_plan.md`. Records: `benchmarks/aimnet2/charge_probe.json` (probe, `examples/aimnet2_charge_probe.py`) and
`benchmarks/aimnet2/gpu_run.json` (`examples/aimnet2_gpu_run.py`). Provider: `smeltery.providers.aimnet2.Aimnet2`.

Measured 2026-10-15, Linux, CPU for the probe. `aimnet` 0.2.0, model `aimnet2` (wB97M-D3), ensemble member 0, torch 2.9.1 (probe) / 2.7.1+cu126 (GPU run), ferric 0.1.0rc7, xtb 6.7.1.
Reference: ferric B3LYP/aug-cc-pVDZ single points on GFN2-xtb geometries with the right charge (the ANI-2x probe's reference, `docs/potentials.md`). Single geometries, no error bars.
The model's training level (wB97M-D3) differs from the reference functional and basis; the neutral control row sets the scale of ordinary error.

## Result in one line

AIMNet2 uses the charge (unlike ANI-2x), but **charge -1 is not entered in `supported_charge_states`.** The pair that keeps it out is **methoxide - methanol, +7.3 kcal/mol, over the 5.0 bar**. The two
carboxylate pairs were inside the bar (acetate +3.9, benzoate +2.9), but the plan's rule counts every pair involving the charge state, and two small molecules is thin evidence in any case. This
document does **not** say -1 is supported for carboxylates. The danuglipron anion is therefore still refused by `evaluate`.
The rule was applied as written; the bar and the species were not changed after seeing the numbers, and the project owner confirmed keeping it. A carboxylate-specific rule is a new, separately
pre-declared plan: `docs/benchmarks/aimnet2_carboxylate_plan.md`.

## Is the charge used?

Acetate coordinates: energy -143495.2 kcal/mol at `charge=-1`, -143373.8 kcal/mol at `charge=0`. They differ (ANI-2x via OpenMM-ML returned bit-identical energies).

## Charged minus neutral partner, kcal/mol (E_model difference minus E_DFT difference)

| Pair | DFT | AIMNet2 | error | within 5.0 |
|---|---|---|---|---|
| acetate - acetic acid | 353.0 | 357.0 | +3.9 | yes |
| benzoate - benzoic acid | 346.3 | 349.2 | +2.9 | yes |
| methoxide - methanol | 387.1 | 394.4 | +7.3 | **no** |
| methylammonium - methylamine | -221.3 | -222.5 | -1.2 | yes |
| CONTROL: dimethyl ether - ethanol | 12.0 | 12.1 | +0.03 | yes |

For comparison, ANI-2x minus DFT on the first, third and fourth pairs was +38.8, +16.1 and -148.6 kcal/mol (`docs/potentials.md`).

## Isodesmic (proton transfer between a carboxylate and methanol), kcal/mol

| Reaction | DFT | AIMNet2 | error |
|---|---|---|---|
| acetate + methanol -> acetic acid + methoxide | 34.1 | 37.5 | +3.4 |
| benzoate + methanol -> benzoic acid + methoxide | 40.8 | 45.3 | +4.5 |

Both are within the bar. The methoxide in them is the same species whose bare pair error is 7.3, so the isodesmic comparison cancels most of that error; it does not remove the fact that the rule's
pair criterion fails.

## Relaxed geometry

Largest bond-length change on relaxing with AIMNet2 (our `relax`, fmax 0.05 kcal/mol/Angstrom) from the xtb geometry, Angstrom: acetate 0.026, benzoate 0.011, methoxide 0.035, methylammonium 0.024,
acetic acid 0.011, benzoic acid 0.010. No B3LYP relaxation was run for these species in this study, so this is a change from xtb, not an error against DFT.

## Forces against a finite difference of the provider's own energy (CPU, float64, acetate, displaced geometry)

| step (Angstrom) | relative error | bar `FORCE_FD_RELATIVE_BAR` |
|---|---|---|
| 1e-4 | 1.1e-3 | 1e-4: **not met** |
| 1e-3 | 1.6e-4 | 1e-4: **not met** |
| 1e-2 | 1.2e-3 | 1e-4: **not met** |

The bar is not met at any step. Converting the model and its external modules to float64 brings the smallest error to 1.6e-4; a one-off check on a different geometry (an RDKit acetate, step 1e-3, not recorded in the
committed JSON) gave 1.3e-4 with all terms, 1.3e-4 without D3 and 9.9e-5 without the Coulomb term, so the remaining error was not localised. `Aimnet2` declares `energy_noise = 1e-4` kcal/mol as its energy resolution for `relax`; that value is not independently measured
beyond this check.

## Charge states entered

`supported_charge_states = {0, +1}` by the rule. +1 rests on **one** pair (methylammonium) and 0 on the control; neither is evidence about drug-sized molecules. -1 is excluded by the methoxide pair.
The project owner decided to keep the rule as written; -1 stays out, and any carboxylate-only rule is the separate plan above, not a retrofit of this one.

## GPU run (c)

* torch 2.8.0 and 2.9.1 (cu126/cu128 builds) carry no sm_61 kernels: a matmul fails with "no kernel image is available" on the GTX 1080 (compute capability 6.1).
* torch 2.7.1+cu126 ships sm_50 to sm_90 and runs. `aimnet` declares `torch>=2.8`; 2.7.1 is outside that declaration and worked for this check.
* warp-lang 1.18.0 (CUDA 13 build) refuses the device ("CUDA 13.4 requires sm_75 or higher"); 1.11.1 is missing a symbol `nvalchemiops` needs; **1.14.0 and 1.16.0 import and run** (1.16.0 used).
* The package's CUDA convolution kernel accepts float32 only (TypeError at float64), so the GPU run is float32; the provider keeps float32 on `cuda` devices.

Danuglipron anion (carboxylate, 70 atoms, MMFF geometry, charge -1), CPU float64 vs GPU float32, one run, one machine:

| | energy, kcal/mol | wall, s (one call, after warm-up) |
|---|---|---|
| CPU | -1176319.5538 | 0.049 |
| cuda:0 | -1176319.5538 | 0.029 |

Energy difference -5.1e-5 kcal/mol, largest force-component difference 9.6e-5 kcal/mol/Angstrom (largest force component 47.4). Timing is a single call on a 70-atom molecule; no speed claim is made.

## What this does not show

No claim about the danuglipron anion's energies in the pocket (the model has no electrostatic-embedding input; `supports_external_charges = False`), nothing about conformational energetics of a
70-atom anion against DFT (no such reference was computed), and nothing about cations beyond methylammonium.
