# PairedPoses: accept a known scaffold core, cross-validated

## Status

Design, awaiting approval. Supersedes nothing. Implements the finding recorded in
`docs/sbdd-bond-head-retrospective.md` follow-up work: generated analogues that are perfectly good
substitutions cannot be paired, because the all-atom MCS search cannot finish.

## The problem

`PairedPoses` finds the parent/analogue atom correspondence with `rdFMCS.FindMCS` on the
hydrogen-added molecules, and refuses (`NoCommonCoreError`) when the search is cancelled. That
refusal is correct in general: an unfinished search can return a partial core, and a partial core
silently changes the quantity a paired ΔΔE measures.

For scaffold-growth output it refuses nearly everything. Measured on 480 generated molecules
(`external/sbdd-study/bond_Jlast_ob`, 15 cells, repaired filters):

| Observation | Value |
|---|---|
| Accepted by `process_samples` (repaired) | 376 / 480 |
| All-atom MCS completes within 10 s | ~0 (every sampled molecule cancelled) |
| Heavy-atom MCS completes within 10 s | 106 / 130 |
| A usable heavy-atom core is found | **130 / 130** |
| Heavy-atom MCS time | median 0.00 s, mean 2.15 s |

The key measurement: **cancellation does not mean no core was found.** Every cancelled search had
already located a 38-atom core — exactly the scaffold size — and that core maps cleanly onto both
molecules. The search simply could not *prove* maximality in time. One molecule in the same cell
completed in 0.01 s and confirmed 38 is the answer.

Meanwhile the correspondence is already known by construction. `ScaffoldGrowth` pins the scaffold
atoms, and measurement confirms it does so almost exactly:

| Check on pinned scaffold atoms | Result |
|---|---|
| RMSD, first *n* atoms vs the scaffold handed to the model | 0.045–0.059 Å |
| Element symbols in order | 38/38, 32/32 |
| Scaffold-internal bonds (connectivity) | 43/43, 0 missing, 0 extra |
| Worst single-atom displacement | 0.12 Å |

And the two routes agree: comparing the generator's mapping against the heavy-atom MCS mapping over
130 molecules gives **119 agree, 11 differ, 0 without a core**.

### The 11 disagreements are automorphism, not error

Every one is the same 4 atoms exchanged pairwise: parent atoms 14↔36 and 15↔35, the two CH₂ arms of
the piperidine ring, which is topologically symmetric about its nitrogen. Parent–parent distances are
2.42 and 2.53 Å, the across-ring separation. Both mappings are chemically correct and a paired ΔΔE
cannot distinguish them.

This is the single most important constraint on the design: **a strict atom-identity comparison would
reject 8.5% of valid pairings.** Validation has to be modulo graph automorphism.

A second observation points the same way: in `s4oxet_k4` the MCS core was 23 atoms against a known
scaffold of 37. The known mapping is sometimes strictly larger, so it should be the primary source,
not a fallback.

## What changes

`PairedPoses` gains an optional known core. When supplied it is used instead of searching, after
validation. Nothing about the existing path changes when it is absent.

### Inputs

A new field, `known_cores: dict[str, list[tuple[int, int]]] | None = None`, keyed by candidate name,
each value a list of `(analogue_heavy_atom, parent_heavy_atom)` pairs — **heavy-atom indices**, the
same orientation as the existing `scaffold_maps` output. `ScaffoldGrowth` can produce it from
`repair`'s `mapping` and the cell's scaffold indices.

### Heavy-atom seeding

`mapping` inside `_paired` indexes the hydrogen-added molecule, so a heavy-atom core must be
extended. This is mechanical: `Chem.AddHs` preserves heavy-atom indices and appends hydrogens
contiguously (verified: 41 heavy → 71 total, indices preserved, heavy-first, H contiguous). For each
mapped heavy pair, the analogue's H neighbours are paired with the parent's H neighbours in index
order.

Where a heavy atom bears more than one hydrogen (9 such carbons in the parent) the pairing among
those hydrogens is arbitrary. This is sound — they are automorphic, and the core is copied
coordinate-wise from the parent, so either choice lands each H on an equivalent position — but it is
a deliberate arbitrary choice and is documented as such in the docstring. An H count mismatch between
a mapped pair means the mapping is not a true correspondence: the pair's hydrogens are left unmapped
rather than guessed, and the heavy atoms stay mapped.

