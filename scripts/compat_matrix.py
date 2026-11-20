#!/usr/bin/env python3
"""Which ferric versions a smeltery tree (or release) works with: one fresh venv per ferric, ONE variable at a time.

    uv run python scripts/compat_matrix.py --ferric 0.1.0rc12 --ferric main          # run + merge into the doc
    uv run python scripts/compat_matrix.py run --ferric 0.1.0rc11 --out cells/       # cells only (CI)
    uv run python scripts/compat_matrix.py render cells/*.json --write               # merge cells into the doc
    uv run python scripts/compat_matrix.py list-ferric --include main                # the CI matrix (JSON)
    uv run python scripts/compat_matrix.py constraints --check                       # compat/constraints.txt is current

A CELL is one smeltery x one ferric. For each, `run` makes a fresh venv (uv, `--python`, default 3.11, smeltery's
floor and CI's), installs smeltery (`--smeltery`, default the `--source` checkout; e.g. `smeltery==0.1.0` with
`--source` a checkout of the v0.1.0 tag) with the `posebusters` extra and the requested ferric, and checks:

* `install`  -- ferric installs. Everything else is constrained to the SOURCE TREE'S uv.lock (`uv export`), so the
               only thing that differs between two cells of one row is ferric. Measured on 2026-11-20: with
               dependencies left free, rdkit drifted past the lock and golden-path gate G1 refused a different
               molecule, a failure that had nothing to do with ferric. ferric itself goes in as an OVERRIDE, so a
               version below smeltery's declared floor can still be tested; the cell then says it is below the floor.
* `tests`    -- the subset in TESTS (each one calls the real engine), run from a copy of the source tree's tests/ so
               they import the INSTALLED smeltery, never ./src.
* `example`  -- examples/mwe_benzoic.py runs, and its run record names the installed ferric (the build stamp's commit
               when the wheel carries one) with a VERIFIED provenance.

A ferric SPEC is a PyPI version (`0.1.0rc12`), `main` (= git+https://github.com/mgoldey/ferric@main, which builds
from source and needs Rust, libint2 and OpenBLAS), any `git+URL[@ref]`, or a path to a built `.whl`. `LABEL=SPEC`
names the column (CI passes `main=/path/to/nightly.whl`).

`render` merges cells into the JSON embedded in docs/compatibility.md (a newer cell for the same smeltery, ferric
label, Python and platform replaces the older one) and rewrites the Markdown from that data alone, so the doc is a
pure function of its data block (tests/test_compat_matrix.py pins both the format and that the committed doc is
current). It never commits: CI uploads the regenerated doc as an artifact (docs/compatibility.md says why).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "compatibility.md"
STACK = ROOT / "compat" / "stack.toml"
CONSTRAINTS = ROOT / "compat" / "constraints.txt"
CELL_SCHEMA = "smeltery.compat-cell/1"
FERRIC_GIT = "https://github.com/mgoldey/ferric"
#: The extra every cell installs: the golden path's CLI runs PoseBusters (gate G1).
EXTRA = "posebusters"
#: Each of these calls the real engine; together they are ~25 s of a cell. A file the source tree lacks is reported.
TESTS = [
    "tests/test_funnel.py",  # the cut, the run record and ferric identity (build stamp, index, file wheel)
    "tests/test_anchors.py",  # exact zeros and the Angstrom->Bohr factor ferric itself uses
    "tests/test_interaction_energy.py",  # the pocket interaction energy through ferric
    "tests/test_golden_path_smoke.py",  # the documented path end to end through `smeltery run` (subprocess)
    # Why the floor is 0.1.0rc10 (b401d10): rc8's bundled STO-3G stopped at Ar. These two fail on rc8, pass from rc10;
    # without them every cell down to rc6 passed, which would have said the floor was arbitrary.
    "tests/test_cost.py",  # every STO-3G basis-function count in the cost table is ferric's own
    "tests/test_plb_measure.py::test_basis_gaps_asks_ferric_rather_than_assuming_which_elements_it_covers",
]
#: The golden path's per-gate mutation sweep reruns the whole path ten times; it tests smeltery's gates, not ferric.
DESELECT = "not test_disabling_one_gate_fails_the_smoke_test"
EXAMPLE = "examples/mwe_benzoic.py"
STATUSES = ("pass", "fail", "not-installable")
DATA_RE = re.compile(r"<!-- compat-data\n(.*?)\n-->", re.S)


# ------------------------------------------------------------------------------------------------- ferric specs


def parse_ferric(spec: str) -> dict:
    """`[LABEL=]SPEC` -> {label, kind, requirement}. Pure."""
    label = None
    m = re.fullmatch(r"([A-Za-z][\w.-]*)=(?!=)(.+)", spec.strip())
    if m and not spec.startswith("git+"):
        label, spec = m.groups()
    spec = spec.strip()
    if spec == "main":
        label, spec = label or "main", f"git+{FERRIC_GIT}@main"
    if spec.startswith("git+"):
        ref = spec.rsplit("@", 1)[1] if "@" in spec.split("://", 1)[1] else "HEAD"
        return {"label": label or f"git:{ref}", "kind": "git", "requirement": f"ferric @ {spec}"}
    if spec.endswith(".whl"):
        path = Path(spec).resolve()
        version = path.name.split("-")[1] if path.name.count("-") >= 2 else "?"
        return {"label": label or f"wheel:{version}", "kind": "wheel", "requirement": f"ferric @ {path.as_uri()}"}
    version = spec.removeprefix("==")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+!-]*", version):
        raise ValueError(f"not a ferric version, `main`, git+URL or .whl path: {spec!r}")
    return {"label": label or version, "kind": "pypi", "requirement": f"ferric=={version}"}


def declared_ferric(pyproject: Path) -> str:
    """The ferric specifier smeltery declares in `pyproject` (e.g. `>=0.1.0rc10`)."""
    deps = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    req = next(d for d in deps if re.match(r"ferric\b", d))
    return req[len("ferric") :].strip()


def satisfies(version: str, specifier: str) -> bool:
    from packaging.specifiers import SpecifierSet

    return SpecifierSet(specifier, prereleases=True).contains(version)


def releases_satisfying(pypi_payload: dict, specifier: str) -> list[str]:
    """ferric releases on the index that the specifier admits, oldest first; yanked and file-less ones dropped. Pure."""
    from packaging.version import Version

    keep = [
        v
        for v, files in pypi_payload["releases"].items()
        if files and not all(f.get("yanked") for f in files) and satisfies(v, specifier)
    ]
    return sorted(keep, key=Version)


def list_ferric(include: list[str]) -> list[str]:
    with urllib.request.urlopen("https://pypi.org/pypi/ferric/json", timeout=30) as r:
        payload = json.load(r)
    return releases_satisfying(payload, declared_ferric(ROOT / "pyproject.toml")) + include


# ------------------------------------------------------------------------------------------------- constraints


def lock_constraints(export_text: str) -> str:
    """`uv export` output minus ferric (the variable under test) and minus anything that is not a pin. Pure."""
    lines = [ln for ln in export_text.splitlines() if ln and not ln.lstrip().startswith(("#", "-"))]
    return "\n".join(ln for ln in lines if not re.match(r"ferric\s*(==|@|;|$)", ln)) + "\n"


def render_constraints(stack: dict) -> str:
    """compat/constraints.txt from compat/stack.toml: the smeltery environment's pins. Pure."""
    env = stack["smeltery-env"]
    out = [
        "# GENERATED from compat/stack.toml by `scripts/compat_matrix.py constraints --write`; do not edit by hand.",
        "# The pinned compatible set for the environment smeltery runs in (docs/compatibility.md#the-pinned-stack).",
        "# smeltery itself is not pinned here: install it from the SAME ref this file was read from.",
        "# attestation runs in its own environment (see stack.toml [attestation-env]), never this one.",
    ]
    for name, pin in sorted(env["pins"].items()):
        out.append(f"{name}=={pin['version']}")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------------------- running a cell


