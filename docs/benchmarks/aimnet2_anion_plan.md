# Plan: is AIMNet2 a charge-aware potential for the danuglipron anion? (issue #22 (b), and (c) a GPU run)

Written and committed before any AIMNet2 number is computed. ANI-2x results and method: `docs/potentials.md` (its probe script is `examples/ani2x_charge_probe.py`).

## Question

#22 stays open for (b) a charge-aware model for the anion and (c) no GPU run. The ANI-2x backends cannot take a charge. AIMNet2 takes the net charge as an input, so whether it handles the carboxylate is a measurement,
not an assumption.

## What is run

* **Model:** `aimnet2` (the `aimnet2-wb97m-d3` family, ensemble member 0), `aimnet==0.2.0`, weights fetched by the package. Licence: package MIT; the weights' model card on Hugging Face declares `license: mit`
  (the only statement found; not an independent legal review). No MACE-family model is used.
* **Reference and geometries:** identical to the ANI-2x probe: GFN2-xtb geometries with the right charge, ferric B3LYP/aug-cc-pVDZ single points (ferric 0.1.0rc7, PyPI wheel).
* **Species:** the ANI probe's set (acetic acid / acetate, methanol / methoxide, methylamine / methylammonium, control dimethyl ether and ethanol) plus benzoic acid / benzoate, the aromatic carboxylate
  closest to the danuglipron ligand's acid. The danuglipron anion itself has no DFT reference at this size; it is only run through the potential (below).

## Quantities, fixed now

1. **Charge is used:** the energy of acetate coordinates at `charge=-1` differs from the energy of the same coordinates at `charge=0`. (ANI-2x via OpenMM-ML was bit-identical.)
2. **Pair error:** for each charged/neutral pair, `[E_model(charged) - E_model(neutral)] - [E_DFT(charged) - E_DFT(neutral)]` in kcal/mol, as in `docs/potentials.md`.
3. **Isodesmic error:** acetate + methanol -> acetic acid + methoxide, and benzoate + methanol -> benzoic acid + methoxide, model reaction energy minus DFT reaction energy. Composition is balanced
   so the functional and basis offset between the model's level (wB97M-D3) and the reference (B3LYP/aug-cc-pVDZ) largely cancels; the pair error of item 2 carries one H atom's offset and is read with that caveat.
4. **Control:** the neutral isomer pair (dimethyl ether - ethanol), same coordinates, to size ordinary error.
5. **Geometry:** relax each species with the model and with B3LYP as the ANI probe did; the largest bond-length error against the DFT minimum (Angstrom).
6. **Bar:** a charge state is entered in `supported_charge_states` only if every pair error and isodesmic error involving it is within **5.0 kcal/mol**, the magnitude of the neutral control error in the ANI probe.
   The bar is a convention taken from that control, not fitted; single geometries, no error bars. If it is not met, the state is not supported and the numbers are written into the docs.
7. **Forces:** the provider's forces against a central finite difference of its own energy, steps 1e-4, 1e-3, 1e-2 Angstrom, with the same relative bar `FORCE_FD_RELATIVE_BAR` the ANI-2x provider is held to.
8. **Pocket guard and exactness anchor:** the same conformance tests as the other providers (`supports_external_charges = False` raises in a pocket context; the harmonic anchor is provider-independent).

## GPU run (c)

The machine has 2 x GTX 1080 (compute capability 6.1). A GPU run is attempted with the torch build that supports it; if the installed torch has no kernels for sm_61 the failure is recorded as measured.
If it runs: the danuglipron anion (as `PARENT_SMILES` deprotonated at the carboxylic acid, xtb-free MMFF geometry, charge -1) energy and forces on CPU and GPU, agreement of the two to float tolerance, and wall time
of each. Timing is one run, one machine, no statistics.

## Not done here

No docking-level or campaign claim is made from this. A pocket-context run needs the point-charge field, which AIMNet2 (no electrostatic embedding input) still cannot see; the provider keeps
`supports_external_charges = False`. Cations other than the measured ammonium are not examined.

## Outcome language

Results are stated as differences against B3LYP/aug-cc-pVDZ on single geometries, with the functional mismatch named. "Supported" in `supported_charge_states` means "met the bar above on these species", not
"validated for the danuglipron ligand".