### Cross-validation

With a known core supplied, run the heavy-atom MCS anyway and compare, because it is cheap (median
0.00 s) and always yields a core:

1. Run `FindMCS` on the **heavy-atom** molecules with the tier's existing compare settings, at
   `mcs_timeout_s`.
2. If it produced a core, enumerate the analogue's symmetry-equivalent matches with
   `GetSubstructMatches(core, uniquify=False)` and accept the known mapping if it agrees with **any**
   of them on their shared atoms. This is the automorphism allowance the 11 disagreements require.
3. If the known mapping agrees with none, raise `NoCommonCoreError` naming both mappings. A genuine
   conflict is a bug in the caller's mapping and must not be papered over.
4. If the MCS found no core at all, accept the known mapping and record that it was unvalidated. (Not
   observed in 130 molecules; the branch exists so a cancelled-and-coreless search does not become a
   hard failure.)

A cancelled-but-cored search still validates: cancellation bounds the proof, not the correctness of
the core it found, and the known mapping supplies the corroboration the refusal was protecting
against.

### What does not change

- No known core supplied → today's behaviour exactly, including the cancellation refusal.
- `min_core_heavy`, the identical-to-parent self-mapping guard, and `_check_given_pose` all still run
  against the resulting mapping.
- The self-pair anchor (pairing a molecule with itself gives exactly 0.0 per pose) must still hold,
  and is the first test.
- Coordinates are still copied atom-wise from the parent pose; the mapping only decides which atom
  copies from which.

## Why not the alternatives

**Raise `mcs_timeout_s`.** Does not work. Cancelled at 10 s stayed cancelled at 30, 60 and 120 s, on
every molecule tried, with the found core unchanged at 38 atoms. The search is exponential in these
inputs, and the molecules are the hard case for it: several near-identical fragments (two fluorinated
aryls, two saturated N-rings) generate many equivalent partial mappings.

**Relax the MCS compare settings.** Tried. `CompareAny` bonds and dropping `completeRingsOnly` gave
identical cancellations at identical core sizes. Bond comparison is not the cost driver; hydrogens
are (heavy-only completes 106/130, all-atom ~0/130).

**Trust the known mapping without validating.** Rejected on your instruction and on the evidence: the
mapping comes from a caller, and 11 of 130 differ from MCS even when both are right. Without a check
there is no way to tell an automorphism from a caller bug.

## Testing

1. **Self-pair anchor with a known core.** Pair a molecule with itself via `known_cores`; every pose
   must be exactly 0.0, as on the MCS path.
2. **Agreement case.** A molecule whose known mapping matches MCS atom-for-atom pairs identically
   with and without `known_cores` — same poses, same `scaffold_maps`.
3. **Automorphism case.** One of the 11 piperidine-swap molecules must pair successfully, and the
   resulting poses must be coordinate-identical to the MCS path's (the swapped atoms are equivalent,
   so the copied coordinates agree).
4. **Conflict case.** A deliberately wrong mapping (two atoms of different elements, or a permutation
   that is not an automorphism) must raise `NoCommonCoreError` naming both mappings.
5. **Hydrogen extension.** For a CH₂-bearing core, every mapped heavy atom's hydrogens are mapped,
   total mapped count equals the expected heavy + H count, and an H-count mismatch leaves that pair's
   hydrogens unmapped without dropping the heavy pair.
6. **No-known-core regression.** The existing test suite for `PairedPoses` passes unchanged.
7. **Funnel effect.** On the 480-molecule study set, report pairable-with-known-core against the
   current 112/480. This is a measurement, not an assertion: it belongs in the study, not the test
   suite.

## Scope

`src/smeltery/tiers.py` (`PairedPoses`: one new field, mapping-resolution split out of `_paired`,
hydrogen extension, validation) and its tests. `ScaffoldGrowth` emitting `known_cores` is a separate,
later change — this spec only makes the tier able to accept one, so the study harness can supply it
and measure the effect first.

## Open question

`min_core_frac` for "usable pairing" was set to 0.75 by assertion in the study harness, and the
observed median core fraction is 0.659. Whether 0.659 is enough for `paired_ddE`'s variance reduction
is unmeasured, and the right way to settle it is to compute ΔΔE variance against core fraction. That
is out of scope here and does not block this change.
