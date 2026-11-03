"""A TOML writer for the shapes a `smeltery run` config has: scalars, lists, tables, one level of nesting, and
arrays of tables. Python ships a TOML reader (`tomllib`) but no writer; `dumps` output is read back by `tomllib`
in the tests, so this does not need to handle anything a config cannot contain."""

from __future__ import annotations

import json
import math
import re

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE.match(k) else json.dumps(k)


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"TOML config values must be finite, got {v!r}")
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # a JSON string is a valid TOML basic string
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"no TOML form for {type(v).__name__}: {v!r}")


def _is_table_array(v) -> bool:
    return isinstance(v, (list, tuple)) and bool(v) and all(isinstance(x, dict) for x in v)


def _body(d: dict, prefix: str, out: list[str]) -> None:
    for k, v in d.items():
        if not isinstance(v, dict) and not _is_table_array(v):
            out.append(f"{_key(k)} = {_value(v)}")
    for k, v in d.items():
        name = f"{prefix}{_key(k)}"
        if isinstance(v, dict):
            if not v or any(not isinstance(x, dict) and not _is_table_array(x) for x in v.values()):
                out += ["", f"[{name}]"]  # a table holding only sub-tables needs no header of its own
            _body(v, name + ".", out)
        elif _is_table_array(v):
            for item in v:
                out += ["", f"[[{name}]]"]
                _body(item, name + ".", out)


def dumps(d: dict) -> str:
    out: list[str] = []
    _body(d, "", out)
    return "\n".join(out).lstrip("\n") + "\n"
