"""Edit a `smeltery run` config as text, without regenerating it.

The config TOML is the pipeline, and its comments are documentation people (and `examples/*.toml`) rely on. These
functions change only what differs from the text they are given: values are replaced in place (keeping the comment
on their line), keys keep their order, and a table or array-of-tables element that is gone from the new config is
removed together with the comment lines directly above its header. `apply` ends by reading its own output back and
refusing to return text that does not parse to the config it was asked for.
"""

from __future__ import annotations

import difflib
import re
import tomllib
from typing import Any

import tomlkit
from tomlkit.exceptions import ParseError, TOMLKitError

_HEADER = re.compile(r"^\s*(\[\[?)\s*[^\[\]#].*$")


class ConfigTextError(ValueError):
    """Config text that does not parse. `.line` is the 1-based line of the problem."""

    def __init__(self, message: str, line: int = 1):
        super().__init__(f"line {line}: {message}")
        self.line = line


def _parse(text: str):
    try:
        tomllib.loads(text)
        return tomlkit.parse(text)
    except tomllib.TOMLDecodeError as e:
        m = re.search(r"line (\d+)", str(e))
        raise ConfigTextError(str(e), int(m.group(1)) if m else 1) from e
    except ParseError as e:
        raise ConfigTextError(str(e), e.line) from e


