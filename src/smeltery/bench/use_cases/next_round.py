"""Close the loop: the config for the next round, built from a run's config plus the decisions and proposals made on it.

The scientist's loop is run -> read -> decide -> run again. Everything but the last arrow already exists:
`smeltery.decisions` records the judgement, `smeltery run` reads `[decisions] file` and drops the rejected candidates
before posing (docs/gui-contract.md section 3), and `POST /api/pipelines {from_run}` copies a run's config into a new
pipeline folder. This module writes the edge between them, so nobody hand-edits a TOML to carry a decision forward.

The config text is edited, never regenerated (`smeltery.config_text`): the comments in a config are the project's
documentation, and a round trip through `Pipeline` would drop them.

`plan(...)` is the whole decision. It says what the new config text is, which files the new pipeline folder needs and
what to tell the user, writing anything new (a pruned decisions file) into the staging directory it is given. It
raises `NextRoundRefused` for a round that is empty or impossible, and it touches no store and no HTTP, so the API
module only has to turn that refusal into a 400.

`from_run(...)` is where every new pipeline built from a run starts (the plain copy and the next round alike), and
`gather(...)` collects what `plan` needs from the run's folder and the project store.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smeltery.bench.ports.project_store import ProjectStore
from smeltery.config_text import apply, set_value
from smeltery.decisions import Decision, DecisionSet, decisions_path, utc_now
from smeltery.gui.pipeline import Pipeline
from smeltery.methods.catalog import catalog
from smeltery.toml_write import dumps


class NextRoundRefused(ValueError):
    """The next round cannot be built as asked: the message says why (a 400 over HTTP)."""


@dataclass(frozen=True)
class Proposed:
    """One accepted proposal, as much of it as a candidate needs: the name it takes in `[candidates]` and its SMILES."""

    id: str
    candidate: str
    smiles: str


@dataclass
class NextRound:
    """What to create: the new config text, the files to copy into the new folder, and the notes to show."""

    toml: str
    files: dict[str, Path] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class InputsMissing(Exception):
    """The run's record names an input copy that is not in its folder (a 409 over HTTP: the run, not the request,
    is what is wrong)."""


@dataclass
class Start:
    """A new pipeline's first state: its config text, {relative path: file to copy into its folder}, and notes."""

    toml: str
    files: dict[str, Path] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def rebuild(r: Any) -> Start:
    """A starting pipeline for the next round, rebuilt from what the run record holds (`r` is the loaded run): its
    candidates and parent, the cut's keep and z, a floor that came from the config, and the gates that ran. The
    record stores the field's charges as resolved positions, not the atom-index sites that produced them, so sites
    are not recovered: the notes say so, and click-to-place puts them back."""
    inputs = r.record.get("inputs", {})
    p = Pipeline.minimal(r.campaign, r.parent, dict(r.candidates))
    notes = []
    cut = {k: v for k, v in inputs.get("cut", {}).items() if k in ("keep", "z")}
    floor = inputs.get("floor", {})
    if str(floor.get("source", "")).startswith("config"):
        cut["floor"] = floor.get("value")
    p.set("cut", **cut)
    by_gate = {m.gate_key: k for k, m in catalog().items() if m.gate_key}  # the TOML's gate names -> stage keys
    gates = inputs.get("gates", {})
    for gate, on in gates.items():
        key = by_gate.get(gate)
        if on is True and key and key in p.addable():
            p.add(key)  # a copy of what ran: the workspace defaults are for methods added new, not for this
    if "forcefield" in p.keys() and gates.get("max_strain_kcal") is not None:
        p.set("forcefield", max_strain_kcal=gates["max_strain_kcal"])
    if inputs.get("field"):
        notes.append(
            f"This run's {len(inputs['field'])} pocket charges are recorded as positions, not as the atom sites that "
            "placed them, so they are not carried over: place the sites again by clicking atoms."
        )
    return Start(toml=dumps(p.to_config()), notes=notes)


def from_run(out_dir: Path, record: dict, load: Callable[[], Any]) -> Start:
    """A new pipeline built from the run in `out_dir` whose record is `record`: its recorded config and the input
    copies it names. A run that recorded no config file is rebuilt from its record (`rebuild(load())`, where `load`
    returns the loaded run). Raises `InputsMissing` when a recorded input copy is not there."""
    cfg = record.get("results", {}).get("config")
    cfg_file = out_dir / cfg["file"] if isinstance(cfg, dict) and cfg.get("file") else None
    if cfg_file is None or not cfg_file.is_file():
        return rebuild(load())
    files: dict[str, Path] = {}
    for dotted, info in (cfg.get("inputs") or {}).items():
        copy = info.get("copy") if isinstance(info, dict) else None
        if not copy:
            continue
        src = out_dir / copy
        if not src.is_file():
            raise InputsMissing(f"run records input {dotted} as {copy}, but {src} is not there")
        files[copy] = src
    return Start(toml=cfg_file.read_text(), files=files)


