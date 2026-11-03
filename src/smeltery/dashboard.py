"""A centralized, dependency-free dashboard for smeltery runs and tasks.

Serves a single HTML page plus a small JSON API over the standard library's http.server.
It discovers run records under a root directory, surfaces task status from a TaskManager,
and can submit new tasks (typically `smeltery run ...`). No GUI library is required.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from smeltery.avogadro import export_run, launch_avogadro
from smeltery.gui.results import load_run
from smeltery.tasks import TemporalTaskManager as TaskManager


def discover_runs(root: str | Path, *, limit: int = 500) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    found = sorted(root.rglob("run-*.json"))
    for p in found:
        try:
            run = load_run(p)
        except Exception as e:  # noqa: BLE001 - a bad record must not break discovery
            out.append({"path": str(p), "error": str(e)})
            continue
        try:
            out.append(
                {
                    "path": str(p),
                    "out_dir": str(run.out_dir),
                    "campaign": run.campaign,
                    "parent": run.parent,
                    "candidates": list(run.candidates),
                    "analogues": run.analogues,
                    "survivors": run.survivors,
                    "unranked": run.unranked,
                    "groups": run.groups,
                    "floor": run.floor,
                    "floor_source": run.floor_source,
                    "z": run.z,
                    "keep": run.keep,
                }
            )
        except Exception as e:  # noqa: BLE001
            out.append({"path": str(p), "error": str(e)})
    return out[:limit]


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>smeltery dashboard</title>
<style>
body { font-family: system-ui, sans-serif; margin: 1.5rem; color: #222; }
h1 { margin: 0 0 .5rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: .25rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { border: 1px solid #ddd; padding: .35rem .5rem; font-size: .9rem; vertical-align: top; }
th { background: #f4f4f4; text-align: left; }
tr:hover { background: #fafafa; }
.badge { display: inline-block; padding: .1rem .4rem; border-radius: .25rem; font-size: .75rem; }
.running { background: #fff3cd; }
.succeeded { background: #d4edda; }
.failed { background: #f8d7da; }
.cancelled { background: #e2e3e5; }
.queued { background: #d1ecf1; }
form { display: grid; gap: .5rem; max-width: 720px; margin: 1rem 0; }
input, textarea { width: 100%; padding: .4rem; box-sizing: border-box; font-family: monospace; }
button { padding: .4rem .8rem; cursor: pointer; }
.row { display: flex; gap: .5rem; align-items: center; }
.small { font-size: .8rem; color: #666; }
pre { background: #f7f7f7; padding: .5rem; overflow: auto; max-height: 240px; }
</style>
</head>
<body>
<h1>smeltery dashboard</h1>
<p class="small">Root: <code id="root"></code></p>

<h2>Runs</h2>
<div id="runs">loading…</div>

<h2>Tasks</h2>
<div id="tasks">loading…</div>

<h3>Submit a task</h3>
<form id="submit">
  <input name="name" placeholder="name (optional)">
  <input name="command" placeholder="command, e.g. smeltery run config.toml --out out" required>
  <input name="cwd" placeholder="working directory (optional)">
  <button type="submit">Submit</button>
</form>

<script>
const root = new URLSearchParams(location.search).get('root') || '';
document.getElementById('root').textContent = root || '(server root)';

async function j(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function renderRuns(runs) {
  if (!runs.length) { document.getElementById('runs').textContent = 'no run records found'; return; }
  let html = '<table><tr><th>Campaign</th><th>Parent</th><th>Survivors</th>' +
    '<th>Unranked</th><th>Groups</th><th>Record</th><th>Actions</th></tr>';
  for (const r of runs) {
    if (r.error) { html += `<tr><td colspan="6" class="small">${r.path}: ${r.error}</td></tr>`; continue; }
    html += `<tr>
      <td>${r.campaign}</td><td>${r.parent}</td>
      <td>${r.survivors === null ? '—' : r.survivors.join(', ') || '(none)'}</td>
      <td>${r.unranked}</td>
      <td>${r.groups.map(g=>g.join(',')).join(' | ')}</td>
      <td class="small">${r.path}</td>
      <td><button onclick="openAvogadro('${r.path}')">Avogadro</button></td></tr>`;
  }
  html += '</table>';
  document.getElementById('runs').innerHTML = html;
}

function renderTasks(tasks) {
  if (!tasks.length) { document.getElementById('tasks').textContent = 'no tasks'; return; }
  let html = '<table><tr><th>Name</th><th>Status</th><th>Command</th><th>Return</th><th>Actions</th></tr>';
  for (const t of tasks) {
    html += `<tr>
      <td>${t.name}</td>
      <td><span class="badge ${t.status}">${t.status}</span></td>
      <td class="small">${t.command.join(' ')}</td>
      <td>${t.returncode === null ? '—' : t.returncode}</td>
      <td><button onclick="cancelTask('${t.id}')">cancel</button>
      <button onclick="showLog('${t.id}')">log</button></td></tr>`;
  }
  html += '</table><pre id="log"></pre>';
  document.getElementById('tasks').innerHTML = html;
}

async function openAvogadro(path) {
  try {
    const r = await j('/api/avogadro', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({run_path: path})});
    alert('Opened ' + r.opened);
  } catch (e) { alert(e.message); }
}

async function refresh() {
  try { renderRuns(await j('/api/runs')); } catch (e) { document.getElementById('runs').textContent = e.message; }
  try { renderTasks(await j('/api/tasks')); } catch (e) { document.getElementById('tasks').textContent = e.message; }
}

async function cancelTask(id) {
  await fetch('/api/tasks/' + id + '/cancel', {method:'POST'});
  refresh();
}

async function showLog(id) {
  const r = await j('/api/tasks/' + id + '/log');
  document.getElementById('log').textContent = r.log;
}

document.getElementById('submit').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const cmd = (fd.get('command') || '').trim();
  if (!cmd) return;
  await j('/api/tasks', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: fd.get('name'), command: cmd, cwd: fd.get('cwd')})
  });
  e.target.reset();
  refresh();
});

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "smeltery-dashboard/1"

    def _json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, text: str, content_type: str = "text/html; charset=utf-8", status: int = 200) -> None:
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/" or path == "/index.html":
            self._text(HTML)
            return
        if path == "/api/runs":
            limit = int(qs.get("limit", ["500"])[0])
            self._json(self.server.discover_runs(limit=limit))
            return
        if path == "/api/tasks":
            try:
                tasks = self.server.task_manager.list()
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, status=503)
                return
            self._json([t.to_dict() for t in tasks])
            return
        if path.startswith("/api/tasks/") and path.endswith("/log"):
            tid = path.split("/")[3]
            lines = int(qs.get("lines", ["40"])[0])
            try:
                log = self.server.task_manager.tail_log(tid, lines=lines)
            except KeyError:
                self._json({"error": "unknown task"}, status=404)
                return
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, status=503)
                return
            self._json({"log": log})
            return
        self._text("not found", status=404)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, status=400)
            return
        if path == "/api/tasks":
            command = body.get("command")
            if isinstance(command, str):
                try:
                    command = shlex.split(command)
                except ValueError as e:
                    self._json({"error": f"invalid command: {e}"}, status=400)
                    return
            if not isinstance(command, list) or not command:
                self._json({"error": "command required"}, status=400)
                return
            name = body.get("name") or ""
            cwd = body.get("cwd") or os.getcwd()
            try:
                task = self.server.task_manager.submit(name, command, cwd=cwd)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, status=503)
                return
            self._json(task.to_dict(), status=201)
            return
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "cancel":
            tid = parts[2]
            try:
                ok = self.server.task_manager.cancel(tid)
            except KeyError:
                self._json({"error": "unknown task"}, status=404)
                return
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, status=503)
                return
            t = self.server.task_manager.get(tid)
            self._json({"cancelled": ok, "task": t.to_dict() if t else None})
            return
        if path == "/api/avogadro":
            run_path = body.get("run_path")
            if not run_path:
                self._json({"error": "run_path required"}, status=400)
                return
            try:
                run = load_run(run_path)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, status=400)
                return
            out_dir = Path(run_path) if Path(run_path).is_dir() else Path(run_path).parent
            dest = out_dir / "avogadro"
            try:
                export_run(run, dest)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, status=500)
                return
            target = None
            sdf = sorted(dest.glob("*.sdf"))
            if sdf:
                target = sdf[0]
            elif (dest / "receptor.pdb").exists():
                target = dest / "receptor.pdb"
            if target is not None:
                try:
                    launch_avogadro(target)
                except Exception as e:  # noqa: BLE001
                    self._json({"error": str(e), "dest": str(dest)}, status=500)
                    return
            self._json({"dest": str(dest), "opened": str(target) if target else None})
            return
        self._text("not found", status=404)

    def log_message(self, *args):
        return


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root, host="127.0.0.1", port=0, task_manager=None, temporal_host="127.0.0.1:7233"):
        self.root = Path(root).resolve()
        self.task_manager = task_manager or TaskManager(host=temporal_host)
        super().__init__((host, port), DashboardHandler)

    def discover_runs(self, *, limit: int = 500):
        return discover_runs(self.root, limit=limit)


def serve(root, *, host="127.0.0.1", port=0, task_manager=None, temporal_host="127.0.0.1:7233", open_browser=False):
    httpd = DashboardServer(root, host=host, port=port, task_manager=task_manager, temporal_host=temporal_host)
    actual = httpd.server_address[1]
    url = f"http://{host}:{actual}"
    print(f"smeltery dashboard: serving {httpd.root} at {url}", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="smeltery dashboard", description="Centralized dashboard for runs and tasks.")
    ap.add_argument("root", nargs="?", default=".", help="directory to scan for run records")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--temporal-host", default="127.0.0.1:7233", help="Temporal server host")
    ap.add_argument("--open", action="store_true", help="open a browser")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return serve(
        args.root,
        host=args.host,
        port=args.port,
        temporal_host=args.temporal_host,
        open_browser=args.open,
    )


if __name__ == "__main__":
    raise SystemExit(main())
