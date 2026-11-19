"""The core run record (`attestation.run/1`, v1-DRAFT) beside smeltery's own `run-<digest12>.json`.

smeltery keeps writing `OUT/run-<digest12>.json` (the `RunRecord` the GUI contract reads); this module derives the
core record from it and writes it with attestation-schema's own writer, laid out per `attestation_schema.layout`:

    OUT/run-<digest12>.json, poses/, calcs.jsonl, config.toml, inputs/ ...   unchanged (docs/gui-contract.md)
    OUT/steps/NN-<stage>/stage.json      one per funnel stage, in execution order: its funnel row and stage-log rows
    OUT/attestation/digests.json         sha256 of every file in OUT outside attestation/ (the legacy record included)
    OUT/attestation/run.json             the core record, written last

OUT is the run directory, not a sibling of it: the manifest then binds every file the run left (poses, SCF log,
staged inputs, the legacy record) to the core record through one digest, and the GUI contract is untouched because
only new names (`attestation/`, `steps/`) are added to OUT.

Mapping (docs/core-record.md): campaign -> family; arm -> null (smeltery has no arm); the legacy `inputs` -> config;
staged inputs, the receptor and the decisions file -> data (role input), the legacy record (role output) and the
manifest; funnel counts and the paired ddE -> metrics, what each is about (a stage, an analogue) in `labels`; tier seeds
and seed lists -> seeds; smeltery's own identity -> code, on the same ladder as a dependency; ferric, Python and the
other packages -> dependencies, through the same identity ladder (`record.package_identity`). Tiers and everything
chemistry-shaped -> `ext.smeltery`. Where the draft schema has no honest slot for something smeltery knows, it is left
out and listed in docs/core-record.md ("Schema findings"), never squeezed into a field that means something else.

Needs the optional `attestation` extra (attestation-schema). Without it `AVAILABLE` is False and the run path prints a
one-line notice instead of writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

try:
    import attestation_schema as _schema
    from attestation_schema import layout as _layout
except ImportError:  # the `attestation` extra is not installed
    _schema = None
    _layout = None

from .record import package_identity, strip_credentials

AVAILABLE = _schema is not None
NOT_INSTALLED = "attestation-schema is not installed (the `attestation` extra): no core record written"

#: Every package smeltery itself imports to run a funnel; recorded on every live run.
CORE_PACKAGES = (("rdkit", "rdkit"), ("numpy", "numpy"))
#: Packages a tier or gate imports only when configured: recorded on a live run only when the run imported them
#: (present in `sys.modules`), because "installed" is not "ran on".
OPTIONAL_PACKAGES = (
    ("scipy", "scipy"),
    ("gemmi", "gemmi"),
    ("vina", "vina"),
    ("meeko", "meeko"),
    ("posebusters", "posebusters"),
    ("openmm", "openmm"),
    ("openff-toolkit", "openff.toolkit"),
    ("openmmforcefields", "openmmforcefields"),
    ("openmmml", "openmmml"),
    ("torch", "torch"),
    ("torchani", "torchani"),
    ("aimnet", "aimnet"),
)
#: Tier settings keys under which smeltery's tiers record a dependency's version at run time -> dependency name.
#: External binaries (xtb, pdb2pqr) are known only this way.
TIER_VERSION_KEYS = {
    "rdkit": "rdkit",
    "openmm": "openmm",
    "openff-toolkit": "openff-toolkit",
    "openmmforcefields": "openmmforcefields",
    "xtb_version": "xtb",
    "pdb2pqr_version": "pdb2pqr",
}

_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
STEP_FILE = "stage.json"


#: The quantity whose paired difference is `paired_ddE` (cli.QUANTITY); its unit is the record's `results.units` entry.
DDE_QUANTITY = "dE_int"


def utc_now() -> str | None:
    """Now, spelled the one way the core record accepts (`attestation_schema.format_timestamp`). None without the
    extra: then no core record is written, so there is nothing to stamp."""
    return _schema.format_timestamp(datetime.now(UTC)) if _schema else None


def _require() -> None:
    if not AVAILABLE:
        raise RuntimeError(NOT_INSTALLED)


# ---- identity -> dependency / code ---------------------------------------------------------------------------------


def provenance_level(sentence: object) -> str:
    """smeltery's provenance sentence ("VERIFIED from ...", "INFERRED ...", "UNKNOWN") -> the core enum."""
    text = str(sentence or "")
    if text.startswith("VERIFIED"):
        return "verified"
    if text.startswith("INFERRED"):
        return "inferred"
    return "unknown"


