# forcefield

`smeltery.tiers.ForceField`. MMFF94 energies per pose (RDKit), at the pose as given and after relaxing a copy.

## Computes

- **Quantity:** `E_mmff`, unit `kcal/mol`: the MMFF energy at the incoming geometry.
- **Quantity:** `E_mmff_relaxed`, unit `kcal/mol`: the energy after minimising a copy. Their difference is the pose's strain.
- The tier never writes coordinates back: a quantum tier after it scores the pose it was handed (`tests/test_forcefield.py::test_never_writes_coordinates_back_and_reports_strain`).
- `path="mmff"` (RDKit, core) is what the quantities above describe. `path="openmm"` writes `E_openmm` and `E_openmm_relaxed` (kcal/mol, converted from kJ/mol) with the same contract; it needs the optional conda environment in `environment-openff.yml` (OpenMM with OpenFF Sage 2.2.1 ligand parameters and AM1-BCC charges), is a vacuum ligand-only calculation, and is not exercised by CI (`docs/environments.md`).

## Native settings

| key | default |
|---|---|
| `path` | `mmff` |
| `variant` | `MMFF94` |
| `max_iters` | `2000` |
| `rdkit` | the installed RDKit version |

`variant` may be `MMFF94` or `MMFF94s`; any other value, and any `path` other than `mmff` or `openmm`, is refused at construction.

## Cost

- **Cost:** 1.8 ms; **Unit of work:** `ForceField().run` on one pose (SMILES parse plus MMFF94 minimisation of a copy); **System:** benzoic acid, 15 atoms, RDKit 2026.03.6, Python 3.11, one thread, box load average about 4.6 on 12 cores, ETKDG seed 1, 50 repeats, 2026-10-10; **Basis:** none (MMFF94 force field, no basis set); **Source:** `docs/environments.md`
- Same record, other molecules: aspirin (21 atoms) 3.9 ms; ibuprofen (33 atoms) 12.4 ms. The benzoic acid range was 1.8 to 8.8 ms.
- `estimate_cost()` returns `predicted=None` and points at that record: the timing is of the tier on single molecules, not a model of a candidate list.

## Gates

- **G1, pose validity (applied by this tier).** `ForceField.run` calls `require_passing_poses` on every candidate first.
- Its own guards: a molecule MMFF cannot type raises `ForceFieldTypingError` (no energy is invented); a pose whose atom order differs from `AddHs(smiles)` raises `ValueError`; a minimisation that does not converge in `max_iters` raises `RuntimeError` rather than reporting a half-relaxed energy.

## Systematic floor

- **Floor (`E_mmff`):** UNMEASURED
- **Floor (`E_mmff_relaxed`):** UNMEASURED
- `systematic_floor` returns `None` for both ("not measured", `src/smeltery/tiers.py`).

## Not licensed to claim

- A ranking. MMFF energies are not comparable across formulas, and with no floor `funnel.cut(tier=...)` refuses the tier.
- That a low strain means a good pose, or that a high strain means a bad one, at any threshold: no threshold is measured.
- That the relaxed energy is where a quantum tier will put the molecule: the relaxed copy is discarded.

## Evidence

- **Anchor:** `tests/test_forcefield.py::test_an_already_relaxed_pose_has_near_zero_strain`
