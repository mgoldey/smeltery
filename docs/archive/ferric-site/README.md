# Archived ferric site pages

Moved from `mgoldey/ferric` `site/src/` at ferric commit `922a3b9b6203e10fedbb4e143ac24f0352e55230` (issue #13). They document the
Python pipeline that used to live in ferric's `tools/` directory (now removed from ferric) and the
danuglipron campaign notes written while it was built. They are kept verbatim as a record:

- They describe `tools.*`, which is **not** `smeltery.*` (see `experiments/README.md` for how the two
  differ). Code samples in them do not run against smeltery.
- Numbers in them are labelled MEASURED or ESTIMATED by their authors; this move did not re-measure anything.
- Links to other ferric pages, and to `tools/` paths, point into ferric and may be dead.

| file | what it was |
|---|---|
| `pipeline-golden-path.md` | formats to docking to xtb to DFT walkthrough and notebook |
| `pharma-use-case-coverage.md` | which pharma use cases the pipeline covered |
| `substitution-pipeline.md`, `substitution-pipeline-resolved.md` | proposing substitutions at an active site, on danuglipron |
| `toxicity.md` | `python -m tools.tox`, the liability screening CLI |