def source(ident: dict | None) -> str | None:
    """Where an identity block says the package was installed from, as the core `source`: `index` (a package index;
    which one is not recorded), `git+<url>` (a git install's direct_url.json), `file` (a local directory or wheel file:
    the path names a machine, not a source, so it is not written), the installer for a conda/distro install, or the URL
    of a wheel fetched from one. None when the block does not say -- a build stamp (`ferric.__build__`) names how the
    build was identified, not where it was installed from, so a stamped build's source is unknown."""
    ident = ident or {}
    src = ident.get("source")
    if not isinstance(src, str) or not src or src.endswith(".__build__"):
        return None
    if src == "package index":
        return "index"
    if src.startswith("installed by "):
        return src.removeprefix("installed by ")
    if src.startswith("file:"):
        return "file"
    if "git install" in str(ident.get("provenance")):
        return f"git+{strip_credentials(src)}"
    return strip_credentials(src)


def dependency(name: str, ident: dict | None) -> dict:
    """One core `dependencies` entry from a smeltery identity block (`record.package_identity` / the legacy record's
    `ferric`). The same mapping for every package: the enum from the sentence's ladder level, `build_sha` from the
    commit (a stamped build or a git install), the sentence itself as `evidence`, and `source` (`source()`)."""
    if not ident:
        return {
            "name": name,
            "version": None,
            "build_sha": None,
            "dirty": None,
            "provenance": "unknown",
            "evidence": None,
            "source": None,
        }
    level = provenance_level(ident.get("provenance"))
    commit = ident.get("commit")
    return {
        "name": name,
        "version": ident.get("version"),
        "build_sha": commit if isinstance(commit, str) and _SHA.fullmatch(commit) else None,
        "dirty": ident.get("dirty"),
        "provenance": level,
        "evidence": None if level == "unknown" else str(ident["provenance"]),
        "source": source(ident),
    }


def _code(ident: dict | None, version: str | None) -> dict:
    """smeltery's own identity -> `code`, on the dependency ladder: `provenance` from the sentence (an editable
    install is INFERRED: the checkout's HEAD now, not what was imported), the sentence as `evidence`. `repo` is the
    checkout's remote (credentials already stripped), never the install's `file://` path, which names a machine rather
    than a repository. `version` is the identity's own, else the legacy `smeltery_version` (records from before the
    identity block); never the converter's."""
    ident = ident or {}
    commit = ident.get("commit")
    level = provenance_level(ident.get("provenance"))
    return {
        "git_sha": commit if isinstance(commit, str) and _SHA.fullmatch(commit) else None,
        "dirty": ident.get("dirty"),
        "repo": ident.get("git_remote"),
        "version": ident.get("version") or version,
        "provenance": level,
        "evidence": None if level == "unknown" else str(ident["provenance"]),
    }


def _tier_recorded(tiers: list[dict]) -> dict[str, dict]:
    """Dependencies a tier recorded the version of at run time (`TIER_VERSION_KEYS`), keyed by dependency name. The
    tier read a version string, not where the package came from, so this is INFERRED identity."""
    found: dict[str, dict] = {}
    for i, tier in enumerate(tiers):
        blocks = [("", tier)] + [(f".{k}", v) for k, v in sorted(tier.items()) if isinstance(v, dict)]
        for where, block in blocks:
            for key, name in TIER_VERSION_KEYS.items():
                version = block.get(key)
                if not isinstance(version, str) or not version:
                    continue
                version = version.removeprefix(f"{name} ").strip()  # pdb2pqr reports "pdb2pqr 3.8.0"
                prior = found.get(name)
                if prior is not None and prior["version"] != version:
                    raise ValueError(f"tiers record two versions of {name}: {prior['version']} and {version}")
                found.setdefault(
                    name,
                    {
                        "name": name,
                        "version": version,
                        "build_sha": None,
                        "dirty": None,
                        "provenance": "inferred",
                        "evidence": f"version string recorded at run time in tiers[{i}]{where}.{key} "
                        f"(tier {tier.get('name')!r}); where it was installed from was not recorded",
                        "source": None,
                    },
                )
    return found


def _python(host: dict | None) -> dict:
    version = (host or {}).get("python")
    if not version:
        return dependency("python", None)
    return {
        "name": "python",
        "version": version,
        "build_sha": None,
        "dirty": None,
        "provenance": "verified",
        "evidence": "platform.python_version() of the interpreter that ran (legacy record host.python)",
        "source": None,
    }


