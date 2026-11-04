# Pipeline editor

**A pipeline is edited visually while its TOML stays the only state.**

## Requirements

1. The palette is `smeltery.gui.catalog.hierarchy()`, grouped by rung. Forms are generated from
   typed `Param` knobs.
2. `POST /api/pipeline/check {"toml"}` returns every `Issue` (`level`, `stage`, `message`) from
   `Pipeline.check()`, not just the first. Errors block launching.
3. Unwired tiers (`wired=False`) are listed with their reason and are absent from
   `GET /api/pipeline/addable`.
4. Rigour is never drawn as one ladder across quantities: `Method.comparable_rigour` is False
   across quantities, and the UI groups by quantity.
5. A TOML pane and the visual editor are two views of one `Pipeline`. Every edit round-trips
   through `Pipeline.from_toml` / `to_toml`.
6. `POST /api/pipeline/site {"toml","atom","from_atom","q","distance"}` applies `place_site`
   (`click-to-place.md`) and returns the new TOML.
7. Launching writes the config into `OUT/config.toml`, staging every referenced input file into
   `OUT` by sha256 through `ArtifactStore` and rewriting its `path` knob, so relative paths resolve
   inside OUT. The task argv is `["smeltery","run","<OUT>/config.toml","--out","<OUT>"]`.

## In the app (Run → Pipeline)
8. The palette lists methods by rung. Unwired methods are shown disabled with their reason.
9. Stages can be added and removed with mouse and keyboard alone. They are listed in the order `smeltery run`
   executes them (`EXECUTION_ORDER`), which is fixed; the TOML has nowhere to hold another order, so there is no
   reorder control (one would silently snap back on the next edit).
10. Each stage's knobs are a form generated from its `Param` kinds.
11. An issues panel shows every issue from `/api/pipeline/check` and updates on each edit.
12. A TOML pane (CodeMirror) and the visual editor stay in sync both ways: an edit in either
    updates the other through the API's round trip.
13. **Click-to-place.** The parent is drawn in 2D by RDKit.js with explicit hydrogens. Each atom is
    an SVG element with `data-atom-index` equal to its index in `Chem.AddHs` order. Clicking an
    atom and then its `from_atom` opens a small form for `q` and `distance`; confirming posts to
    `/api/pipeline/site`, and the TOML pane shows the new `[[pocket.site]]`. A 3D view of the
    provisional conformer, labelled "provisional geometry", highlights the same two atoms.
14. "Run pipeline" is disabled while any error-level issue exists; once clicked, it shows
    "Pipeline running" and the task appears in the work panel.
