# Per-candidate run split

**One campaign runs as one process per candidate and produces the same record as one process.**

## Why
A long-lived worker held 8.4-13.1 GB against a 5.6 GB prediction, swapped 32 GB, and turned one
memory problem into 67 heartbeat timeouts. One process per candidate resets resident memory.

## Requirements

1. `smeltery run CONFIG --out OUT --only-candidate NAME` measures candidate NAME only and writes
   `OUT/partial-<NAME>.json`. It writes no cut and no `run-*.json`.
2. **The parent is measured once.** `smeltery run CONFIG --out OUT --only-candidate <parent>`
   writes the parent's partial. A non-parent invocation does not re-measure the parent; it pairs
   against the poses of the parent partial. N analogues cost N + 1 measurements, not 2N.
3. `smeltery run CONFIG --out OUT --combine` checks that a partial exists for every candidate in
   the config (parent included), then runs pairing and the cut once and writes the normal record
   through `write_outputs`.
4. Combine carries **every** per-pose quantity present in the partials (`dE_int` and also
   `E_mmff`, `E_mmff_relaxed`, `E_gfn2` when recorded), not just `dE_int`.
5. **The combined record is identical to the single-process record** for the same config, apart
   from timing fields listed explicitly in the test, and `input_digest` is unchanged.
6. Every refusal prints what is wrong to stderr and exits 2: an unknown candidate name, and each
   missing partial named. No bare `SystemExit(2)`.
7. Resident memory per process stays roughly flat across a 10-candidate split run.

## Data source for checks
The fixture configs under `tests/`, run on the cheapest tier they allow. Peak RSS is read per
process (`/usr/bin/time -v` or `psutil`), not estimated.
