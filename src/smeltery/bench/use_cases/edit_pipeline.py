"""Editing a pipeline's TOML text: one step edit, the active-site choice, the candidate list.

Every edit here takes the user's text and returns the new text. The edit is made on a `Pipeline` read from the text,
and only what it changed is written back into the text (`config_text.apply`), so the user's comments and layout stay
and nothing the `Pipeline` would normalise is touched.

Nothing here knows about HTTP. A refusal is raised as:

- `ConfigTextError`: the text (or a baseline) is not TOML, or the edited text cannot be written back;
- `EditRefused`: a refusal whose message is the whole answer, to be shown exactly as it is;
- any other `KeyError` / `ValueError` (from `Pipeline` itself: an unknown method or knob): its message is shown with
  the quotes a `KeyError` adds stripped.

The API maps all three to a 400 (`api.common.apply_edit`).
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from smeltery.bench.provisional import place_site
from smeltery.config_text import apply, loads
from smeltery.gui import steps as step_model
from smeltery.gui.pipeline import Pipeline
from smeltery.methods.catalog import method

Defaults = dict[str, dict[str, Any]]

#: The answer when the text is TOML but not a pipeline: the editor's panes cannot act on it.
NOT_A_PIPELINE = "this TOML is not a pipeline config; fix it in the TOML pane"


class EditRefused(ValueError):
    """The edit cannot be made; the message says why, in words to show as they are."""


class NotAPipeline(EditRefused):
    """The text is TOML, but `Pipeline` cannot read it as a pipeline."""


# ------------------------------------------------------------------------------------------------ reading


def parse(text: str) -> Pipeline | None:
    """The pipeline `text` describes. Text that is not TOML is a `ConfigTextError` carrying its line; TOML that
    `Pipeline` cannot read as a pipeline is None (reported as an issue, never a refusal)."""
    return Pipeline.try_from_config(loads(text))


def require(text: str) -> Pipeline:
    """`parse`, refusing TOML that is not a pipeline with `NotAPipeline(NOT_A_PIPELINE)`."""
    p = parse(text)
    if p is None:
        raise NotAPipeline(NOT_A_PIPELINE)
    return p


# ------------------------------------------------------------------------------------------------ writing


def patch(target: dict, before: dict, after: dict) -> None:
    """Carry what changed from `before` to `after` (two `Pipeline.to_config()`s) into `target` (the user's config as
    parsed), so the edit touches only the keys it changed and nothing the Pipeline would normalise."""
    for k in [k for k in before if k not in after]:
        target.pop(k, None)
    for k, v in after.items():
        old = before.get(k)
        if isinstance(v, dict) and isinstance(old, dict) and isinstance(target.get(k), dict):
            patch(target[k], old, v)
        elif k not in before or old != v or type(old) is not type(v):
            target[k] = v


def rewrite(text: str, mutate: Callable[[Pipeline], None]) -> str:
    """`text` with `mutate` applied to the pipeline it describes: only the lines the edit changes are written."""
    p = require(text)
    before = p.to_config()
    mutate(p)
    target = tomllib.loads(text)
    patch(target, before, p.to_config())
    return apply(text, target)


# ------------------------------------------------------------------------------------------------ one step edit


@dataclass(frozen=True)
class Site:
    """A hand-placed charge: `atom` of the parent, pushed `distance` A away from `from_atom`."""

    atom: int
    from_atom: int
    q: float = 1.0
    distance: float = 3.0


StepOp = Literal["add", "remove", "set", "choose", "site", "revert"]


def revert_step(p: Pipeline, step: str, kept: Pipeline) -> None:
    """Put step `step` of `p` back as it is in `kept`: the methods it places and their knobs (or, for Ligands, the
    campaign, parent, candidates and decisions). Every other step keeps its draft edits."""
    offered = {k: methods for k, _, methods in step_model.step_table()}
    if step not in offered:
        raise ValueError(f"no step {step!r}; steps: {list(offered)}")
    methods = offered[step]
    if not methods:
        p.campaign, p.parent, p.candidates, p.decisions = (
            kept.campaign,
            kept.parent,
            dict(kept.candidates),
            kept.decisions,
        )
        return
    for m in methods:
        if m in p.keys() and m not in kept.keys():
            p.remove(m)
    for m in methods:
        if m in kept.keys():
            if m not in p.keys():
                p.add(m)
            p.stage(m).params = dict(kept.stage(m).params)


def _step_edit(
    p: Pipeline,
    op: StepOp,
    key: str,
    params: dict[str, Any] | None,
    site: Site | None,
    baseline_toml: str | None,
    defaults: Defaults,
) -> None:
    if op == "revert":
        kept = parse(baseline_toml) if baseline_toml is not None else None
        if kept is None:
            raise ValueError("a revert needs baseline_toml, the kept pipeline")
        revert_step(p, key, kept)
        return
    if op == "add":
        p.add(key, params or None, defaults=defaults)
    elif op == "remove":
        p.remove(key)
    elif op == "set":
        p.set(key, **(params or {}))
    elif op == "choose":  # the other method of a step: drop the ones it excludes, then place it
        for other in method(key).exclusive:
            if other in p.keys():
                p.remove(other)
        if key not in p.keys():
            p.add(key, params or None, defaults=defaults)
    else:
        if site is None:
            raise ValueError("a site edit needs the site: atom, from_atom, q, distance")
        place_site(p, site.atom, site.from_atom, q=site.q, distance=site.distance)


def apply_step_edit(
    text: str,
    op: StepOp,
    key: str,
    *,
    params: dict[str, Any] | None = None,
    site: Site | None = None,
    baseline_toml: str | None = None,
    defaults: Callable[[], Defaults] = dict,
) -> str:
    """One step edit applied to the user's text. `key` is a catalog method key (for `revert`, a step key).

    `defaults` is called, after the text is read, only for an edit that adds a method (`add`, `choose`): the
    workspace defaults are copied into a method when it is added, and an edit that adds nothing never reads them."""

    def mutate(p: Pipeline) -> None:
        _step_edit(p, op, key, params, site, baseline_toml, defaults() if op in ("add", "choose") else {})
        if op == "remove" and key in Pipeline.from_config(p.to_config()).keys():
            others = ", ".join(repr(o) for o in method(key).exclusive) or "nothing"
            raise EditRefused(f"every pipeline needs {key!r}; choose its alternative instead: {others}")

    return rewrite(text, mutate)


# ------------------------------------------------------------------------------------------------ the active site


@dataclass(frozen=True)
class ActiveSite:
    """Step 1 done one of its three ways, and the knobs that way needs."""

    choice: Literal["structure", "pocket_file", "hand_placed"]
    spec: str | None = None
    reference_ligand: str | None = None
    center: list[float] | None = None
    pocket_cutoff: float | None = None
    allow_network: bool = False
    file: str | None = None
    receptor: str | None = None
    box_center: list[float] | None = None
    box_size: list[float] | None = None


def _vec3(v: list[float] | None, what: str) -> list[float] | None:
    if v is None:
        return None
    if len(v) != 3:
        raise ValueError(f"{what} is three numbers")
    return [float(x) for x in v]


def _choose(p: Pipeline, b: ActiveSite, defaults: Defaults) -> None:
    def ensure(key: str) -> None:
        if key not in p.keys():
            p.add(key, defaults=defaults)

    if b.choice == "structure":
        if not b.spec or b.pocket_cutoff is None or (b.reference_ligand is None and b.center is None):
            raise ValueError("a structure needs spec, pocket_cutoff and a reference_ligand or a center")
        ensure("structure")
        p.set(
            "structure",
            spec=b.spec,
            reference_ligand=b.reference_ligand,
            center=_vec3(b.center, "center") if b.reference_ligand is None else None,
            pocket_cutoff=b.pocket_cutoff,
            allow_network=True if b.allow_network else None,
        )
        if "pocket" in p.keys():
            p.remove("pocket")
        ensure("docking")
        p.set("docking", receptor=None, box_center=None)
    elif b.choice == "pocket_file":
        if not b.file or not b.receptor:
            raise ValueError("a pocket file needs the file and the prepared receptor")
        if "structure" in p.keys():
            p.remove("structure")
        ensure("pocket")
        p.set("pocket", file=b.file, site=None)
        ensure("docking")
        p.set("docking", receptor=b.receptor, box_center=_vec3(b.box_center, "box_center"))
        if b.box_size is not None:
            p.set("docking", box_size=_vec3(b.box_size, "box_size"))
    else:
        if "structure" in p.keys():
            p.remove("structure")
        if "docking" in p.keys():
            p.remove("docking")
        ensure("pocket")
        p.set("pocket", file=None)


def choose_active_site(text: str, site: ActiveSite, defaults: Callable[[], Defaults] = dict) -> str:
    """Switch step 1 to one of its three ways: a structure adds `[structure]` and `[docking]`, a pocket file keeps
    `[pocket] file` with the docking receptor and box, hand-placed charges drop both. `defaults` is read once the
    text is known to be a pipeline, whichever way is chosen."""
    return rewrite(text, lambda p: _choose(p, site, defaults()))


# ------------------------------------------------------------------------------------------------ the candidates


def write_candidates(text: str, cands: dict[str, str], parent: str) -> str:
    """The one writer of `[candidates]` and `parent` into the live text, shared by every edit that changes them."""
    target = tomllib.loads(text)
    target["candidates"] = cands
    target["parent"] = parent
    return apply(text, target)


CandidateOp = Literal["add", "remove", "rename", "set_smiles", "set_parent"]


def edit_candidates(
    text: str, op: CandidateOp, name: str, *, smiles: str | None = None, new_name: str | None = None
) -> str:
    """Apply one add / remove / rename / set_smiles / set_parent to the user's live text. Removing the parent is
    refused: pick another parent first. A rename of the parent moves `parent` with it. The candidates are read from
    `text` itself, so an edit can never be built from a stale copy of the list."""
    p = require(text)
    cands = dict(p.candidates)
    parent = p.parent
    name = name.strip() if op == "add" else name
    if op == "add":
        if not name:
            raise EditRefused("a candidate needs a name")
        if name in cands:
            raise EditRefused(f"there is already a candidate named {name!r}; names must be unique")
        cands[name] = (smiles or "").strip()
    elif name not in cands:
        raise EditRefused(f"no candidate named {name!r}")
    elif op == "remove":
        if name == parent:
            raise EditRefused(
                f"{name!r} is the parent, and every pair is measured against it; "
                "pick another parent first, then remove it"
            )
        del cands[name]
    elif op == "set_smiles":
        cands[name] = smiles or ""
    elif op == "set_parent":
        parent = name
    else:  # rename (the text editor appends a new key, so the entry moves to the end)
        new = (new_name or "").strip()
        if not new:
            raise EditRefused("a candidate needs a name")
        if new != name and new in cands:
            raise EditRefused(f"there is already a candidate named {new!r}; names must be unique")
        cands = {(new if k == name else k): v for k, v in cands.items()}
        if parent == name:
            parent = new
    return write_candidates(text, cands, parent)
