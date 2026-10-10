# ML potentials: the ANI-2x backend, and what was measured (issue #22)

Measured 2026-10-10 on Linux, CPU only, Python 3.11. Backend: `smeltery.providers.openmm_ml.OpenMMMLAni2x`,
install with `pip install 'smeltery[ml-potential]'` (pulls torch; not part of CI).

| Package | Version measured |
|---|---|
| openmm | 8.6.1 |
| openmmml (OpenMM-ML) | 1.8 |
| torchani | 2.9.0 (torch 2.13.0; torchani 2.9.0 declares `torch<=2.13`) |
| ferric (reference DFT) | 0.1.0rc7 (PyPI wheel) |
| xtb (reference geometries) | 6.7.1, GFN2 |

## Model choice and licence

ANI-2x (H, C, N, O, F, S, Cl; target wB97X/6-31G(d) per the torchani docstring) via OpenMM-ML's `MLPotential("ani2x")`.

* torchani 2.9.0 declares `License-Expression: MIT` in its package metadata.
* The weights (`ani2x_state_dict.pt`, sha256 `ad5c45c9...a98bf9`, equal to the git-LFS oid on the model page) are
  fetched by torchani from <https://huggingface.co/roitberg-group/ani2x>, whose model card metadata says
  `license: mit`. That is the only licence statement found for the weights; the original results repository
  (github.com/cdever01/ani-2x_results) declares none (GitHub API: `license: null`). `license_id = "MIT"` is
  therefore the Hugging Face declaration, not an independent legal review. **UNVERIFIED beyond that metadata.**
* MACE-OFF (restrictive licence, per the project brief) was not evaluated and is not used. AIMNet2 and MACE also
  exist in OpenMM-ML 1.8; neither was tried.

Weights are loaded by torchani with `torch.load(..., weights_only=True)` (its code path for state dicts).

## The flags, and where each comes from

* `supports_external_charges = False`. ANI-2x takes species and coordinates only; OpenMM-ML adds no electrostatic
  embedding. So `evaluate(provider, pose, point_charges)` raises `PocketContextError`
  (`tests/test_ml_potential.py::test_in_pocket_use_raises_end_to_end_with_the_real_backend`, real backend).
* `supported_charge_states = {0}`. From the measurements below, not from the paper: **the ANI-2x paper's full text
  was not accessible here (the abstract says nothing about charge), so a statement from the paper is UNVERIFIED.**
  What is verified is torchani's own code: `torchani/arch.py` has
  `assert charge == 0, "Model only supports neutral molecules"` in the model's input check.

## Anion (and cation) support: MEASURED

### (b) What the stack does with charged input: silently wrong, not an error

OpenMM-ML 1.8 does not pass a charge to torchani (its ANI path calls `model((species, positions))`). Its
`MLPotential.createSystem(topology, charge=-1)` accepts and ignores the keyword. Measured: acetate coordinates give
the bit-identical energy with `charge=-1` and without it
(`test_openmmml_itself_ignores_the_charge_argument...`; if it ever fails, upstream changed: re-measure).
So an unguarded call treats an anion or cation as a neutral molecule of the same atoms, without a word.
The provider therefore refuses every charge but 0 (`UnsupportedChargeStateError`).

### (c) Against a reference we can compute

Reference: ferric B3LYP/aug-cc-pVDZ (diffuse functions for the anions), closed-shell; geometries from GFN2-xtb
(6.7.1) with the correct charge. ANI-2x is run on the same coordinates with its charge dropped (as the unguarded
stack does). Chosen because ferric's DFT is closed-shell only and its functional list has no plain wB97X; this
is a different functional and basis from ANI-2x's training level, so the control row below is what sets the scale.

**1. Energy differences, charged minus neutral partner (kcal/mol), at the xtb geometries**
(`examples/ani2x_charge_probe.py`; with E(H+) = 0 the DFT column is a proton-removal energy, B3LYP/aug-cc-pVDZ
electronic energy, no ZPE or thermal terms; ANI's column has no physical meaning and is shown only to size the
disagreement):

| Pair | DFT | ANI-2x | ANI minus DFT |
|---|---|---|---|
| acetate - acetic acid | 353.0 | 391.9 | +38.8 |
| methoxide - methanol | 387.1 | 403.3 | +16.1 |
| methylammonium - methylamine | -221.3 | -369.8 | -148.6 |
| CONTROL, neutral isomers: dimethyl ether - ethanol | 12.0 | 7.1 | -5.0 |

A deprotonation energy or an ionization-state penalty from this model would be wrong by 16 to 150 kcal/mol,
against about 5 kcal/mol on a neutral isomer control. (The project already lost ~143 kcal/mol once to a wrong
ionization state.) Single geometry per species, single run: no error bars.

**2. Relaxed geometries.** Each species was relaxed with ANI-2x (our `relax`, from the xtb geometry, fmax 0.05
kcal/mol/Å) and with B3LYP/aug-cc-pVDZ (ferric gradients through the same `relax`, fmax 1.0 kcal/mol/Å, from the
same start). Largest heavy-atom-or-X-H bond-length error against the DFT minimum, Å (xtb shown for scale):

