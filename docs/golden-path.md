# Golden path: from target to a decision-ready, shareable run

One page for an end user or an ELN integration. Every step is one command.

## 1. Scaffold

```sh
smeltery golden init --out myrun.toml --parent "lead" --parent-smiles "..."
```

Edit `myrun.toml`: add analogue SMILES under `[candidates]`, and (optionally) a pocket file / structure spec.

## 2. Plan first (no SCF, seconds)

```sh
smeltery run myrun.toml --plan
```

Checks poses, pocket, parity, digest. Fix config errors (exit 2) here.

## 3. Run

```sh
OPENBLAS_NUM_THREADS=1 smeltery run myrun.toml --out out/myrun
```

Writes `out/myrun/run-<digest>.json`, pose SDFs, receptor, and prints the cut (survivors / tie groups / UNRANKED).

## 4. Inspect

```sh
smeltery golden open out/myrun
```

Opens the GUI (results, grid, pose, pipeline) and Avogadro 2 on the receptor + first pose. Or individually:

```sh
smeltery gui out/myrun
smeltery avogadro out/myrun
smeltery report out/myrun -o out/myrun/report.html
```

## 5. Decide

In the GUI **Grid** tab: tick candidates, set `keep`/`reject`/`hold` with a reason, **Record decision**.
This writes `out/myrun/decisions-<digest>.json` (separate from the run record).

## 6. Iterate

Add to the next config:

```toml
[decisions]
file = "decisions-<digest>.json"
```

Rejected candidates are dropped before posing; the new record lists them under `results.decisions_in.excluded`.

## 7. Share / ELN

```sh
smeltery golden bundle out/myrun -o myrun-bundle.zip
```

The zip contains the run record, poses, receptor, decisions, and `report.html` (generate it first with `smeltery report`).

## Long / interrupted work

Use Temporal instead of a bare `smeltery run`:

```sh
smeltery task-worker &                      # one shell
smeltery dashboard out/ --port 8123         # centralized view + task submission
```

Submit `smeltery run myrun.toml --out out/myrun` from the dashboard; Temporal handles retries, timeouts and resume.

## Provenance

Every run record carries the ferric commit, settings, input digest, and (when used) the decisions file sha256. `out/` is the unit of sharing: copy it, bundle it, or point an ELN at it.
