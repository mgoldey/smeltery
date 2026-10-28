"""The smeltery Marimo app: Pipeline / Results / Grid / Pose for one run directory.

Serve it with `smeltery gui OUT` (smeltery.gui.app.main), or directly:
`SMELTERY_GUI_OUT=OUT marimo run src/smeltery/gui/views/notebook.py` (or `... -- --out OUT`).
"""

import marimo

__generated_with = "0.25.2"
app = marimo.App(width="full", app_title="smeltery")

with app.setup:
    import copy
    import getpass

    import marimo as mo

    from smeltery.decisions import REASONS, STATUSES
    from smeltery.gui import Pipeline
    from smeltery.gui import app as gui_app
    from smeltery.gui.results import ddE_table, funnel_rows, load_run, per_pose_table, resolved_matrix
    from smeltery.gui.scenes import pose_scene
    from smeltery.gui.views import builder
    from smeltery.gui.views.forest import forest_figure
    from smeltery.gui.views.funnel import funnel_figure
    from smeltery.gui.views.grid import grid_iframe, grid_records, molgrid, selected_candidates
    from smeltery.gui.views.matrix import matrix_figure
    from smeltery.gui.views.per_pose import per_pose_figure
    from smeltery.gui.views.pose import pose_iframe

    TABS = ("Results", "Grid", "Pose", "Pipeline")


@app.cell
def _():
    out_dir = gui_app.resolve_out(mo.cli_args())
    return (out_dir,)


@app.cell
def _(out_dir):
    run = load_run(out_dir)
    rows = ddE_table(run)
    units = run.record["results"].get("units", {})
    unit = units.get("dE_int", "kcal/mol")
    return rows, run, unit


@app.cell
def _(run):
    _cut = (
        "**UNRANKED**: the keep boundary falls inside a tie group, so the cut dropped nothing"
        if run.unranked
        else f"kept {len(run.survivors or [])} of {len(run.analogues)} analogues"
    )
    header = mo.md(
        f"## {run.campaign}\n\n"
        f"Parent **{run.parent}** · {len(run.analogues)} analogues · z = {run.z:g} · "
        f"floor {run.floor:g} ({run.floor_source}) · keep = {run.keep} · {_cut} · "
        f"run `{run.record['input_digest'][:12]}`"
    )
    # The tabs are a selector only: each view is its own cell's output, shown when its tab is picked, so redrawing one
    # view (a decision saved, a knob set) neither resets the tab nor reloads another view (the grid keeps its ticks).
    nav = mo.ui.tabs(dict.fromkeys(TABS, ""), value=TABS[0])
    mo.vstack([header, nav])
    return header, nav


# ------------------------------------------------------------------------------------------------ Results


@app.cell
def _(nav, rows, run, unit):
    _names, _mat = resolved_matrix(run)
    try:
        _pp = per_pose_figure(per_pose_table(run), run, unit=unit, rows=rows)
    except KeyError as _e:
        _pp = mo.callout(mo.md(f"No per-pose view: {_e}"), kind="warn")
    results_view = mo.vstack(
        [
            forest_figure(rows, run, unit),
            mo.md(
                "Bands are the cut's tie groups: inside a band there is **no order**, whatever the vertical "
                "position suggests. The keep boundary is drawn only where the cut placed one."
            ),
            mo.hstack([matrix_figure(_names, _mat, rows), funnel_figure(funnel_rows(run))], widths="equal"),
            _pp,
        ]
    )
    results_view if nav.value == "Results" else None  # noqa: B018 (the cell's output)
    return (results_view,)


# ------------------------------------------------------------------------------------------------ Decisions + Grid


@app.cell
def _(run):
    try:
        _ds, _msg = gui_app.load_decisions(run), None
    except ValueError as _e:  # an invalid file on disk: say so, start empty, and never overwrite it silently
        _ds, _msg = gui_app.new_decision_set(run), ("danger", f"Existing decisions file not loaded: {_e}")
    get_decisions, set_decisions = mo.state(_ds)
    get_decision_msg, set_decision_msg = mo.state(_msg)
    return get_decision_msg, get_decisions, set_decision_msg, set_decisions


@app.cell
def _(get_decisions, nav, rows, run):
    # Shown from the cell that defines the selection widget: a cell that only displays it would re-run on every
    # tick and reload the grid's iframe, losing the ticks.
    grid_rows = grid_records(run, rows, gui_app.decision_map(get_decisions()))
    grid = molgrid(grid_rows, name=f"smeltery-{run.record['input_digest'][:12]}")
    grid_selection = mo.ui.anywidget(grid.widget)
    grid_panel = mo.vstack([grid_selection, mo.Html(grid_iframe(grid))])
    grid_panel if nav.value == "Grid" else None  # noqa: B018 (the cell's output)
    return grid_panel, grid_rows, grid_selection