def _sh(cmd: list[str | Path], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], text=True, capture_output=True, **kw)


def _tail(text: str, n: int = 1200) -> str:
    return text[-n:].strip()


def _git(source: Path, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", str(source), *args], text=True, capture_output=True)
    return r.stdout.strip() if r.returncode == 0 else None


def junit_counts(xml_text: str) -> dict:
    """{passed, failed, skipped} from pytest's --junitxml (errors count as failed). Pure."""
    root = ET.fromstring(xml_text)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return {"passed": tests - failed - skipped, "failed": failed, "skipped": skipped}


def record_identity_problem(record: dict, build: dict | None, version: str) -> str | None:
    """None if the run record names the installed ferric with a VERIFIED provenance; else why not. Pure."""
    f = record.get("ferric") or {}
    if not str(f.get("provenance", "")).startswith("VERIFIED"):
        return f"record provenance is {f.get('provenance')!r}, not VERIFIED"
    if build and build.get("git_sha"):
        if f.get("commit") != build["git_sha"]:
            return f"record names commit {f.get('commit')!r}, the installed build stamp says {build['git_sha']!r}"
    elif f.get("version") != version:
        return f"record names ferric {f.get('version')!r}, {version!r} is installed"
    return None


def with_extra(requirement: str, extra: str) -> str:
    """`smeltery==0.1.0` -> `smeltery[extra]==0.1.0` (and `smeltery[a] @ URL` -> `smeltery[a,extra] @ URL`). Pure."""
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[([^\]]*)\])?(.*)", requirement.strip())
    if not m:
        raise ValueError(f"not a requirement: {requirement!r}")
    name, extras, rest = m.groups()
    have = [e.strip() for e in (extras or "").split(",") if e.strip()]
    return f"{name}[{','.join([*have, *([extra] if extra not in have else [])])}]{rest}"


