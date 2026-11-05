"""Regenerate src/smeltery/prebuilt/danuglipron-halogen/imported/ from the 2026-10-16 record.

    .venv/bin/python scripts/import_danuglipron.py

Reads benchmarks/danuglipron_halogen/results/danuglipron-halogen-38fa833cb90c.json (never modified); replaces the
imported/ folder with the byte-identical original and the converted run record.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from smeltery.bench.importers import import_run

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = ROOT / "benchmarks" / "danuglipron_halogen" / "results" / "danuglipron-halogen-38fa833cb90c.json"
OUT = ROOT / "src" / "smeltery" / "prebuilt" / "danuglipron-halogen" / "imported"


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    print(import_run(ORIGINAL, OUT))


if __name__ == "__main__":
    main()
