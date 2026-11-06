# Bench workspace: the pipeline is the page

Date: 2026-11-04. Status: design, approved direction (mockups `workspace.html`, `step-in-place.html` in
`.superpowers/brainstorm/`), not yet built. Builds on Bench as merged in PR #119.

## Why

Bench today can show a finished run, but a computational chemist cannot use it to do the work. In the user's
words: there is no way to design a pipeline in the GUI, no way to load an active site into a pipeline, no way to
see which calculations produced which results, provenance is lost, the GUI does not show the pipeline, and the
steps are not explained. The current screens read as "notebook theater": pages of output with the method
hidden in a TOML box and a palette of stage names.

The fix is not more pages. It is one workspace where the pipeline is drawn, every step can be opened and
changed in place, every step explains itself and shows what it produced, and every number can be traced back to
the calculations and settings that made it.

## Who

A computational chemist at a pharma company, on their own workstation, who knows the chemistry and the
methods (RHF vs PBE, basis sets, pocket fields) but should not have to know smeltery's config keys or read
the code to set up, understand or audit a run. Bench chemists keep the existing read-only Report; nothing here
changes it.

## Goals

1. **The pipeline is drawn.** A run's pipeline, and a draft pipeline, appear as their steps in execution order
   (active site → ligands → paired poses → pose checks → ΔE_int → cut), each showing its state (ready,
   needs input, off, changed, result will change).