def run_cell(ferric: dict, source: Path, smeltery_req: str | None, python: str) -> dict:
    source = source.resolve()
    floor = declared_ferric(source / "pyproject.toml")
    sha = _git(source, "rev-parse", "HEAD")
    dirty = bool(_git(source, "status", "--porcelain", "--untracked-files=no")) if sha else None
    cell: dict = {
        "schema": CELL_SCHEMA,
        "date": dt.datetime.now(dt.UTC).strftime("%Y-%m-%d"),
        "python": python,
        "platform": f"{sys.platform}-{platform.machine()}",
        "smeltery": {"requirement": smeltery_req or "the source checkout", "source_sha": sha, "source_dirty": dirty},
        "ferric": {
            "label": ferric["label"],
            "kind": ferric["kind"],
            "requested": ferric["requirement"],
            "floor": floor,
            # only an index release is judged against the floor: a build from source says 0.1.0.dev0 whatever it is
            "satisfies_floor": satisfies(ferric["label"], floor) if ferric["kind"] == "pypi" else None,
        },
        "checks": {},
    }
    with tempfile.TemporaryDirectory(prefix="compat-") as tmp_:
        tmp = Path(tmp_)
        venv, py = tmp / "venv", tmp / "venv" / "bin" / "python"
        r = _sh(["uv", "venv", "--quiet", "--python", python, venv])
        if r.returncode:
            raise SystemExit(f"cannot make a Python {python} venv: {_tail(r.stderr)}")
        export = _sh(
            ["uv", "export", "--frozen", "--no-hashes", "--no-emit-project", "--extra", EXTRA, "--extra", "dev"],
            cwd=source,
        )
        if export.returncode:
            raise SystemExit(f"uv export failed in {source}: {_tail(export.stderr)}")
        (tmp / "constraints.txt").write_text(lock_constraints(export.stdout))
        (tmp / "overrides.txt").write_text(ferric["requirement"] + "\n")
        target = with_extra(smeltery_req, EXTRA) if smeltery_req else f"smeltery[{EXTRA}] @ {source.as_uri()}"
        r = _sh(
            ["uv", "pip", "install", "--python", py, "-c", tmp / "constraints.txt", "--override", tmp / "overrides.txt"]
            + [target, "pytest"]
        )
        if r.returncode:
            cell["checks"]["install"] = {"status": "fail", "detail": _tail(r.stderr)}
            cell["status"] = "not-installable"
            return cell
        probe = (
            "import json, importlib.metadata as m, ferric, smeltery; d = m.distribution('ferric'); "
            "print(json.dumps({'ferric': d.version, 'build': getattr(ferric, '__build__', None), "
            "'direct_url': d.read_text('direct_url.json'), 'smeltery': m.version('smeltery'), "
            "'smeltery_file': smeltery.__file__, 'python': '.'.join(map(str, __import__('sys').version_info[:3]))}))"
        )
        r = _sh([py, "-c", probe], cwd=tmp)
        if r.returncode:
            cell["checks"]["install"] = {
                "status": "fail",
                "detail": "installed but does not import: " + _tail(r.stderr),
            }
            cell["status"] = "fail"
            return cell
        ident = json.loads(r.stdout)
        cell["python"] = ident["python"]
        cell["smeltery"]["version"] = ident["smeltery"]
        build = ident["build"] if isinstance(ident["build"], dict) else None
        cell["ferric"].update(
            version=ident["ferric"],
            build_sha=build.get("git_sha") if build else None,
            build_dirty=build.get("dirty") if build else None,
            from_index=ident["direct_url"] is None,
        )
        cell["checks"]["install"] = {
            "status": "pass",
            "detail": f"ferric {ident['ferric']}, smeltery {ident['smeltery']}",
        }
        if "site-packages" not in ident["smeltery_file"]:
            cell["checks"]["install"] = {"status": "fail", "detail": f"smeltery imported from {ident['smeltery_file']}"}

        # tests: a copy of the source tree's tests/, so `import smeltery` is the installed one
        work = tmp / "work"
        # the golden path reads docs/tiers/index.md's gate catalogue; basis_gaps loads scripts/run_plb_ddE.py
        for tree in ("tests", "docs", "examples", "scripts"):
            if (source / tree).is_dir():
                shutil.copytree(source / tree, work / tree, ignore=shutil.ignore_patterns("__pycache__"))
        present = [t for t in TESTS if (source / t.split("::")[0]).is_file()]
        missing = [t for t in TESTS if t not in present]
        env = {
            **{k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "SMELTERY_NO_FERRIC")},
            "OPENBLAS_NUM_THREADS": "1",
            "VIRTUAL_ENV": str(venv),
            "PATH": f"{venv / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        junit = tmp / "junit.xml"
        r = _sh(
            [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", DESELECT, f"--junitxml={junit}", *present],
            cwd=work,
            env=env,
        )
        counts = junit_counts(junit.read_text()) if junit.is_file() else {"passed": 0, "failed": 1, "skipped": 0}
        failed_ids = re.findall(r"^(?:FAILED|ERROR) (\S+)", r.stdout, re.M)
        ok = r.returncode == 0 and counts["failed"] == 0 and counts["passed"] > 0
        cell["checks"]["tests"] = {
            "status": "pass" if ok else "fail",
            **counts,
            "files": present,
            "missing": missing,
            "detail": "" if ok else ("; ".join(failed_ids[:8]) or _tail(r.stdout + r.stderr, 600)),
        }

        # the example, and the provenance its record states
        scratch = tmp / "example"
        scratch.mkdir()
        r = _sh([py, source / EXAMPLE], cwd=scratch, env=env)
        records = sorted((scratch / "out").glob("run-*.json"))
        if r.returncode or not records:
            detail = _tail(r.stderr or r.stdout, 600) if r.returncode else "the example wrote no run record"
            cell["checks"]["example"] = {"status": "fail", "detail": detail}
        else:
            record = json.loads(records[-1].read_text())
            why = record_identity_problem(record, build, ident["ferric"])
            cell["checks"]["example"] = {
                "status": "fail" if why else "pass",
                "detail": why or f"{record['ferric'].get('source')}: {record['ferric'].get('provenance')}",
            }
    failed = [k for k, c in cell["checks"].items() if c["status"] != "pass"]
    cell["status"] = "fail" if failed else "pass"
    return cell


def cell_filename(cell: dict) -> str:
    key = f"{cell['smeltery'].get('version', 'x')}__{cell['ferric']['label']}__py{cell['python']}"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key) + ".json"


