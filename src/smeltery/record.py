"""The campaign run record: enough to reproduce a result from its inputs.

ferric's own version string is not yet a build identity (every build
reports 0.1.0). When ferric exposes `ferric.__build__` (git SHA and dirty
flag stamped at build time) that is used and is VERIFIED. Otherwise the
record reads where the installed ferric came from. An install from a package index is pinned by its version, because
index releases are immutable. A git install records its exact commit in the package's
`direct_url.json` (PEP 610), which is VERIFIED provenance: it is what was
built. A local-directory install records only a path; the record then reads
that checkout's git state and labels it INFERRED, because the directory may
have changed since the build.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import platform
import subprocess
import time
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse


def _git(path: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                              check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _ferric_build() -> dict | None:
    """The engine's own build stamp, `ferric.__build__`, if it exposes one.

    Expected shape (proposed upstream): {"git_sha": str, "dirty": bool}. Anything
    else (absent, wrong type, missing sha) counts as no stamp.
    """
    try:
        build = getattr(importlib.import_module("ferric"), "__build__", None)
    except ImportError:
        return None
    if isinstance(build, dict) and isinstance(build.get("git_sha"), str) and build["git_sha"]:
        return build
    return None


def ferric_identity() -> dict:
    ident: dict = {"version": None, "source": None, "commit": None, "dirty": None, "provenance": "UNKNOWN"}
    build = _ferric_build()
    if build is not None:
        try:
            ident["version"] = metadata.version("ferric")
        except metadata.PackageNotFoundError:
            pass
        ident.update(source="ferric.__build__", commit=build["git_sha"], dirty=bool(build.get("dirty")),
                     provenance="VERIFIED from ferric.__build__ (stamped at build time)")
        return ident
    try:
        dist = metadata.distribution("ferric")
    except metadata.PackageNotFoundError:
        return ident
    ident["version"] = dist.version
    raw = dist.read_text("direct_url.json")
    if not raw:
        # Installed from a package index. Index versions are immutable, so the
        # version string itself pins the exact build.
        ident.update(source="package index", commit=None, dirty=False,
                     provenance=f"VERIFIED: index release {dist.version} (immutable)")
        return ident
    du = json.loads(raw)
    ident["source"] = du.get("url")
    vcs = du.get("vcs_info")
    if vcs and vcs.get("commit_id"):
        ident.update(commit=vcs["commit_id"], dirty=False, provenance="VERIFIED from direct_url.json (git install)")
    elif "dir_info" in du and ident["source"]:
        path = Path(unquote(urlparse(ident["source"]).path))
        ident["commit"] = _git(path, "rev-parse", "HEAD")
        st = _git(path, "status", "--porcelain", "--untracked-files=no")
        ident["dirty"] = None if st is None else bool(st)
        ident["provenance"] = "INFERRED from the local source directory's current git state"
    return ident


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class RunRecord:
    campaign: str
    inputs: dict
    tiers: list[dict]
    results: dict
    smeltery_version: str = field(default_factory=lambda: metadata.version("smeltery"))
    ferric: dict = field(default_factory=ferric_identity)
    host: dict = field(default_factory=lambda: {"node": platform.node(), "python": platform.python_version()})
    started_utc: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    @property
    def input_digest(self) -> str:
        return digest({"inputs": self.inputs, "tiers": self.tiers})

    def to_json(self) -> str:
        d = asdict(self)
        d["input_digest"] = self.input_digest
        return json.dumps(d, indent=2, sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> RunRecord:
        d = json.loads(s)
        d.pop("input_digest", None)
        return cls(**d)
