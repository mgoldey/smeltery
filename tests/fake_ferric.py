"""A fake `ferric` for the Temporal runner's tests: deterministic energies, convergence and hangs on demand.

The same module serves the test process (`install()` puts it in `sys.modules`) and the score child processes the
runner spawns (run as a script: it installs itself, then calls `smeltery.temporal_run.child_main`), so a plain run and
a Temporal run compute the same numbers. Behaviour comes from the JSON file named by `$FAKE_FERRIC`:

    {"rules": {"<label>|<leg>": "never" | "needs:<kwarg>" | "hang" | "hang_once"}, "dir": "<scratch dir>"}

`<label>` is the xyz comment line ("ethanol pose 0"), `<leg>` "vacuum" or "field". Every completed SCF appends a line
to `<dir>/calls.jsonl`; every started one appends its pid to `<dir>/pids`. "hang" sleeps for 10 minutes; "hang_once"
does so only the first time (marked by `<dir>/hung-<label>|<leg>`), and writes `<dir>/hanging` while it hangs.
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

import numpy as np


def _cfg() -> dict:
    p = os.environ.get("FAKE_FERRIC")
    return json.loads(Path(p).read_text()) if p else {"rules": {}, "dir": None}


def _append(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)


class BasisSet:
    @staticmethod
    def bundled(name):
        return name


class Molecule:
    def __init__(self, label, xyz):
        self.label, self.xyz = label, xyz

    @staticmethod
    def from_xyz_string(text, charge=0):
        lines = text.splitlines()
        xyz = np.array([[float(v) for v in ln.split()[1:4]] for ln in lines[2:] if ln.strip()])
        return Molecule(lines[1], xyz)


def run_rhf(mol, bs, point_charges=None, **kw):
    cfg = _cfg()
    d = Path(cfg["dir"]) if cfg.get("dir") else None
    leg = "vacuum" if point_charges is None else "field"
    key = f"{mol.label}|{leg}"
    rule = cfg.get("rules", {}).get(key, "")
    if d is not None:
        _append(d / "pids", f"{os.getpid()}\n")
    if rule == "hang" or (rule == "hang_once" and d is not None and not (d / f"hung-{key}").exists()):
        if d is not None:
            (d / f"hung-{key}").write_text(str(os.getpid()))
            (d / "hanging").write_text(str(os.getpid()))
        time.sleep(600)
    e = -0.1 * len(mol.xyz) - 0.01 * float(np.abs(mol.xyz).sum())
    if point_charges:
        for q, x, y, z in point_charges:
            dist = np.linalg.norm(mol.xyz - np.array([x, y, z]) / 1.8897261246, axis=1)
            e += -0.02 * q * float(np.sum(1.0 / (dist + 1.0)))
    ok = True
    if rule == "never":
        ok = False
    elif rule.startswith("needs:"):
        ok = rule.split(":", 1)[1] in kw
    if d is not None:
        _append(d / "calls.jsonl", json.dumps({"label": mol.label, "leg": leg, "kw": kw, "pid": os.getpid()}) + "\n")
    return types.SimpleNamespace(converged=bool(ok), iterations=7, energy=e)


def install() -> types.ModuleType:
    mod = types.ModuleType("ferric")
    mod.BasisSet, mod.Molecule, mod.run_rhf = BasisSet, Molecule, run_rhf  # type: ignore[attr-defined]
    sys.modules["ferric"] = mod
    return mod


if __name__ == "__main__":
    install()
    from smeltery.temporal_run import child_main

    sys.exit(child_main(sys.argv[1:]))
