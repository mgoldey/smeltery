# Troubleshooting

The failures a user is most likely to hit, with the message as the code prints it (or its stable
prefix; `...` stands for names and numbers that vary), what it means, and the fix. Grouped by
where it happens: [install](#install-and-environment), [config](#config-exit-2),
[run](#run-exit-3-and-4) and [Bench](#bench).

`smeltery run` exit codes: 0 success (including `UNRANKED`), 2 a bad config, 3 a stage failed
(the message names the stage), 4 `UNRANKED` under `--fail-on-unranked`. Config errors print as
`config error: ...`, stage failures as `error: ...`, both on stderr. For `--temporal` see
[temporal-runs.md](temporal-runs.md#exit-codes).

## Install and environment

### `uv sync` or `uv run` is compiling ferric

Something is pointing `[tool.uv.sources]` at a git commit or a local path. That setup is only for
developing against unreleased ferric, and it needs Rust, libint2 and OpenBLAS (see ferric's
README). Remove it to get the wheel.

### Not on Linux x86_64

ferric publishes no wheels for other platforms yet, so you would have to build it from source as
above. `scripts/dev-check.sh` runs the tests against a labelled ferric stub instead. Tests marked
`needs_ferric` are skipped, not failed. It is a smoke check and does not replace CI.

### Slow or thrashing SCFs

Set `OPENBLAS_NUM_THREADS=1`. ferric parallelizes with Rayon, and BLAS threads on top of that
oversubscribe the machine. See the measured costs in [limits.md](limits.md).

### A missing optional extra

```text
install the docking extra: `pip install 'smeltery[docking]'` (or `uv sync --extra docking`). ...
install the posebusters extra: `pip install 'smeltery[posebusters]'` (or `uv sync --extra posebusters`)
reading ... needs the `...` package, which is not installed. Install it with:  pip install 'smeltery[...]'
smeltery gui needs the gui extra: pip install 'smeltery[gui]'
smeltery gui --desktop needs pywebview: pip install 'smeltery[desktop]'
config error: --temporal needs temporalio (the `temporal` extra)
```

The feature you configured imports its backend only when it is used. Install the named extra, for
example `uv run --extra docking --extra posebusters smeltery run ...`. Vina ships wheels for
CPython 3.8 to 3.12 only; on 3.13 or later it builds from source and needs Boost. Boltz cannot be
installed as an extra (it conflicts with ferric's gemmi); see [boltz.md](boltz.md).

### pdb2pqr30 is missing

```text
<file>.pdb is a PDB and needs a working `pdb2pqr30` to assign charges: `pdb2pqr30` is not on PATH; install it (pip install pdb2pqr) or pass a pre-made .pqr
```

A `.pdb` pocket (or a `[structure]` section) is charged by `pdb2pqr30`. Install it with
`pip install pdb2pqr`, or point `[pocket] file` at a `.pqr`. If the text says it "is on PATH but
`--version` fails", the script is a broken shim: reinstall it. If a digest differs between
machines, first check that `pdb2pqr30` is installed on both ([glossary.md](glossary.md)).

## Config (exit 2)

### The Bench UI is not built (`smeltery bench` exits 2)

```text
the Bench UI is not built: run `cd ui && npm ci && npm run build`
```

A source checkout has no built UI. Run the command it prints, then `smeltery bench` again. See
[bench.md](bench.md#starting-it).

### `smeltery gui` cannot pick a run

```text
smeltery gui: ...: need exactly one run-*.json, found ...; pass the one you mean
```

The directory holds none or several run records. Point `smeltery gui` at the `--out` directory of
a single run, or at that run's `run-*.json` file directly. `smeltery verify-run` and `inspect` say
`error: ...: expected exactly one run-*.json, found N` for the same reason.

### `--out` already holds a run

```text
config error: out already holds the run record run-....json; use another --out, or --force to rerun
```

`smeltery run` will not overwrite a finished run. Use a new `--out`, or pass `--force` to
overwrite. Bench says `... already holds the run record ...; pick another out folder` for the
same refusal. See [golden-path.md](golden-path.md#3-run).

### Unknown or missing config keys

```text
config error: unknown key(s) [...] in ...; allowed: [...]
config error: missing required key '...'
config error: parent '...' is not listed in [candidates]
config error: candidate '...': unparseable SMILES '...'
```

The config is strict: an unknown key is an error, not ignored, so a typo cannot silently change a
run. The message lists the keys that are allowed. The parent must be one of the `[candidates]`,
and every SMILES must parse. Check the shipped examples under `examples/`, or let
`smeltery golden init` write a valid starting file ([golden-path.md](golden-path.md#1-scaffold)).
`smeltery run myrun.toml --plan` finds these in seconds, before any SCF.

### Two sections that cannot be combined

```text
config error: [pocket] cannot be combined with [structure]: the field comes from [structure]
config error: [...] gives the parent's poses; so does docking: keep one
config error: [...] and [...] are each a ...; keep one
```

Some sections fill the same slot (the field, the parent's poses, how dE_int is measured). Keep
one. For the field, `[pocket]` is replaced by `[structure]`; the two measurement tiers
(`[field_interaction]` and `[field_dft_interaction]`) are alternatives too.

### The floor is unmeasured

```text
config error: refusing to cut on 'dE_int': tier '...' has not measured its systematic floor (systematic_floor() is None). ... Set [cut] floor explicitly to proceed, knowing it is not a measured floor.
```

Both measurement tiers have an unmeasured floor today, so the config must state `[cut] floor` in
kcal/mol; the run record then says the floor came from the config. Also `[cut] keep must be an
integer in 1..N (the number of analogues)` when `keep` is out of range. See
[glossary.md](glossary.md) (floor, tie group) and [limits.md](limits.md).

### A path in the config does not exist

```text
config error: [pocket] file '...' does not exist
config error: [docking] receptor '...' does not exist
config error: [decisions] file '...' does not exist
```

Relative paths resolve against the folder holding the config file. Fix the path, or copy the
file next to the config.

## Run (exit 3 and 4)

### A pair has no common core

```text
error: stage '...' failed: NoCommonCoreError: ...: no common core with the parent
error: ... MCS search timed out after mcs_timeout_s=... s; an unfinished core is not a core. ...
```

The MCS gate refuses an analogue it cannot align on the parent, rather than build a degraded
pairing. Check the SMILES for a scaffold change that leaves too little in common, or raise
`[poses] mcs_timeout_s` (default 10 s) for drug-sized molecules. See
[glossary.md](glossary.md) and [limits.md](limits.md).

### An odd electron count

```text
error: stage 'parity' failed: ... has N electrons at charge ...; the field-interaction tier is closed-shell RHF and is undefined for an odd electron count
```

Both measurement tiers are closed-shell (RHF, or restricted KS-DFT). Fix the candidate's formal
charge or remove the radical; a salt with a counter-ion needs the SMILES of the species you mean.

### The SCF did not converge

```text
error: stage '...' failed: RuntimeError: ...: SCF did not converge
```

The run stops loudly instead of recording a non-measurement, and keeps the SCFs it did run in
`calcs.jsonl`. Look at which pose failed with `smeltery inspect`, and at the geometry (a clash
or a bad pose is the usual cause). Under `--temporal` a non-converged leg is a final outcome and
timeouts are retried by running the same command again
([temporal-runs.md](temporal-runs.md#failed-scfs-pairwise-drops)).

### The run would not fit in memory

```text
... needs ~N GB resident (AO cache ... + RI tensor ...), over the ... GB budget. ferric would spill the RI tensor to disk ...
... GB available, below the ... GB this run requires. A DFT process that outgrows free memory does not slow down gracefully ...
```

The KS-DFT tier refuses before its first SCF. The first message is the fixed `memory_budget_gb`:
use a smaller basis, or raise it (and `FERRIC_MEM_BUDGET_GB`) if the machine really has the
memory; `check_memory = false` runs anyway. The second is `require_free_gb`, checked against the
memory actually free: free some and retry, or lower it. A long run has been measured holding
~11 GB against a ~2 GB prediction, so leave headroom. `smeltery run --plan` prints
`memory (...): ... the run will refuse before its first SCF` ahead of time.

### A gate rejected a pose

```text
error: stage 'posebusters' failed: ...
error: stage 'forcefield' failed: ... have MMFF94 strain ... kcal/mol > the configured max_strain_kcal ...
config error: [gates] ... must be true or false
```

PoseBusters, or the MMFF94 strain limit `max_strain_kcal`, rejected poses before the SCFs (the xtb
record only records an energy and rejects nothing; its own failure reads `stage 'xtb' failed: ...`). A rejected
gate still leaves every pose's result in `gates.json` in `--out`; read it to see which pose failed
which check. `[poses] relax_unmapped = true` relaxes only the new substituent, which otherwise
fails PoseBusters at its junction with the copied core ([README](../README.md)).

### Docked poses fall outside the search box

```text
error: stage 'docking' failed: N of M docked pose(s) have their centroid outside the search box; the receptor and the box are not in one frame
```

The receptor and the box centre are in different frames. State `center` (or a `reference_ligand`)
in the same frame as the receptor.

### A run reports `UNRANKED`

`smeltery run` exits 0 even when it prints `UNRANKED`: no candidate could be ordered because every
pair is within the other's uncertainty or the floor. That is a result, not an error; read it in
[glossary.md](glossary.md) (tie group, floor). With `--fail-on-unranked` it exits 4 instead
(in-process runs and `--combine`, not `--temporal`).

### `verify-run` reports a mismatch

```text
MISMATCH: record_sha256 differs: the record was edited after it was written
MISMATCH: poses[...]: ... sha256 differs from the record
MISMATCH: poses[...]: ... is missing
```

`verify-run` exits 1 when the run record or a file it hashes no longer matches what was written.
It catches accidental edits only. Restore the file from the original run, or rerun; do not edit
a record by hand. A record without the self-hash prints a warning, not a mismatch. See
[golden-path.md](golden-path.md#provenance).

### A Temporal run refuses to start or finish

```text
a Temporal run (workflow ...) is still running in ...; re-staging its config would change what its activities read. Wait for it, terminate it (...), or use another --out
... holds a Temporal run (workflow ...) and its server cannot be asked whether it is still running (...); start the server, or remove ... if you know the run is over
calcs.jsonl was written by N different builds (...): its SCFs are not one code's measurement. Re-measure with one build (a fresh --out), or set [run] allow_mixed_builds = true to record the run as mixed
```

Re-running with the same staged config attaches to the live workflow; a changed config is refused.
Start the Temporal server to let smeltery check, or use another `--out`. Rows measured by
different ferric or smeltery builds are not one measurement: rerun with one build, or accept the
mix with `[run] allow_mixed_builds = true`, which records every build in `results.builds`. See
[temporal-runs.md](temporal-runs.md).

## Bench

### "Cannot reach the Bench server"

```text
Cannot reach the Bench server. Is `smeltery bench` still running?
```

The browser lost the process that serves the Bench (it exited, or the host or port changed).
Start `smeltery bench <root> --open` again; run records live on disk, so nothing is lost. If the
port is taken, pass `--port`. See [bench.md](bench.md#starting-it).

### `database is locked`

Older Bench builds could surface this when several requests hit the sqlite files at once. The
adapters now use WAL, a 30 s busy timeout and always close their connections, so it should no
longer appear. If you still see it, another process is holding the Bench's `.sqlite` files: stop
the second `smeltery bench` on the same root.

### 409 `run is INCOMPLETE`

```text
run is INCOMPLETE: missing ...
```

The run directory has a record but lacks something the Bench needs (a finished run record, its
poses, a result). It is still running, was cut short, or was copied without all its files. Wait
for it, or rerun. See [Verdict states](bench.md#verdict-states). `unreadable run record` and
`not a run record` (also 409) mean the `run-*.json` itself is damaged.

### 413 on an upload

```text
...: larger than 64 MB
bundle larger than ... MB
the bundle expands to more than its declared size
```

Structure and ligand uploads are capped at 64 MB, and bundles at their own cap, checked from the
declared length first so the server is not made to buffer them. Upload a trimmed structure (one
chain, the pocket), or a smaller file.

### A bundle will not import

```text
not a zip file
a bundle holds exactly one top folder: <name>/config.toml, ...
<name>/manifest.json is missing: only bundles from Download bundle can be imported
sha256 does not match manifest.json
```

Only a zip made by the Bench's Download bundle can be imported: one top folder holding
`config.toml` and a `manifest.json` whose hashes match every file. A file that was edited after
download, or an archive repacked by hand, is refused file by file with the reason. Download the
bundle again. A name clash answers `pipeline ... already exists`; import with `?name=` or rename
the existing one. See [bench.md](bench.md).

### A launch is refused

```text
... already holds the run record ...; pick another out folder
a Temporal run (workflow ...) is still running in ...
```

Nothing was written. These are the same refusals as `smeltery run`: pick another out folder, or
wait for the live run. See above.