2. **Every step is edited in place,** in one panel with three parts:
   - **Inputs:** what flows in and which step it came from, with concrete values (for example "18 poses,
     largest 15 atoms / 59 basis functions", "2 charges, +1.0 e, 3.0 Å from atoms 0 and 2"), and what the
     step gives to the next one.
   - **Method:** the method choice where there is one (RHF or KS-DFT; generated conformers or docking) and
     every setting, each with a one-line plain explanation of what it changes. A changed value is
     highlighted, shows its previous value, and resets in one click.
   - **View:** an interactive visualisation of what the step produced (table below), linked across steps
     where it helps (click a pose in the ΔE_int plot, see that pose in 3D in the field).
   - **Impact:** before keeping an edit, what it does: wall time, memory per calculation against the budget,
     free memory against `require_free_gb`, which later results change, that it makes a new input digest,
     and every issue the validator reports. All of it computed by the code (tier `estimate_cost`,
     `estimate_memory`, `cli.describe_memory`, `Pipeline.check`), never written by hand.
3. **Nearly everything is changeable** through the steps or the Methods tab: candidates and parent, the
   active site (structure, reference ligand, cutoff, or hand-placed sites), poses and their knobs, gates,
   the ΔE_int method and its settings, the cut, the decisions file. What is fixed: the execution order and
   the ranked quantity (`dE_int`), because `smeltery run` fixes them.
4. **An active site loads into a pipeline.** Step 1 opens a protein (local `.pdb` or PDB id with network
   allowed), lists chains and HETATM groups, takes a reference ligand or centre and a cutoff, previews the
   pocket in 3D (residues as charges, cutoff sphere, reference ligand) with charge count, net charge and
   nearest charge to the ligand, and "use as active site" writes the `[structure]` section and turns on
   docking. Hand-placed charges (click two atoms on the parent) stay as the other choice.
5. **A Methods tab configures existing methods.** One entry per method in the catalog (RHF, KS-DFT, paired
   poses, docking, PoseBusters, MMFF strain, GFN2, the cut, the pocket field): what it computes, every
   setting with default and explanation, its cost and memory model and their limits (for example "memory is
   modelled for STO-3G only"), its systematic floor or "unmeasured", and which pipelines use it. Here the user
   sets workspace defaults for a method's settings.
6. **Prebuilt pipelines that work,** with their input files and a finished example run, so the user sees a
   correct result, its views and its provenance before changing anything. Starting from one makes the user's
   own copy; the prebuilt original is never changed.
7. **Click a number, see what made it.** Any ΔΔE (and any per-pose ΔE_int) opens its lineage: the per-pose
   values it averages and their differences, the settings of every step that fed it, the field, the engine
   build, the input digest, the config file, when and by whom it ran, and how long it took; with "open the
   pipeline that made this" and "change a setting and re-run".
8. **Night mode works.** Every new surface uses Bench's colour tokens (`ui/src/styles/tokens.css`) in both
   schemes; figures keep coming themed from Python (`?theme=`).

### What each step's view shows

| Step | View |
|---|---|
| 1 · Active site | Mol*: the pocket's charges, the reference ligand and the cutoff sphere; or the parent with hand-placed sites. Counts: charges, net charge, nearest charge to the ligand. |
| 2 · Ligands | 2D structures of parent and analogues, each validated (SMILES, formal charge, electron parity) with the shared core against the parent highlighted. |
| 3 · Paired poses | Mol*: a ligand's poses overlaid, core atoms (copied from the parent) distinct from substituent atoms (placed fresh); pick a ligand and a pose. |
| 4 · Pose checks | Per pose, pass or fail with the check's own reason (PoseBusters criteria, MMFF strain in kcal/mol). |
| 5 · ΔE_int | Per-pose ΔE_int for every ligand (the plot in `step-in-place.html`); clicking a pose shows that pose in 3D in the field and each ligand's value and difference from the parent; each calculation's SCF log one click away. |
| 6 · Cut | The existing forest plot and resolved matrix, unchanged. |

Before a draft has run, a view shows what can be computed without SCFs (poses, field, parity, gates' inputs:
what `smeltery run --plan` computes) and says plainly what needs a run.

## Non-goals

- New scientific figures. The forest plot, resolved matrix, per-pose pairs and pose viewer stay as built.
- Defining new methods or tiers from the GUI. The Methods tab configures methods that exist in code.
- Caching unchanged steps. Today a run recomputes every step; the impact strip says so ("a new run computes
  every step"). Caching is separate work.
- Multi-user, accounts, a deployed service. Still a local single-user app behind the existing ports.
- Changing the Report or the bench-chemist flow.

## Anti-goals (failure even if it technically works)

- **A second source of truth.** The config TOML stays the pipeline. Every form writes TOML through
  `smeltery.gui.pipeline.Pipeline`; the workspace always shows the config lines an edit writes. No pipeline
  state exists only in the browser or in a database.
- **A run that cannot be reproduced from its directory.** Workspace defaults (Methods tab) must never be a
  hidden input at run time.
- **Explanations, estimates or "fits" that the code did not compute.** Where a model cannot say (def2-SVP
  memory, an element outside the STO-3G table, Br at STO-3G), the UI says "cannot say", never a number or "fits".
- **Notebook theater again:** long pages of output with the method hidden. If a screen shows a result, the
  step that made it and its settings are one click away.
- **Breaking night mode,** or colour literals outside the tokens.

## Constraints

- Stack as built: React 19 + TypeScript (strict) in `ui/`, FastAPI in `smeltery.bench`, onion architecture
  (ports and adapters; `create_app` takes every port). Mol* + MolViewSpec, RDKit.js, Plotly with figures built
  in Python. API types generated (`npm run gen:api`); CI fails on drift.
- The CLI (`smeltery run`) is the executor and the validator. The GUI may not accept a pipeline the CLI
  rejects, and must show the CLI's own messages.
- Browser tests run offline against fixture runs; every feature is mutation-checked (break it, see the test
  fail) as in PR #119.
- `smeltery run` executes in a fixed order (`EXECUTION_ORDER`); steps are added and removed, never reordered.

## Decided how

- **Layout** (mockup `workspace.html`): the pipeline drawn across the top of the run (or draft) page; the
  selected step's panel below it with Inputs | Method | View columns and the impact strip, then the config
  lines it writes. This replaces today's Pipeline tab (palette + stage list + knob form + TOML pane); the
  TOML pane stays available, read and write, beside the steps.
- **Step state** (shown on each step in the drawing): ready; needs input (with what); off (optional step not
  placed, "add"); changed (draft edits not yet kept); result will change (a later step whose output an
  earlier edit invalidates).
- **Edits are drafts** until "Keep"; "Discard" restores the step. Keeping writes the config. The source run is
  never touched; running the draft makes a new run directory.
- **Methods tab defaults are copied, not referenced** (my call). Setting a workspace default for a method
  changes the value a step gets when that method is added to a pipeline, and the step then holds the value
  explicitly in its config. A config never refers to workspace defaults, so a run stays reproducible from its
  directory. Defaults live as a TOML file in the workspace (`ROOT/.smeltery/bench/methods.toml`, my call),
  visible and editable as text like everything else. A step whose value differs from the workspace default
  says so.
- **Prebuilt pipelines** ship in the repository, one folder each, holding the config, every input file the
  config reads, and a finished example run (run record, pose SDFs, logs). Initial set, from the configs that
  work today: `benzoic` (hand-placed charges; RHF and a PBE variant), `docking` (Vina into the committed
  pentapeptide pocket, PoseBusters and MMFF gates), `structure` (start from a target structure, then dock).
  The example runs are produced by running each config and committed; viewing them needs no extras, re-running
  `docking` and `structure` needs the `docking` and `posebusters` extras and `pdb2pqr30` (stated on the card).
  "Start from this" copies the folder into the workspace; the copy is the user's.
- **Provenance fixes** the lineage view depends on, each recorded in the run record:
  - the exact config text and its sha256, written into every run directory by `smeltery run` itself (today
    only Bench's Launch writes `config.toml`, and the record keeps pocket charges as positions, so
    "open the pipeline that made this" cannot recover hand-placed sites);
  - the smeltery source commit and whether the tree was dirty, read at run time. Today `smeltery_version` is
    `importlib.metadata`'s version, which for an editable install is the version stamped when it was built
    (a run on 2026-11-04 recorded `0.0.2.dev307+g37fb504`, the 2026-10-29 install);
  - wall time per step and per SCF calculation, and each calculation's log location.
- **Lineage view** opens from any ΔΔE or per-pose ΔE_int, as an inspector beside the result.

## Settled after review (my calls; the user approved the two above and the direction)

1. **A draft may be saved while it does not pass `check()`.** "Needs input" is a real state: switching step 1
   from hand-placed charges to a structure also needs docking added, and that takes more than one edit. Keep
   writes the config whatever its issues; only **Run** is blocked while there is an error, and each issue is
   shown on the step it belongs to, in the CLI's own words. "The GUI may not accept a pipeline the CLI rejects"
   means it may not *run* one.
2. **Edits preserve the user's TOML text.** Comments, ordering and layout survive a Keep (the prebuilt configs
   explain themselves in comments). Edits are surgical (a format-preserving TOML writer such as tomlkit, in
   place of regenerating through `smeltery.gui._toml`); the "config lines an edit writes" view is the real diff
   of the file.
3. **A run copies its inputs.** `smeltery run` does what Bench's Launch already does: every input file the
   config reads (structure, pocket, receptor, decisions file) is copied into the run directory under its
   sha256, and the config written there refers to those copies, so a run directory re-runs on its own after
   the originals move. "Start from this" and drafts resolve paths the same way.
4. **Previews never run docking or SCFs on an edit.** The impact strip and step views compute live only what
   is cheap and offline (validation, parity, cost and memory from existing poses, hand-placed fields). Where an
   estimate needs poses:
   - if the steps up to poses are unchanged from the source run (same config sections), the source run's
     poses are used, and the strip says so;
   - otherwise the strip says "cannot say until poses exist" and offers **Preview poses**, which runs
     `smeltery run --plan` as a task through the task queue and fills the estimates when it finishes.
5. **Pose checks record every pose.** `smeltery run` evaluates every gate on every pose and records each
   pose's result (PoseBusters criteria, MMFF strain) before it fails the stage, so step 4 can show what failed
   and why. Failing still exits 3 and names the stage, as today.
6. **Per-calculation logs come from smeltery, not ferric.** ferric is out of scope. smeltery records, for every
   SCF, what the result returns (converged, iterations, energy, wall time) as a per-calculation log in the run
   directory; "SCF log" in the views opens that.
7. **Step 1 has three active-site choices:** from a protein structure (`[structure]`), from a prepared pocket
   file with a receptor and box (`[pocket] file` with `[docking]`, the `docking` prebuilt), or hand-placed
   charges. All three are editable.
8. **Order of delivery:** provenance first (config and inputs saved with each run, source commit,
   per-step and per-calculation timings and logs, per-pose gate results), then the lineage inspector on the
   results that already exist, then the drawn pipeline with in-place step panels, then the step views, then
   the Methods tab, then the prebuilt pipelines with their committed runs.

## Left to the builder

- Component structure, API shape and routes for step previews, per-step views and the Methods tab, within the
  constraints above.
- How a draft pipeline that has never run is stored between sessions (it must be a config file in the
  workspace, since the config is the pipeline; where and how named is the builder's call).
- The exact wording of each setting's explanation, provided it is sourced from the method's code or docs
  (`docs/tiers/*.md`, catalog `help`) rather than written in the UI, so it cannot drift.
- Whether step 4 (checks) is one step or one per gate in the drawing.
- Interaction details of the views (selection, hover, keyboard), meeting the existing accessibility checks.

## Open questions

None blocking; the reviewer's eight are settled above. The prebuilt set may grow (for example a PocketXMol or danuglipron campaign) once each one
has a config that runs end to end and a result worth committing.