@app.cell
def _(grid_rows, grid_selection, run):
    _picked = selected_candidates(grid_selection.value, grid_rows)
    decision_form = (
        mo.md(
            "**Record a decision**\n\n{targets}\n\n{status} {reason}\n\n{note}\n\n{user}\n\n"
            "A choice inside a tie group is a human choice, not a ranking: use `within_tie_choice` and say why."
        )
        .batch(
            targets=mo.ui.multiselect(
                list(run.candidates), value=_picked, label="candidates (or tick them in the grid)"
            ),
            status=mo.ui.dropdown(list(STATUSES), value=STATUSES[0], label="status"),
            reason=mo.ui.dropdown(list(REASONS), value=REASONS[0], label="reason"),
            note=mo.ui.text_area(placeholder="note (required for within_tie_choice and other)", full_width=True),
            user=mo.ui.text(value=getpass.getuser(), label="user"),
        )
        .form(submit_button_label="Record decision", bordered=True)
    )
    return (decision_form,)


@app.cell
def _(decision_form, get_decisions, run, set_decision_msg, set_decisions):
    if decision_form.value is not None:
        _v = decision_form.value
        _ds, _problems = gui_app.apply_decisions(
            run, get_decisions(), _v["targets"], _v["status"], _v["reason"], _v["note"], _v["user"]
        )
        if _problems:
            set_decision_msg(("danger", "Not saved: " + "; ".join(_problems)))
        else:
            set_decisions(_ds)
            set_decision_msg(
                ("success", f"Saved {len(_v['targets'])} decision(s) to `{gui_app.decisions_path(run).name}`")
            )
    return


@app.cell
def _(decision_form, get_decision_msg, get_decisions, nav, run):
    _msg = get_decision_msg()
    _table = [{"candidate": k, **v} for k, v in gui_app.decision_map(get_decisions()).items()]
    decision_view = mo.hstack(
        [
            decision_form,
            mo.vstack(
                [
                    mo.callout(mo.md(_msg[1]), kind=_msg[0]) if _msg else mo.md(""),
                    mo.md(f"**Decisions** (`{gui_app.decisions_path(run)}`)"),
                    mo.ui.table(_table, selection=None) if _table else mo.md("_none yet_"),
                ]
            ),
        ],
        widths=[1, 1],
        align="start",
    )
    decision_view if nav.value == "Grid" else None  # noqa: B018 (the cell's output)
    return (decision_view,)


# ------------------------------------------------------------------------------------------------ Pose


@app.cell
def _(rows, run):
    _poses = run.record["results"]["poses"]
    _order = [r["candidate"] for r in rows]  # cut order
    pose_candidate = mo.ui.dropdown(_order, value=_order[0] if _order else None, label="analogue")
    pose_index = mo.ui.number(
        start=0, stop=max(0, min(p["n_poses"] for p in _poses.values()) - 1), value=0, label="pose"
    )
    pose_with_parent = mo.ui.checkbox(value=True, label=f"superpose {run.parent}")
    return pose_candidate, pose_index, pose_with_parent


@app.cell
def _(nav, pose_candidate, pose_index, pose_with_parent, run):
    _controls = mo.hstack([pose_candidate, pose_index, pose_with_parent], justify="start", gap=2)
    if pose_candidate.value is None:
        pose_view = mo.vstack([_controls, mo.md("_no analogue to show_")])
    else:
        _c, _i = pose_candidate.value, int(pose_index.value)
        _pp = run.record["results"].get("per_pose", {})
        _vals = []
        for _q, _byq in (_pp.get(_c) or {}).items():
            _par = (_pp.get(run.parent) or {}).get(_q)
            if _i < len(_byq) and _par and _i < len(_par):
                _u = run.record["results"].get("units", {}).get(_q, "")
                _vals.append(f"{_q}: {_byq[_i]:.3f} vs parent {_par[_i]:.3f} {_u} (paired {_byq[_i] - _par[_i]:+.3f})")
        try:
            _view = pose_iframe(pose_scene(run, _c, _i, with_parent=pose_with_parent.value), run.out_dir)
        except Exception as _e:  # a scene that cannot be drawn is shown as such, not as a crash
            _view = mo.callout(mo.md(f"Cannot draw {_c} pose {_i}: {_e}"), kind="danger")
        pose_view = mo.vstack([_controls, mo.md(" · ".join(_vals) or ""), _view])
    pose_view if nav.value == "Pose" else None  # noqa: B018 (the cell's output)
    return (pose_view,)