| Species | charge | max bond error ANI-2x | max bond error xtb |
|---|---|---|---|
| acetic acid | 0 | 0.010 | 0.020 |
| methanol | 0 | 0.016 | 0.021 |
| methylamine | 0 | 0.007 | 0.012 |
| acetate | -1 | 0.023 | 0.027 |
| methylammonium | +1 | 0.026 | 0.029 |
| methoxide | -1 | 0.061 | 0.047 |

Acetate: ANI C-C 1.582, C-O 1.243/1.244 (symmetric, as a carboxylate should be) vs DFT C-C 1.559, C-O
1.263/1.261. So the anion geometry is NOT catastrophically wrong, but errors are 2 to 4x the neutrals', and for
methoxide ANI is worse than the semi-empirical reference (C-O 1.280 vs DFT 1.341). Also measured: the DFT force
at the ANI minimum (kcal/mol/Å, max component): acetic acid 10.2, acetate 32.8, methoxide 83.9, methylammonium
19.9. Both references (B3LYP and GFN2) are not the model's own level of theory, so these bound but do not prove.

**3. Same-species relative energy (symmetric C-O stretch, ANI vs DFT, kcal/mol, relative to the xtb geometry):**

| Displacement (Å) | acid ANI | acid DFT | acetate ANI | acetate DFT |
|---|---|---|---|---|
| -0.05 | 6.8 | 7.4 | 5.9 | 8.6 |
| +0.10 | 12.3 | 9.5 | 13.7 | 7.6 |
| +0.20 | 44.7 | 37.7 | 48.7 | 34.1 |

The acetate stretch is 43% too stiff at +0.2 Å (acid: 19%). Reference zero is the xtb geometry, which is not the
minimum of either method, so read it as a rough indication.

### Verdict

**Anion support is NOT established; the model is not charge-aware, so carboxylate input is outside what the model
can represent.** What is measured: the stack accepts the input silently, ignores the charge, gets
energy differences wrong by tens to hundreds of kcal/mol, and gets anion/cation geometries worse than neutrals
(but not absurd). What is NOT measured: any statement about ANI-2x's training set (not read), larger anions, or
anything in solvent or in a pocket. The provider's `supported_charge_states = {0}` is the consequence; an
ionization-aware potential (a model with a charge input, or a different tier) is needed for the danuglipron
anion. Not tried: AIMNet2 (OpenMM-ML lists it; its published scope includes charged species, UNVERIFIED here).

## Force validation (acceptance bar `FORCE_FD_RELATIVE_BAR = 1e-4`): NOT MET

`check_forces` on acetic acid (fixed geometry in the test file, `||F_fd - F|| / ||F||`):

| FD step (Å) | acetic acid | acetate coordinates (charge ignored) |
|---|---|---|
| 1e-4 | 1.0 | 2.87 |
| 1e-3 | 0.258 | 0.297 |
| 1e-2 | 0.041 | 0.026 |
| 3e-2 | 0.058 | 0.028 |

**The bar is not met by this backend, and was not loosened.** Cause (measured): OpenMM-ML's ANI path computes in
float32 (`dtype=torch.float32` is hard-coded in `openmmml/models/anipotential.py`), and the total energy is
about -1.4e5 kcal/mol, whose float32 resolution is about 0.016 kcal/mol; a central difference at step h carries
an error of order 0.016/(2h). The test
`test_finite_difference_bar_is_not_met_by_the_float32_backend` is `xfail(strict=True)`: it flips to a failure the
day the backend becomes accurate enough, as a prompt to update this file.

What does hold: against an independent float64 central difference of torchani's own energy (step 1e-3 Å), the
provider's forces agree to a relative 1.02e-4 (float32 autograd precision), asserted below 1e-3
(`test_forces_match_torchanis_own_float64_finite_difference`). That is a different, looser comparison from the bar.

### An upstream bug found on the way (openmmml 1.8)

OpenMM-ML 1.8's ANI path returns forces **10x too small**: it differentiates with respect to positions in
Angstrom and gives kJ/mol/Å to `PythonForce`, which expects kJ/mol/nm (its AIMNet2 and MACE paths multiply by 10;
ANI does not). Measured: ratio of OpenMM-ML force to torchani autograd force 0.1000000. The provider applies the
measured factor only for versions in `FORCE_SCALE_BY_OPENMMML_VERSION` (1.8) and raises for any other version
until a scale is measured and passed as `force_scale=`. Not reported upstream from here. Anyone running MD directly
on `MLPotential("ani2x")` from openmmml 1.8 is affected; whether a later release fixed it was not checked.

## Exactness anchor

The harmonic-test-potential anchor is unchanged and passing (`tests/test_potential_provider.py`). `relax` gained two
things so a real molecule can use it, neither changing the anchor: `max_step_ang` (default 0.2 Å per coordinate,
because the identity first Hessian turned tens of kcal/mol/Å into steps of tens of Å) and a provider-declared
`energy_noise` (default 0) so the line search does not stall on float32 round-off. Without `energy_noise` the ANI
relaxation stalled at a force of 1.8 kcal/mol/Å; with it, acetic acid converged in about 24 steps.

## Not done

* No CI job installs the extra (torch is large); the real-backend tests skip with a reason in CI.
* No GPU run: CPU was enough, so GPU support for the GTX 1080s (sm_61) was not tested.
* The per-species geometry-relaxation and stretch numbers above came from throwaway scripts that were not
  committed; the energy-pair table is reproducible with `examples/ani2x_charge_probe.py`.
