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
import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass, field, fields
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse


def _git(path: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def strip_credentials(url: str | None) -> str | None:
    """`https://user:token@host/x` -> `https://host/x`; ssh://git@host and scp-style URLs pass through."""
    if not url:
        return None
    parts = urlparse(url)
    if parts.scheme in ("http", "https") and "@" in parts.netloc:
        return parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]).geturl()
    return url


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


ferric_build = _ferric_build  # the public name, for the Temporal activities' code identity


def _distribution_identity(name: str, ident: dict) -> dict:
    """Fill `ident` from the installed distribution `name`: index pin, git install, or local dir."""
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return ident
    ident["version"] = dist.version
    raw = dist.read_text("direct_url.json")
    if not raw:
        # Installed from a package index. Index versions are immutable, so the
        # version string itself pins the exact build.
        ident.update(
            source="package index",
            commit=None,
            dirty=False,
            provenance=f"VERIFIED: index release {dist.version} (immutable)",
        )
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
    elif "archive_info" in du and ident["source"]:
        # A built wheel installed from a path or URL. Reached only when the wheel carries NO build stamp: ferric's
        # own CI wheels stamp `ferric.__build__` with the commit, which the caller prefers, and `direct_url.json`
        # records how this machine installed the file (often a local path) rather than where it was built. Without
        # a stamp the hash pip recorded is the only identity there is: it pins the artifact, claiming no more.
        h = (du["archive_info"] or {}).get("hash") or (du["archive_info"] or {}).get("hashes", {}).get("sha256")
        name = Path(unquote(urlparse(ident["source"]).path)).name
        ident["dirty"] = False
        ident["provenance"] = f"INFERRED from the installed wheel {name}" + (
            f" ({h})" if h else " (pip recorded no hash)"
        )
    return ident


def smeltery_identity() -> dict:
    """Which smeltery source ran. An editable install reports the checkout's HEAD, not the install-time stamp."""
    ident: dict = {"version": None, "source": None, "commit": None, "dirty": None, "provenance": "UNKNOWN"}
    ident = _distribution_identity("smeltery", ident)
    ident["git_remote"] = ident["git_branch"] = None
    if str(ident["provenance"]).startswith("INFERRED") and ident["source"]:
        path = Path(unquote(urlparse(ident["source"]).path))
        ident["git_remote"] = strip_credentials(_git(path, "config", "--get", "remote.origin.url"))
        branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        ident["git_branch"] = None if branch in (None, "", "HEAD") else branch  # HEAD: detached
    return ident


def ferric_identity() -> dict:
    ident: dict = {"version": None, "source": None, "commit": None, "dirty": None, "provenance": "UNKNOWN"}
    build = _ferric_build()
    if build is not None:
        try:
            ident["version"] = metadata.version("ferric")
        except metadata.PackageNotFoundError:
            pass
        ident.update(
            source="ferric.__build__",
            commit=build["git_sha"],
            dirty=bool(build.get("dirty")),
            provenance="VERIFIED from ferric.__build__ (stamped at build time)",
        )
        return ident
    return _distribution_identity("ferric", ident)


#: The UTC timestamp every smeltery file stamps, `YYYY-MM-DDTHH:MM:SSZ`; `decisions`, `project` and Bench read it here.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> str:
    """The current UTC time as `TIME_FORMAT`."""
    return time.strftime(TIME_FORMAT, time.gmtime())


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def input_digest(inputs: dict, tiers: list[dict]) -> str:
    """The identity a run record is addressed by: the hash of its inputs and its tiers' settings."""
    return digest({"inputs": inputs, "tiers": tiers})


def file_sha256(path: str | Path) -> str:
    """sha256 (hex) of a file's bytes, read in 1 MiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text_atomic(path: str | Path, text: str) -> None:
    """Write `text` to `path` whole, then rename it into place: a reader never sees a half-written file. The
    temporary file sits beside `path` (same directory, so the rename is atomic) as `<name>.tmp`."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


SCHEMA_VERSION = 1


def _canonical_sha256(d: dict) -> str:
    """sha256 of the record's canonical JSON, excluding `record_sha256` itself."""
    return digest({k: v for k, v in d.items() if k != "record_sha256"})


def stamp_record(d: dict) -> dict:
    """Set `d["record_sha256"]` to the hash of `d`'s canonical JSON (everything else in it); returns `d`."""
    d["record_sha256"] = _canonical_sha256(json.loads(json.dumps(d, default=str)))
    return d


def dump_record(d: dict) -> str:
    """A record dict as its file holds it."""
    return json.dumps(d, indent=2, sort_keys=True, default=str)


@dataclass
class RunRecord:
    campaign: str
    inputs: dict
    tiers: list[dict]
    results: dict
    smeltery_version: str = field(default_factory=lambda: metadata.version("smeltery"))
    ferric: dict = field(default_factory=ferric_identity)
    smeltery: dict | None = field(default_factory=smeltery_identity)
    host: dict = field(default_factory=lambda: {"node": platform.node(), "python": platform.python_version()})
    started_utc: str = field(default_factory=utc_now)

    @property
    def input_digest(self) -> str:
        return input_digest(self.inputs, self.tiers)

    def to_json(self) -> str:
        d = asdict(self)
        d["input_digest"] = self.input_digest
        d["schema_version"] = SCHEMA_VERSION
        return dump_record(stamp_record(d))

    @classmethod
    def from_json(cls, s: str) -> RunRecord:
        d = json.loads(s)
        d.setdefault("smeltery", None)  # old records: never invent today's identity
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})  # input_digest, schema_version etc. are derived
