# field_dft_interaction

`smeltery.tiers.FieldDftInteraction`. The point-charge field's interaction energy with a pose at KS-DFT: ΔE_int = E_PBE(in field) − E_PBE(vacuum), at the same geometry, with ferric. The DFT counterpart of [field_interaction](field_interaction.md), which is RHF.

## Computes

- **Quantity:** `dE_int`, unit `kcal/mol`. Per pose, including the polarisation the field induces. It is a difference, so it compares across formulas, which a total energy does not.
- **Quantity:** `E_d3`, unit `kcal/mol`. The D3(BJ) dispersion correction for the pose's geometry, reported on its own and **not** added into `dE_int`.
- Closed-shell KS-DFT only. Two SCFs per pose (vacuum, then in the field), plus one `ferric.d3bj_energy` call.
- Needs `ctx["field"]`, a list of `PointCharge` in Å. A missing key is a bare `KeyError`, not an explained error.

**Why `E_d3` is separate.** `ferric.d3bj_energy(mol, functional)` takes only the geometry: no point charges, no density. At a fixed geometry it is therefore identical in both legs and cancels exactly in the difference, measured as −0.109504 Ha in each leg of one 74-atom ligand. Dispersion contributes nothing to a fixed-geometry `dE_int`, and the number is reported because it is free and does matter for total energies, for comparing different geometries, and for relaxation.

**Dispersion must be added by hand.** A plain `functional="PBE"` applies none: `ferric.DftResult.e_dispersion` is `None` and `total_energy` equals `e_scf`. ferric rejects `PBE-D3`, `PBE-D3BJ` and `PBE+D3` as unknown libxc names. `_dft_energy` raises if a ferric applies dispersion inside the SCF, where `total_energy` would already include it and the separate term would double-count.

## Native settings

`FieldDftInteraction.settings()` reports `method`, `functional`, `dispersion`, `basis`, `energy_conv`, `density_conv`, `max_iter`, `memory_budget_gb`, `require_free_gb`, `engine` and `field`.

| key | default |
|---|---|
| `method` | `KS-DFT PBE + D3(BJ)` |
| `functional` | `PBE` |
| `dispersion` | `D3(BJ), reported as E_d3; cancels in dE_int at fixed geometry` |
| `basis` | `sto-3g` |
| `energy_conv` | `1e-10` |
| `density_conv` | `1e-08` |
| `max_iter` | `None` |
| `memory_budget_gb` | `8.0` |
| `require_free_gb` | `None` |
| `engine` | `ferric` |
| `field` | provenance of the pocket field after a run (file digest, pdb2pqr30 version, cutoff); `None` before |

`max_iter=None` leaves ferric's own iteration limit in force, and a value is passed to every SCF of the tier, vacuum and field. `memory_budget_gb` is checked before the first SCF when `check_memory` is true (its default): a run whose AO cache plus packed RI tensor exceeds it raises `DftMemoryError` rather than letting ferric spill the tensor to disk and re-read it every iteration. `check_memory=False` skips that gate entirely and sets the reported `memory_budget_gb` to `None`.

`require_free_gb` answers a different question from `memory_budget_gb`, which is a fixed prediction for one calculation and so cannot see a machine that has no memory left. When set, the tier reads `MemAvailable` from `/proc/meminfo` and refuses to start below that figure, because a DFT process that outgrows free memory does not degrade gradually: it pages the RI tensor off disk on every SCF iteration. It is `None` by default, so no existing caller changes behaviour, and a platform with no `/proc` reads as "cannot tell" and proceeds rather than failing.

## Cost

