# field_interaction

`smeltery.tiers.FieldInteraction`. The point-charge field's interaction energy with a pose: ΔE_int = E_RHF(in field) − E_RHF(vacuum), at the same geometry, with ferric.

## Computes

- **Quantity:** `dE_int`, unit `kcal/mol`. Per pose, including the polarisation the field induces. It is a difference, so it compares across formulas, which a total energy does not.
- Closed-shell RHF only. Two SCFs per pose (vacuum, then in the field).
- Needs `ctx["field"]`, a list of `PointCharge` in Å. A missing key is a bare `KeyError`, not an explained error.

## Native settings

`FieldInteraction.settings()` reports `method`, `basis`, `energy_conv`, `density_conv`, `engine` and `field`.

| key | default |
|---|---|
| `method` | `RHF` |
| `basis` | `sto-3g` |
| `energy_conv` | `1e-10` |
| `density_conv` | `1e-08` |
| `engine` | `ferric` |
| `field` | provenance of the pocket field after a run (file digest, pdb2pqr30 version, cutoff); `None` before |

## Cost

- **Cost:** 14.9 s; **Unit of work:** one pose, vacuum RHF plus field RHF; **System:** benzoic acid, 15 atoms, 51 basis functions, 36 SCF iterations in total, ferric 0.1.0 at commit 8637a5d, `RAYON_NUM_THREADS=1`, median of 5 repeats (14.0 to 16.3 s); **Basis:** STO-3G only; **Source:** `src/smeltery/data/cost_measurements.json`, produced by `scripts/measure_cost.py`
- All five measured molecules (seconds per pose, STO-3G): ethanol 21 bf 0.765; benzoic acid 51 bf 14.9; ibuprofen 93 bf 91.6 (the three calibration samples); aspirin 73 bf 43.5; 4-fluorobenzoic acid 55 bf 16.1 (held out). `estimate_cost()` scales the benzoic acid time by (nbf/51)^2.71, the exponent fitted on seconds per SCF iteration over the calibration samples (`src/smeltery/cost.py`, `fit_rhf_calibration`).
- **Basis dependence.** The model holds for STO-3G on that machine, single-threaded, and assumes the reference's iteration count; any other basis, or an element outside the STO-3G table, gets `predicted=None`. No def2-SVP RHF timing of this tier exists. The only recorded basis comparison is of ferric's DFT funnel run on benzoic acid analogues (`src/smeltery/cost.py`, module docstring; `docs/archive/ferric-site/pharma-use-case-coverage.md`): docking is 72.7% of that campaign at STO-3G and 39.8% at def2-svp, where DFT is 60.0%. That split inverts with the basis and is not a measurement of this RHF tier.

## Gates

- **G1, pose validity (applied by this tier).** `FieldInteraction.run` calls `require_passing_poses` on every candidate first; a pose that failed PoseBusters raises `PoseGateError`. A candidate with no report runs unless `ctx["require_pose_report"]` is true.
- **G3, electron parity (not applied by this tier).** Only `smeltery run` checks parity (`src/smeltery/cli.py`, `_check_parity`). The tier calls `ferric.Molecule.from_xyz_string` without a charge or multiplicity, so every molecule is scored at charge 0, multiplicity 1; ferric refuses an odd electron count (run during this documentation, one H atom: `ValueError`, "inconsistent charge/multiplicity"). A charged molecule is scored as the neutral atom list it is given. The CLI's parity check uses the SMILES formal charge, which the tier does not.
- Its own guards: SCF non-convergence of either calculation raises `RuntimeError`.
- **G4, unmeasured floor (applies to using the output).** `funnel.cut(tier=...)` refuses this tier because its floor is unmeasured; `smeltery run` then requires an explicit `[cut] floor` and records that it came from the config.

## Systematic floor

- **Floor (`dE_int`):** UNMEASURED
- `systematic_floor("dE_int")` returns `None` with the comment "charge-model / basis sensitivity not yet measured" (`src/smeltery/tiers.py`). `gates.charge_sensitivity` measures the charge-model part per run; it is not a stored number.
- Related, and not this tier's floor: the pose-ensemble ddE noise floor, **ddE noise floor:** 4.07 kcal/mol (`smeltery.pocket.DDE_NOISE_FLOOR_KCAL_MOL`). It is SEM·√2 at n = 100 poses of an UNPAIRED difference of two independently embedded ensembles, from a per-pose sd of 28.75 kcal/mol on danuglipron in 7LCJ (`experiments/danuglipron/RESULTS.md`, M13 table; cited as M4 to M14 in `src/smeltery/pocket/binding_energy.py`). It is pose-sampling noise, not a systematic error of RHF/STO-3G, and the record does not establish that its `pose_fit` quantity is this tier's `dE_int`. Paired differences have a smaller SEM: 0.221 to 0.615 kcal/mol for F-for-H and Cl-for-H on one paracetamol-like molecule with MMFF energies in the gas phase, recorded as provisional (`experiments/danuglipron/RESULTS.md`, M17). Not measured for this tier.

## Not licensed to claim

- A ranking of analogues whose ΔΔE differs by less than the noise the ensemble leaves. With the floor unmeasured the funnel has no number to separate them by; the 4.07 figure above is 2 to 4 times the 1 to 2 kcal/mol substituent effects (`src/smeltery/pocket/binding_energy.py`).
- A binding free energy. `dE_int` is a single-geometry electronic interaction energy at RHF/STO-3G: no dispersion, no solvation, no entropy, no relaxation of the pose or the pocket.
- A result for a charged or open-shell molecule (see G3 above).

## Evidence

- **Anchor:** `tests/test_anchors.py::test_empty_field_gives_exactly_zero_interaction`, `tests/test_anchors.py::test_self_pair_gives_exactly_zero_on_every_pose`
