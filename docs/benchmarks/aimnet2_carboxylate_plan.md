# Plan: is AIMNet2 valid for carboxylates (charge -1)? A follow-up to the alkoxide miss

**This plan was written after seeing the methoxide miss** (`docs/benchmarks/aimnet2_anion.md`: methoxide - methanol +7.3 kcal/mol against a 5.0 bar). It is a new, separately pre-declared plan with a
narrower question. It is **not a rescue of the earlier rule**: that rule stands, `supported_charge_states` stays {0, +1} under it, and nothing here changes it. No number for this plan has been computed.
Written and committed before any AIMNet2 or DFT energy of the species below is computed.

## Question

Does AIMNet2 (`aimnet2`, wB97M-D3, ensemble member 0, `aimnet==0.2.0`) reproduce a DFT reference for carboxylate deprotonation to within 5.0 kcal/mol across more than two small molecules and for a
drug-sized carboxylate, including the danuglipron anion (or a truncated version of it)? If yes, `-1` may be entered for carboxylates only, as a **carboxylate-specific** state, not as a general -1.

## Species and references

* Small carboxylic acid / carboxylate pairs, already measured: acetic acid / acetate, benzoic acid / benzoate (not re-run; their recorded errors are +3.9 and +2.9).
* **New small pairs:** formic acid / formate, propanoic acid / propanoate.
* **A drug-sized carboxylate:** one commonly used drug-like carboxylic acid of 20 to 35 heavy atoms, chosen and named in the commit that adds it, before its energy is computed, and not swapped after.
* **The danuglipron anion:** the carboxylic acid of `PARENT_SMILES` deprotonated, as in `examples/aimnet2_gpu_run.py`. The full 70-atom pair needs a B3LYP/aug-cc-pVDZ single point of a 70-atom anion
  and its neutral; if that is beyond the CPU budget below, a **truncated** version (the benzimidazole-carboxylic-acid fragment with the acid, H-capped at a C-C cut) is run instead and the truncation is stated.
* Reference and geometries as before: GFN2-xtb geometries with the right charge, ferric B3LYP/aug-cc-pVDZ single points (ferric 0.1.0rc7). Same functional mismatch caveat as the earlier document;
  the neutral control (dimethyl ether - ethanol, +0.03) is not repeated.

## Quantities and the bar, fixed now

1. **Pair error** for each acid/carboxylate pair: `[E_model(anion) - E_model(acid)] - [E_DFT(anion) - E_DFT(acid)]`, kcal/mol.
2. **Isodesmic error** against acetic acid: `carboxylate + acetic acid -> acid + acetate`, model reaction energy minus DFT reaction energy, for each new carboxylate (the offset between the model's
   functional and the reference cancels better here than in the bare pair).
3. **Bar: 5.0 kcal/mol**, as before, applied to **every** pair and isodesmic error of the carboxylates in this plan, including the two already measured. A single miss means carboxylates are not entered.
4. **Forces criterion: not met so far.** The earlier measurement found the provider's forces against a finite difference of its own energy at 1.6e-4 relative at best, against the bar 1e-4
   (`FORCE_FD_RELATIVE_BAR`), in float64 on CPU. This plan re-measures it on the drug-sized carboxylate and the danuglipron anion. It does not change the bar. If it is still not met, a carboxylate state
   may be entered for **energies only**, with the force finding recorded beside it, and `relax` on this provider remains accordingly limited; that restriction is written into the docs.
5. **Evidence strength:** with these species the evidence is at most 5 carboxylates (2 small already measured, 2 new small, 1 drug-sized) plus the danuglipron case: still small, still single geometries,
   no error bars. "Entered" would mean "met the bar on these species", not "validated for the danuglipron ligand in a pocket" (the model has no electrostatic-embedding input;
   `supports_external_charges = False` remains).

## If the bar is met

Add a distinct carboxylate-only mechanism (for example a provider argument that admits -1 only for a molecule containing a carboxylate group and refuses it otherwise), with a test that a non-carboxylate
anion (methoxide) is still refused. The general `supported_charge_states` stays {0, +1}.

## Budget

At most 6 CPU-h in total. The two 70-atom B3LYP/aug-cc-pVDZ single points are the dominant cost; each DFT job is limited to 1,500 CPU-s by `RLIMIT_CPU` and a limit hit is recorded as "not
completed", not retried. If the full danuglipron pair does not finish inside its limit, the truncated version is run (see above).

## Not done here

No pocket-context claim, no cation, no statement about other anions, and no change to the earlier rule or to the recorded +7.3.
