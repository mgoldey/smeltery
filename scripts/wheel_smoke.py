#!/usr/bin/env python3
"""Smoke-test a BUILT smeltery wheel, with and without each of its extras (issue #31).

    python scripts/wheel_smoke.py list-extras dist/smeltery-*.whl      # JSON list for a CI matrix
    python scripts/wheel_smoke.py check dist/smeltery-*.whl --extra docking [--resolve-only]
    python scripts/wheel_smoke.py check dist/smeltery-*.whl --extra none --example examples/mwe_benzoic.py

`check` installs the wheel (plus the one extra) with plain pip into a FRESH venv, so what runs is the installed
package, not ./src, and then:

* imports smeltery and fails if it came from anywhere but site-packages;
* imports what the extra promises (`EXTRA_CHECKS`): the third-party modules and the smeltery modules built on them;
* runs the installed `smeltery` console script on a config in `--plan` mode (no SCF);
* with `--example`, first requires ferric in the venv to have come from the package index (no `direct_url.json`),
  then runs the example in a scratch directory and requires its run record to report a VERIFIED ferric
  provenance (what a user who `pip install`s gets).

An extra with no entry in `EXTRA_CHECKS` is an error: add the entry when you add the extra. `--resolve-only`
(for extras too heavy to install on every run) only proves that pip can resolve them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

#: extra -> python snippets that must run in the fresh venv. "none" is the wheel with no extras.
EXTRA_CHECKS: dict[str, list[str]] = {
    "none": [],
    "gemmi": ["import gemmi"],
    "rdkit": ["import rdkit"],
    "posebusters": ["import posebusters, rdkit", "from smeltery.gates import posebusters_check"],
    "docking": [
        "import vina, meeko, rdkit, scipy, gemmi",
        "from smeltery.docking import VinaProvider, Docking, Box",
    ],
    "experiments": ["import psutil, scipy"],
}
#: extras that exist only for development; never part of a user's install.
SKIP_EXTRAS = {"dev"}


def wheel_extras(wheel: Path) -> list[str]:
    """`Provides-Extra` of the wheel's own METADATA, minus the development-only extras."""
    with zipfile.ZipFile(wheel) as z:
        name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        text = z.read(name).decode()
    extras = [line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("Provides-Extra:")]
    return sorted(e for e in extras if e not in SKIP_EXTRAS)


#: How a record can honestly verify an index-installed ferric: the engine's own build stamp (`ferric.__build__`,
#: which current PyPI wheels carry) or, failing that, the immutable index release version.
VERIFIED_SOURCES = ("ferric.__build__", "package index")


def verified_provenance(record: dict) -> str | None:
    """None if the record's ferric identity is VERIFIED by a mechanism valid for an index install; else why not."""
    ferric = record.get("ferric") or {}
    if ferric.get("source") not in VERIFIED_SOURCES:
        return f"ferric source is {ferric.get('source')!r}, expected one of {VERIFIED_SOURCES}"
    if not str(ferric.get("provenance", "")).startswith("VERIFIED"):
        return f"ferric provenance is {ferric.get('provenance')!r}, expected it to start with VERIFIED"
    return None


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, **kw)


def check(wheel: Path, extra: str, resolve_only: bool, example: Path | None, config: Path | None) -> int:
    if extra != "none" and extra not in wheel_extras(wheel):
        print(f"error: {extra!r} is not an extra of {wheel.name} (has {wheel_extras(wheel)})", file=sys.stderr)
        return 2
    if extra not in EXTRA_CHECKS:
        print(
            f"error: no smoke checks defined for extra {extra!r}: add it to EXTRA_CHECKS in {__file__}", file=sys.stderr
        )
        return 2
    spec = f"{wheel}[{extra}]" if extra != "none" else str(wheel)
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        _run([sys.executable, "-m", "venv", str(venv)])
        py, pip = venv / "bin" / "python", venv / "bin" / "pip"
        _run([str(pip), "install", "--quiet", "--upgrade", "pip"])
        if resolve_only:
            _run([str(pip), "install", "--dry-run", "--quiet", spec])
            print(f"resolve-only: {extra!r} resolves; NOT installed or imported")
            return 0
        _run([str(pip), "install", "--quiet", spec])
        _run([str(pip), "check"])
        scratch = Path(tmp) / "run"  # not the repo: nothing under ./src can shadow the installed package
        scratch.mkdir()
        out = _run(
            [str(py), "-c", "import smeltery, sys; print(smeltery.__file__); print(smeltery.__version__)"],
            capture_output=True,
            cwd=scratch,
        ).stdout.split()
        if "site-packages" not in out[0]:
            print(f"error: smeltery imported from {out[0]}, not the installed wheel", file=sys.stderr)
            return 1
        print(f"smeltery {out[1]} imported from {out[0]}")
        for snippet in EXTRA_CHECKS[extra]:
            _run([str(py), "-c", snippet], cwd=scratch)
        if config is not None:
            _run([str(venv / "bin" / "smeltery"), "run", str(config.resolve()), "--plan"], cwd=scratch)
        if example is not None:
            probe = (
                "import importlib.metadata as m; print(m.distribution('ferric').read_text('direct_url.json') is None)"
            )
            from_index = _run([str(py), "-c", probe], capture_output=True, cwd=scratch).stdout.strip()
            if from_index != "True":
                print("error: ferric in the smoke venv was not installed from the package index", file=sys.stderr)
                return 1
            _run(
                [str(py), str(example.resolve())],
                cwd=scratch,
                env={"OPENBLAS_NUM_THREADS": "1", "PATH": str(venv / "bin")},
            )
            records = sorted((scratch / "out").glob("run-*.json"))
            if not records:
                print("error: the example wrote no run record under out/", file=sys.stderr)
                return 1
            record = json.loads(records[-1].read_text())
            why = verified_provenance(record)
            if why:
                print(f"error: {records[-1].name}: {why}", file=sys.stderr)
                return 1
            print(f"{records[-1].name}: ferric provenance VERIFIED ({record['ferric']['source']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list-extras", help="print the wheel's user-facing extras (plus 'none') as a JSON list")
    ls.add_argument("wheel", type=Path)
    ck = sub.add_parser("check", help="install the wheel with one extra in a fresh venv and smoke-test it")
    ck.add_argument("wheel", type=Path)
    ck.add_argument("--extra", required=True, help="an extra name, or 'none'")
    ck.add_argument("--resolve-only", action="store_true")
    ck.add_argument("--example", type=Path, help="an example script to run from the installed wheel")
    ck.add_argument("--config", type=Path, help="a config for `smeltery run --plan` via the installed console script")
    args = ap.parse_args(argv)
    if args.cmd == "list-extras":
        print(json.dumps(["none", *wheel_extras(args.wheel)]))
        return 0
    return check(args.wheel, args.extra, args.resolve_only, args.example, args.config)


if __name__ == "__main__":
    sys.exit(main())
