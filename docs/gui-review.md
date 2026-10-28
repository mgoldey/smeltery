# GUI review: visualizing results and choosing candidates

This page surveys existing tools for two jobs. The first is visualizing a funnel's results: structures, poses in the
pocket, paired ddE with its uncertainty, and where candidates were lost. The second is choosing candidates: keeping,
rejecting or holding compounds with a reason, and carrying those decisions into the next run. It ends with a proposed
plan for smeltery. Building pipelines is covered in [gui.md](gui.md).

Versions and licences of the recommended packages were read from PyPI on 2026-10-28. Anything marked ⚠ comes from
secondary sources and was not checked against a primary one.

## What smeltery gives a GUI today

| Need | Available | Gap |
|---|---|---|
| Ranked result | `results.paired_ddE` (mean, SEM, n, per-pose differences), `results.cut` (groups, survivors, unranked) | None |
| Uncertainty | SEM per candidate; the floor and its source in `inputs.floor` | None |
| Attrition | `results.funnel` and `results.stages` | Gates stop the run rather than drop candidates, so this view is nearly flat. Real attrition comes from generator rejections (`GenerationResult.rejected`, closed `REASONS`), which a run record does not yet hold |
| Per-pose view | Paired differences only | The raw per-pose `dE_int` of each candidate is not recorded, so parent-to-analogue paired lines cannot be drawn |
| 3D poses | None | **The record holds no geometry.** Poses exist only in memory during a run. Docking and structure stages write the receptor under `<out>/structure/` but not the ligand poses |
| Liabilities | `properties.RdkitAlertProvider` (rank-only; CANNOT_DISCRIMINATE) | Not part of a run |
| Decisions | None | Nothing records which candidates a person chose, or why |

The no-geometry gap blocks everything 3D. Closing it is the first piece of work.

## Landscape

### Commercial tools, as UX reference only

- **Schrödinger LiveDesign** is the reference for choosing. Each idea has a lifecycle status (idea → nominated →
  synthesized) and rationale columns, and stays connected to its assay results. Design Hub (ChemAxon, now Certara)
  records the hypothesis behind each idea in the same way.
- **BioSolveIT SeeSAR** is the reference for triaging poses. It shows small pose cards with per-atom contribution
  colouring, and you filter down to a shortlist.
- **Cresset Flare** and the **FEP+ map viewer** are the references for showing ΔΔG with error: a graph whose edges are
  coloured by error or hysteresis, with drill-down to each edge.
- **StarDrop** carries uncertainty through its multi-parameter score. It is the closest commercial counterpart to our
  tie groups.
- **Datagrok** Hit Triage and Hit Design run a campaign → stages → decisions flow. The packages in its public repo are
  open, but the platform is commercial ⚠.

### Open desktop tools

**DataWarrior** (GPL-3) is excellent at linked 2D plots and filters but has no pocket view. **PyMOL** (open source)
renders pose images well and can run headless. **ChimeraX**'s licence is non-commercial ⚠. **KNIME** and **Galaxy**
are heavyweight workflow systems; Galaxy's provenance model (histories) is worth studying, but neither is a triage UI.
None of these can be embedded in a smeltery GUI. All of them read SDF, so **exporting an SDF that carries properties
covers them all for very little work**.

### Python components

| Role | Pick | Version / licence | Notes |
|---|---|---|---|
| 2D grid and selection | **mols2grid** | 2.2.0 (2025-12-20), Apache | Selection by checkbox with export, filtering and callbacks. Runs in Jupyter, Marimo and Streamlit |
| 3D pocket and pose | **molviewspec** (Mol\*) | 1.8.1 (2025-12-22), MIT | A declarative JSON scene per pose, which fits a JSON run record. Exports a standalone HTML file. Mol\* itself is MIT |
| 3D fallback | py3Dmol | 2.5.5 (2026-05-23), MIT | Light, and works as static HTML |
| Widget layer | **anywidget** | 0.11.0 (2026-04-27), MIT | Write a widget once and it runs in Jupyter, Marimo and Solara |
| App shell | **Marimo** | 0.25.2 (2026-10-19), Apache-2.0 | A reactive notebook that also runs as an app. The file is a `.py` that diffs in git, and it hosts anywidget |
| App shell, alternative | Panel / HoloViews | 1.10.0 (2026-10-23), BSD | Strongest linked brushing. The same code serves a notebook and an app |
| Chemical space | chemiscope | 1.1.0 (2026-06-28), BSD-3 | A property map linked to structures, for hundreds of generated molecules |
| Interactions | ProLIF | 2.2.3 (2026-10-09), Apache-2.0 | Interaction fingerprints per pose, and 2D LigNetwork diagrams |

