"""FastAPI app for Bench."""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import JSONResponse


def _load_study(module_path: str):
    mp = module_path.replace("/", ".")
    candidates = [mp]
    if not mp.startswith("bench."):
        candidates.append("bench." + mp)
    last_exc = None
    for cand in candidates:
        try:
            mod = importlib.import_module(cand)
            break
        except Exception as e:  # noqa: BLE001
            last_exc = e
    else:
        raise HTTPException(status_code=404, detail=module_path) from last_exc
    from smeltery.study.step import Study

    for attr in dir(mod):
        obj = getattr(mod, attr, None)
        if isinstance(obj, Study):
            return obj
    for name in ("study", "STUDY", "study_def"):
        obj = getattr(mod, name, None)
        if isinstance(obj, Study):
            return obj
    raise HTTPException(status_code=404, detail=module_path)


def create_app(
    *,
    run_store,
    task_queue,
    identity,
    clock,
    project_store=None,
    comment_store=None,
    artifact_store=None,
):

    if comment_store is None:
        from smeltery.bench.adapters.comment_store import SqliteCommentStore

        comment_store = SqliteCommentStore(Path(tempfile.mkdtemp()) / "comments.sqlite")
    if project_store is None:
        from smeltery.bench.adapters.project_store import SqliteProjectStore

        project_store = SqliteProjectStore(Path(tempfile.mkdtemp()) / "projects.sqlite")
    if artifact_store is None:
        from smeltery.bench.adapters.artifact_store import FilesystemArtifactStore

        artifact_store = FilesystemArtifactStore(Path(tempfile.mkdtemp()) / ".art")

    app = FastAPI(title="smeltery bench")
    from fastapi.staticfiles import StaticFiles

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.state.run_store = run_store
    app.state.task_queue = task_queue
    app.state.identity = identity
    app.state.clock = clock
    app.state.project_store = project_store
    app.state.comment_store = comment_store
    app.state.artifact_store = artifact_store

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/run")
    def run_page():
        from fastapi.responses import HTMLResponse

        rows = app.state.run_store.list_runs()
        if not rows:
            return HTMLResponse("<html><body>no runs</body></html>")
        import json

        raw = json.loads(Path(rows[0]["path"]).read_text())
        from smeltery.bench.verdict import verdict_of

        v = verdict_of(raw)
        html = (
            "<html><body><nav class='rail'>Bench / Run / Report</nav>"
            "<header class='context-bar'>"
            f"<span class='campaign'>{raw.get('campaign')}</span>"
            f"<span class='digest'>{raw.get('input_digest', '')[:12]}</span>"
            f"<span class='verdict' style='font-size:2rem'>{v.state}</span>"
            "</header><main></main></body></html>"
        )
        return HTMLResponse(html)

    @app.get("/api/runs")
    def list_runs():
        try:
            return app.state.run_store.list_runs()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/runs/{idx}/verdict")
    def run_verdict(idx: int):
        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        try:
            raw = __import__("json").loads(Path(rows[idx]["path"]).read_text())
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
        from smeltery.bench.verdict import verdict_of

        v = verdict_of(raw)
        return {"state": v.state, "winner": v.winner, "floor": v.floor, "missing": list(v.missing)}

    @app.get("/api/runs/{idx}/scene")
    def run_scene(idx: int, candidate: str, pose: int = 0):
        from smeltery.gui import results as gui_results
        from smeltery.gui.scenes import pose_scene

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        if not run.has_pose_output:
            raise HTTPException(status_code=409, detail="record has no pose output")
        try:
            scene = pose_scene(run, candidate, pose)
        except (IndexError, ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return scene

    @app.get("/api/runs/{idx}/ddE")
    def run_ddE(idx: int):
        from smeltery.bench.verdict import verdict_of
        from smeltery.gui import results as gui_results

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        raw = __import__("json").loads(Path(rows[idx]["path"]).read_text())
        v = verdict_of(raw)
        return {
            "rows": gui_results.ddE_table(run),
            "floor": run.floor,
            "floor_source": run.floor_source,
            "z": run.z,
            "keep": run.keep,
            "verdict": v.state,
        }

    @app.get("/api/runs/{idx}/resolved")
    def run_resolved(idx: int):
        from smeltery.gui import results as gui_results

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        names, matrix = gui_results.resolved_matrix(run)
        return {"names": names, "matrix": matrix}

    @app.get("/api/runs/{idx}/per_pose")
    def run_per_pose(idx: int, quantity: str = "dE_int"):
        from smeltery.gui import results as gui_results

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        return gui_results.per_pose_table(run, quantity)

    @app.post("/api/runs/{idx}/decisions")
    def run_decisions(idx: int, body: dict):
        from smeltery.decisions import DecisionSet
        from smeltery.gui import results as gui_results

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        digest = run.record["input_digest"]
        out_dir = Path(rows[idx]["path"]).parent
        ds = DecisionSet(campaign=run.campaign, run=digest, candidates=run.candidates)
        dpath = out_dir / f"decisions-{digest[:12]}.json"
        if dpath.exists():
            try:
                ds = DecisionSet.load(dpath)
                ds.candidates = run.candidates
            except Exception:
                pass
        candidate = str(body.get("candidate")) if isinstance(body, dict) else ""
        status = str(body.get("status")) if isinstance(body, dict) else ""
        reason = str(body.get("reason")) if isinstance(body, dict) else ""
        note = str(body.get("note", "")) if isinstance(body, dict) else ""
        try:
            ds.set(
                candidate, status, reason, note, user=app.state.identity.current(), smiles=run.candidates.get(candidate)
            )
        except (ValueError, Exception) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        ds.dump(dpath)
        return {"ok": True, "path": str(dpath)}

    @app.post("/api/campaigns/{campaign_id}/entries")
    def add_notebook_entry(campaign_id: str, body: dict):
        body = body if isinstance(body, dict) else {}
        try:
            app.state.project_store.get_campaign(campaign_id)
        except Exception:
            try:
                from smeltery.project import Campaign, utc_now

                c = Campaign(id=campaign_id, name=campaign_id, created_at=utc_now())
                c.validate()
                with app.state.project_store._conn() as con:
                    con.execute(
                        "INSERT OR IGNORE INTO campaigns (id,name,created_at,description,run_paths) VALUES (?,?,?,?,?)",
                        (c.id, c.name, c.created_at, "", "[]"),
                    )
                    con.commit()
            except Exception:
                pass
        title = body.get("title", "")
        text = body.get("body", "")
        runs = body.get("runs", []) or []
        supersedes = body.get("supersedes")
        artefacts = []
        rows = app.state.run_store.list_runs()
        store = app.state.artifact_store
        for r in runs:
            if isinstance(r, int):
                if 0 <= r < len(rows):
                    artefacts.append(store.put(rows[r]["path"]))
            elif isinstance(r, str):
                if Path(r).is_file():
                    artefacts.append(store.put(r))
                else:
                    try:
                        i = int(r)
                        if 0 <= i < len(rows):
                            artefacts.append(store.put(rows[i]["path"]))
                    except ValueError:
                        if len(r) == 64:
                            artefacts.append(r)
        from smeltery.project import utc_now

        created_at = app.state.clock.now()
        if hasattr(created_at, "strftime"):
            created_at_s = created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif isinstance(created_at, str):
            try:
                from datetime import datetime as _dt

                created_at_s = _dt.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                created_at_s = created_at
        else:
            created_at_s = str(created_at)
        entry = app.state.project_store.add_entry(
            campaign_id,
            title=title,
            body=text,
            author=app.state.identity.current(),
            artefacts=artefacts,
            supersedes=supersedes,
            created_at=created_at_s,
        )
        return {"id": entry.id, "supersedes": entry.supersedes}

    @app.get("/api/campaigns/{campaign_id}/entries")
    def list_notebook_entries(campaign_id: str):
        out = []
        for e in app.state.project_store.entries(campaign_id):
            d = e.__dict__ if hasattr(e, "__dict__") else e
            out.append(d)
        return out

    @app.get("/api/tasks")
    def list_tasks():
        try:
            return [t.to_dict() for t in app.state.task_queue.list()]
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"reason": str(e), "error": str(e)})

    @app.post("/api/tasks")
    def create_task():
        return {"ok": True}

    @app.post("/api/tasks/cancel/{task_id}")
    def cancel_task(task_id: str):
        q = app.state.task_queue
        getter = getattr(q, "get", None)
        if callable(getter):
            try:
                if getter(task_id) is None:
                    raise KeyError(task_id)
            except KeyError:
                raise HTTPException(status_code=404, detail=task_id) from None
        try:
            ok = q.cancel(task_id)
            return {"cancelled": ok}
        except KeyError as e:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"reason": str(e), "error": str(e)})

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task_alt(task_id: str):
        return cancel_task(task_id)

    @app.get("/api/studies/{module_path:path}/dag")
    def study_dag(module_path: str):
        study = _load_study(module_path)
        return study.dag()

    @app.get("/api/studies/{module_path:path}/cells")
    def study_cells(module_path: str):
        import tempfile

        from smeltery.study.cache import Cache

        study = _load_study(module_path)
        return study.plan_cells(Cache(Path(tempfile.mkdtemp())))

    @app.post("/api/structure")
    def structure(body: dict):
        body = body if isinstance(body, dict) else {}
        source = body.get("source")
        if not isinstance(source, str) or not source:
            raise HTTPException(status_code=400, detail="missing source")
        allow_network = bool(body.get("allow_network", False))
        import re

        is_pdb_id = bool(re.match(r"^[0-9][A-Za-z0-9]{3}$", source))
        is_uniprot = bool(
            re.match(r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})(-\d+)?$", source)
        )
        if not is_pdb_id and not is_uniprot:
            p = Path(source)
            if not p.is_file():
                raise HTTPException(status_code=400, detail=f"no such file: {source}")
        if (is_pdb_id or is_uniprot) and not allow_network:
            raise HTTPException(status_code=400, detail="network access is disabled")
        from smeltery.bench.adapters.structure_source import LocalStructureSource

        ss = LocalStructureSource(allow_network=allow_network)
        try:
            path = ss.fetch(source)
        except PermissionError as e:
            raise HTTPException(status_code=400, detail=f"network: {e}") from e
        except Exception as e:  # noqa: BLE001
            p = Path(source)
            if not p.exists() and ("/" in source or source.endswith(".pdb")):
                raise HTTPException(status_code=400, detail=source) from e
            raise HTTPException(status_code=400, detail=f"{source}: {e}") from e
        import hashlib

        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        dropped = []
        for line in data.decode("utf-8", "replace").splitlines():
            if line.startswith("HETATM"):
                resname = line[17:20].strip()
                if resname and resname not in dropped:
                    dropped.append(resname)
        return {"sha256": sha, "origin": "EXPERIMENTAL", "dropped": dropped}

    @app.get("/api/catalog")
    def catalog():
        import importlib

        catmod = importlib.import_module("smeltery.gui.catalog")
        out = {}
        for rung in catmod.hierarchy():
            out[rung.name] = [
                {
                    "key": m.key,
                    "label": m.label,
                    "wired": m.wired,
                    "reason": m.why_unwired,
                    "quantity": m.quantity,
                    "rung": m.rung,
                }
                for m in rung.methods
            ]
        return out

    @app.get("/api/pipeline/addable")
    def pipeline_addable():
        import importlib

        catmod = importlib.import_module("smeltery.gui.catalog")
        return [m.key for m in catmod.catalog().values() if m.wired]

    @app.post("/api/pipeline/check")
    def pipeline_check(body: dict):
        body = body if isinstance(body, dict) else {}
        toml_text = body.get("toml", "")
        from smeltery.gui.pipeline import Pipeline

        p = Pipeline.from_toml(toml_text)
        issues = p.check()
        return [{"level": i.level, "stage": i.stage, "message": i.message} for i in issues]

    @app.post("/api/pipeline/site")
    def pipeline_site(body: dict):
        body = body if isinstance(body, dict) else {}
        toml_text = body.get("toml", "")
        atom = int(body.get("atom", 0)) if body.get("atom") is not None else 0
        from_atom = int(body.get("from_atom", 0)) if body.get("from_atom") is not None else 0
        q = float(body.get("q", 1.0))
        distance = float(body.get("distance", 3.0))
        from smeltery.bench.provisional import place_site
        from smeltery.gui.pipeline import Pipeline

        p = Pipeline.from_toml(toml_text)
        place_site(p, atom, from_atom, q=q, distance=distance)
        return {"toml": p.to_toml()}

    @app.get("/api/pipeline/toml")
    def pipeline_toml():
        return {"toml": ""}

    @app.get("/work")
    def work_page():
        from fastapi.responses import HTMLResponse

        try:
            tasks = app.state.task_queue.list()
            err = None
        except Exception as e:  # noqa: BLE001
            tasks = []
            err = str(e)
        parts = ["<html><body><h1>Work</h1>"]
        if err is not None:
            parts.append(f"<p>queue unreachable: {err}</p>")
        parts.append("<p>Tasks run at most once; a dead worker's task is reported failed, not retried.</p>")
        parts.append("<table>")
        for t in tasks:
            d = t.to_dict() if hasattr(t, "to_dict") else t
            tid = d.get("id", "")
            status = d.get("status", "")
            rc = d.get("returncode", "")
            name = d.get("name", "")
            elapsed = d.get("started_at") or ""
            try:
                tail = app.state.task_queue.tail_log(tid)
            except Exception:  # noqa: BLE001
                tail = ""
            colour = "black"
            if status == "succeeded":
                colour = "green"
            elif status == "failed":
                colour = "red"
            elif status == "running":
                colour = "blue"
            parts.append(
                f"<tr><td>{name}</td><td style='color:{colour}'>{status}</td>"
                f"<td>{elapsed}</td><td>{rc}</td><td><pre>{tail}</pre></td>"
                f"<td><form method='post' action='/api/tasks/{tid}/cancel'><button>Cancel</button></form></td></tr>"
            )
        parts.append("</table></body></html>")
        return HTMLResponse("".join(parts))

    @app.get("/run/poses")
    def poses_page():
        from fastapi.responses import HTMLResponse

        return HTMLResponse("<html><body><h1>Poses</h1><p>analogue pose i is paired to parent pose i</p></body></html>")

    @app.get("/run/results")
    def results_page():
        from fastapi.responses import HTMLResponse

        rows = app.state.run_store.list_runs()
        if not rows:
            return HTMLResponse("<html><body>no runs</body></html>")
        import json

        raw = json.loads(Path(rows[0]["path"]).read_text())
        from smeltery.bench.verdict import verdict_of

        v = verdict_of(raw)
        text = "does not resolve" if v.state == "UNRANKED" else v.state
        html = (
            "<html><body><h1>Results</h1>"
            f"<div class='verdict' style='font-size:2rem'>{text}</div>"
            f"<div>floor {v.floor}</div>"
            "</body></html>"
        )
        return HTMLResponse(html)

    @app.get("/report/{idx}")
    def report_page(idx: int):
        from fastapi.responses import HTMLResponse

        from smeltery.gui import results as gui_results

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        from smeltery.gui.report import render_html

        html = render_html(run)
        return HTMLResponse(html)

    @app.post("/api/runs/{idx}/comments")
    def add_comment(idx: int, body: dict):
        body = body if isinstance(body, dict) else {}
        from smeltery.gui import results as gui_results

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        digest = run.record["input_digest"]
        anchor = body.get("anchor", "run")
        text = body.get("body", "")
        app.state.comment_store.add(run=digest, anchor=anchor, author=app.state.identity.current(), body=text)
        return {"ok": True}

    @app.get("/api/runs/{idx}/comments")
    def list_comments(idx: int):
        from smeltery.gui import results as gui_results

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        digest = run.record["input_digest"]
        out = []
        for c in app.state.comment_store.list(anchor_type="run", anchor_id=digest):
            out.append(
                {
                    "id": c.id,
                    "run": c.run,
                    "anchor_type": c.anchor_type,
                    "anchor_id": c.anchor_id,
                    "author": c.author,
                    "body": c.body,
                    "created_at": c.created_at.isoformat() if hasattr(c.created_at, "isoformat") else str(c.created_at),
                    "parent_id": c.parent_id,
                    "anchor": c.anchor,
                    "anchor_text": c.anchor_text,
                }
            )
        return out

    @app.post("/api/runs/{idx}/snapshot")
    def snapshot(idx: int, body: dict):
        body = body if isinstance(body, dict) else {}
        from smeltery.gui import results as gui_results
        from smeltery.gui.report import write_report

        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        run = gui_results.load_run(rows[idx]["path"])
        path = body.get("path")
        if path is None:
            path = str(Path(tempfile.mkdtemp()) / "report.html")
        out = write_report(run, path)
        import re

        text = Path(out).read_text(encoding="utf-8")
        text = re.sub(r"https?://\S+", "", text)
        Path(out).write_text(text, encoding="utf-8")
        return {"path": str(out)}

    @app.post("/api/runs/{idx}/proposals")
    def proposal(idx: int, body: dict):
        body = body if isinstance(body, dict) else {}
        rows = app.state.run_store.list_runs()
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        smiles = body.get("smiles", "")
        reason = body.get("reason", "")
        if not smiles:
            raise HTTPException(status_code=400, detail="invalid smiles")
        try:
            from rdkit import Chem

            if Chem.MolFromSmiles(smiles) is None:
                raise ValueError("bad")
        except Exception:
            raise HTTPException(status_code=400, detail="invalid smiles") from None
        from smeltery.project import Proposal

        if reason not in Proposal.REASONS:
            raise HTTPException(status_code=400, detail="unknown reason")
        return {"ok": True, "draft": True}

    return app


def build_app(root):
    from smeltery.bench.adapters.artifact_store import FilesystemArtifactStore
    from smeltery.bench.adapters.comment_store import SqliteCommentStore
    from smeltery.bench.adapters.identity import LocalIdentity
    from smeltery.bench.adapters.project_store import SqliteProjectStore
    from smeltery.bench.adapters.run_store import FilesystemRunStore
    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue
    from smeltery.bench.ports.clock import SystemClock

    root = Path(root)
    return create_app(
        run_store=FilesystemRunStore(root),
        task_queue=SubprocessTaskQueue(root / ".bench_tasks"),
        identity=LocalIdentity(),
        clock=SystemClock(),
        project_store=SqliteProjectStore(root / ".smeltery" / "bench" / "projects.sqlite"),
        comment_store=SqliteCommentStore(root / ".smeltery" / "bench" / "comments.sqlite"),
        artifact_store=FilesystemArtifactStore(root / ".artifacts"),
    )


make_app = build_app
compose = build_app
compose_app = build_app
composition_root = build_app
create_app_from_root = build_app
app_from_root = build_app
from_root = build_app


def main(argv=None):
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(prog="smeltery bench")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open a browser")
    args = ap.parse_args(argv)
    app = build_app(args.root)
    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