def _ferric(legacy: dict) -> dict:
    """ferric's core entry. A `--temporal` run finalized with `allow_mixed_builds` lists in `results.builds` every
    build that wrote its SCF rows; when more than one distinct ferric wrote them, the run has no one ferric, so the
    entry is `unknown` with `evidence` naming the mix (the spec's rule for mixed builds), not the finalizing process's
    ferric. Rows with no build recorded do not count as a second build (the legacy refusal counts the same way)."""
    builds = [b for b in ((legacy.get("results") or {}).get("builds") or []) if isinstance(b.get("ferric"), dict)]
    distinct = {json.dumps(b["ferric"], sort_keys=True) for b in builds}
    if len(distinct) <= 1:
        return dependency("ferric", legacy.get("ferric"))
    each = "; ".join(
        f"{str(b['ferric'].get('git_sha') or 'unstamped')[:12]}{' (dirty)' if b['ferric'].get('dirty') else ''}: "
        f"{b.get('n_rows')} row(s)"
        for b in builds
    )
    return {
        **dependency("ferric", None),
        "evidence": f"mixed builds: results.builds lists {len(distinct)} ferric builds that wrote this run's SCF rows "
        f"({each}); one identity per dependency cannot state them",
    }


def recorded_dependencies(legacy: dict) -> list[dict]:
    """What a legacy record itself says the run ran on: ferric (its identity block), Python, and the versions tiers
    recorded. smeltery's core packages it does not mention are listed `unknown`, never filled from today's
    environment (a conversion runs later, maybe elsewhere)."""
    deps = [_ferric(legacy), _python(legacy.get("host"))]
    tiered = _tier_recorded(legacy.get("tiers") or [])
    for name, _module in CORE_PACKAGES:
        deps.append(tiered.pop(name, None) or dependency(name, None))
    deps += [tiered[n] for n in sorted(tiered)]
    return deps


def live_dependencies(legacy: dict) -> list[dict]:
    """What this process ran on, for a record written at the end of a live run: ferric and Python from the legacy
    record (measured when it was built, in this process), every core package and every optional package the run
    imported through `record.package_identity`, then binaries only a tier recorded (xtb, pdb2pqr)."""
    deps = [_ferric(legacy), _python(legacy.get("host"))]
    for dist, module in CORE_PACKAGES + tuple(p for p in OPTIONAL_PACKAGES if p[1] in sys.modules):
        deps.append(dependency(dist, package_identity(dist, module)))
    names = {d["name"] for d in deps}
    tiered = _tier_recorded(legacy.get("tiers") or [])
    deps += [tiered[n] for n in sorted(tiered) if n not in names]
    return deps


# ---- legacy record -> core fields ----------------------------------------------------------------------------------


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def wire_value(value: float) -> float | str:
    """A metric value as the core record's JSON spells it: a non-finite float is the string "NaN", "Infinity" or
    "-Infinity" (JSON has no NaN; `attestation_schema.from_dict` takes JSON values and refuses a float NaN)."""
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    return value


def _metric(
    name: str, value: float, direction: str | None, unit: str | None = None, labels: dict | None = None
) -> dict:
    return {
        "name": name,
        "value": wire_value(value),
        "direction": direction,
        "step": None,
        "split": None,
        "unit": unit,
        "labels": labels or {},
    }


def metrics(results: dict) -> list[dict]:
    """Final values only; what a value is about goes in `labels`, never in its name.

    Per funnel stage (`labels.stage`), the candidates in and out (`stage.candidates_in`, `stage.survivors`; no
    direction: more survivors is not better). An unranked cut's survivor count (`candidates_out: null` beside
    `unranked: true`) is "NaN": the cut ran and its survivor set is undefined. Any other null count (a stage that does
    not count candidates, such as `structure`) is not reported, so it is left out. `cut.survivors` likewise.

    Per analogue (`labels.analogue`), the paired ddE `paired_ddE.mean` with direction "min" -- the cut ranks ascending
    mean, best group first, so smeltery genuinely knows lower is better -- and `paired_ddE.sem` and `paired_ddE.n` with
    none. mean and sem carry the unit of the quantity they pair (`results.units[DDE_QUANTITY]`); n is a count. A
    non-finite value (a one-pose analogue's sem is NaN) is written as such: computed and undefined, not missing. Values
    are JSON (`wire_value`): NaN is the string "NaN"."""
    out: list[dict] = []
    for row in results.get("funnel") or []:
        stage = {"stage": str(row.get("stage"))}
        for key, name in (("candidates_in", "candidates_in"), ("candidates_out", "survivors")):
            value = row.get(key)
            if _number(value):
                out.append(_metric(f"stage.{name}", value, None, labels=stage))
            elif value is None and key == "candidates_out" and row.get("unranked") is True:
                out.append(_metric(f"stage.{name}", math.nan, None, labels=stage))
    cut = results.get("cut") or {}
    if cut.get("survivors") is not None:
        out.append(_metric("cut.survivors", len(cut["survivors"]), None))
    elif cut.get("unranked") is True:
        out.append(_metric("cut.survivors", math.nan, None))
    unit = (results.get("units") or {}).get(DDE_QUANTITY)
    paired = results.get("paired_ddE") or {}
    order = [c for c in results.get("candidate_order") or [] if c in paired]
    order += sorted(c for c in paired if c not in order)
    for cand in order:
        m = paired[cand]
        for key, direction, u in (("mean", "min", unit), ("sem", None, unit), ("n", None, None)):
            if _number(m.get(key)):
                out.append(_metric(f"paired_ddE.{key}", m[key], direction, u, {"analogue": str(cand)}))
    return out