**Avoid** Streamlit's chemistry components (streamlit-molstar, stmol, streamlit-ketcher; last released 2022–2024),
molplotly (2023) and dash-bio (2022). All are unmaintained. Streamlit's rerun-everything model also makes stateful
selection awkward. dash-molstar is maintained but LGPL-2.1, so check that before depending on it.

For the browser side, Mol\* (MIT) is the 3D engine and RDKit.js MinimalLib (BSD-3) draws 2D SVG on the client. Those
two are enough for a single-file HTML report that needs no Python to view.

### Free-energy viewers

OpenFE's **cinnabar** sets the convention for ΔΔG/ΔG scatter plots with error bars, and **openfe** draws ligand
networks and atom mappings. Get both from conda-forge: the PyPI releases are stale ⚠. Neither of them has tie groups.

## Showing a ranking that may refuse to rank

No open tool found draws tie groups. smeltery has to build this view itself, under one constraint: **the display must
make exactly the decisions `funnel.cut` made, no more**.

- **Forest plot (primary).** Paired ddE mean ± z·SEM per analogue, sorted by mean. Tie groups are alternating
  background bands, and a band of ±floor is drawn around each group boundary. An UNRANKED cut is labelled as such, and
  the `keep` boundary is drawn only where the cut placed one.
- **"Resolved?" matrix.** For every pair of analogues, the matrix shows `funnel.resolved(a, b, z, floor)`: the same
  predicate the cut uses, with tie groups as blocks on the diagonal. It answers "can I tell these two apart?" without
  any new statistics.
- **Per-pose paired lines.** One line per pose from the parent's value to the analogue's, plus the unpaired SEM for
  comparison. This shows why pairing reduces noise. It needs the raw per-pose `dE_int` in the record.
- **Rank-probability plots (rankograms, SUCRA) are not recommended yet.** They need a noise model. The floor is a
  systematic bound, and `cut` combines it by max, not in quadrature, so any Monte Carlo over N(mean, SEM² + floor²)
  would resolve pairs the cut refuses to resolve. Add them only alongside a noise model that the cut also uses.

## Choosing candidates

No open tool records keep/reject decisions with their reasons and carries them into the next run. The proposed design
borrows from LiveDesign and Design Hub and uses the generators' existing idea of a closed set of reasons.

- A **decisions file** sits next to each run record (`decisions-<input_digest[:12]>.json`). It is kept apart from the
  record: the record is for reproducing the run, and decisions are judgement. Each entry holds the candidate name and
  SMILES, a status (`keep`, `reject` or `hold`), a reason from a closed set, free text, a user and a time. Reasons
  include `cut_survivor`, `within_tie_choice`, `liability`, `synthesis`, `pose_implausible`, `novelty`, `other`.
- **A choice inside a tie group is recorded as a human choice, not a ranking.** The GUI requires a reason when someone
  picks among candidates the cut could not separate.
- The **next run** takes a decisions file as input. Rejected candidates are left out and listed in the new record,
  held candidates stay in, and the GUI shows what changed against the previous run.

## Proposed plan

1. **Record what is missing**, in the CLI and record:
   - raw per-pose values per candidate;
   - pose files: one SDF per candidate under `<out>/poses/` with per-pose properties, each identified by sha256 in the
     record;
   - generator rejections, when a generator ran.

   This also produces the SDF that DataWarrior, PyMOL and Maestro read.
2. **`smeltery.gui.results` and `smeltery.gui.decisions`**, with no toolkit, following the pattern of
   `smeltery.gui.pipeline`. They load a record into tidy tables, compute the forest-plot and resolved-matrix data with
   the funnel's own functions, and read, write and validate decisions. They are unit-tested with no GUI installed.
3. **Marimo app** (`smeltery gui <out>`) with the views below, pulling in mols2grid, molviewspec and plotly through a
   new `gui` extra.
4. **Static report**: one HTML file (RDKit.js and Mol\*) for sharing a run with someone who will not install anything.

The app has four views:

| View | Contents |
|---|---|
| Pipeline | The builder from [gui.md](gui.md) |
| Results | The forest plot and the resolved matrix |
| Grid | A mols2grid view with a decision column |
| Pose | Mol\* showing parent and analogue pose *i* superposed in the pocket, with point charges drawn as spheres |
