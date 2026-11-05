"""A pipeline drawn as six steps, each edited in place.

Pure data for the UI: which methods a step offers and which is placed, what its knobs are now and were in a
baseline, and whether the step is ready, off, changed or stale. The rules stay in `Pipeline.check()`; this only
sorts its issues into steps and compares two pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .catalog import method
from .pipeline import Issue, Pipeline

#: (key, label, catalog methods the step offers). Ligands holds the campaign, parent, candidates and decisions,
#: which are inputs of the pipeline and not methods.
STEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("active_site", "Active site", ("structure", "pocket", "docking")),
    ("ligands", "Ligands", ()),  # campaign, parent, candidates, decisions
    ("poses", "Paired poses", ("paired_poses",)),
    ("checks", "Pose checks", ("posebusters", "forcefield", "gfn2")),
    ("measure", "ΔE_int", ("field_interaction", "field_dft_interaction")),
    ("cut", "Cut", ("cut",)),
)

State = Literal["ready", "needs_input", "off", "changed", "stale"]

#: What flows into and out of each step (static: the order of the steps is the data flow).
_INPUTS: dict[str, list[dict]] = {
    "active_site": [{"from": None, "text": "a PDB code or a prepared pocket"}],
    "ligands": [{"from": None, "text": "the campaign, the parent and the candidates"}],
    "poses": [
        {"from": "active_site", "text": "the receptor and its box"},
        {"from": "ligands", "text": "the parent and its analogues"},
    ],
    "checks": [{"from": "poses", "text": "the paired poses"}],
    "measure": [
        {"from": "poses", "text": "the paired poses"},
        {"from": "active_site", "text": "the pocket field"},
    ],
    "cut": [{"from": "measure", "text": "each analogue's ΔE_int against the parent"}],
}
_GIVES: dict[str, list[dict]] = {
    "active_site": [
        {"to": "poses", "text": "the receptor and its box"},
        {"to": "measure", "text": "the pocket field"},
    ],
    "ligands": [{"to": "poses", "text": "the molecules to pose"}],
    "poses": [{"to": "checks", "text": "one pose per analogue"}, {"to": "measure", "text": "the poses to score"}],
    "checks": [{"to": "measure", "text": "the poses that pass"}],
    "measure": [{"to": "cut", "text": "ΔE_int per analogue"}],
    "cut": [],
}


@dataclass
class StepView:
    key: str
    label: str
    summary: str  # from the catalog `Method.summary` of the placed method (or the first one offered)
    state: State
    inputs: list[dict]  # {"from": step key | None, "text": str}
    gives: list[dict]  # {"to": step key, "text": str}
    methods: list[str]  # catalog keys offered
    chosen: str | None  # the first placed method of those offered
    params: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def _value(p: Pipeline | None, key: str, name: str, default):
    """The value the run would use for knob `name` of method `key`; a missing method has no value (`_ABSENT`)."""
    if p is None or key not in p.keys():
        return _ABSENT
    v = p.stage(key).params.get(name)
    return default if v is None else v


_ABSENT = object()


def _own_inputs(p: Pipeline) -> dict:
    return {"campaign": p.campaign, "parent": p.parent, "candidates": dict(p.candidates), "decisions": p.decisions}


def _step_signature(p: Pipeline, key: str, methods: tuple[str, ...]):
    """Everything about a step that changes what it does: which methods are placed, and their effective knobs."""
    if not methods:
        return _own_inputs(p)
    sig = {}
    for m in methods:
        if m in p.keys():
            sig[m] = {x.name: _value(p, m, x.name, x.default) for x in method(m).params}
    return sig


def _belongs(issue: Issue, methods: tuple[str, ...]) -> bool:
    if issue.stage is not None:
        return issue.stage in methods
    # A pipeline-level issue ("add 'cut' (...)") names the method it wants in quotes.
    return any(f"'{m}'" in issue.message for m in methods)


def steps(p: Pipeline, baseline: Pipeline | None = None, defaults: dict | None = None) -> list[StepView]:
    """The six steps of `p`. `baseline` is the pipeline to compare against (a saved version, the last run);
    `defaults` is `{method_key: {param_name: value}}`, the workspace's defaults, shown beside each knob."""
    defaults = defaults or {}
    issues = p.check()
    views: list[StepView] = []
    first_changed: int | None = None
    for i, (key, label, methods) in enumerate(STEPS):
        placed = [m for m in methods if m in p.keys()]
        chosen = placed[0] if placed else None
        errors_here = [x for x in issues if _belongs(x, methods) and x.level == "error"]
        changed = baseline is not None and _step_signature(p, key, methods) != _step_signature(baseline, key, methods)
        if changed and first_changed is None:
            first_changed = i
        if methods == () and not (p.campaign and p.parent and p.candidates):
            state: State = "needs_input"
        elif errors_here:
            state = "needs_input"
        elif changed:
            state = "changed"
        elif first_changed is not None and i > first_changed and (placed or not methods):
            state = "stale"
        elif methods and not placed:
            state = "off"
        else:
            state = "ready"
        offered = chosen or (methods[0] if methods else None)
        params = []
        for m in placed:
            for x in method(m).params:
                now = _value(p, m, x.name, x.default)
                before = _value(baseline, m, x.name, x.default)
                params.append(
                    {
                        "method": m,
                        "name": x.name,
                        "value": now,
                        "default": x.default,
                        "help": x.help,
                        "kind": x.kind,
                        "changed_from": None if before is _ABSENT or before == now else before,
                        "workspace_default": defaults.get(m, {}).get(x.name),
                    }
                )
        views.append(
            StepView(
                key=key,
                label=label,
                summary=method(offered).summary if offered else "",
                state=state,
                inputs=[dict(d) for d in _INPUTS[key]],
                gives=[dict(d) for d in _GIVES[key]],
                methods=list(methods),
                chosen=chosen,
                params=params,
                issues=[x for x in issues if _belongs(x, methods)],
            )
        )
    return views