def _same(a: Any, b: Any) -> bool:
    """Equality that does not let `True == 1` or `2 == 2.0` pass: the text must hold the type asked for."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


def _plain(v: Any) -> Any:
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


# -- removal of whole tables, by lines: tomlkit keeps the comment above a header with the table before it ----------


def _header_path(line: str) -> tuple[tuple[str, ...], bool] | None:
    if not _HEADER.match(line):
        return None
    try:
        d = tomllib.loads(line)
    except tomllib.TOMLDecodeError:
        return None
    path: list[str] = []
    node: Any = d
    while True:
        if isinstance(node, list):
            node = node[0]
        if not isinstance(node, dict) or len(node) != 1:
            break
        ((k, node),) = node.items()
        path.append(k)
    return tuple(path), line.lstrip().startswith("[[")


def _headers(lines: list[str]) -> list[tuple[int, tuple[str, ...], bool]]:
    out = []
    for i, ln in enumerate(lines):
        h = _header_path(ln) if ln.lstrip().startswith("[") else None
        if h:
            out.append((i, h[0], h[1]))
    return out


def _attached(lines: list[str], i: int) -> int:
    while i > 0 and lines[i - 1].lstrip().startswith("#"):
        i -= 1
    return i


def _remove_block(text: str, path: tuple[str, ...], nth: int, array: bool) -> str | None:
    """Delete the `nth` header at `path` (and its sub-tables) with the comments directly above each. None when the
    text has no such header (the table is inline or built from dotted keys)."""
    lines = text.splitlines(keepends=True)
    heads = _headers(lines)
    matches = [k for k, (_, p, is_arr) in enumerate(heads) if p == path and is_arr == array]
    if nth >= len(matches):
        return None
    idx = matches[nth]
    i = heads[idx][0]
    end = len(lines)
    for j, p, is_arr in heads[idx + 1 :]:
        if p[: len(path)] != path or (array and p == path and is_arr):
            end = _attached(lines, j)
            break
    start = _attached(lines, i)
    rest = lines[:start] + lines[end:]
    while rest and end == len(lines) and not rest[-1].strip():
        rest.pop()
    if rest and not rest[-1].endswith("\n"):
        rest[-1] += "\n"
    return "".join(rest)


def _remove_gone(text: str, new: dict) -> str:
    """Remove every table, and every array-of-tables element, that the old text has and `new` does not."""
    while True:
        old = tomllib.loads(text)
        target = _first_gone(old, new, ())
        if target is None:
            return text
        path, nth, array = target
        out = _remove_block(text, path, nth, array)
        if out is None:
            return text  # inline or dotted: the tomlkit pass deletes it
        text = out


def _first_gone(old: dict, new: dict, prefix: tuple[str, ...]):
    for k, ov in old.items():
        path = (*prefix, k)
        if k not in new:
            if isinstance(ov, dict):
                return path, 0, False
            if isinstance(ov, list) and ov and all(isinstance(x, dict) for x in ov):
                return path, 0, True
            continue
        nv = new[k]
        if isinstance(ov, dict) and isinstance(nv, dict):
            r = _first_gone(ov, nv, path)
            if r:
                return r
        elif isinstance(ov, list) and ov and all(isinstance(x, dict) for x in ov) and isinstance(nv, (list, tuple)):
            if len(nv) < len(ov) and all(isinstance(x, dict) for x in nv):
                return path, _gone_index(ov, list(nv)), True  # the loop comes back for the next
    return None


def _gone_index(old: list, new: list) -> int:
    """Which old element to drop: the first with no identical element left in `new`, so each remaining element
    keeps its own comments; the last when every one still has a match."""
    left = list(new)
    for i, o in enumerate(old):
        for j, n in enumerate(left):
            if _same(o, n):
                del left[j]
                break
        else:
            return i
    return len(old) - 1


# -- in-place sync ----------------------------------------------------------------------------------------------


def _is_aot(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and bool(v) and all(isinstance(x, dict) for x in v)


def _sync(node, new: dict) -> None:
    for k in [k for k in node if k not in new]:
        del node[k]
    for k, nv in new.items():
        if k not in node:
            node[k] = _item(nv)
            continue
        cur = node[k]
        ov = _plain(cur.unwrap() if hasattr(cur, "unwrap") else cur)
        if _same(ov, _plain(nv)):
            continue
        if (
            isinstance(nv, dict)
            and isinstance(ov, dict)
            and isinstance(cur, (tomlkit.items.Table, tomlkit.items.InlineTable))
        ):
            _sync(cur, nv)
        elif _is_aot(nv) and isinstance(cur, tomlkit.items.AoT):
            _sync_aot(cur, list(nv))
        else:
            node[k] = _item(nv)


def _sync_aot(aot, new: list) -> None:
    while len(aot) > len(new):
        del aot[len(aot) - 1]
    for i, nv in enumerate(new):
        if i < len(aot):
            _sync(aot[i], nv)
        else:
            aot.append(_item(nv))


def _item(v: Any):
    return tomlkit.item(_plain(v))


def apply(text: str, new_cfg: dict) -> str:
    """`text` edited so it parses to `new_cfg`; everything unchanged keeps its comments, order and layout."""
    _parse(text)
    new_cfg = _plain(new_cfg)
    stripped = _remove_gone(text, new_cfg)
    doc = _parse(stripped)
    try:
        _sync(doc, new_cfg)
        out = tomlkit.dumps(doc)
        got = tomllib.loads(out)
    except (TOMLKitError, tomllib.TOMLDecodeError) as e:
        raise ConfigTextError(f"cannot write the config: {e}") from e
    if not _same(got, new_cfg):
        raise ConfigTextError("edited text does not read back as the requested config")
    return out


def set_value(text: str, dotted: str, value: Any) -> str:
    """Set one key (`"pocket.file"`), creating the tables on the way; the rest of the text is untouched."""
    doc = _parse(text)
    *tables, key = dotted.split(".")
    node: Any = doc
    for t in tables:
        if t not in node:
            node[t] = tomlkit.table()
        node = node[t]
    if key in node and _same(_plain(node[key].unwrap() if hasattr(node[key], "unwrap") else node[key]), _plain(value)):
        return text
    node[key] = _item(value)
    return tomlkit.dumps(doc)


def diff_lines(old: str, new: str) -> list[tuple[str, str]]:
    """Line diff as `("+"|"-"|" ", line)`, unchanged lines included so a reader sees the context."""
    a, b = old.splitlines(), new.splitlines()
    out: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += [(" ", ln) for ln in a[i1:i2]]
            continue
        out += [("-", ln) for ln in a[i1:i2]]
        out += [("+", ln) for ln in b[j1:j2]]
    return out