# ------------------------------------------------------------------------------------------------- rendering


def cell_key(cell: dict) -> tuple:
    return (cell["smeltery"].get("version"), cell["ferric"]["label"], ".".join(cell["python"].split(".")[:2]))


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """Cells keyed by (smeltery version, ferric label, Python minor); the later date wins, `new` wins a tie. Pure."""
    out: dict[tuple, dict] = {}
    for c in [*old, *new]:
        k = cell_key(c)
        if k not in out or c["date"] >= out[k]["date"]:
            out[k] = c
    return sorted(out.values(), key=_sort_key)


def _version_key(v: str | None):
    from packaging.version import InvalidVersion, Version

    try:
        return (0, Version(v or ""))
    except InvalidVersion:
        return (1, v or "")


def _sort_key(c: dict):
    return (_version_key(c["smeltery"].get("version")), _ferric_col_key(c["ferric"]["label"]), c["python"])


def _ferric_col_key(label: str):
    k = _version_key(label)
    return (k[0], k[1]) if k[0] == 0 else (1, label)


def _short(sha: str | None) -> str:
    return sha[:8] if sha else "--"


def _mark(cell: dict | None) -> str:
    if cell is None:
        return ""
    word = {"pass": "pass", "fail": "**FAIL**", "not-installable": "not installable"}[cell["status"]]
    if cell["ferric"].get("satisfies_floor") is False:
        word += " (below floor)"
    return word


