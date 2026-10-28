# designability

`smeltery.tiers.Designability`. Would a chemist make this molecule? Physicochemical properties, their drift from the parent, and a synthetic-accessibility heuristic, from the SMILES alone.

## Computes

- **Quantity:** `mw`, unit `g/mol`. RDKit `Descriptors.MolWt`.
- **Quantity:** `logp`, unit `log units (calculated)`. RDKit `Crippen.MolLogP`, a calculated estimate and not a measurement.
- **Quantity:** `tpsa`, unit `A^2`. RDKit `Descriptors.TPSA`.
- **Quantity:** `qed`, unit `0-1 (unitless)`. RDKit `QED.qed`.
- **Quantity:** `sa_score`, unit `1-10 (unitless)`. Ertl & Schuffenhauer synthetic accessibility from RDKit's contrib `sascorer`, 1 easy to 10 hard.
- **Quantity:** `heavy_atoms`, unit `count`.
- **Quantity:** `d_mw`, unit `g/mol`. Analogue minus `ctx["parent"]`.
- **Quantity:** `d_logp`, unit `log units (calculated)`.
- **Quantity:** `d_tpsa`, unit `A^2`.
- **Quantity:** `d_qed`, unit `0-1 (unitless)`.
- **Quantity:** `d_sa`, unit `1-10 (unitless)`.
- **Quantity:** `d_heavy`, unit `count`.

Needs `ctx["parent"]`, a `Candidate`. Every quantity is a function of the SMILES alone, so no pose tier is required and the value is constant across a candidate's poses.

**Why the drift is the point.** The parent here is a real drug-like molecule, so the conventional ranges matter less than what a substituent *does* to it. Absolute values are dominated by the shared scaffold and discriminate poorly between generators; `d_mw`, `d_logp`, `d_sa` and `d_qed` are the quantities that carry signal. Pairing a candidate with itself gives exactly `0.0` for every drift, the same self-pair anchor [paired_poses](paired_poses.md) relies on.

**Why this tier exists.** Every other tier measures validity or energy. A fixed-geometry interaction energy such as [field_dft_interaction](field_dft_interaction.md)'s `dE_int` grows more negative as atoms are added, so a generator that invents large, greasy, hard-to-make substituents scores *better* on it than a conservative one. That bias is live rather than hypothetical: on the gen-compare set the two generators' grown mass differs by a median 7 versus 2 heavy atoms.

**Values are written pose-major and repeat.** `per_pose[q]` holds the same number once per pose, because `per_pose` is the only channel the `Tier` protocol and the run record provide. The repetition is intentional, not a per-pose dependence: averaging over poses is a no-op and the SEM is zero.

## Native settings

`Designability.settings()` reports `properties`, `sa_score`, `reference`, `alerts`, `rdkit` and `parent_properties`.

| key | default |
|---|---|
| `properties` | `RDKit MolWt, Crippen.MolLogP, TPSA, QED` |
| `sa_score` | `Ertl & Schuffenhauer, RDKit contrib sascorer, 1 (easy) to 10 (hard)` |
| `reference` | `ctx['parent']; drift is analogue minus parent` |
| `alerts` | `not computed here; see smeltery.properties.RdkitAlertProvider (PAINS/Brenk/NIH)` |
| `rdkit` | the installed RDKit version |
| `parent_properties` | `None` until `run`, then the parent's own absolute values |

`parent_properties` is filled in by `run` so a reader can reconstruct the absolute values behind any drift without re-deriving them. Structural alerts are deliberately absent: `smeltery.properties.RdkitAlertProvider` already covers PAINS, Brenk and NIH with its own licence provenance, and a second implementation would give two numbers that could disagree.

## Cost

- **Cost:** UNMEASURED

RDKit descriptors on a SMILES are sub-millisecond and pose-independent, so this tier has never been the cost of a run and no timing was taken. `estimate_cost` returns `predicted: None` with that basis in `src/smeltery/tiers.py` rather than a number nobody measured.

## Gates

- None applied by this tier. It is a descriptor tier: it computes, refuses nothing, and sets no threshold.
- An unparseable SMILES raises `ValueError`; a stripped RDKit without the contrib `SA_Score` module raises `SaScoreUnavailableError` naming the expected path.

## Systematic floor

- **Floor (`mw`):** UNMEASURED
- **Floor (`logp`):** UNMEASURED
- **Floor (`tpsa`):** UNMEASURED
- **Floor (`qed`):** UNMEASURED
- **Floor (`sa_score`):** UNMEASURED
- **Floor (`heavy_atoms`):** UNMEASURED
- **Floor (`d_mw`):** UNMEASURED
- **Floor (`d_logp`):** UNMEASURED
- **Floor (`d_tpsa`):** UNMEASURED
- **Floor (`d_qed`):** UNMEASURED
- **Floor (`d_sa`):** UNMEASURED
- **Floor (`d_heavy`):** UNMEASURED

These are descriptors and heuristics, not calibrated predictions, so `systematic_floor` returns `None` for every quantity.

## Not licensed to claim

- That a molecule is synthesizable. `sa_score` is a fragment-contribution heuristic, not a retrosynthesis; it screens for a tail of implausible chemistry and a single value carries no verdict.
- That a molecule is drug-like. `qed` is a composite fitted to oral drugs, and a scaffold-grown analogue of one specific parent is not what it was fitted to. It is reported, not ranked on.
- A measured logP. `Crippen.MolLogP` is an atom-contribution estimate.
- Toxicity, promiscuity or assay interference. No alert catalog runs here.
- Binding affinity, potency or selectivity. Nothing in this tier touches the pocket.
- That passing conventional property ranges predicts anything about a specific target.

## Evidence

- **Anchor:** `tests/test_designability.py::test_self_pair_drift_is_exactly_zero`, `tests/test_designability.py::test_a_bigger_greasier_analogue_drifts_the_way_a_chemist_would_expect`

Verified on the gen-compare scaffold-growth set, 324 PocketXMol and 59 DiffSBDD molecules against the danuglipron-series parent (`mw` 555.6, `logp` 4.89, `sa_score` 3.34):

| | PocketXMol | DiffSBDD |
|---|---|---|
| median `d_heavy` | +2 | +0 |
| median `d_mw` | +28.6 | +4.0 |
| median `d_logp` | **+0.40** | **-1.11** |
| median `d_sa` | +0.61 | +0.75 |
| `d_sa > 1.0` | 20% | 34% |
| `logp` outside 5.6 | **41%** | 5% |

The self-pair anchor (`d_* == 0.0` for a candidate paired with itself) is asserted in `tests/test_designability.py`.
