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
    "pocket-charges": ["import openmm"],
    "temporal": [
        "import temporalio",
        "from temporalio import activity, workflow",
        "from smeltery.settings import resolve; assert resolve({'SMELTERY_BACKEND': 'temporal'}).backend == 'temporal'",
        "from smeltery.services import serve_main, worker_main",
    ],
    # pdbfixer repairs an unprepped crystal pocket (missing side-chain heavy atoms, a missing chain
    # terminal, absent hydrogens) before the force field types it; openmm.app.Modeller cannot add
    # missing HEAVY atoms, which is why the extra exists. Used by scripts/plb_pocket_charges.py --prep.
    "pocket-prep": ["import openmm, pdbfixer"],
    "posebusters": ["import posebusters, rdkit", "from smeltery.gates import posebusters_check"],
    "docking": [
        "import vina, meeko, rdkit, scipy, gemmi",
        "from smeltery.docking import VinaProvider, Docking, Box",
    ],
    "experiments": ["import psutil, scipy"],
    # Write a Parquet file from a run the wheel itself ships and read its metadata back; an Arrow IPC file too.
    "dataset": [
        "import pyarrow, pyarrow.parquet",
        "import tempfile, pyarrow.parquet as pq; from importlib import resources; "
        "from smeltery.dataset_export import write_dataset; "
        "d = tempfile.mkdtemp(); run = resources.files('smeltery.prebuilt').joinpath('benzoic-rhf').joinpath('run'); "
        "n = write_dataset([str(run)], d + '/o.parquet'); "
        "t = pq.read_table(d + '/o.parquet'); assert t.num_rows == n > 0, n; "
        "assert t.schema.metadata[b'smeltery.schema_version'] == b'3'; "
        "write_dataset([str(run)], d + '/o.arrow', 'arrow')",
        # A dataset DIRECTORY also needs attestation-schema (the `attestation` extra), absent here: the module imports
        # and every entry point refuses naming the release it needs, rather than failing on an import.
        "import tempfile; from importlib import resources; from smeltery import dataset_record as r; "
        "assert not r.AVAILABLE; run = resources.files('smeltery.prebuilt').joinpath('benzoic-rhf').joinpath('run'); "
        "assert r.main([str(run), '-o', tempfile.mkdtemp() + '/ds']) == 2",
        # A Hugging Face folder (card + croissant.json) needs only pyarrow: no huggingface_hub, no datasets, no network.
        "import json, sys, tempfile; from importlib import resources; "
        "from smeltery.dataset_export import write_dataset; from smeltery import hub_export as h; "
        "d = tempfile.mkdtemp(); run = resources.files('smeltery.prebuilt').joinpath('benzoic-rhf').joinpath('run'); "
        "write_dataset([str(run)], d + '/o.parquet'); out = h.export_hf(d + '/o.parquet', d + '/hf'); "
        "assert h.verify_folder(out) == [] and 'huggingface_hub' not in sys.modules; "
        "doc = json.loads((out / 'croissant.json').read_text()); assert 'license' not in doc; "
        "assert doc['recordSet'][0]['field'][0]['name'] == 'run_id' and (out / 'README.md').is_file()",
    ],
    # A scaffold split and a target split of an exported table, loaded back through ExportDataset as tensors.
    "torch": [
        "import torch, pyarrow",
        "import tempfile, torch; from importlib import resources; "
        "from smeltery.dataset_export import write_dataset; "
        "from smeltery.dataset_splits import make_split, write_split; "
        "from smeltery.dataset_torch import ExportDataset; "
        "pre = resources.files('smeltery.prebuilt'); d = tempfile.mkdtemp(); "
        "runs = [str(pre.joinpath(n).joinpath('run')) for n in ('benzoic-rhf', 'structure')]; "
        "write_dataset(runs, d + '/o.parquet'); "
        "write_split(make_split(d + '/o.parquet', 'target', fractions={'train': 0.5, 'test': 0.5}), d + '/s.json'); "
        "ds = ExportDataset(d + '/o.parquet', features=['score_n'], target='score', split=d + '/s.json', "
        "partition='train'); x, y = ds[0]; assert isinstance(x, torch.Tensor) and len(ds) > 0",
    ],
    # `smeltery export hf --push`: the client imports, and a push of a folder written from a run the wheel ships is
    # planned (a dry run: no token, no network); the folder is re-checked against croissant.json first.
    "hub": [
        "import huggingface_hub, pyarrow; from huggingface_hub import HfApi",
        "import tempfile; from importlib import resources; "
        "from smeltery.dataset_export import write_dataset; from smeltery import hub_export as h; "
        "d = tempfile.mkdtemp(); run = resources.files('smeltery.prebuilt').joinpath('benzoic-rhf').joinpath('run'); "
        "write_dataset([str(run)], d + '/o.parquet'); "
        "assert h.main([d + '/o.parquet', '-o', d + '/hf', '--license', 'CC0-1.0', '--push', 'owner/name']) == 0; "
        "plan = h.plan_push(d + '/hf', 'owner/name'); assert plan.url.endswith('/datasets/owner/name'), plan",
    ],
    # Convert a run the wheel ships into its core record and read it back with attestation-schema's own reader. The
    # schema supports Python >= 3.11 (smeltery's floor), so the extra is never empty: this runs on CI's 3.11 too. The
    # docking run is the one with a seed list and NaN (unranked cut) values, so it exercises the wire forms.
    "attestation": [
        "import sys; from smeltery import core_record as c; assert c.AVAILABLE, sys.version_info",
        "import pathlib, shutil, tempfile; from importlib import resources; from smeltery import core_record as c; "
        "import attestation_schema as a; r = resources.files('smeltery.prebuilt'); "
        "runs = {n: pathlib.Path(tempfile.mkdtemp()) / n for n in ('benzoic-rhf', 'docking')}; "
        "[shutil.copytree(str(r.joinpath(n).joinpath('run')), d) for n, d in runs.items()]; "
        "assert [c.main(['core', str(d)]) for d in runs.values()] == [0, 0]; "
        "rec = a.read(runs['benzoic-rhf']); assert rec.family == 'mwe-benzoic-cli', rec.family; "
        "assert rec.history is None and a.read_history(runs['benzoic-rhf'], rec) is None, rec.history; "
        "assert {m.unit for m in rec.metrics if m.name == 'paired_ddE.mean'} == {'kcal/mol'}; "
        "assert rec.config['campaign'] == rec.family and 'candidates' in rec.ext['smeltery']['inputs'], rec.config; "
        "dock = a.read(runs['docking']); assert dock.seeds['docking'] == [7], dock.seeds; "
        "assert a.dumps(dock) == (runs['docking'] / 'attestation' / 'run.json').read_bytes()",
        # Campaign jobs (smeltery.jobs) need this extra: the local backend is a JobBackend and opens its queue, and
        # job state is attestation-schema's own (`attestation_schema.state`, attestation 9fb7eed), not a copy.
        "import tempfile, attestation_schema as a; from attestation_schema import state; from smeltery import jobs; "
        "assert jobs.AVAILABLE and jobs.read_state is state.read_state and jobs.transition is state.transition; "
        "assert isinstance(jobs.LocalJobBackend(tempfile.mkdtemp()), a.JobBackend)",
        # Dataset directories (smeltery.dataset_record) are available exactly when the installed schema has
        # attestation.dataset/1 (REQUIRED_SCHEMA, 0.2.0); 0.1.0, the extra's current floor, does not.
        "from importlib import metadata; from smeltery import dataset_record as r; "
        "have = tuple(int(x) for x in metadata.version('attestation-schema').split('.')[:2]); "
        "need = tuple(int(x) for x in r.REQUIRED_SCHEMA.split('.')[:2]); "
        "assert r.AVAILABLE == (have >= need), (have, r.not_installed())",
    ],
    # The MCP surface: the SDK imports, the server builds with its four tools, and the target-class table the
    # tool descriptions are generated from loads out of the installed package data (not the checkout).
    "mcp": [
        "import mcp; from mcp.server.mcpserver import MCPServer",
        "import asyncio; from smeltery.mcp.server import build_server, ToolContext; "
        "from smeltery.settings import resolve; "
        "s = build_server(ToolContext(resolve({}))); "
        "names = sorted(t.name for t in asyncio.run(s.list_tools())); "
        "assert names == ['estimate_cost', 'explain_drop', 'run_status', 'screen_candidates'], names",
        "from smeltery.mcp import targets; "
        "assert targets.default_name() in targets.names() and len(targets.names()) >= 2, targets.names()",
    ],
    "ml-potential": [
        "import openmm, openmmml, torch, torchani",
        "from smeltery.providers.openmm_ml import OpenMMMLAni2x",
    ],
    "aimnet2": ["import aimnet, torch", "from smeltery.providers.aimnet2 import Aimnet2"],
    "gui": [
        "import marimo, anywidget, mols2grid, molviewspec, plotly",
        "from smeltery.gui.app import APP_FILE, main; assert APP_FILE.is_file()",
        "from smeltery.gui.views import builder, forest, funnel, grid, matrix, per_pose, pose",
    ],
    "desktop": ["import webview, marimo", "from smeltery.gui.app import main"],
    "bench": [
        "import fastapi, uvicorn, plotly, molviewspec",
        # each snippet is its own interpreter, so the import and the assert share one
        "from smeltery.bench.app import create_app, WEB_DIR; assert (WEB_DIR / 'index.html').is_file(), WEB_DIR",
        # the prebuilt pipelines ship as package data: each has its config and a finished run
        # One segment per joinpath: MultiplexedPath.joinpath takes several only from 3.12, and CI runs 3.11.
        "from importlib import resources; r = resources.files('smeltery.prebuilt'); "
        "bad = [n for n in ('benzoic-rhf', 'benzoic-pbe', 'docking', 'structure') "
        "if not (r.joinpath(n).joinpath('pipeline.toml').is_file() and r.joinpath(n).joinpath('run').is_dir() "
        "and any(c.name.startswith('run-') for c in r.joinpath(n).joinpath('run').iterdir()))]; assert not bad, bad",
    ],
}
#: extras too heavy to install on every run: `check` only proves that pip can RESOLVE them unless `--full` is given
#: (the release workflow gives it). The reason is stated so the next person can judge whether it still holds.
HEAVY_EXTRAS = {
    "ml-potential": "pulls torch and torchani (several GB)",
    "aimnet2": "pulls torch and the aimnet warp/nvalchemi stack (several GB)",
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


def resolves_only(extra: str, resolve_only: bool, full: bool) -> bool:
    """Whether `check` should only resolve the extra: asked for, or a heavy extra without `--full`."""
    return resolve_only or (extra in HEAVY_EXTRAS and not full)


def check(
    wheel: Path, extra: str, resolve_only: bool, example: Path | None, config: Path | None, full: bool = False
) -> int:
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
        if resolves_only(extra, resolve_only, full):
            _run([str(pip), "install", "--dry-run", "--quiet", spec])
            why = (
                f" ({HEAVY_EXTRAS[extra]}; pass --full to install it)"
                if extra in HEAVY_EXTRAS and not resolve_only
                else ""
            )
            print(f"resolve-only: {extra!r} resolves; NOT installed or imported{why}")
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
            # With `--out`, so the run stages its config and input files. Staging rewrites TOML in place and so
            # needs tomlkit; a plain `--plan` never calls it, which let a core install that could not stage ship
            # green. The staged directory is the real deliverable of a run, so the smoke test writes one.
            _run(
                [str(venv / "bin" / "smeltery"), "run", str(config.resolve()), "--plan", "--out", "staged"],
                cwd=scratch,
            )
            if not (scratch / "staged" / "config.toml").is_file():
                print("error: `smeltery run --out staged` wrote no staged config.toml", file=sys.stderr)
                return 1
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
    ck.add_argument("--full", action="store_true", help="install heavy extras too instead of only resolving them")
    ck.add_argument("--example", type=Path, help="an example script to run from the installed wheel")
    ck.add_argument("--config", type=Path, help="a config for `smeltery run --plan` via the installed console script")
    args = ap.parse_args(argv)
    if args.cmd == "list-extras":
        print(json.dumps(["none", *wheel_extras(args.wheel)]))
        return 0
    return check(args.wheel, args.extra, args.resolve_only, args.example, args.config, args.full)


if __name__ == "__main__":
    sys.exit(main())
