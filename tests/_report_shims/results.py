"""TEMPORARY stand-in for `smeltery.gui.results` (docs/gui-contract.md section 2), for developing the report while the
real module is written in parallel. Delete once the real one is merged: tests/test_gui_report.py uses the real
module whenever it imports, and this file only otherwise.

It implements the contract's names over a run record, nothing more.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from smeltery.funnel import resolved
from smeltery.model import Measurement


@dataclass
class Run:
    record: dict
    out_dir: Path

    @property
    def _inputs(self) -> dict:
        return self.record["inputs"]

    @property
    def _results(self) -> dict:
        return self.record["results"]

    @property
    def campaign(self) -> str:
        return self.record["campaign"]

    @property
    def parent(self) -> str:
        return self._inputs["parent"]

    @property
    def candidates(self) -> dict[str, str]:
        return dict(self._inputs["candidates"])

    @property
    def analogues(self) -> list[str]:
        return [c for c in self.candidates if c != self.parent]

    @property
    def floor(self) -> float:
        return float(self._inputs["floor"]["value"])

    @property
    def floor_source(self) -> str:
        return self._inputs["floor"]["source"]

    @property
    def z(self) -> float:
        return float(self._inputs["cut"]["z"])

    @property
    def keep(self) -> int:
        return int(self._inputs["cut"]["keep"])

    @property
    def groups(self) -> list[list[str]]:
        return [list(g) for g in self._results["cut"]["groups"]]

    @property
    def survivors(self) -> list[str] | None:
        s = self._results["cut"]["survivors"]
        return None if s is None else list(s)

    @property
    def unranked(self) -> bool:
        return bool(self._results["cut"]["unranked"])


def load_run(path: str | Path) -> Run:
    p = Path(path)
    if p.is_dir():
        found = sorted(p.glob("run-*.json"))
        if len(found) != 1:
            raise ValueError(f"{p} holds {len(found)} run records: {[f.name for f in found]}")
        p = found[0]
    return Run(json.loads(p.read_text()), p.parent)


def _m(run: Run, name: str) -> Measurement:
    d = run.record["results"]["paired_ddE"][name]
    return Measurement(float(d["mean"]), float(d["sem"]), int(d["n"]), tuple(d["per_pose"]))


def ddE_table(run: Run) -> list[dict]:
    rows = []
    surv = run.survivors
    for gi, g in enumerate(run.groups):
        for name in g:
            m = _m(run, name)
            rows.append(
                {
                    "candidate": name,
                    "smiles": run.candidates[name],
                    "mean": m.mean,
                    "sem": m.sem,
                    "n": m.n,
                    "lo": m.mean - run.z * m.sem,
                    "hi": m.mean + run.z * m.sem,
                    "unpaired_sem": run.record["results"]["unpaired_sem"][name],
                    "group": gi,
                    "survivor": None if run.unranked or surv is None else name in surv,
                }
            )
    return rows


def resolved_matrix(run: Run) -> tuple[list[str], list[list[bool]]]:
    names = [r["candidate"] for r in ddE_table(run)]
    ms = [_m(run, n) for n in names]
    return names, [
        [i != j and bool(resolved(a, b, run.z, run.floor)) for j, b in enumerate(ms)] for i, a in enumerate(ms)
    ]


def per_pose_table(run: Run, quantity: str = "dE_int") -> list[dict]:
    pp = run.record["results"].get("per_pose", {})
    have = sorted({q for v in pp.values() for q in v})
    if quantity not in have:
        raise KeyError(f"{quantity!r} not recorded; per_pose quantities present: {have}")
    parent = pp[run.parent][quantity]
    return [
        {"candidate": a, "pose": i, "value": v, "parent_value": parent[i], "paired": v - parent[i]}
        for a in run.analogues
        for i, v in enumerate(pp[a][quantity])
    ]


def funnel_rows(run: Run) -> list[dict]:
    return list(run.record["results"]["funnel"])


def pose_sdf(run: Run, candidate: str) -> str:
    entry = run.record["results"]["poses"][candidate]
    data = (run.out_dir / entry["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError(f"{entry['file']}: sha256 does not match the record")
    return data.decode()
