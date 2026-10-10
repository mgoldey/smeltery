# docking

`smeltery.docking.Docking`. Proposes poses by search through any `DockingProvider` (Vina is the reference one, in the `docking` extra), and writes a per-pose score.

## Computes

- **Quantity:** `dock_score`, unit `kcal/mol` for the Vina provider; in general the unit is whatever the provider's `score_unit()` returns. An empirical ranking heuristic, not a binding free energy: the tier sets `is_delta_g = False`, and `funnel.paired_delta` refuses it.
- It also writes `Candidate.poses`, sorted best score first, pooled over all seeds, and `receptor_flex` when the provider returns flexible sidechains.
- Each seed is an independent ETKDG embedding of the ligand and an independent search seed. Effort is added by adding seeds.
- Needs `ctx["receptor"]` and `ctx["box"]`; a missing key is a `DockingError` that names the key.

## Native settings

`Docking.settings()` reports these keys; the provider's own settings are nested under `provider_settings`.

| key | default |
|---|---|
| `provider` | the provider's `name` (required constructor argument) |
| `provider_settings` | the provider's `settings()` (for Vina: `engine`, `scoring`, `exhaustiveness`, `n_poses`, `cpu`) |
| `exhaustiveness` | `4` |
| `seeds` | `[61453]` |
| `score_unit` | the provider's `score_unit()` |

`VinaProvider` defaults to `n_poses=10` and `cpu=1` (`src/smeltery/docking/provider.py`).

## Cost

- **Cost:** UNMEASURED
- `estimate_cost()` returns `predicted=None`: "no timing recorded in smeltery". The tier itself has not been timed in this repository.
- Related campaign measurements of Vina on one target, not this tier's cost: danuglipron into 7LCJ, best-of-10 redock, exhaustiveness 4, 26.4 s per dock with `cpu=0` (12 cores) and 109.0 s with `cpu=1`; exhaustiveness 8, 16, 32 cost 34.5, 67.6, 102.8 s at `cpu=0` (`experiments/danuglipron/RESULTS.md`, M11). The tier's provider default is `cpu=1`.
- **Basis dependence of a share of a campaign.** In ferric's funnel run on 10 benzoic acid analogues, docking took 32.7 s: 72.7% of a 45.0 s run at STO-3G and 39.8% of an 82.1 s run at def2-svp, where DFT was 60.0% (`src/smeltery/cost.py`, module docstring; `docs/archive/ferric-site/pharma-use-case-coverage.md`). Docking's share inverts with the DFT basis; its absolute cost did not move between the rows.

## Gates

- It runs no gate on the poses. The Vina provider returns poses with every hydrogen restored from the SMILES (`src/smeltery/docking/provider.py`, `restore_hydrogens`), because a PDBQT is united-atom; `smeltery.docking.united_atom` raises `PoseMismatchError` or `StereochemistryError` when the restoration cannot be trusted.
- **G1, pose validity (applies to this tier's output).** Quantum and descriptor tiers refuse docked poses that fail PoseBusters; the check is run by the caller (`gates.check_candidate_poses`, or `[gates] posebusters = true` in `smeltery run`).
- **G5, not a free energy (applied to this tier's output).** `funnel.paired_delta(..., tier=Docking(...))` raises `IncomparableError` because `is_delta_g` is `False` (`tests/test_scoring.py::test_docking_scores_are_refused_by_paired_delta`).
- **G4, unmeasured floor (applied to this tier's output).** `funnel.cut(tier=...)` refuses it.
- Its own guards: a ligand SMILES that does not parse, has several fragments, or cannot be embedded raises `DockingError`; a candidate for which every seed returns no poses raises `DockingError` listing each seed's error. `seeds` must be non-empty.

## Systematic floor

- **Floor (`dock_score`):** UNMEASURED
- `systematic_floor("dock_score")` returns `None` ("an empirical score: its systematic error is not measured").

## Not licensed to claim

- A binding free energy, or a difference of scores as a ΔΔG (`is_delta_g = False`).
- That the best-scored pose is the best pose. In the campaign Vina rank 0 carried a bias of +27.43 kcal/mol against the mean over 15 poses on an in-pocket energy (`experiments/danuglipron/RESULTS.md`, M13), and the redock RMSD of 0.95 Å (M9) is one draw, not a property of the protocol.
- That more exhaustiveness helps. On that target 8x more exhaustiveness moved the mean redock RMSD (1.00 to 1.05 Å) less than the seed-to-seed SEM of 0.13 to 0.14 Å (M11); the default is exhaustiveness 4 and seeds are the lever.
- Anything about induced fit unless a flexible receptor was used; the default receptor is rigid.

## Evidence

- **Anchor:** `tests/test_docking_real_vina.py::test_real_vina_is_seed_reproducible`
