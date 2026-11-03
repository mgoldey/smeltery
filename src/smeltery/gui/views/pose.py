"""The pose view: Mol* showing `scenes.pose_scene(run, candidate, pose)`.

A scene refers to its structures by URL. Any URL that is not http(s) or a data URI is read as a file (relative to
OUT, or absolute) and packed with the scene into an MVSX archive, so the page needs no file server: Mol* loads the
archive from the page itself. The page is `molviewspec.molstar_html` (Mol* from the jsDelivr CDN, pinned below),
shown in an iframe.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

#: Mol* release the page loads (molviewspec's own default is "latest", which would make the view drift).
MOLSTAR_VERSION = "5.13.1"
_REMOTE = re.compile(r"^(https?:|data:)", re.IGNORECASE)


def bundle(state: dict, out_dir: str | Path) -> tuple[dict, dict[str, bytes]]:
    """Rewrite each local `download` URL in `state` to a name inside an MVSX archive; return the new state and the
    {archive name: bytes} to pack with it. Remote URLs are left alone. A missing local file raises
    FileNotFoundError naming it."""
    state = copy.deepcopy(state)
    data: dict[str, bytes] = {}
    names: dict[Path, str] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("kind") == "download":
                params = node.setdefault("params", {})
                url = params.get("url", "")
                if url and not _REMOTE.match(url):
                    path = Path(url[7:] if url.startswith("file://") else url)
                    path = path if path.is_absolute() else Path(out_dir) / path
                    path = path.resolve()
                    if not path.is_file():
                        raise FileNotFoundError(f"scene refers to {url!r}, which is not a file ({path})")
                    if path not in names:
                        stem = re.sub(r"[^A-Za-z0-9._-]", "_", path.name)
                        name = f"data/{len(names)}-{stem}"
                        names[path] = name
                        data[name] = path.read_bytes()
                    params["url"] = names[path]
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(state)
    return state, data


def pose_html(state: dict, out_dir: str | Path) -> str:
    """A standalone HTML page showing `state` in Mol*."""
    from molviewspec import molstar_html

    state, data = bundle(state, out_dir)
    return molstar_html(state, data=data or None, molstar_version=MOLSTAR_VERSION)


def pose_iframe(state: dict, out_dir: str | Path, *, height: int = 560):
    """The page as a marimo iframe element."""
    import marimo as mo

    return mo.iframe(pose_html(state, out_dir), height=f"{height}px")
