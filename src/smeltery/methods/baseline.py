"""`baseline`: an opt-in control that runs cheap baselines beside ddE and reports whether ddE ranks better.

Config (`[baseline]`; absent means off, and then nothing about the run changes, not one byte of its record):

    [baseline]
    labels = "labels.csv"      # optional: `name,value` per analogue, same sign as ddE (higher = worse binding)
    logp = true                # Crippen logP of each analogue (default true)
    mmgbsa = false             # MM/GBSA ddG; computed only if its optional packages are installed
    mmgbsa_file = "mm.csv"     # or ddG_MM per analogue computed elsewhere (scripts/plb_mmgbsa.py)
    seed = 20261010
    n_boot = 10000

It is an `analysis`: it runs after the cut, reads `results.paired_ddE` and writes `results.baseline` only. It never
touches the ranking, the cut or any other key. The statistics and the verdict phrases are `smeltery.baseline`'s.

MM/GBSA: `mmgbsa_file` is used as given (no packages needed). Otherwise `mmgbsa = true` needs openmm,
openmmforcefields, openff-toolkit and `sqm` already installed; if any is missing the baseline is recorded as
skipped, with the missing ones named. This build has no in-process MM/GBSA engine, so with the packages present and
no file it is skipped too, saying so (`MMGBSA_ENGINE` is the injection point; tests set it).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import baseline as core
from ..config import ConfigError, _validate_section
from . import MethodSpec, register, registry
from .catalog import Param

#: An in-process MM/GBSA engine: `engine(plan) -> {analogue: ddG_MM}`. None in this build.
MMGBSA_ENGINE: Callable[[Any], dict[str, float]] | None = None


@dataclass
class BaselineKnobs:
    labels: str | None = None
    logp: bool = True
    mmgbsa: bool = False
    mmgbsa_file: str | None = None
    seed: int = core.DEFAULT_SEED
    n_boot: int = core.DEFAULT_N_BOOT


def _resolve(cfg: dict, name: str) -> Path:
    p = Path(name)
    return p if p.is_absolute() else Path(cfg.get("_dir", ".")) / p


def read_values(path: Path, what: str) -> dict[str, float]:
    """`name,value` (or tab or space separated) per line; `#` comments, blank lines and one non-numeric header line
    are skipped. A name twice, or a value that is not a finite number, is an error naming the line."""
    out: dict[str, float] = {}
    first = True
    for i, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = (
            [p.strip() for p in line.replace("\t", ",").split(",")]
            if ("," in line or "\t" in line)
            else line.rsplit(None, 1)
        )
        if len(parts) != 2 or not parts[0]:
            raise ValueError(f"{what} {path.name} line {i}: expected `name,value`, got {raw!r}")
        try:
            v = float(parts[1])
        except ValueError:
            if first:  # a header line
                first = False
                continue
            raise ValueError(f"{what} {path.name} line {i}: {parts[1]!r} is not a number") from None
        first = False
        if not math.isfinite(v):
            raise ValueError(f"{what} {path.name} line {i}: {parts[1]!r} is not finite")
        if parts[0] in out:
            raise ValueError(f"{what} {path.name} line {i}: {parts[0]!r} appears twice")
        out[parts[0]] = v
    if not out:
        raise ValueError(f"{what} {path.name}: no `name,value` lines")
    return out


def _validate(cfg: dict) -> None:
    """`[baseline]`: a table of its knobs; types, and that some baseline is asked for."""
    _validate_section(registry()["baseline"], cfg)
    s = cfg.get("baseline")
    if s is None:
        return
    for k in ("labels", "mmgbsa_file"):
        if k in s and not (isinstance(s[k], str) and s[k]):
            raise ConfigError(f"[baseline] {k} must be a path (a string)")
    for k in ("logp", "mmgbsa"):
        if k in s and not isinstance(s[k], bool):
            raise ConfigError(f"[baseline] {k} must be true or false")
    for k in ("seed", "n_boot"):
        if k in s and not (isinstance(s[k], int) and not isinstance(s[k], bool)):
            raise ConfigError(f"[baseline] {k} must be an integer")
    if s.get("n_boot", core.DEFAULT_N_BOOT) < 200:
        raise ConfigError("[baseline] n_boot must be at least 200: a percentile CI from fewer resamples is not a CI")
    if not (s.get("logp", True) or s.get("mmgbsa", False) or s.get("mmgbsa_file")):
        raise ConfigError("[baseline] asks for no baseline: set logp = true, mmgbsa = true or mmgbsa_file")


class BaselineControl:
    """The `analysis` hook. `mmgbsa_available` (tests) replaces the package check."""

    def __init__(self) -> None:
        self.mmgbsa_available: Callable[[], tuple[bool, str]] | None = None

    def _mmgbsa(self, plan: Any, s: dict) -> Callable[[], dict[str, float]] | str:
        if s.get("mmgbsa_file"):
            path = _resolve(plan.cfg, s["mmgbsa_file"])
            return lambda: read_values(path, "[baseline] mmgbsa_file")
        ok, why = (self.mmgbsa_available or core.mmgbsa_availability)()
        if not ok:
            return why
        if MMGBSA_ENGINE is None:
            return (
                "its packages are installed but this build has no in-process MM/GBSA engine: run "
                "scripts/plb_mmgbsa.py and pass its ddG_MM as [baseline] mmgbsa_file"
            )
        engine = MMGBSA_ENGINE
        return lambda: engine(plan)

    def run(self, plan: Any, rec: Any, ctx: dict) -> dict:
        s = plan.cfg.get("baseline", {})
        ddE = {
            k: float(v["mean"])
            for k, v in (rec.results.get("paired_ddE") or {}).items()
            if v.get("mean") is not None and math.isfinite(v["mean"])
        }
        smiles = {c.name: c.smiles for c in plan.candidates if c.name in ddE}
        labels = read_values(_resolve(plan.cfg, s["labels"]), "[baseline] labels") if s.get("labels") else None
        baselines: dict[str, Any] = {}
        if s.get("logp", True):
            baselines["crippen_logp"] = lambda: core.crippen_logp(smiles)
        if s.get("mmgbsa", False) or s.get("mmgbsa_file"):
            baselines["mmgbsa"] = self._mmgbsa(plan, s)
        return core.baseline_control(
            ddE,
            labels,
            baselines,
            seed=int(s.get("seed", core.DEFAULT_SEED)),
            n_boot=int(s.get("n_boot", core.DEFAULT_N_BOOT)),
        )


HOOK = BaselineControl()


def register_methods() -> None:
    register(
        MethodSpec(
            "baseline",
            "Baseline control (logP, MM/GBSA)",
            "empirical",
            "recorded",
            step="cut",
            slot="analysis",
            section="baseline",
            knobs=BaselineKnobs,
            file_knobs=("labels", "mmgbsa_file"),
            order=160,
            needs=("dE_int",),
            hook=HOOK,
            validate=_validate,
            cost_model=lambda: (
                "cannot say: no recorded timing (Crippen logP is milliseconds per molecule; no SCF runs)"
            ),
            summary=(
                "Cheap baselines beside ddE: with experimental labels, Delta-rho against ddE with a paired "
                "bootstrap CI and a verdict that says when n is too small to conclude."
            ),
            params=(
                Param("labels", "path", None, optional=True, help="name,value per analogue, the sign of ddE"),
                Param("logp", "bool", True, help="Crippen logP baseline"),
                Param(
                    "mmgbsa",
                    "bool",
                    False,
                    help="MM/GBSA baseline; skipped, with the reason, if its packages are absent",
                ),
                Param("mmgbsa_file", "path", None, optional=True, help="ddG_MM per analogue computed elsewhere"),
                Param("seed", "int", core.DEFAULT_SEED, help="bootstrap seed"),
                Param("n_boot", "int", core.DEFAULT_N_BOOT, help="bootstrap resamples"),
            ),
        )
    )
