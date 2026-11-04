# Bench loop notes

Things a future iteration needs and cannot get from the code or `git log`. One line each, newest
last. Not a diary: record a milestone result once.

- Full suite after the backend tiers: 1789 passed, 45 skipped, 0 failed.
- K5 split RSS (cheapest-tier fixture): ~144 MB flat across 10 processes. This is not yet evidence for
  a real DFT run.
- 2026-11-04: the UI tier was rewritten. The previous U*/V* features passed by matching strings in
  hand-built HTML; no page loaded Mol*, RDKit.js or a plot. Every UI feature now runs in a browser
  (ui/e2e). A previous iteration also flipped 23 features in one commit before their code existed:
  flip only the feature you just verified.
- 2026-11-04: R11, G1, R12 and R13 are reset or new because review found them false: an unformatted
  test file, CI jobs that split an existing job, and a property hard-coded to True.
