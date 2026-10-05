"""The campaign run record: enough to reproduce a result from its inputs.

ferric's own version string is not yet a build identity (every build
reports 0.1.0), so the record also reads where the installed ferric came
from. A git install records its exact commit in the package's
`direct_url.json` (PEP 610), which is VERIFIED provenance: it is what was
built. A local-directory install records only a path; the record then reads
that checkout's git state and labels it INFERRED, because the directory may
have changed since the build.
"""

from __future__ import annotations

import hashlib
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


def ferric_identity() -> dict:
    ident: dict = {"version": None, "source": None, "commit": None, "dirty": None, "provenance": "UNKNOWN"}
    try:
        dist = metadata.distribution("ferric")
    except metadata.PackageNotFoundError:
        return ident
    ident["version"] = dist.version
    raw = dist.read_text("direct_url.json")
    if not raw:
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
