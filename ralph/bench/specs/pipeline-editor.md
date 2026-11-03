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
