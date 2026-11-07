"""Read-only tools over a finished run's record: `verify-run`, `inspect`, `diff-run`."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .record import _canonical_sha256


class RunToolError(Exception):
    pass


def locate_record(target: str) -> Path:
    """The `run-*.json` for a run dir, or the given record file itself."""
    p = Path(target)
    if p.is_dir():
        found = sorted(p.glob("run-*.json"))
        if len(found) != 1:
            raise RunToolError(f"{p}: expected exactly one run-*.json, found {len(found)}")
        return found[0]
    if not p.is_file():
        raise RunToolError(f"{p}: no such run directory or record")
    return p


def load(target: str) -> tuple[Path, dict]:
    path = locate_record(target)
    try:
        return path, json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RunToolError(f"{path}: not valid JSON ({e})") from e


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_entries(results: dict):
    """(label, entry) for `results.poses` values and `results.receptor` that carry a path and sha256."""
    for name, e in sorted((results.get("poses") or {}).items()):
        yield f"poses[{name}]", e
    if isinstance(results.get("receptor"), dict):
        yield "receptor", results["receptor"]


def verify(path: Path, rec: dict) -> tuple[list[str], list[str]]:
    """(problems, notes): problems make the exit status 1."""
    problems, notes = [], []
    stored = rec.get("record_sha256")
    if stored is None:
        notes.append("warning: record_sha256 absent (record predates self-hash); record not checked")
    elif stored != _canonical_sha256(rec):
        problems.append("record_sha256 differs: the record was edited after it was written")
    else:
        notes.append("record_sha256 ok")
    for label, e in _file_entries(rec.get("results") or {}):
        f, want = e.get("file") or e.get("path"), e.get("sha256")
        if not f or not want:
            continue
        fp = path.parent / f
        if not fp.is_file():
            problems.append(f"{label}: {f} is missing")
        elif _sha(fp) != want:
            problems.append(f"{label}: {f} sha256 differs from the record")
        else:
            notes.append(f"{label}: {f} ok")
    return problems, notes


def _run_verify(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="smeltery verify-run",
        description="Recompute a run record's self-hash and the sha256 of the pose/receptor files it lists. "
        "This detects accidental edits, not malicious ones: there is no signature, so anyone who can edit the "
        "record can recompute its hash.",
    )
    ap.add_argument("run", metavar="OUT_OR_RECORD_JSON")
    path, rec = load(ap.parse_args(argv).run)
    problems, notes = verify(path, rec)
    for line in notes:
        print(line)
    for line in problems:
        print(f"MISMATCH: {line}")
    return 1 if problems else 0


def summary(rec: dict) -> list[str]:
    res, inp = rec.get("results") or {}, rec.get("inputs") or {}
    cut = res.get("cut") or {}
    verdict = (
        "UNRANKED at the cut"
        if cut.get("unranked")
        else f"survivors: {', '.join(cut.get('survivors') or []) or 'none'}"
    )
    group = {c: i + 1 for i, g in enumerate(cut.get("groups") or []) for c in g}
    lines = [f"campaign: {rec.get('campaign')}", f"input_digest: {rec.get('input_digest')}", f"verdict: {verdict}"]
    lines.append("candidates:")
    for name, m in sorted((res.get("paired_ddE") or {}).items()):
        row = f"  {name}: ddE={m.get('mean'):.3f} sem={m.get('sem'):.3f}" if m.get("mean") is not None else f"  {name}"
        if name in group:
            row += f" group={group[name]}"
        lines.append(row)
    floor = inp.get("floor")
    lines.append(f"floor: {floor.get('value') if isinstance(floor, dict) else floor}")
    lines.append("stages:")
    for s in res.get("stages") or []:
        lines.append(f"  {s.get('stage')}: {s.get('summary', '')}")
    lines.append("inputs:")
    for label, e in _file_entries(res):
        lines.append(f"  {label}: {e.get('file') or e.get('path')} sha256={e.get('sha256')}")
    return lines


def _run_inspect(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="smeltery inspect", description="Plain-text summary of a run record.")
    ap.add_argument("run", metavar="OUT_OR_RECORD_JSON")
    print("\n".join(summary(load(ap.parse_args(argv).run)[1])))
    return 0


def diff_keys(a: dict, b: dict) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def _run_diff(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="smeltery diff-run", description="Compare two runs' inputs and tiers.")
    ap.add_argument("a", metavar="A")
    ap.add_argument("b", metavar="B")
    ns = ap.parse_args(argv)
    ra, rb = load(ns.a)[1], load(ns.b)[1]
    same = ra.get("input_digest") == rb.get("input_digest")
    print(f"input_digest: {'equal' if same else 'different'}")
    ia, ib = ra.get("inputs") or {}, rb.get("inputs") or {}
    for k in diff_keys(ia, ib):
        print(f"inputs.{k} differs")
    ta, tb = ra.get("tiers") or [], rb.get("tiers") or []
    for i in range(max(len(ta), len(tb))):
        x, y = (ta[i] if i < len(ta) else {}), (tb[i] if i < len(tb) else {})
        for k in diff_keys(x, y):
            print(f"tiers[{i}].{k} differs")
    return 0 if same else 1


COMMANDS = {"verify-run": _run_verify, "inspect": _run_inspect, "diff-run": _run_diff}


def run(command: str, argv: list[str]) -> int:
    try:
        return COMMANDS[command](argv)
    except RunToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
