"""Experimental ddG benchmark: typed loader, ddG arithmetic and suitability analysis.

The committed data lives in ``benchmarks/plb/manifest.json`` (see ``benchmarks/plb/README.md``). This module
reads it STRICTLY: a missing provenance field, an unknown unit, a non-positive value, an empty licence or
attribution, or a stored ddG that does not recompute from the raw measurement raises `BenchmarkError`.
There are no silent defaults.

ddG definition (written out here because every number in the manifest depends on it):

    X          = IC50 or Ki as a molar concentration (pIC50: X = 10**(-pIC50) M)
    dG_i       = R T ln(X_i / 1 M)                       (apparent; IC50 is NOT Kd, Cheng-Prusoff NOT applied)
    ddG_i      = dG_i - dG_ref = R T ln(X_i / X_ref)     kcal/mol; negative = binds tighter than the reference
    sigma_lnX  = error / value            (value in the same unit as error; first-order propagation)
                 ln(10) * error           (pIC50)
    sigma_ddG  = R T sqrt(sigma_lnX_i**2 + sigma_lnX_ref**2)   (independent errors; zero for the reference
                 itself, whose error is the reference's own and cancels by definition)
    R = 1.987204258640832e-3 kcal/(mol K) (CODATA 2018, J/4184), T = 298.15 K (an assumption: the assays were
    run at whatever temperature their papers state; PLB does not record it).

The reported `error` is whatever PLB copied from the source paper; PLB's README says only "Error of
measurement" and does not say whether it is an SD, an SEM or a range. UNVERIFIED.

RDKit (a core dependency) is imported lazily and only by the suitability functions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
R_KCAL_MOL_K = 8.31446261815324 / 4184.0  # CODATA 2018 exact R, J/(mol K) -> kcal/(mol K)
TEMPERATURE_K = 298.15

MEASUREMENT_TYPES = ("ic50", "ki", "pic50")
# Unit -> factor to molar. pIC50 is dimensionless: its unit must be the empty string, exactly as PLB writes it.
_UNIT_TO_MOLAR = {"M": 1.0, "mM": 1e-3, "uM": 1e-6, "nM": 1e-9, "pM": 1e-12}
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "benchmarks" / "plb" / "manifest.json"


class BenchmarkError(ValueError):
    """The manifest (or a measurement) is incomplete, inconsistent or outside what this module accepts."""


# --------------------------------------------------------------------------------------------- dataclasses


@dataclass(frozen=True)
class Source:
    kind: str  # "doi" or "url"
    id: str


@dataclass(frozen=True)
class Measurement:
    type: str
    value: float
    unit: str
    error: float | None  # None only where PLB reports null
    sources: tuple[Source, ...]
    comment: str

    def molar(self) -> float:
        return to_molar(self.type, self.value, self.unit)

    def sigma_ln(self) -> float | None:
        return sigma_ln(self.type, self.value, self.unit, self.error)


@dataclass(frozen=True)
class Ligand:
    name: str
    smiles: str  # RDKit canonical, heavy atoms only (derived)
    smiles_upstream: str  # verbatim from PLB ligands.yml
    measurement: Measurement
    ddg_kcal_mol: float
    ddg_sigma_kcal_mol: float | None
    plb_charge: int | None  # PLB's per-ligand `charge`, where it has one


@dataclass(frozen=True)
class SourceFile:
    kind: str  # protein_pdb | ligands_sdf | ligands_yml | target_yml
    path: str  # relative to the upstream repository root
    url: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Target:
    name: str
    pdb_id: str
    assay_type: str  # "ic50" | "ki" | "pic50"
    reference: str  # ligand name
    reference_rule: str
    protein_net_charge: int | None  # PLB's `netcharge` is the PROTEIN's; None where PLB gives none
    protonation: str
    ligands: tuple[Ligand, ...]
    files: tuple[SourceFile, ...]
    suitability: Mapping[str, Any]

    def ligand(self, name: str) -> Ligand:
        for lig in self.ligands:
            if lig.name == name:
                return lig
        raise KeyError(name)


@dataclass(frozen=True)
class Benchmark:
    schema_version: int
    name: str
    benchmark_version: str
    temperature_k: float
    upstream_url: str
    upstream_commit: str
    upstream_commit_date: str
    retrieval_date: str
    data_licence: str
    data_licence_url: str
    code_licence: str
    copyright: str
    attribution: str
    noise_floor_kcal_mol: float
    assay_variability: Mapping[str, Any]
    targets: Mapping[str, Target]
    raw: Mapping[str, Any]


# --------------------------------------------------------------------------------------------- arithmetic


def to_molar(mtype: str, value: float, unit: str) -> float:
    """A measurement as a molar concentration. Refuses unknown types/units and non-positive values."""
    if mtype not in MEASUREMENT_TYPES:
        raise BenchmarkError(f"unknown measurement type {mtype!r} (accepted: {MEASUREMENT_TYPES})")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BenchmarkError(f"measurement value {value!r} is not a finite number")
    if value <= 0:
        raise BenchmarkError(f"non-positive measurement value {value!r}")
    if mtype == "pic50":
        if unit != "":
            raise BenchmarkError(f"pIC50 is dimensionless; unit must be '' but is {unit!r}")
        return 10.0 ** (-value)
    if unit not in _UNIT_TO_MOLAR:
        raise BenchmarkError(f"unknown unit {unit!r} (accepted: {sorted(_UNIT_TO_MOLAR)})")
    return value * _UNIT_TO_MOLAR[unit]


def sigma_ln(mtype: str, value: float, unit: str, error: float | None) -> float | None:
    """First-order sigma of ln(X): error/value (concentration types) or ln(10)*error (pIC50). None if no error."""
    to_molar(mtype, value, unit)  # validates
    if error is None:
        return None
    if isinstance(error, bool) or not isinstance(error, (int, float)) or not math.isfinite(error) or error < 0:
        raise BenchmarkError(f"measurement error {error!r} is not a finite non-negative number")
    return math.log(10.0) * error if mtype == "pic50" else error / value


def ddg_kcal_mol(x_molar: float, x_ref_molar: float, temperature_k: float = TEMPERATURE_K) -> float:
    """ddG = R T ln(X / X_ref) in kcal/mol; negative = tighter than the reference."""
    if x_molar <= 0 or x_ref_molar <= 0:
        raise BenchmarkError("ddG needs positive concentrations")
    return R_KCAL_MOL_K * temperature_k * math.log(x_molar / x_ref_molar)


def ddg_sigma_kcal_mol(
    s_ln: float | None, s_ln_ref: float | None, temperature_k: float = TEMPERATURE_K
) -> float | None:
    """R T sqrt(s^2 + s_ref^2); None when either error is unreported."""
    if s_ln is None or s_ln_ref is None:
        return None
    return R_KCAL_MOL_K * temperature_k * math.hypot(s_ln, s_ln_ref)


def ddg_between(target: Target, a: str, b: str) -> float:
    """ddG(a) - ddG(b) = RT ln(X_a / X_b) for two ligands of one target."""
    return target.ligand(a).ddg_kcal_mol - target.ligand(b).ddg_kcal_mol


# --------------------------------------------------------------------------------------------- strict loader

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _req(d: Mapping[str, Any], key: str, typ: type | tuple[type, ...], where: str, nonempty: bool = True) -> Any:
    if not isinstance(d, Mapping) or key not in d:
        raise BenchmarkError(f"{where}: missing required field {key!r}")
    v = d[key]
    if isinstance(v, bool) and bool not in (typ if isinstance(typ, tuple) else (typ,)):
        raise BenchmarkError(f"{where}.{key}: expected {typ}, got bool")
    if not isinstance(v, typ):
        raise BenchmarkError(f"{where}.{key}: expected {typ}, got {type(v).__name__}")
    if nonempty and isinstance(v, (str, list, dict)) and len(v) == 0:
        raise BenchmarkError(f"{where}.{key}: must not be empty")
    if nonempty and isinstance(v, str) and not v.strip():
        raise BenchmarkError(f"{where}.{key}: must not be blank")
    return v


def _parse_sources(raw: Any, where: str) -> tuple[Source, ...]:
    if not isinstance(raw, list) or not raw:
        raise BenchmarkError(f"{where}: sources must be a non-empty list")
    out = []
    for i, s in enumerate(raw):
        kind = _req(s, "kind", str, f"{where}[{i}]")
        sid = _req(s, "id", str, f"{where}[{i}]")
        if kind not in ("doi", "url"):
            raise BenchmarkError(f"{where}[{i}]: source kind {kind!r} must be 'doi' or 'url'")
        if kind == "doi" and not sid.startswith("10."):
            raise BenchmarkError(f"{where}[{i}]: {sid!r} is not a DOI")
        if kind == "url" and not sid.startswith("https://"):
            raise BenchmarkError(f"{where}[{i}]: {sid!r} is not an https URL")
        out.append(Source(kind, sid))
    return tuple(out)


def _parse_measurement(m: Any, where: str) -> Measurement:
    mtype = _req(m, "type", str, where)
    unit = _req(m, "unit", str, where, nonempty=False)
    value = _req(m, "value", (int, float), where)
    if "error" not in m:
        raise BenchmarkError(f"{where}: missing required field 'error' (null is allowed, absence is not)")
    err = m["error"]
    comment = _req(m, "comment", str, where, nonempty=False)
    sources = _parse_sources(m.get("sources"), f"{where}.sources")
    sigma_ln(mtype, value, unit, err)  # validates type, unit, positivity, error
    return Measurement(mtype, float(value), unit, None if err is None else float(err), sources, comment)


def _parse_target(name: str, t: Any, temperature_k: float) -> Target:
    where = f"targets.{name}"
    assay = _req(t, "assay_type", str, where)
    ref = _req(t, "reference", str, where)
    ligs_raw = _req(t, "ligands", list, where)
    ligands = []
    for i, lg in enumerate(ligs_raw):
        lw = f"{where}.ligands[{i}]"
        lname = _req(lg, "name", str, lw)
        meas = _parse_measurement(_req(lg, "measurement", dict, lw), f"{lw}.measurement")
        if meas.type != assay:
            raise BenchmarkError(f"{lw}: measurement type {meas.type!r} differs from the target's assay_type {assay!r}")
        ddg = _req(lg, "ddg_kcal_mol", (int, float), lw, nonempty=False)
        if "ddg_sigma_kcal_mol" not in lg:
            raise BenchmarkError(f"{lw}: missing required field 'ddg_sigma_kcal_mol' (null is allowed)")
        pc = lg.get("plb_charge", "MISSING")
        if pc == "MISSING":
            raise BenchmarkError(f"{lw}: missing required field 'plb_charge' (null is allowed)")
        ligands.append(
            Ligand(
                lname,
                _req(lg, "smiles", str, lw),
                _req(lg, "smiles_upstream", str, lw),
                meas,
                float(ddg),
                None if lg["ddg_sigma_kcal_mol"] is None else float(lg["ddg_sigma_kcal_mol"]),
                pc,
            )
        )
    names = [lg.name for lg in ligands]
    if len(set(names)) != len(names):
        raise BenchmarkError(f"{where}: duplicate ligand names")
    if ref not in names:
        raise BenchmarkError(f"{where}: reference {ref!r} is not a ligand of the target")
    # ddG must recompute from the raw measurements.
    ref_l = ligands[names.index(ref)]
    x_ref, s_ref = ref_l.measurement.molar(), ref_l.measurement.sigma_ln()
    for lig in ligands:
        want = ddg_kcal_mol(lig.measurement.molar(), x_ref, temperature_k)
        if abs(want - lig.ddg_kcal_mol) > 1e-9:
            raise BenchmarkError(f"{where}.{lig.name}: stored ddG {lig.ddg_kcal_mol} != recomputed {want}")
        if lig.name == ref:
            want_s = 0.0 if lig.measurement.error is not None else None
        else:
            want_s = ddg_sigma_kcal_mol(lig.measurement.sigma_ln(), s_ref, temperature_k)
        if (want_s is None) != (lig.ddg_sigma_kcal_mol is None) or (
            want_s is not None and abs(want_s - lig.ddg_sigma_kcal_mol) > 1e-9
        ):
            raise BenchmarkError(f"{where}.{lig.name}: stored ddG sigma != recomputed {want_s}")
    files = []
    for i, f in enumerate(_req(t, "files", list, where)):
        fw = f"{where}.files[{i}]"
        sha = _req(f, "sha256", str, fw)
        if not _SHA256.match(sha):
            raise BenchmarkError(f"{fw}: sha256 {sha!r} is not 64 lowercase hex digits")
        files.append(
            SourceFile(
                _req(f, "kind", str, fw),
                _req(f, "path", str, fw),
                _req(f, "url", str, fw),
                sha,
                _req(f, "size_bytes", int, fw),
            )
        )
    pnc = t.get("protein_net_charge", "MISSING")
    if pnc == "MISSING":
        raise BenchmarkError(f"{where}: missing required field 'protein_net_charge' (null is allowed)")
    return Target(
        name,
        _req(t, "pdb_id", str, where),
        assay,
        ref,
        _req(t, "reference_rule", str, where),
        pnc,
        _req(t, "protonation", str, where),
        tuple(ligands),
        tuple(files),
        _req(t, "suitability", dict, where),
    )


def parse_manifest(raw: Mapping[str, Any]) -> Benchmark:
    """Validate a decoded manifest and return it as typed objects. Raises BenchmarkError on any defect."""
    if _req(raw, "schema_version", int, "manifest") != SCHEMA_VERSION:
        raise BenchmarkError(f"unsupported schema_version {raw['schema_version']!r}")
    prov = _req(raw, "provenance", dict, "manifest")
    commit = _req(prov, "upstream_commit", str, "provenance")
    if not _COMMIT.match(commit):
        raise BenchmarkError(f"provenance.upstream_commit {commit!r} is not a 40-hex commit sha")
    for k in ("upstream_commit_date", "retrieval_date"):
        if not _DATE.match(_req(prov, k, str, "provenance")):
            raise BenchmarkError(f"provenance.{k} is not YYYY-MM-DD")
    lic = _req(raw, "licence", dict, "manifest")
    temperature = _req(raw, "temperature_k", (int, float), "manifest", nonempty=False)
    if temperature <= 0:
        raise BenchmarkError("temperature_k must be positive")
    floor = _req(raw, "noise_floor", dict, "manifest")
    targets = {n: _parse_target(n, t, float(temperature)) for n, t in _req(raw, "targets", dict, "manifest").items()}
    attribution = _req(lic, "attribution", str, "licence")
    if "CC BY 4.0" not in attribution and "CC-BY-4.0" not in attribution:
        raise BenchmarkError("licence.attribution must name the licence (CC BY 4.0)")
    return Benchmark(
        SCHEMA_VERSION,
        _req(raw, "name", str, "manifest"),
        _req(raw, "benchmark_version", str, "manifest"),
        float(temperature),
        _req(prov, "upstream_url", str, "provenance"),
        commit,
        prov["upstream_commit_date"],
        prov["retrieval_date"],
        _req(lic, "data_licence", str, "licence"),
        _req(lic, "data_licence_url", str, "licence"),
        _req(lic, "code_licence", str, "licence"),
        _req(lic, "copyright", str, "licence"),
        attribution,
        float(_req(floor, "value_kcal_mol", (int, float), "noise_floor", nonempty=False)),
        _req(raw, "assay_variability", dict, "manifest"),
        targets,
        raw,
    )


def load_benchmark(path: str | Path = DEFAULT_MANIFEST) -> Benchmark:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot read manifest {p}: {exc}") from exc
    return parse_manifest(raw)


# --------------------------------------------------------------------------------------------- fetching

Downloader = Callable[[str], bytes]


def _urllib_download(url: str) -> bytes:  # pragma: no cover - live network, opt-in
    import urllib.request

    if not url.startswith("https://"):
        raise BenchmarkError(f"refusing non-https URL {url!r}")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        return resp.read()


def fetch_files(
    bench: Benchmark,
    dest: str | Path,
    targets: Iterable[str] | None = None,
    kinds: Iterable[str] = ("protein_pdb", "ligands_sdf"),
    downloader: Downloader | None = None,
) -> list[Path]:
    """Download the un-committed upstream files into `dest/<target>/<basename>`, verifying sha256.

    A digest mismatch raises BenchmarkError and nothing is left at the destination path. `downloader` is
    injectable for tests; the default is a plain https GET. Callers (the CLI script) must gate the live
    network behind SMELTERY_NETWORK=1.
    """
    dl = downloader or _urllib_download
    kinds = tuple(kinds)
    wanted = list(targets) if targets is not None else list(bench.targets)
    unknown = [t for t in wanted if t not in bench.targets]
    if unknown:
        raise BenchmarkError(f"unknown targets {unknown}")
    out: list[Path] = []
    for tname in wanted:
        for f in bench.targets[tname].files:
            if f.kind not in kinds:
                continue
            data = dl(f.url)
            digest = hashlib.sha256(data).hexdigest()
            if digest != f.sha256:
                raise BenchmarkError(f"{tname}/{f.kind}: sha256 {digest} != pinned {f.sha256} ({f.url})")
            path = Path(dest) / tname / Path(f.path).name
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            out.append(path)
    return out


# --------------------------------------------------------------------------------------------- suitability

# Elements treated as ordinary organic chemistry; anything else in a ligand is reported.
_ORGANIC = {"H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Se", "Br", "I"}
# Warhead screen. A match is a CANDIDATE for covalent binding needing manual review; absence is not proof.
COVALENT_SMARTS = {
    "acrylamide/acrylate/enone": "[CX3;!a]=[CX3;!a][CX3](=O)",
    "vinyl sulfone": "[CX3]=[CX3][SX4](=O)(=O)",
    "alpha-halocarbonyl": "[CX4]([Cl,Br,I])[CX3]=O",
    "epoxide/aziridine": "[C]1[O,N]C1",
    "isothiocyanate": "N=C=S",
    "aldehyde": "[CX3H1](=O)[#6]",
    "boronic acid": "[BX3]([OH])[OH]",
    "sulfonyl fluoride": "S(=O)(=O)F",
}


def mol_from_smiles(smiles: str):
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise BenchmarkError(f"RDKit cannot parse SMILES {smiles[:60]!r}...")
    return mol


def heavy_canonical(smiles: str) -> str:
    """RDKit canonical isomeric SMILES with explicit hydrogens removed."""
    from rdkit import Chem

    return Chem.MolToSmiles(Chem.RemoveHs(mol_from_smiles(smiles)))


def ligand_chemistry(smiles: str) -> dict[str, Any]:
    from rdkit import Chem

    mol = mol_from_smiles(smiles)
    heavy = Chem.RemoveHs(mol)
    elems = sorted({a.GetSymbol() for a in mol.GetAtoms()} - _ORGANIC)
    warheads = []
    for label, sma in COVALENT_SMARTS.items():
        patt = Chem.MolFromSmarts(sma)
        if patt is None:
            raise BenchmarkError(f"bad warhead SMARTS {label}")
        if heavy.HasSubstructMatch(patt):
            warheads.append(label)
    return {
        "net_charge": Chem.GetFormalCharge(mol),
        "heavy_atoms": heavy.GetNumAtoms(),
        "non_organic_elements": elems,
        "warhead_candidates": warheads,
    }


def power_summary(ddg: list[float], floor: float, ref_index: int) -> dict[str, Any]:
    """Which experimental differences are at least as large as the ddE noise floor (best case: ddE on the ddG scale).

    Counts only; it says nothing about whether smeltery's ddE tracks ddG (that is issue #27's measurement).
    """
    n = len(ddg)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    d = [abs(ddg[i] - ddg[j]) for i, j in pairs]
    vs_ref = [abs(ddg[i] - ddg[ref_index]) for i in range(n) if i != ref_index]
    mean = sum(ddg) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in ddg) / (n - 1)) if n > 1 else 0.0
    return {
        "floor_kcal_mol": floor,
        "n_analogues": n - 1,
        "n_analogues_ge_floor_vs_reference": sum(1 for x in vs_ref if x >= floor),
        "n_analogues_ge_2floor_vs_reference": sum(1 for x in vs_ref if x >= 2 * floor),
        "n_pairs": len(pairs),
        "n_pairs_ge_floor": sum(1 for x in d if x >= floor),
        "n_pairs_ge_2floor": sum(1 for x in d if x >= 2 * floor),
        "ddg_sd_over_floor": sd / floor,
        "max_abs_ddg_vs_reference": max(vs_ref) if vs_ref else 0.0,
        "ddg_range": max(ddg) - min(ddg),
    }