def seeds(tiers: list[dict]) -> dict[str, int | list[int]] | None:
    """`{tier: seed}` from each tier's integer `seed`, or `{tier: [seed, ...]}` from a tier's seed list (docking runs
    Vina once per seed, in order). None (unknown) when no tier records one, and when a tier records a seed list that is
    not a non-empty list of integers, or two tiers of one name record different seeds: a partial object would claim
    the rest were unseeded."""

    def integer(v: object) -> bool:
        return isinstance(v, int) and not isinstance(v, bool)

    found: dict[str, int | list[int]] = {}
    for tier in tiers:
        name = str(tier.get("name"))
        if "seeds" in tier:
            seed = tier["seeds"]
            if not (isinstance(seed, list) and seed and all(integer(s) for s in seed)):
                return None
        elif integer(tier.get("seed")):
            seed = tier["seed"]
        else:
            continue
        if found.setdefault(name, seed) != seed:
            return None
    return found or None


def data(legacy: dict, legacy_name: str, legacy_sha256: str) -> list[dict]:
    """Files the run read, by the digests the legacy record already holds (role input): the staged config and its
    staged inputs, the receptor, the decisions file, a tier's pocket input. One entry per content digest. Then the
    legacy record itself (role output); the manifest entry is appended by the writer."""
    results = legacy.get("results") or {}
    out: list[dict] = []
    seen: set[str] = set()

    def add(name: object, sha: object, role: str) -> None:
        if isinstance(name, str) and name and isinstance(sha, str) and sha not in seen:
            seen.add(sha)
            out.append({"name": name, "sha256": sha, "role": role})

    config = results.get("config") or {}
    add(config.get("file"), config.get("sha256"), "input")
    for key in sorted(config.get("inputs") or {}):
        entry = config["inputs"][key] or {}
        add(entry.get("copy"), entry.get("sha256"), "input")
    for key in ("receptor", "decisions_in"):
        entry = results.get(key) or {}
        add(entry.get("file"), entry.get("sha256"), "input")
    for tier in legacy.get("tiers") or []:
        field = tier.get("field")
        if isinstance(field, dict):
            add(field.get("input"), field.get("input_sha256"), "input")
    add(legacy_name, legacy_sha256, "output")
    return out


