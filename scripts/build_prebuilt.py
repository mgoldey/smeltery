"""Regenerate the example runs under src/smeltery/prebuilt/<name>/run.

    .venv/bin/python scripts/build_prebuilt.py [name ...] [--force]

Each run is `smeltery run <name>/pipeline.toml --out <name>/run`, so it re-runs from its own folder.
An existing run is never overwritten unless --force. `docking` and `structure` need the docking and
posebusters extras and pdb2pqr30 on PATH; the benzoic pipelines need only the base install.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PREBUILT = Path(__file__).resolve().parent.parent / "src" / "smeltery" / "prebuilt"
NEEDS = {
    "benzoic-rhf": "the base install (RHF: ~219 s on 2 cores, measured 2026-11-04)",
    "benzoic-pbe": "the base install (PBE: 88 s on 2 cores, measured 2026-11-04)",
    "docking": "the docking and posebusters extras and pdb2pqr30 on PATH",
    "structure": "the docking and posebusters extras and pdb2pqr30 on PATH",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help=f"subset of {', '.join(NEEDS)} (default: all)")
    ap.add_argument("--force", action="store_true", help="replace an existing run/ directory")
    args = ap.parse_args(argv)
    names = args.names or list(NEEDS)
    unknown = [n for n in names if n not in NEEDS]
    if unknown:
        ap.error(f"unknown pipeline(s): {', '.join(unknown)}")
    existing = [n for n in names if (PREBUILT / n / "run").exists()]
    if existing and not args.force:
        print(f"refusing to overwrite existing run(s): {', '.join(existing)} (use --force)", file=sys.stderr)
        return 2
    for name in names:
        folder = PREBUILT / name
        print(f"== {name}: needs {NEEDS[name]}", flush=True)
        tmp = folder / "run.new"
        shutil.rmtree(tmp, ignore_errors=True)
        cmd = [sys.executable, "-m", "smeltery", "run", str(folder / "pipeline.toml"), "--out", str(tmp)]
        if subprocess.run(cmd, check=False).returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            print(f"{name}: run failed; any existing run/ is kept", file=sys.stderr)
            return 1
        shutil.rmtree(folder / "run", ignore_errors=True)
        tmp.rename(folder / "run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