# ------------------------------------------------------------------------------------------------ Pipeline


@app.cell
def _(run):
    get_pipeline, set_pipeline = mo.state(builder.initial_pipeline(run))
    get_pipeline_msg, set_pipeline_msg = mo.state(None)
    get_knob_focus, set_knob_focus = mo.state(None)
    return get_knob_focus, get_pipeline, get_pipeline_msg, set_knob_focus, set_pipeline, set_pipeline_msg


@app.cell
def _(get_knob_focus, get_pipeline):
    _p = get_pipeline()
    add_form = mo.ui.dropdown(_p.addable(), label="add stage").form(submit_button_label="Add")
    remove_form = mo.ui.dropdown(_p.keys(), label="remove stage").form(submit_button_label="Remove")
    _focus = get_knob_focus() if get_knob_focus() in _p.keys() else (_p.keys()[-1] if _p.keys() else None)
    edit_stage = mo.ui.dropdown(_p.keys(), value=_focus, label="edit knobs of")
    toml_form = mo.ui.code_editor(value=builder.toml_preview(_p)[0], language="toml", min_height=200).form(
        submit_button_label="Load this TOML"
    )
    return add_form, edit_stage, remove_form, toml_form


@app.cell
def _(edit_stage, get_pipeline):
    knob_form = (
        builder.knob_widgets(get_pipeline(), edit_stage.value).form(submit_button_label="Set knobs")
        if edit_stage.value
        else None
    )
    return (knob_form,)


@app.cell
def _(add_form, get_pipeline, remove_form, set_pipeline, set_pipeline_msg, toml_form):
    def _apply(change, what):
        _p = copy.deepcopy(get_pipeline())
        try:
            change(_p)
        except (ValueError, KeyError) as _e:
            set_pipeline_msg(("danger", f"{what}: {_e}"))
            return
        set_pipeline(_p)
        set_pipeline_msg(("success", what))

    if add_form.value:
        _apply(lambda p: p.add(add_form.value), f"added {add_form.value}")
    if remove_form.value:
        _apply(lambda p: p.remove(remove_form.value), f"removed {remove_form.value}")
    if toml_form.value:
        try:
            _new = Pipeline.from_toml(toml_form.value)
        except Exception as _e:
            set_pipeline_msg(("danger", f"cannot read that TOML: {_e}"))
        else:
            set_pipeline(_new)
            set_pipeline_msg(("success", "loaded the TOML"))
    return


@app.cell
def _(edit_stage, get_pipeline, knob_form, set_knob_focus, set_pipeline, set_pipeline_msg):
    if knob_form is not None and knob_form.value is not None:
        _knobs, _errors = builder.parse_knobs(get_pipeline(), edit_stage.value, knob_form.value)
        if _errors:
            set_pipeline_msg(("danger", "; ".join(_errors)))
        else:
            _p = copy.deepcopy(get_pipeline())
            _p.set(edit_stage.value, **_knobs)
            set_knob_focus(edit_stage.value)
            set_pipeline(_p)
            set_pipeline_msg(("success", f"set knobs of {edit_stage.value}"))
    return


@app.cell
def _(add_form, edit_stage, get_pipeline, get_pipeline_msg, knob_form, nav, remove_form, toml_form):
    _p = get_pipeline()
    _msg = get_pipeline_msg()
    _toml, _ok = builder.toml_preview(_p)
    pipeline_view = mo.vstack(
        [
            mo.md("### Palette, by rung (cheapest first)"),
            mo.Html(builder.palette_html(_p)),
            mo.hstack([add_form, remove_form], justify="start", gap=2),
            mo.callout(mo.md(_msg[1]), kind=_msg[0]) if _msg else mo.md(""),
            mo.hstack(
                [
                    mo.vstack([mo.md("### Stages, as `smeltery run` executes them"), mo.Html(builder.stages_html(_p))]),
                    mo.vstack(
                        [
                            mo.md("### Knobs"),
                            edit_stage,
                            knob_form if knob_form is not None else mo.md(""),
                        ]
                    ),
                ],
                widths=[3, 2],
                align="start",
            ),
            mo.md("### Config (`smeltery run` reads this)" + ("" if _ok else ": draft, errors above block it")),
            toml_form,
            mo.download(data=_toml.encode(), filename=f"{_p.campaign or 'pipeline'}.toml", label="Download TOML")
            if _ok
            else mo.md(""),
        ]
    )
    pipeline_view if nav.value == "Pipeline" else None  # noqa: B018 (the cell's output)
    return (pipeline_view,)


if __name__ == "__main__":
    app.run()
