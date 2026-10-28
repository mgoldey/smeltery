"""`smeltery gui OUT`: serve the Marimo app (Pipeline / Results / Grid / Pose) for one run directory.

The app itself is `smeltery/gui/views/notebook.py`, a plain Marimo `.py` file; this module launches it (`main`) and
holds the decision bookkeeping the app calls, so neither needs a GUI library to import. OUT reaches the app through
the `SMELTERY_GUI_OUT` environment variable, or `-- --out OUT` after `marimo run` / `marimo edit`.

Decisions go to `OUT/decisions-<input_digest[:12]>.json` through `smeltery.decisions.DecisionSet.dump`; its
`validate` is what refuses, for example, a `within_tie_choice` without a note. The GUI adds no rule of its own.
"""

from __future__ import annotations

import argparse
import copy
import getpass
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

APP_FILE = Path(__file__).resolve().parent / "views" / "notebook.py"
ENV_OUT = "SMELTERY_GUI_OUT"
GUI_HINT = "pip install 'smeltery[gui]'"
DESKTOP_HINT = "pip install 'smeltery[desktop]'"


# ---------------------------------------------------------------------------- where the run is


def resolve_out(cli_args=None) -> Path:
    """The OUT directory (or run-*.json) the app shows: `--out` after `--` on the marimo command line, else
    `$SMELTERY_GUI_OUT`."""
    value = (cli_args or {}).get("out") or os.environ.get(ENV_OUT)
    if not value:
        raise ValueError(f"no run to show: set {ENV_OUT}=OUT, or pass `-- --out OUT` to marimo")
    return Path(value)


def sibling_runs(out: Path, *, limit: int = 60) -> list[Path]:
    """Other run directories beside `out`, for the Compare tab. Never includes `out` itself.

    A comparison needs several runs and the app is given one, so the siblings are found rather than
    configured: every immediate sibling directory of `out` that holds a `run-*.json`. That is a
    convention, not a contract -- a study whose runs live elsewhere shows nothing here, and the tab
    says so rather than pretending the comparison is unavailable in principle.
    """
    out = Path(out)
    # `out` may be the OUT directory or a run-*.json inside it; siblings are always beside the DIRECTORY.
    # Excluding by the passed path alone would leave a record's own directory in the list, since a file
    # path never equals a directory path.
    out_dir = out if out.is_dir() else out.parent
    base = out_dir.parent
    if not base.is_dir():
        return []
    found = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.resolve() == out_dir.resolve():
            continue
        if any(d.glob("run-*.json")):
            found.append(d)
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------------------- decisions


def decisions_path(run) -> Path:
    """`OUT/decisions-<input_digest[:12]>.json`, next to the run record (docs/gui-review.md)."""
    return Path(run.out_dir) / f"decisions-{run.record['input_digest'][:12]}.json"


def new_decision_set(run):
    """An empty decision set for `run`, knowing the run's SMILES so `set` can fill them in."""
    from smeltery.decisions import DecisionSet

    return DecisionSet(campaign=run.campaign, run=run.record["input_digest"], candidates=dict(run.candidates))


def load_decisions(run):
    """The decisions already saved for this run, or an empty set. A file that does not validate is a ValueError
    naming it (`DecisionSet.load`)."""
    from smeltery.decisions import DecisionSet

    path = decisions_path(run)
    if not path.exists():
        return new_decision_set(run)
    ds = DecisionSet.load(path)
    ds.candidates = dict(run.candidates)
    return ds


def decision_map(ds) -> dict[str, dict]:
    """{candidate: {status, reason, note, user, time}} for drawing."""
    return {
        d.candidate: {"status": d.status, "reason": d.reason, "note": d.note, "user": d.user, "time": d.time}
        for d in ds.decisions
    }


def apply_decisions(run, ds, candidates, status: str, reason: str, note: str = "", user: str | None = None):
    """Set one decision on each of `candidates` and save, all or nothing. Returns (decision set, problems): on any
    problem (`DecisionSet.set` / `validate` refuse, e.g. `within_tie_choice` without a note) nothing is saved and
    the decision set passed in is returned unchanged."""
    candidates = list(candidates or [])
    if not candidates:
        return ds, ["select at least one candidate"]
    user = user or getpass.getuser()
    new = copy.deepcopy(ds)
    problems = []
    for c in candidates:
        if c not in run.candidates:
            problems.append(f"{c!r} is not a candidate of this run")
            continue
        try:
            new.set(c, status, reason, note or "", user, smiles=run.candidates[c])
        except ValueError as e:
            problems.append(str(e))
    if problems:
        return ds, problems
    path = decisions_path(run)
    if path.exists():
        try:
            load_decisions(run)
        except ValueError as e:
            return ds, [f"{path.name} exists but does not validate; fix or move it first ({e})"]
    try:
        new.dump(path)
    except (ValueError, OSError) as e:
        return ds, [str(e)]
    return new, []


# ---------------------------------------------------------------------------- serving


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(port: int, proc: subprocess.Popen, timeout: float = 60.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def server_command(out: Path, port: int, *, headless: bool) -> list[str]:
    cmd = [sys.executable, "-m", "marimo", "run", str(APP_FILE), "--host", "127.0.0.1", "--port", str(port)]
    if headless:
        cmd.append("--headless")
    return [*cmd, "--", "--out", str(out)]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="smeltery gui", description="Browse a run and record decisions.")
    ap.add_argument("out", type=Path, help="the run's OUT directory (or its run-*.json)")
    ap.add_argument("--port", type=int, default=None, help="port to serve on (default: a free one)")
    ap.add_argument("--desktop", action="store_true", help=f"open in a desktop window ({DESKTOP_HINT})")
    ap.add_argument("--no-browser", action="store_true", help="serve only; do not open a browser")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    webview = None
    if args.desktop:
        try:
            import webview  # pywebview
        except ImportError:
            print(f"smeltery gui --desktop needs pywebview: {DESKTOP_HINT}", file=sys.stderr)
            return 2
    try:
        import marimo  # noqa: F401
    except ImportError:
        print(f"smeltery gui needs the gui extra: {GUI_HINT}", file=sys.stderr)
        return 2
    from smeltery.gui.results import load_run

    try:
        run = load_run(args.out)
    except (ValueError, OSError) as e:
        print(f"smeltery gui: {e}", file=sys.stderr)
        return 2
    out = Path(args.out).resolve()
    port = args.port or _free_port()
    env = {**os.environ, ENV_OUT: str(out)}
    headless = args.desktop or args.no_browser
    cmd = server_command(out, port, headless=headless)
    url = f"http://127.0.0.1:{port}"
    if webview is None:
        print(f"smeltery gui: serving {run.campaign} at {url} (Ctrl+C stops)", file=sys.stderr)
        try:
            return subprocess.call(cmd, env=env)
        except KeyboardInterrupt:
            return 0
    proc = subprocess.Popen(cmd, env=env)
    try:
        if not _wait_for(port, proc):
            print("smeltery gui: the server did not start", file=sys.stderr)
            return 1
        webview.create_window(f"smeltery: {run.campaign}", url, width=1400, height=900)
        webview.start()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
