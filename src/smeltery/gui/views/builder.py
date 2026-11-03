"""The pipeline builder over `smeltery.gui.Pipeline` and `catalog_json()`: a palette by rung, the stage list with
its issues, a knob form per stage and the TOML preview. All rules are the pipeline's (`Pipeline.check()`); this
module only draws them. The HTML functions need no GUI library; `knob_widgets` needs marimo."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from ..catalog import catalog_json, method
from ..pipeline import Pipeline, PipelineError

_LEVEL = {"error": ("#e34948", "error"), "warning": ("#eda100", "warning"), "note": ("#2a78d6", "note")}


def initial_pipeline(run) -> Pipeline:
    """The pipeline to start from for a run: the config in OUT when there is exactly one `*.toml`, else the
    minimal pipeline for the run's candidates with its cut settings (the pocket's sites are not in the record)."""
    tomls = sorted(Path(run.out_dir).glob("*.toml"))
    if len(tomls) == 1:
        try:
            return Pipeline.from_toml(tomls[0].read_text())
        except Exception:  # a broken file: fall back, the user can paste it into the TOML box to see why
            pass
    p = Pipeline.minimal(run.campaign, run.parent, run.candidates)
    knobs = {"keep": run.keep, "z": run.z}
    if "explicit" in (run.floor_source or ""):
        knobs["floor"] = run.floor
    names = {p.name for p in method("cut").params}
    p.set("cut", **{k: v for k, v in knobs.items() if k in names})
    return p


def palette(pipeline: Pipeline) -> list[dict]:
    """The palette rows by rung, cheapest first: each method with whether it can be added now, and why not."""
    cat = catalog_json()
    addable = set(pipeline.addable())
    placed = set(pipeline.keys())
    out = []
    for rung in cat["rungs"]:
        methods = []
        for key in rung["methods"]:
            m = cat["methods"][key]
            if key in placed:
                state, why = "placed", "in the pipeline"
            elif key in addable:
                state, why = "addable", ""
            elif not m["wired"]:
                state, why = "unwired", m["why_unwired"]
            elif m["implicit"]:
                state, why = "implicit", "always runs"
            else:
                state, why = "blocked", "excluded by a placed stage"
            methods.append({"key": key, "label": m["label"], "role": m["role"], "state": state, "why": why})
        out.append({"rung": rung["name"], "description": rung["description"], "methods": methods})
    return out


def palette_html(pipeline: Pipeline) -> str:
    chips = {
        "addable": "background:#eef4fc;border:1px solid #2a78d6;color:#0b0b0b",
        "placed": "background:#2a78d6;border:1px solid #2a78d6;color:white",
        "unwired": "background:#f1f0ec;border:1px dashed #c3c2b7;color:#6b6a66",
        "implicit": "background:#f1f0ec;border:1px solid #c3c2b7;color:#52514e",
        "blocked": "background:#f1f0ec;border:1px solid #c3c2b7;color:#6b6a66;text-decoration:line-through",
    }
    parts = ['<div style="display:flex;flex-direction:column;gap:6px;font-size:13px">']
    for row in palette(pipeline):
        parts.append(
            '<div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap">'
            f'<div style="width:110px;font-weight:600" title="{escape(row["description"])}">{escape(row["rung"])}</div>'
        )
        for m in row["methods"]:
            tip = f"{m['key']} ({m['role']})" + (f": {m['why']}" if m["why"] else "")
            parts.append(
                f'<span title="{escape(tip)}" style="{chips[m["state"]]};border-radius:4px;padding:2px 8px">'
                f"{escape(m['label'])}</span>"
            )
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def stages_html(pipeline: Pipeline) -> str:
    """The stages in execution order (implicit ones included), with every issue under the stage it names."""
    issues = pipeline.check()
    by_stage: dict = {}
    for i in issues:
        by_stage.setdefault(i.stage, []).append(i)
    rows = []

    def issue_html(i) -> str:
        colour, label = _LEVEL[i.level]
        return (
            f'<div style="margin-left:16px;border-left:3px solid {colour};padding-left:6px;color:#0b0b0b">'
            f"<b>{label}</b>: {escape(i.message)}</div>"
        )

    for r in pipeline.view():
        n_in = "" if r["candidates_in"] is None else r["candidates_in"]
        n_out = r["candidates_out"]
        out = "" if n_out is None and r["candidates_in"] is None else (n_out if n_out is not None else "?")
        if r.get("may_be_unranked"):
            out = f"{out} (or UNRANKED)"
        tag = " <i>(implicit)</i>" if r["implicit"] else ""
        rows.append(
            '<div style="padding:4px 0;border-bottom:1px solid #e4e3de">'
            f"<b>{escape(r['label'])}</b>{tag} "
            f'<span style="color:#6b6a66">{escape(r["stage"])} · {escape(r["rung"])} · {escape(r["role"])}'
            f"{f' · {n_in} → {out}' if n_in != '' else ''}</span>"
            + "".join(issue_html(i) for i in by_stage.get(r["stage"], []))
            + "</div>"
        )
    general = by_stage.get(None, []) + [i for k, v in by_stage.items() if k not in pipeline.keys() and k for i in v]
    head = ""
    if general:
        head = '<div style="padding:4px 0">' + "".join(issue_html(i) for i in general) + "</div>"
    n_err = sum(i.level == "error" for i in issues)
    status = (
        '<div style="color:#1baf7a;font-weight:600">checks clean: smeltery run accepts this config</div>'
        if not n_err
        else f'<div style="color:#e34948;font-weight:600">{n_err} error(s) block the run</div>'
    )
    return f'<div style="font-size:13px">{status}{head}{"".join(rows)}</div>'


def toml_preview(pipeline: Pipeline) -> tuple[str, bool]:
    """(text, ok). With errors, the draft config is shown under a comment saying it will not run."""
    try:
        return pipeline.to_toml(), True
    except PipelineError as e:
        from .._toml import dumps

        head = "".join(f"# {i.level}: {i.stage or 'pipeline'}: {i.message}\n" for i in e.issues)
        return f"# DRAFT: smeltery run refuses this config until these are fixed\n{head}\n" + dumps(
            pipeline.to_config()
        ), False


# ---------------------------------------------------------------------------- knob forms (marimo)


def _text(value) -> str:
    return "" if value is None else json.dumps(value)


def knob_widgets(pipeline: Pipeline, key: str):
    """A `mo.ui.batch` (value: a dict) of one widget per knob of stage `key`, at its current value. bool is a
    checkbox, choice a dropdown, anything else JSON text (empty = unset, back to the default)."""
    import marimo as mo

    stage = pipeline.stage(key)
    widgets = {}
    for p in stage.info.params:
        cur = stage.params.get(p.name)
        label = f"{p.name}{' *' if p.required else ''}"
        if p.kind == "bool":
            widgets[p.name] = mo.ui.checkbox(value=bool(cur if cur is not None else p.default), label=label)
        elif p.kind == "choice":
            opts = ["(default)"] + [str(c) for c in p.choices]
            widgets[p.name] = mo.ui.dropdown(opts, value=str(cur) if cur is not None else "(default)", label=label)
        else:
            widgets[p.name] = mo.ui.text(
                value=_text(cur), placeholder=_text(p.default) or p.kind, label=label, full_width=True
            )
    if not widgets:
        return mo.md(f"_{key} has no knobs_").batch()
    return mo.md("\n\n".join(f"{{{name}}}" for name in widgets)).batch(**widgets)


def parse_knobs(pipeline: Pipeline, key: str, values: dict) -> tuple[dict, list[str]]:
    """Form values to `Pipeline.set(key, **knobs)` arguments (None unsets), plus any parse errors."""
    params = {p.name: p for p in pipeline.stage(key).info.params}
    out, errors = {}, []
    for name, v in values.items():
        p = params[name]
        if p.kind == "bool":
            out[name] = bool(v) if bool(v) != bool(p.default) or name in pipeline.stage(key).params else None
        elif p.kind == "choice":
            out[name] = None if v in (None, "(default)") else next(c for c in p.choices if str(c) == v)
        elif not str(v).strip():
            out[name] = None
        else:
            try:
                out[name] = json.loads(v)
            except json.JSONDecodeError:
                if p.kind in ("str", "path"):
                    out[name] = str(v)
                else:
                    errors.append(f"{name}: {v!r} is not JSON (a {p.kind})")
    return out, errors
