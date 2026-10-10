"""Fetch the PLB structure files that are NOT committed, verifying sha256 against the manifest.

    SMELTERY_NETWORK=1 uv run python scripts/fetch_plb.py DEST [--targets cdk2 ...] [--kinds protein_pdb ...]

Files land in DEST/<target>/. A digest mismatch aborts and leaves nothing at that path. The download is opt-in:
without SMELTERY_NETWORK=1 the script refuses to touch the network. The files are CC BY 4.0 (Open Forcefield
Group); keep the attribution in benchmarks/plb/manifest.json with any redistribution.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from smeltery.benchmark import DEFAULT_MANIFEST, BenchmarkError, fetch_files, load_benchmark


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest", type=Path)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--targets", nargs="*", default=None)
    ap.add_argument("--kinds", nargs="*", default=["protein_pdb", "ligands_sdf"])
    args = ap.parse_args(argv)
    if os.environ.get("SMELTERY_NETWORK") != "1":
        print("refusing to use the network: set SMELTERY_NETWORK=1 to opt in", file=sys.stderr)
        return 2
    try:
        paths = fetch_files(load_benchmark(args.manifest), args.dest, args.targets, args.kinds)
    except BenchmarkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