@dataclass
class Gathered:
    """What `plan` needs beyond the config: the run's decisions file (loaded, and its name) and the proposals."""

    decisions: DecisionSet | None
    decisions_name: str | None
    proposals: list[Proposed]


def gather(*, out_dir: Path, digest: str, run_ref: str, projects: ProjectStore, proposal_ids: list[str]) -> Gathered:
    """The decisions file run `run_ref` wrote into `out_dir` (`decisions-<digest[:12]>.json`, or none) and the
    proposals `proposal_ids` names. A decisions file that does not load is its `ValueError`; a proposal that does
    not belong to this run is `NextRoundRefused`."""
    dpath = decisions_path(out_dir, digest)
    decisions = DecisionSet.load(dpath) if dpath.is_file() else None
    by_id = {p.id: p for p in projects.list_proposals(digest)}
    proposed: list[Proposed] = []
    for pid in proposal_ids:
        p = by_id.get(pid)
        if p is None:
            raise NextRoundRefused(f"proposal {pid!r} does not belong to run {run_ref} (digest {digest[:12]})")
        proposed.append(Proposed(id=p.id, candidate=p.candidate, smiles=p.smiles))
    return Gathered(decisions, dpath.name if decisions is not None else None, proposed)


def _free_name(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    for i in range(2, 1000):
        if f"{base}-{i}" not in taken:
            return f"{base}-{i}"
    raise NextRoundRefused(f"cannot find a free candidate name starting with {base!r}")


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def plan(
    *,
    text: str,
    candidates: dict[str, str],
    parent: str,
    decisions: DecisionSet | None = None,
    decisions_name: str | None = None,
    carry_decisions: bool = True,
    proposals: list[Proposed] | None = None,
    drop: list[str] | None = None,
    staging: Path | None = None,
) -> NextRound:
    """The next round for the run whose recorded config is `text`.

    `candidates` and `parent` are that run's, as `[candidates]` has them. `decisions` is the run's decisions file
    loaded, `decisions_name` its file name (`decisions-<digest12>.json`), or both None when the run has none.
    `proposals` are the accepted proposals to add under `[candidates]`. `drop` names candidates to delete outright,
    which is not the same as rejecting one: a reject stays in `[candidates]` and `smeltery run` drops it before posing
    and records what it excluded, while a drop takes the molecule out of the chemistry altogether.

    A decision on a dropped candidate would make the carried file refer to a candidate the config no longer lists,
    which `smeltery run` refuses, so those entries are left out of the copy and a note says so.

    `staging` is a directory to write new files into (the copied decisions file); it is required whenever decisions
    are carried.
    """
    proposals = list(proposals or [])
    drop = list(dict.fromkeys(drop or []))
    notes: list[str] = []
    files: dict[str, Path] = {}

    carrying = bool(carry_decisions and decisions is not None and decisions_name is not None)
    if not carrying and not proposals and not drop:
        if carry_decisions and decisions is None:
            raise NextRoundRefused(
                "this run has no decisions file and no proposals were given, so the next round would be a copy of "
                "this one: decide on some candidates first, or pass add_proposals or drop"
            )
        raise NextRoundRefused(
            "nothing to carry forward: decide on this run's candidates, or pass add_proposals or drop"
        )
    if parent in drop:
        raise NextRoundRefused(
            f"drop names the parent {parent!r}: every analogue is measured against it; "
            "choose another parent or take it out of drop"
        )
    cfg_now = tomllib.loads(text)
    in_config = cfg_now.get("candidates")
    if not isinstance(in_config, dict):
        raise NextRoundRefused("this run's config has no [candidates] table to carry forward")
    # `candidates` (the run record) has earlier rounds' rejects already removed; the config text still lists
    # them, and the config is what we edit. Use the config, and keep the record only to spot the difference.
    earlier_rejects = [n for n in in_config if n not in candidates and n != parent]

    unknown = [n for n in drop if n not in in_config]
    if unknown:
        raise NextRoundRefused(f"drop names {unknown}, which this run's [candidates] does not list")

    kept = {n: str(s) for n, s in in_config.items() if n not in drop}
    # Dropped names stay reserved: letting a proposal take one would leave the dropped candidate's decision
    # pointing at a name that now holds a different molecule, and `smeltery run` refuses that SMILES mismatch.
    taken = set(kept) | set(drop)
    added: list[str] = []
    for p in proposals:
        name = _free_name((p.candidate or "").strip() or f"proposed-{p.id[:8]}", taken)
        taken.add(name)
        kept[name] = p.smiles
        added.append(name)

    if drop or added:
        cfg_now["candidates"] = kept
        text = apply(text, cfg_now)
    if drop:
        notes.append(f"dropped {len(drop)} {_plural(len(drop), 'candidate', 'candidates')}: {', '.join(drop)}")
    if added:
        what = _plural(len(added), "candidate", "candidates")
        notes.append(f"added {len(added)} proposed {what}: {', '.join(added)}")

    if carrying:
        assert decisions is not None and decisions_name is not None
        if staging is None:
            raise NextRoundRefused("carrying decisions needs a staging directory to copy the file into")
        stale = [d.candidate for d in decisions.decisions if d.candidate not in kept]
        carried = [d for d in decisions.decisions if d.candidate in kept]
        decided = {d.candidate for d in carried}
        # An earlier round rejected these; `[decisions] file` is about to be repointed at this round's file, so
        # unless they are named here they come back. They are re-recorded as rejects of this round.
        readded = [n for n in earlier_rejects if n in kept and n not in decided]
        for n in readded:
            carried.append(
                Decision(
                    candidate=n,
                    smiles=kept[n],
                    status="reject",
                    # `other` is the vocabulary's catch-all and requires a note, which is exactly right here:
                    # this entry restates an earlier round's judgement rather than making a new one.
                    reason="other",
                    note="rejected in an earlier round; carried so it is not posed again",
                    user=decisions.decisions[0].user if decisions.decisions else "",
                    time=utc_now(),
                )
            )
        copied = DecisionSet(
            campaign=decisions.campaign,
            run=decisions.run,
            decisions=carried,
            schema=decisions.schema,
        )
        staging.mkdir(parents=True, exist_ok=True)
        copied.dump(staging / decisions_name)
        files[decisions_name] = staging / decisions_name
        text = set_value(text, "decisions.file", decisions_name)
        rejected = copied.by_status("reject")
        held = copied.by_status("hold")
        if rejected:
            notes.append(
                f"carried {decisions_name}: {', '.join(rejected)} {_plural(len(rejected), 'was', 'were')} rejected "
                f"and {_plural(len(rejected), 'is', 'are')} dropped before posing"
            )
        else:
            notes.append(f"carried {decisions_name}: nothing is rejected, so every candidate is posed again")
        if held:
            notes.append(f"on hold, and still in the run: {', '.join(held)}")
        left = len([n for n in kept if n != parent and n not in rejected])
        keep = tomllib.loads(text).get("cut", {}).get("keep")
        if isinstance(keep, int) and not isinstance(keep, bool) and keep > left:
            notes.append(
                f"[cut] keep = {keep} but only {left} analogue(s) are left after the rejects: Run will refuse this "
                "until keep comes down or candidates go in"
            )
        if stale:
            notes.append(f"left out of the copied decisions, no longer a candidate: {', '.join(stale)}")

    notes.append("the pocket, poses and cut are unchanged, so this is a new input digest")
    # The notes are also written into the config as a header comment. `notes` on the API response answers the one
    # request that made the round, and is gone on the next GET; this config is the durable record of why this round
    # exists, and it reaches the person who only ever runs `smeltery run config.toml`.
    return NextRound(toml=_header(notes) + _without_header(text), files=files, notes=notes)


HEADER = "# Next round, carried forward by Bench:"


def _header(notes: list[str]) -> str:
    """The notes as a TOML comment block, for the config to carry its own provenance."""
    lines = [HEADER]
    lines += [f"#   - {n}" for n in notes]
    return "\n".join(lines) + "\n\n"


def _without_header(text: str) -> str:
    """`text` with a previous round's header block removed, so headers never stack.

    A next round is built from a run whose config may itself be a next round's. Prepending without this leaves
    the older block in place, and a reader of the TOML sees two "Next round" blocks that disagree.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != HEADER:
        return text
    i = 1
    while i < len(lines) and lines[i].startswith("#   - "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "".join(lines[i:])


def notes_in(text: str) -> list[str]:
    """The notes `_header` wrote, read back out of a config, so every reader of the config sees what it carried.

    The round is explained wherever the config goes, not only in the one API response that created it. Anything
    else in the file is left alone: only the leading block, and only its `#   - ` lines, are ours.

    A comment cannot follow an edit: the config is editable here and in any text editor, and the comment survives
    both (`config_text.apply` keeps comments on purpose). So the block is reported only while the config still
    agrees with what it claims -- the carried decisions file is still referenced, and every candidate the notes
    name is still a candidate. Once it does not, the notes would assert something untrue about the config they
    sit in, and saying nothing is better than saying that.
    """
    out: list[str] = []
    lines = text.splitlines()
    if not lines or lines[0].strip() != HEADER:
        return out
    for line in lines[1:]:
        if not line.startswith("#   - "):
            break
        out.append(line[len("#   - ") :].strip())
    if not out:
        return out
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []  # a config being edited into shape says nothing reliable about its own history
    named = [n for n in out if n.startswith("carried ")]
    if named and not cfg.get("decisions", {}).get("file"):
        return []  # the decisions it says it carried are no longer referenced
    return out