def _smeltery_name(c: dict) -> str:
    v = c["smeltery"].get("version") or "?"
    return f"`{v}`"


def render(cells: list[dict]) -> str:
    """docs/compatibility.md from its cells alone. Pure: same cells, same bytes."""
    cells = sorted(cells, key=_sort_key)
    labels = sorted({c["ferric"]["label"] for c in cells}, key=_ferric_col_key)
    rows: dict[tuple, dict[str, dict]] = {}
    for c in cells:
        rows.setdefault((_smeltery_name(c), ".".join(c["python"].split(".")[:2])), {})[c["ferric"]["label"]] = c
    lines = [
        "# Compatibility: smeltery x ferric",
        "",
        "<!-- GENERATED by scripts/compat_matrix.py from the compat-data block at the end of this file. Do not edit by",
        "hand: `uv run python scripts/compat_matrix.py render CELLS... --write` merges new cells and rewrites it. -->",
        "",
        "Which ferric versions each smeltery version was tested against, and the result. Each cell is one fresh",
        "virtual environment holding that smeltery and that ferric, with every other dependency pinned to that",
        "smeltery's own `uv.lock`, so ferric is the only thing that differs along a row. A cell passes when the",
        "engine-calling tests (`" + "`, `".join(Path(t).name for t in TESTS) + "`, the gate-mutation sweep",
        "excluded) pass from the installed package and `" + EXAMPLE + "` writes a run record naming the installed",
        "ferric with a VERIFIED provenance. *below floor*: older than the ferric smeltery declares, installed by",
        "override to show where the floor comes from; pip would refuse it. How the table is made and refreshed,",
        "and the pinned set of all three projects, are in [the section after the tables](#how-this-table-is-made).",
        "",
        "## Matrix",
        "",
        "| smeltery | Python | " + " | ".join(f"ferric {lb}" for lb in labels) + " |",
        "|---|---|" + "---|" * len(labels),
    ]
    for (name, py), by in rows.items():
        lines.append(f"| {name} | {py} | " + " | ".join(_mark(by.get(lb)) for lb in labels) + " |")
    lines += [
        "",
        "## Cells",
        "",
        "| smeltery | from tree | ferric | installed | build | tests (pass/fail/skip) | example | date | platform |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cells:
        f, t, ex = c["ferric"], c["checks"].get("tests"), c["checks"].get("example")
        tree = _short(c["smeltery"].get("source_sha")) + (" (dirty)" if c["smeltery"].get("source_dirty") else "")
        build = _short(f.get("build_sha")) + (" (dirty)" if f.get("build_dirty") else "")
        tests = f"{t['passed']}/{t['failed']}/{t['skipped']}" if t else "--"
        lines.append(
            f"| {_smeltery_name(c)} | {tree} | {f['label']} | {f.get('version', '--')} | {build} | {tests} "
            f"| {ex['status'] if ex else '--'} | {c['date']} | {c['platform']}, py{c['python']} |"
        )
    problems = [(c, k, v) for c in cells for k, v in c["checks"].items() if v["status"] != "pass"]
    if problems:
        lines += ["", "### What failed", ""]
        for c, k, v in problems:
            detail = " ".join(str(v.get("detail", "")).split())[:300]
            lines.append(f"- {_smeltery_name(c)} x ferric {c['ferric']['label']}, `{k}`: {detail}")
    lines += [
        "",
        "## How this table is made",
        "",
        "`scripts/compat_matrix.py` makes one cell per (smeltery, ferric) pair: `uv venv`, `uv pip install` with the",
        "source tree's `uv.lock` exported as constraints and the ferric under test as an override, then the tests and",
        "the example above. The `compat` workflow (.github/workflows/compat.yml) runs it on a schedule, on demand",
        "and on every `v*` tag, for every ferric release on PyPI that smeltery's floor admits plus ferric `main`",
        "(its nightly wheel), and uploads the cells and this file, regenerated, as the `compatibility` artifact.",
        "CI never commits it: refreshing the committed copy is `render ... --write` on the downloaded cells and a",
        "normal pull request, so a changed row is reviewed like any other change. To add cells locally:",
        "",
        "```sh",
        "uv run python scripts/compat_matrix.py --ferric 0.1.0rc12 --ferric main   # main builds ferric from source",
        "```",
        "",
        "## The pinned stack",
        "",
        "`compat/stack.toml` names one set of ferric, smeltery, attestation-schema, mcp and attestation known to work",
        "together, and `compat/constraints.txt` is its smeltery-environment half as a pip/uv constraints file.",
        "Install smeltery from a ref and read the constraints from the SAME ref:",
        "",
        "```sh",
        "REF=main   # or a tag or commit",
        "uv pip install -c https://raw.githubusercontent.com/mgoldey/smeltery/$REF/compat/constraints.txt \\",
        "  'smeltery[mcp,attestation] @ git+https://github.com/mgoldey/smeltery@'$REF",
        "```",
        "",
        "attestation is NOT installed into that environment: it runs in its own (Python >= 3.12), at the commit",
        "`compat/stack.toml` pins, because no single environment can hold both today (stack.toml says why, per",
        "member):",
        "",
        "```sh",
        "uv tool install --python 3.12 'git+https://github.com/mgoldey/attestation@<[attestation-env] rev>'",
        "```",
        "",
        "<!-- compat-data",
        json.dumps({"schema": "smeltery.compat/1", "cells": cells}, indent=1, sort_keys=True),
        "-->",
        "",
    ]
    return "\n".join(lines)


def doc_cells(text: str) -> list[dict]:
    m = DATA_RE.search(text)
    return json.loads(m.group(1))["cells"] if m else []


# ------------------------------------------------------------------------------------------------- CLI


def _write_doc(new: list[dict], doc: Path, write: bool) -> str:
    old = doc_cells(doc.read_text()) if doc.is_file() else []
    text = render(merge(old, new))
    if write:
        doc.write_text(text)
        print(f"wrote {doc}")
    return text


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith("--") and argv[0] not in ("-h", "--help"):
        argv = ["all", *argv]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "all"):
        p = sub.add_parser(name, help="run cells" + (" and merge them into the doc" if name == "all" else ""))
        p.add_argument("--ferric", action="append", required=True, help="[LABEL=]SPEC; repeat for several")
        p.add_argument("--source", type=Path, default=ROOT, help="smeltery tree: tests, example, uv.lock")
        p.add_argument("--smeltery", help="install this requirement (e.g. smeltery==0.1.0) instead of --source")
        p.add_argument("--python", default="3.11")
        p.add_argument("--out", type=Path, help="write each cell as JSON here")
        if name == "all":
            p.add_argument("--doc", type=Path, default=DOC)
            p.add_argument("--no-write", action="store_true")
    rp = sub.add_parser("render", help="merge cell JSON files into the doc")
    rp.add_argument("cells", nargs="*", type=Path)
    rp.add_argument("--doc", type=Path, default=DOC)
    rp.add_argument("--write", action="store_true", help="rewrite the doc (else print it)")
    rp.add_argument(
        "--fail-on-supported-failure",
        action="store_true",
        help="exit 1 if a new cell for a released ferric the floor admits failed",
    )
    lp = sub.add_parser("list-ferric", help="ferric PyPI releases smeltery's floor admits, as a JSON list")
    lp.add_argument("--include", action="append", default=[], help="extra specs to append (e.g. main)")
    cp = sub.add_parser("constraints", help="compat/constraints.txt from compat/stack.toml")
    cp.add_argument("--check", action="store_true", help="exit 1 if the committed file differs")
    cp.add_argument("--write", action="store_true", help="rewrite the committed file (else print it)")
    args = ap.parse_args(argv)

    if args.cmd == "list-ferric":
        print(json.dumps(list_ferric(args.include)))
        return 0
    if args.cmd == "constraints":
        text = render_constraints(tomllib.loads(STACK.read_text()))
        if args.check:
            ok = CONSTRAINTS.is_file() and CONSTRAINTS.read_text() == text
            print("compat/constraints.txt is current" if ok else "compat/constraints.txt is stale: run --write")
            return 0 if ok else 1
        if args.write:
            CONSTRAINTS.write_text(text)
            print(f"wrote {CONSTRAINTS}")
        else:
            print(text, end="")
        return 0
    if args.cmd == "render":
        new = [json.loads(p.read_text()) for p in args.cells]
        text = _write_doc(new, args.doc, args.write)
        if not args.write:
            print(text)
        bad = [c for c in new if c["status"] != "pass" and c["ferric"].get("satisfies_floor") is True]
        for c in bad:
            print(f"FAILED: smeltery {c['smeltery'].get('version')} x ferric {c['ferric']['label']}", file=sys.stderr)
        return 1 if (bad and args.fail_on_supported_failure) else 0

    cells = []
    for spec in args.ferric:
        cell = run_cell(parse_ferric(spec), args.source, args.smeltery, args.python)
        cells.append(cell)
        print(f"== smeltery {cell['smeltery'].get('version')} x ferric {cell['ferric']['label']}: {cell['status']}")
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / cell_filename(cell)).write_text(json.dumps(cell, indent=1, sort_keys=True) + "\n")
    if args.cmd == "all":
        _write_doc(cells, args.doc, not args.no_write)
    return 0 if all(c["status"] == "pass" for c in cells) else 1


if __name__ == "__main__":
    sys.exit(main())
