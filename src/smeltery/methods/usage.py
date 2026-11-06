"""Each registered method as a TOML block: what it does, what it needs, and every knob with its type and default.

The text is DERIVED from the registry, never written by hand, so a new method documents itself and an existing one
cannot drift from its registration. `smeltery methods` prints it; `--out DIR` writes one file per method plus an
index. The output is valid TOML: the usage lines are comments above the section, so a block can be pasted into a
pipeline config and edited.
"""

from __future__ import annotations

from pathlib import Path

from ..gui.catalog import Param
from . import MethodSpec, registry

#: How a param's `kind` is written in the generated TOML, and what an unset value looks like.
_EXAMPLE: dict[str, str] = {
    "str": '""',
    "path": '"path/to/file"',
    "int": "0",
    "float": "0.0",
    "bool": "false",
    "choice": '""',
    "vec3": "[0.0, 0.0, 0.0]",
    "int_list": "[]",
    "float_list": "[]",
    "str_list": "[]",
    "sites": "# [[pocket.site]] tables; see docs/methods.md",
    "table_list": "[]",
}


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"no TOML form for {v!r}")


def _param_line(p: Param) -> str:
    """`name = <default or example>` with a trailing comment: the type, whether it must be set, and its help."""
    value = _toml_value(p.default) if p.default is not None else _EXAMPLE.get(p.kind, '""')
    kind = f"{p.kind}" + (f" of {'|'.join(map(str, p.choices))}" if p.choices else "")
    state = "REQUIRED" if p.required else ("optional" if p.optional or p.default is None else "default")
    note = f"{kind}, {state}" + (f" -- {p.help}" if p.help else "")
    prefix = "" if p.required or p.default is not None else "# "
    return f"{prefix}{p.name} = {value}  # {note}"


#: Knobs a config may set that the GUI has no widget for, so `params_from_dataclass` leaves them out
#: (`smeltery.gui.catalog._NOT_KNOBS`). A usage document must still show them, with an example: they are config,
#: not internals. Internals set by a hook (pose lists, core maps) are deliberately absent.
_EXTRA_KNOBS: dict[str, tuple[str, str, str]] = {
    "field_interaction": (
        "remedies",
        "[]",
        "list of tables, optional -- SCF settings tried in order when a leg does not converge; rung 0 is the "
        "settings above. Allowed keys: max_iter (int), diis (pulay|adiis|ediis), diis_size (int), "
        "level_shift (float), soscf (bool). A rung that loosens a threshold or smears is refused: it would change "
        'the quantity measured. Example: remedies = [{ diis = "adiis", level_shift = 0.5 }]',
    ),
}


def _params_of(spec: MethodSpec) -> tuple[Param, ...]:
    from ..gui.catalog import method_from_spec

    return tuple(method_from_spec(spec).params)


def method_toml(spec: MethodSpec) -> str:
    """One method as a commented TOML block: what it is, how it fits the pipeline, and every knob."""
    out = [f"# {spec.label} -- {spec.summary}" if spec.summary else f"# {spec.label}"]
    facts = [f"step {spec.step}", f"rung {spec.rung}", f"role {spec.role}"]
    if spec.slot:
        facts.append(f"fills the {spec.slot} slot")
    if spec.tier:
        facts.append(f"tier {spec.tier}")
    if spec.quantity:
        facts.append(f"measures {spec.quantity}")
    out.append(f"# {', '.join(facts)}.")
    if spec.needs:
        out.append(f"# Needs: {', '.join(spec.needs)}.")
    if spec.provides:
        out.append(f"# Provides: {', '.join(spec.provides)}.")
    if spec.exclusive:
        out.append(f"# Refused beside: {', '.join(spec.exclusive)}.")
    if spec.required:
        out.append("# Required: every pipeline has this section.")
    if spec.extras:
        out.append(f"# Needs the extra(s): {', '.join(spec.extras)} (pip install 'smeltery[{spec.extras[0]}]').")
    if spec.file_knobs:
        out.append(f"# Copied into the run and digested: {', '.join(spec.file_knobs)}.")
    if not spec.wired:
        out.append(f"# NOT WIRED: {spec.why_unwired or 'declared but not run'}.")
    params = _params_of(spec)
    out.append(f"[{spec.section}]" if spec.section else f"# (no config section: {spec.key})")
    if not params:
        out.append("# no knobs")
    for p in params:
        out.append(_param_line(p))
    extra = _EXTRA_KNOBS.get(spec.key)
    if extra is not None:
        name, example, note = extra
        out.append(f"# {name} = {example}  # {note}")
    return "\n".join(out) + "\n"


def all_methods_toml() -> str:
    """Every registered method, in execution order, as one TOML document."""
    specs = sorted(registry().values(), key=lambda s: (s.order if s.order is not None else 10_000, s.key))
    head = [
        "# Every method smeltery can run, derived from the registry (`smeltery methods`).",
        "# A commented knob is unset by default; uncomment it to set it. Each block is valid TOML:",
        "# paste the ones you need into a pipeline config beside campaign/parent/[candidates].",
        "",
    ]
    return "\n".join(head) + "\n".join(method_toml(s) for s in specs)


def write_methods_toml(out_dir: str | Path) -> list[Path]:
    """One `<key>.toml` per method plus `index.toml` (all of them). Returns the paths written, index first."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    index = d / "index.toml"
    index.write_text(all_methods_toml())
    written = [index]
    for spec in sorted(registry().values(), key=lambda s: (s.order if s.order is not None else 10_000, s.key)):
        p = d / f"{spec.key}.toml"
        p.write_text(method_toml(spec))
        written.append(p)
    return written
