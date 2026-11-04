#!/usr/bin/env python
"""Vendor Bench browser assets (offline).

Downloads the pinned Mol*, RDKit.js and font files into
``src/smeltery/bench/static/vendor/`` and writes ``vendor/README.md`` recording
each URL, version and sha256. Run once by a maintainer; the result is committed.
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "smeltery" / "bench" / "static" / "vendor"

# (url, version, destination filename)
ASSETS = [
    ("https://cdn.jsdelivr.net/npm/molstar@5.13.1/build/viewer/molstar.js", "5.13.1", "molstar.js"),
    ("https://cdn.jsdelivr.net/npm/molstar@5.13.1/build/viewer/molstar.css", "5.13.1", "molstar.css"),
    ("https://cdn.jsdelivr.net/npm/@rdkit/rdkit@2026.9.1/dist/RDKit_minimal.js", "2026.9.1", "RDKit_minimal.js"),
    ("https://cdn.jsdelivr.net/npm/@rdkit/rdkit@2026.9.1/dist/RDKit_minimal.wasm", "2026.9.1", "RDKit_minimal.wasm"),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    rows = []
    for url, version, name in ASSETS:
        dest = ROOT / name
        print(f"downloading {url} -> {dest}")
        urllib.request.urlretrieve(url, dest)
        rows.append((name, url, version, sha256_file(dest)))
    lines = ["# Vendored assets", "", "| File | URL | Version | sha256 |", "|---|---|---|---|"]
    for name, url, version, digest in rows:
        lines.append(f"| {name} | {url} | {version} | {digest} |")
    lines.append("")
    (ROOT / "README.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()