def core_fields(
    legacy: dict,
    *,
    legacy_name: str,
    legacy_sha256: str,
    manifest: dict,
    producer: dict,
    dependencies: list[dict],
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict:
    """The core record, as plain JSON, for one finished (not partial) legacy record. Pure: no clock, no environment.

    `started_at`/`finished_at` are the run's real start and end, known only to a live run. The legacy `started_utc`
    is neither (it is stamped when the record object is built, after the measurement tier), so a converted record
    says null and carries it in `ext.smeltery.started_utc` under its own name.
    """
    results = legacy.get("results") or {}
    if "cut" not in results or "funnel" not in results:
        raise ValueError("a partial record (--only-candidate) is not a finished run: combine the partials first")
    input_digest = legacy.get("input_digest")
    if not isinstance(input_digest, str) or len(input_digest) < 12:
        raise ValueError("the record has no input_digest")
    tiers = legacy.get("tiers") or []
    return {
        "schema": _schema.SCHEMA if _schema else "attestation.run/1",
        "run_id": input_digest[:12],
        "family": legacy.get("campaign"),
        "arm": None,
        "status": "complete",
        "started_at": started_at,
        "finished_at": finished_at,
        "producer": producer,
        "code": _code(legacy.get("smeltery"), legacy.get("smeltery_version")),
        "config": legacy.get("inputs") or {},
        "data": [*data(legacy, legacy_name, legacy_sha256), manifest],
        "metrics": metrics(results),
        "history": [],  # smeltery logs no curves: every value it records is final
        "seeds": seeds(tiers),
        "dependencies": dependencies,
        "ext": {
            "smeltery": {
                "tiers": tiers,
                "input_digest": input_digest,
                "host": legacy.get("host"),
                "started_utc": legacy.get("started_utc"),
                "legacy_schema_version": legacy.get("schema_version"),
                "cut_unranked": (results.get("cut") or {}).get("unranked"),
            }
        },
    }


# ---- writing -------------------------------------------------------------------------------------------------------


def _write_steps(run_dir: Path, results: dict) -> None:
    """`steps/NN-<stage>/stage.json` for each funnel stage in execution order: the stage's funnel row and its
    stage-log rows (summary, seconds), as recorded. smeltery's outputs (poses, SCF log) span stages and stay where the
    GUI contract puts them; this is what a stage produced on its own."""
    log = results.get("stages") or []
    for i, row in enumerate(results.get("funnel") or []):
        d = _layout.step_dir(run_dir, i, str(row.get("stage")))
        d.mkdir(parents=True, exist_ok=True)
        body = {"funnel": row, "log": [r for r in log if r.get("stage") == row.get("stage")]}
        (d / STEP_FILE).write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")


def producer(name: str = "smeltery") -> dict:
    try:
        version = metadata.version("smeltery")
    except metadata.PackageNotFoundError:
        version = None
    return {"name": name, "version": version}


def write_core(
    legacy_path: str | Path,
    *,
    live: bool = False,
    started_at: str | None = None,
    finished_at: str | None = None,
    producer_info: dict | None = None,
    overwrite: bool = False,
) -> Path:
    """Write the core record for the legacy record at `legacy_path` into its directory (the run directory): steps,
    then `attestation/digests.json`, then `attestation/run.json` through `attestation_schema.write`. Returns run.json.

    `live`: called by the run that just wrote the record, so dependencies are measured in this process
    (`live_dependencies`); otherwise only what the record says (`recorded_dependencies`). An existing core record is
    refused (FileExistsError) unless `overwrite`; then it, the manifest and `steps/` are replaced.
    """
    _require()
    legacy_path = Path(legacy_path)
    run_dir = legacy_path.parent
    raw = legacy_path.read_bytes()
    legacy = json.loads(raw)
    target = _schema.record_path(run_dir)
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} exists; pass overwrite (--force) to replace it")
    results = legacy.get("results") or {}
    if "cut" not in results or "funnel" not in results:  # before touching anything
        raise ValueError("a partial record (--only-candidate) is not a finished run: combine the partials first")
    target.unlink(missing_ok=True)  # the manifest is refused while a record exists
    shutil.rmtree(run_dir / _layout.STEPS_DIR, ignore_errors=True)  # steps/ is this module's alone in OUT
    _write_steps(run_dir, results)
    manifest = _layout.write_manifest(run_dir)
    fields = core_fields(
        legacy,
        legacy_name=legacy_path.name,
        legacy_sha256=hashlib.sha256(raw).hexdigest(),
        manifest={"name": manifest.name, "sha256": manifest.sha256, "role": manifest.role},
        producer=producer_info or producer("smeltery" if live else "smeltery record-core"),
        dependencies=live_dependencies(legacy) if live else recorded_dependencies(legacy),
        started_at=started_at,
        finished_at=finished_at,
    )
    return _schema.write(_schema.from_dict(fields), run_dir, overwrite=overwrite)


def _legacy_in(path: Path) -> Path:
    if path.is_dir():
        found = sorted(path.glob("run-*.json"))
        if len(found) != 1:
            raise ValueError(f"{path} holds {len(found)} run records ({[p.name for p in found]}); name one")
        return found[0]
    return path


def main(argv: list[str] | None = None) -> int:
    """`smeltery record core RUN.json|OUT [--force]`: write the core record for an existing run."""
    ap = argparse.ArgumentParser(prog="smeltery record", description="Write a run's core record (attestation.run/1).")
    sub = ap.add_subparsers(dest="what", required=True)
    core = sub.add_parser("core", help="derive OUT/attestation/run.json from OUT/run-<digest12>.json")
    core.add_argument("run", type=Path, help="a run-<digest12>.json, or the OUT directory holding exactly one")
    core.add_argument("--force", action="store_true", help="replace an existing core record")
    args = ap.parse_args(argv)
    if not AVAILABLE:
        print(f"error: {NOT_INSTALLED}", file=sys.stderr)
        return 2
    try:
        path = write_core(_legacy_in(args.run), overwrite=args.force)
    except (OSError, ValueError) as e:  # RecordError is a ValueError: a schema refusal names its field
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"core record: {path}")
    return 0
