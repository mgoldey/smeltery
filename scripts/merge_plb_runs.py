"""Merge the cache directories of several `run_plb_ddE.py run` invocations into one directory for `assemble`.

    python scripts/merge_plb_runs.py OUT RUN_A RUN_B [...]

A run rewrites its meta.json with only its own models, so stages that ran in separate directories (docs/benchmarks/
plb_pcm_plan.md, amendment A1) are merged here: tasks.jsonl and commands.log are concatenated, the models' provenance
is united, and every other input (target, poses, seed, jitter, basis, cutoff, pose geometry digests, ...) must be
identical across the runs or the merge refuses (the commit hash may differ and is recorded for every run).
No energy is edited.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PER_MODEL = ("field_eps", "field_solvent", "field_provenance", "prep_provenance")
IGNORED = ("max_cpu_hours_last_invocation", "models", "smeltery_commit", *PER_MODEL)


def merge_meta(metas: list[dict]) -> dict:
    first = metas[0]
    for i, m in enumerate(metas[1:], 1):
        for k in sorted((set(first) | set(m)) - set(IGNORED)):
            if first.get(k) != m.get(k):
                raise SystemExit(f"run {i} differs from run 0 in {k!r}; refusing to merge")
    out = {k: v for k, v in first.items() if k not in IGNORED}
    # the commit may differ between stages (later commits can add tests or docs); all are recorded, the first is the key
    out["smeltery_commit"] = first.get("smeltery_commit")
    out["smeltery_commits_by_run"] = [m.get("smeltery_commit") for m in metas]
    out["models"] = []
    for k in PER_MODEL:
        out[k] = {}
    for m in metas:
        for name in m["models"]:
            if name in out["models"]:
                raise SystemExit(f"model {name!r} appears in two runs; refusing to merge")
            out["models"].append(name)
        for k in PER_MODEL:
            out[k].update(m.get(k, {}))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("runs", type=Path, nargs="+")
    a = ap.parse_args(argv)
    if a.out.exists():
        raise SystemExit(f"{a.out} exists; refusing to overwrite")
    metas = [json.loads((r / "meta.json").read_text()) for r in a.runs]
    meta = merge_meta(metas)
    a.out.mkdir(parents=True)
    (a.out / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
    (a.out / "tasks.jsonl").write_text("".join((r / "tasks.jsonl").read_text() for r in a.runs))
    (a.out / "commands.log").write_text("".join((r / "commands.log").read_text() for r in a.runs))
    print(f"merged {len(a.runs)} runs into {a.out}: models {meta['models']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
