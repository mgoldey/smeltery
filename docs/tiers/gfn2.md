# gfn2

`smeltery.tiers.Gfn2`. GFN2-xTB total energy per pose, in the pocket's point-charge field, from the external `xtb` binary. A gate, not a ranker.

## Computes

- **Quantity:** `E_gfn2`, unit `kcal/mol`: the GFN2-xTB total energy of the pose. A total, not a difference, so it is comparable only between isomers (`funnel.require_same_formula`).
- Point charges are read by xtb in Bohr; `PointCharge` is in Å and is converted once (`PointCharge.as_ferric_bohr`). The net charge comes from the SMILES formal charges. With no field the value is the vacuum energy.
- xtb runs as a subprocess with every thread pool forced to 1.

## Native settings

`Gfn2.settings()` reports these keys.

| key | default |
|---|---|
| `method` | `GFN2-xTB` |
| `engine` | `xtb subprocess, threads=1` |
| `role` | `gate, not ranker` |
| `xtb_path` | the `xtb` found on `PATH`; `None` if absent |
| `xtb_version` | what `xtb --version` reports; `None` if absent |
| `point_charge_unit` | `bohr` |
| `field` | provenance of the pocket field after a run; `None` before |

## Cost

- **Cost:** 0.0887 s; **Unit of work:** `Gfn2().run` on one pose, one xtb GFN2 single point in a subprocess, with two point charges; **System:** benzoic acid, 15 atoms, 1 pose, xtb 6.7.1, one thread, box load average 41.4 on 12 cores, median of 5 (0.0756 to 0.232 s); **Basis:** GFN2-xTB's fixed internal valence basis, not selectable; **Source:** `docs/tiers/measurements.json`, produced by `scripts/measure_tier_costs.py`
- The same pose in vacuum: 0.113 s median (0.0463 to 0.329 s). The spread is large because the box was heavily loaded; the two medians are not distinguishable from each other.
- `estimate_cost()` returns `{"xtb_runs": n, "kind": "xtb subprocess, ~0.1-1 s each"}` and no seconds. That range is not sourced in this repository and the measurement above sits at its lower edge.
- Archived record from ferric's `tier3_gfn2`, which relaxes a pose rather than taking a single point: 0.05 to 0.152 s per pose at 9 to 19 atoms (`docs/archive/ferric-site/pipeline-golden-path.md`, tier table). Not this tier's operation.

## Gates

- **G1, pose validity (applied by this tier).** `Gfn2.run` calls `require_passing_poses` on every candidate first.
- Needs the `xtb` binary on `PATH`; otherwise `XtbUnavailableError` with an install message (`tests/test_gfn2.py::test_absent_binary_gives_actionable_error`).
- Its own guards: xtb exiting non-zero or printing no `TOTAL ENERGY` raises `RuntimeError`; a placeholder energy is never returned.
- The charge is taken from the SMILES. The multiplicity is not passed to xtb; behaviour on an odd electron count was not tested here.

## Systematic floor

- **Floor (`E_gfn2`):** 0.825 kcal/mol; **Source:** `experiments/danuglipron/RESULTS.md` (M16: MAE of xtb against DFT on relative conformer energies); constant `smeltery.tiers.XTB_VS_DFT_MAE_KCAL`
- It applies to the xtb-minus-DFT error of RELATIVE energies of conformers. M16 measured it on one molecule (a paracetamol-like scaffold with one flexible tail), 20 xtb-relaxed conformers spanning about 3 kcal/mol, DFT single points at the same geometries, STO-3G. DFT is not validated against experiment there.
- The same measurement gave a Spearman ρ of 0.011 against DFT (p = 0.96, 95% CI −0.434 to +0.451). Over that span xtb's ordering carries no detectable information about DFT's.

## Not licensed to claim

- An ordering of conformers or analogues that are close in energy. It is a gate for ionisation state and gross failure; the 143 kcal/mol anion/neutral split is resolved (`experiments/danuglipron/RESULTS.md`, M16), a few kcal/mol are not.
- That 0.825 kcal/mol transfers to another molecule, a larger conformer gap or a different basis: it is one molecule, one conformer set.
- An absolute energy across formulas: `E_gfn2` is a total.

## Evidence

- **Anchor:** `tests/test_gfn2.py::test_point_charge_is_read_in_bohr_coulomb_law`, `tests/test_gfn2.py::test_empty_field_reproduces_vacuum_bit_identically`
