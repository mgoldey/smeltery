# qmmm

`smeltery.tiers.Qmmm`. Single-point QM/MM (RHF in a fixed-charge field plus AMBER-form MM) with a real covalent cut, through ferric.

## Computes

- **Quantity:** `E_qmmm`, unit `kcal/mol`: `E_qm_embedded + E_mm`, a total energy of the partitioned system.
- **Quantity:** `E_qm_embedded`, unit `kcal/mol`: the QM region's RHF energy in the MM point-charge field (QM-MM Coulomb is in the embedding).
- **Quantity:** `E_mm`, unit `kcal/mol`: the force field's MM-MM energy plus QM-MM Lennard-Jones.
- The candidate's poses are conformers of the whole structure in the atom order of the caller's `MmParameters`; `qm_indices` picks the QM region. Every bond with exactly one QM end is a cut bond and is capped with a scaled-position link H.
- A single point. There is no geometry optimization, and the tier never writes coordinates back (`tests/test_qmmm.py::test_a_run_writes_only_declared_keys_and_never_touches_the_input_coordinates`).
- RHF singlet only. The MM parameters are caller-supplied; the committed fixture (`tests/data/gly3_ff14sb.json`) is a toy Gly3 whose bonded MM energies mean nothing as strain.

## Native settings

| key | default |
|---|---|
| `method` | `RHF` |
| `basis` | `sto-3g` |
| `energy_conv` | `1e-10` |
| `density_conv` | `1e-08` |
| `engine` | `ferric` |
| `qm_indices` | the caller's QM atom indices (required) |
| `qm_charge` | the caller's QM-region net charge (required) |
| `cut_bonds` | derived: every bond in the parameters with exactly one QM end |
| `boundary_scheme` | `delete-host` |
| `boundary_charge_treatment` | derived from `boundary_scheme` |
| `link_atom` | `scaled-position H` |
| `link_scale` | derived: the C-C scale unless the caller gives one (a non-C-C cut requires it) |
| `min_link_charge_distance_ang` | `0.874` |
| `mm_force_field` | the provenance of the caller's `MmParameters` |
| `mm_terms` | derived: which MM terms ferric applies |

`min_link_charge_distance_ang` is a placeholder, not a measurement: it is the midpoint of two distances quoted in issue #19 (0.443 and 1.305 angstrom) that the implementation could not reproduce, and it is a constructor knob (`MIN_LINK_CHARGE_DISTANCE_ANG` in `src/smeltery/tiers.py`).

## Cost

- **Cost:** UNMEASURED
- `estimate_cost()` returns `predicted=None`: the RHF calibration in `src/smeltery/cost.py` does not cover QM/MM.

## Gates

- **G1, pose validity (applied by this tier).** `Qmmm.run` calls `require_passing_poses` on every candidate first.
- Its own refusals, all before any SCF: a link H closer than `min_link_charge_distance_ang` to an embedding charge (`QmmmBoundaryError`); an odd electron count in the QM region; a cut bond that is not C-C with no explicit `link_scale`; a ferric without the link-atom API; and a ferric that applies Lennard-Jones across the cut without following the bond list, detected by a one-SCF behavioural probe (ferric #319, fixed by ferric PR #336; `QmmmUnavailableError`). The probe is tested with a simulated old engine, not a real pre-fix build.
- `MmParameters` refuses empty provenance and mismatched lengths at construction.

## Systematic floor

- **Floor (`E_qmmm`):** UNMEASURED
- **Floor (`E_qm_embedded`):** UNMEASURED
- **Floor (`E_mm`):** UNMEASURED
- `systematic_floor` returns `None` for all three. Totals depend on the partition, so a floor measured on one partition would not transfer.

## Not licensed to claim

- That energies from different QM/MM partitions are comparable: totals depend on where the cut is, and moving it changes the conformer energy difference. `tests/test_qmmm.py::test_boundary_shift_is_a_measurement_not_a_claim` asserts only sanity on one toy fixture; the magnitude it measured is in the description of PR #75, not asserted anywhere.
- A ranking of analogues, or a binding energy: with no floor, `funnel.cut(tier=...)` refuses the tier.
- That the `keep` boundary-charge scheme diverges at a short link-charge distance. Issue #19 says so; the implementation did not reproduce it (40 `run_optimize_qmmm` steps on the Gly3 fixture, all schemes descended, none converged: comment above `MIN_LINK_CHARGE_DISTANCE_ANG` in `src/smeltery/tiers.py`), and the tier keeps the distance gate on the strength of the issue, not of a measurement.
- That it accounts for bonded MM energies of a real structure: the committed fixture's geometry is a toy.

## Evidence

- **Anchor:** `tests/test_qmmm.py::test_all_qm_region_is_bit_identical_to_plain_rhf_and_has_no_mm_energy`