- **Cost:** 71.1 s; **Unit of work:** one vacuum PBE SCF (the field leg is a second SCF, measured separately at 80.7 s); **System:** a 74-atom ligand from the gen-compare PocketXMol set, 246 basis functions, 610,500 XC grid points, 16 SCF iterations, ferric b22183b release CPU, 2 cores with `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`, single measurement; **Basis:** STO-3G only; **Source:** `src/smeltery/cost.py`
- The field leg costs 13% more than vacuum (80.7 s against 71.1 s) and the difference is two extra SCF iterations (18 against 16), not a larger per-iteration cost: point charges add a one-body term, not new two-electron work. `pbe_seconds_per_pose` scales each leg from its own measurement, so one pose's vacuum-plus-field pair at the reference size is 151.8 s.
- `estimate_cost()` scales `DftSize.xc_fock_work`, which is nbf² × npts, not nbf alone. The XC grid is 75 × 110 points **per atom** and so depends on atom count and not on the basis set (`src/smeltery/cost.py`, module docstring). Any functional other than PBE, or any basis other than STO-3G, returns `predicted=None` with a reason; so does an element outside the STO-3G table, because nbf cannot then be counted.
- **It assumes the reference's iteration count, and that is the weak point.** One validation at the tier's own settings took 21 and 24 iterations where the reference took 16 and 18, giving 191.8 s against a 151.8 s prediction, a ratio of 1.26 explained by the iteration ratio of 1.32. The prediction is a LOWER BOUND for a chemistry that converges more slowly. The same caveat is recorded for ferric's own DFT in `DftSize.xc_fock_work`, where alkanes took exactly 10 iterations and danuglipron took 18.
- **Memory is a separate question from time.** `dft_memory_gb` reports the AO cache and the packed RI three-index tensor. At STO-3G the reference needs 4.81 + 0.92 GB; at def2-SVP the same molecule needs 14.79 + 8.73 GB, and ferric reports spilling that tensor to disk as a performance cliff because `DfK::build` re-reads all of it on every SCF iteration.

## Gates

- **G1, pose validity (applied by this tier).** `FieldDftInteraction.run` calls `require_passing_poses` on every candidate first; a pose that failed PoseBusters raises `PoseGateError`. A candidate with no report runs unless `ctx["require_pose_report"]` is true.
- **G3, electron parity (not applied by this tier).** Only `smeltery run` checks parity (`src/smeltery/cli.py`, `_check_parity`). The tier passes the SMILES' net formal charge to `ferric.Molecule.from_xyz_string` and the multiplicity stays 1, so it scores a closed-shell singlet at that charge. The charge is whatever the SMILES carries: a carboxylic acid given as the neutral acid is scored as the neutral acid.
- **G4, unmeasured floor (applies to using the output).** `funnel.cut(tier=...)` refuses this tier because both its floors are unmeasured; `smeltery run` then requires an explicit `[cut] floor` and records that it came from the config.
- Its own guards: SCF non-convergence of either calculation raises `RuntimeError` naming which leg failed; a basis the cost model cannot count raises `ValueError` from `_size`; a run over `memory_budget_gb` raises `DftMemoryError` before any SCF starts; and a ferric that applies dispersion inside `run_dft` raises `RuntimeError` from `_dft_energy` rather than double-counting it.

## Systematic floor

- **Floor (`dE_int`):** UNMEASURED
- **Floor (`E_d3`):** UNMEASURED
- `systematic_floor` returns `None` for both quantities. STO-3G is a poor basis for absolute energies; the expectation that much of that error is common-mode and cancels in a PAIRED ΔΔE, where the scaffold atoms sit at the same coordinates on both sides, is the design intent of `PairedPoses` and is not measured. A number here would present that expectation as evidence.
- No basis-set comparison of this tier exists: no def2-SVP run of these systems has completed, because the RI tensor does not fit the measured machine's memory.

## Not licensed to claim

- A ranking of analogues whose ΔΔE differs by less than the unmeasured floor, including any claim that PBE orders them better than RHF does. The argument for this tier over [field_interaction](field_interaction.md) is that RHF has no correlation and the field polarises a correlated density differently, which is a reason to prefer it and not a measurement of how much it helps.
- Any dispersion contribution to a fixed-geometry `dE_int`. It is exactly zero there, by the cancellation recorded above.
- A binding free energy. `dE_int` is a single-geometry electronic interaction energy: no solvation, no entropy, no relaxation of the pose or the pocket.
- A result for a charged or open-shell molecule (see G3 above).
- An absolute energy at STO-3G, for anything.

## Evidence

- **Anchor:** `tests/test_cost.py::test_pbe_tier_runs_both_legs_and_reports_dispersion_separately`, `tests/test_cost.py::test_pbe_tier_dispersion_is_identical_in_both_legs_so_it_cancels`
