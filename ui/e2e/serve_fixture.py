"""Serve Bench over three real fixture runs for the browser tests.

Builds, under a fresh temporary root, run directories with `tests/gui_fixture.make_run_dir`:
  a_resolved/out     -- RESOLVED
  b_unranked/out     -- UNRANKED (keep=2 falls inside a tie group)
  c_incomplete/out   -- INCOMPLETE (results.cut deleted, as when a task dies mid-SCF)
writes what a test needs to know about them to e2e/.fixture.json, and serves `smeltery bench` on BENCH_E2E_PORT.
BENCH_E2E_BROKEN_QUEUE=1 serves with a task queue that raises, for the degraded-panel tests.

Tests read expected values from .fixture.json and the API, never from constants in the test.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "src"))

from gui_fixture import make_run_dir  # noqa: E402

RUNS = (("a_resolved", {}, False), ("b_unranked", {"unranked": True}, False), ("c_incomplete", {}, True))


def build(root: Path) -> list[dict]:
    out = []
    for name, kw, incomplete in RUNS:
        d = make_run_dir(root / name, **kw)
        rec_path = next(d.glob("run-*.json"))
        rec = json.loads(rec_path.read_text())
        if incomplete:
            del rec["results"]["cut"]
            rec_path.write_text(json.dumps(rec))
        out.append(
            {"name": name, "path": str(rec_path), "out_dir": str(d), "campaign": rec["campaign"], "digest": rec["input_digest"]}
        )
    return sorted(out, key=lambda r: r["path"])


class BrokenQueue:
    """A task backend that is down: every call raises, so the panel must degrade and the rest must not."""

    def _down(self):
        raise RuntimeError("task backend unreachable (BENCH_E2E_BROKEN_QUEUE)")

    def list(self, status=None):
        self._down()

    def submit(self, *a, **kw):
        self._down()

    def get(self, tid):
        return None

    def cancel(self, tid):
        self._down()

    def tail_log(self, tid, lines=40):
        raise KeyError(tid)


def main() -> int:
    import uvicorn

    from smeltery.bench.app import build_app

    port = int(os.environ.get("BENCH_E2E_PORT", "8799"))
    root = Path(tempfile.mkdtemp(prefix="bench-e2e-"))
    runs = build(root)
    for i, r in enumerate(runs):
        r["idx"] = i
    pdb = REPO / "tests" / "data" / "pocket_pep5.pdb"
    (HERE / ".fixture.json").write_text(
        json.dumps({"root": str(root), "runs": runs, "study": "bench.study_fixture", "pdb": str(pdb)}, indent=1)
    )
    app = build_app(root, studies=("bench.study_fixture",))
    if os.environ.get("BENCH_E2E_BROKEN_QUEUE"):
        app.state.task_queue = BrokenQueue()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
