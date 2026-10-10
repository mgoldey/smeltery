# paired_poses

`smeltery.tiers.PairedPoses`. Builds the parent's pose ensemble and, for each analogue, pose *i* on the parent's pose *i*. It scores nothing.

## Computes

- **Quantity:** none. It writes `Candidate.poses`, not a `per_pose` quantity (`produces()` is empty).
- The parent gets `n_poses` ETKDG conformers, MMFF94-optimised, aligned onto conformer 0, each given a seeded rigid jitter. With `parent_poses_given=True` the parent's existing poses (for example from `docking`) are used unjittered.
- Analogue pose *i*: every atom the maximum common substructure maps onto the parent is copied exactly from parent pose *i*; unmapped atoms come from a fresh embedding aligned onto the core. A molecule paired with itself therefore gets identical coordinates and a per-pose difference of exactly 0.0.

## Native settings

`PairedPoses.settings()` reports these eight keys, taken straight from the dataclass fields (`src/smeltery/tiers.py`).

| key | default |
|---|---|
| `n_poses` | `6` |
| `seed` | `20261005` |
| `jitter_deg` | `15.0` |
| `jitter_ang` | `0.5` |
| `min_core_heavy` | `3` |
| `mcs_timeout_s` | `10` |
| `parent_poses_given` | `False` |
| `relax_unmapped` | `False` |

## Cost

- **Cost:** 0.0862 s; **Unit of work:** `PairedPoses(n_poses=6).run` on a parent and one analogue (6 parent poses plus 6 paired analogue poses); **System:** benzoic acid paired with 4-fluorobenzoic acid, 15 atoms each, RDKit 2026.03.6, Python 3.11.14, one thread, box load average 41.4 on 12 cores, median of 5; **Basis:** none (no basis set; RDKit embedding and MMFF94); **Source:** `docs/tiers/measurements.json`, produced by `scripts/measure_tier_costs.py`
- `estimate_cost()` returns `predicted=None` (`src/smeltery/tiers.py`, `PairedPoses.estimate_cost`); the number above is the measurement, not a model. One measurement on one pair: it says nothing about how the cost grows with atom count, `relax_unmapped=True`, or a large `mcs_timeout_s`.

## Gates

- **G2, common core (applied by this tier).** `PairedPoses._paired` raises `NoCommonCoreError` when the MCS search times out, finds no core, finds fewer than `min_core_heavy` heavy atoms, or maps only part of a molecule identical to the parent. It never returns a degraded mapping.
- **G1, pose validity (applies to this tier's output, not run by it).** Analogue poses built by pairing fail PoseBusters at the junction between the copied core and the new substituent unless `relax_unmapped=True` (`README.md`, Example section; `src/smeltery/cli.py`, `_run_gates`). The tier does not call `require_passing_poses`.
- Its own guards: `parent_poses_given` with no parent poses, or with poses whose atom order differs from the SMILES, raises `ValueError`; an embedding that yields fewer than `n_poses`, or an unconverged or untypable MMFF relaxation, raises `RuntimeError`.

## Systematic floor

- **Floor:** NONE. The tier produces no quantity, so `systematic_floor()` raises `KeyError` for any name.

## Not licensed to claim

- That the poses are bound poses. The jitter is a seeded rigid perturbation "to mimic the spread of docked poses" (docstring), not a search.
- That the analogue geometry is relaxed, unless `relax_unmapped=True`; and with it on, that the ΔΔE is unchanged, since it moves the substituent.
- That a zero self-pair certifies the mapping. It certifies that mapped atoms are copied; it was shown in the campaign (`experiments/danuglipron/RESULTS.md`, M17) that a wrong atom correspondence is pinned to the parent's coordinates and measures as zero drift.

## Evidence

- **Anchor:** `tests/test_anchors.py::test_self_pair_gives_exactly_zero_on_every_pose`, `tests/test_anchors.py::test_analogue_core_is_copied_exactly_from_the_parent_pose`
