"""The frozen report of a run: rendered in memory for a download, or written beside the run as its snapshot.

Both ways out render through `render`, so the file a chemist downloads is byte-for-byte the file the notebook
archives. Rendering and writing are separate on purpose: rendering writes nothing, which is what lets the download
work on a `--read-only` server, where writing the snapshot is refused.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from smeltery.decisions import TIME_FORMAT
from smeltery.gui.report import MOLSTAR_CSS, MOLSTAR_JS, RDKIT_JS, RDKIT_WASM, render_html

from .record_decision import load as load_decisions

#: The frozen report's only external references, by design (docs/gui-contract.md section 4): pinned and
#: integrity-hashed copies of the 2D and 3D libraries. Its numbers, plots and tables need no network.
REPORT_EXTERNAL = (RDKIT_JS, RDKIT_WASM, MOLSTAR_JS, MOLSTAR_CSS)


def _utc(when) -> str:
    return when.strftime(TIME_FORMAT) if hasattr(when, "strftime") else str(when)


def report_name(run) -> str:
    """`report-<input_digest[:12]>.html`: the snapshot's file name, and the download's."""
    return f"report-{run.record['input_digest'][:12]}.html"


def external_urls(data: bytes) -> list[str]:
    """The URLs in `data` that are among `REPORT_EXTERNAL`, sorted."""
    found = sorted(set(re.findall(rb"https?://[^\s\"'<>)]+", data)))
    return [u.decode() for u in found if u.decode() in REPORT_EXTERNAL]


@dataclass(frozen=True)
class Snapshot:
    path: Path
    sha256: str
    #: the external references the written file makes (a subset of `REPORT_EXTERNAL`)
    external: list[str]


class PublishReport:
    def __init__(self, comment_store, clock):
        self.comment_store = comment_store
        self.clock = clock

    def render(self, run) -> str:
        """The report's HTML for `run`, with its comments and decisions, written nowhere."""
        digest = run.record["input_digest"]
        comments = [
            {"anchor": c.anchor_text or "run", "author": c.author, "time": _utc(c.created_at), "body": c.body}
            for c in self.comment_store.for_run(digest)
        ]
        decisions = [d.__dict__ for d in load_decisions(run).decisions]
        # One stamp from the injected clock, not two readings of the wall clock. The download and the snapshot render
        # the same report, and the report carries the time it was written to the second: rendering twice across a tick
        # produced two files differing by one digit, which failed the byte-for-byte promise for no real reason.
        return render_html(run, comments, decisions, _utc(self.clock.now()))

    def write(self, run) -> Snapshot:
        """Render, and write the report beside the run (`report_name`), unmodified."""
        html = self.render(run)
        path = Path(run.out_dir) / report_name(run)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        data = path.read_bytes()
        return Snapshot(path=path, sha256=hashlib.sha256(data).hexdigest(), external=external_urls(data))
