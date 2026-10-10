"""Property providers: liability and ADMET-style endpoints behind one interface.

A `PropertyProvider` answers named endpoints for a molecule (SMILES). Two rules
hold for every provider, because breaking either has misled screens before:

* An endpoint it cannot answer is `None`, never `0.0`. A zero is an answer ("no
  liability"); `None` is "no answer", and the two must not be confused or summed.
* Every endpoint is a RANK-ONLY liability density, not a probability of
  toxicity. `endpoint_notes()` says so per endpoint; nothing here is calibrated.

`RdkitAlertProvider` counts structural-alert matches (PAINS, Brenk, NIH catalogs
shipped with RDKit). It discriminates between MOTIFS, not between substituents
that leave the motif alone: a halogen scan around an unchanged scaffold scores
every analogue identically, and `compare` reports that as CANNOT_DISCRIMINATE
rather than as a tie at the top.

Cost (measured elsewhere in this project on a 21-atom ligand): RDKit alerts
~3.7 ms; an offline assessment ~54 ms; with network lookups (`include_web`) ~1.6 s.
Providers that need the network set `requires_network = True` and are refused
unless the caller passes `allow_network=True`; off by default.

A learned predictor plugs in by implementing the Protocol and setting
`applicability` honestly; callers do not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

RANK_ONLY_NOTE = "rank-only liability density; not a probability of toxicity"

Discrimination = Literal["DISCRIMINATES", "CANNOT_DISCRIMINATE", "UNAVAILABLE"]


class NetworkNotAllowedError(RuntimeError):
    """Raised when a network-backed provider is used without `allow_network=True`."""


@runtime_checkable
class PropertyProvider(Protocol):
    name: str
    requires_network: bool

    def endpoint_notes(self) -> dict[str, str]:
        """{endpoint: note}; every note must state that the value is rank-only."""
        ...

    # Not Protocol members (that would break existing providers under isinstance), but required by
    # `smeltery.providers.conformance`: `license_id: str` (SPDX) and `endpoint_units() -> {endpoint: unit}`.

    def applicability(self, smiles: str) -> bool:
        """Is this molecule inside what the provider can answer? False -> predict returns Nones."""
        ...

    def predict(self, smiles: str) -> dict[str, float | None]:
        """{endpoint: value or None}. Never 0.0 for an unanswerable endpoint."""
        ...

    def settings(self) -> dict: ...


def assess(provider: PropertyProvider, smiles: str, *, allow_network: bool = False) -> dict[str, float | None]:
    """`provider.predict`, refusing a network provider unless the caller opted in."""
    if provider.requires_network and not allow_network:
        raise NetworkNotAllowedError(
            f"provider {provider.name!r} needs the network (slow: ~1.6 s vs ~54 ms offline); pass allow_network=True"
        )
    if not provider.applicability(smiles):
        return {k: None for k in provider.endpoint_notes()}
    out = provider.predict(smiles)
    missing = set(provider.endpoint_notes()) - set(out)
    return {**out, **{k: None for k in missing}}  # an endpoint left out is unanswered, not zero


@dataclass(frozen=True)
class DiscriminationReport:
    status: Discrimination
    endpoint: str
    values: dict[str, float | None]
    note: str


def compare(
    provider: PropertyProvider, smiles_by_name: dict[str, str], endpoint: str, *, allow_network: bool = False
) -> DiscriminationReport:
    """Can this endpoint tell these candidates apart at all?

    UNAVAILABLE: some candidate has no answer (never silently dropped or zeroed).
    CANNOT_DISCRIMINATE: every candidate has the same value -- the endpoint carries
    no information about this series, which is different from a many-way tie.
    DISCRIMINATES: at least two distinct values.
    """
    if endpoint not in provider.endpoint_notes():
        raise KeyError(
            f"provider {provider.name!r} has no endpoint {endpoint!r}; has {sorted(provider.endpoint_notes())}"
        )
    if len(smiles_by_name) < 2:
        raise ValueError("need at least 2 candidates to ask whether an endpoint discriminates")
    values = {n: assess(provider, s, allow_network=allow_network)[endpoint] for n, s in smiles_by_name.items()}
    if any(v is None for v in values.values()):
        gaps = sorted(n for n, v in values.items() if v is None)
        return DiscriminationReport("UNAVAILABLE", endpoint, values, f"no answer for {gaps}; not ranked")
    if len(set(values.values())) == 1:
        return DiscriminationReport(
            "CANNOT_DISCRIMINATE",
            endpoint,
            values,
            f"every candidate scores {next(iter(values.values()))}: "
            f"{endpoint!r} carries no information about this series",
        )
    return DiscriminationReport("DISCRIMINATES", endpoint, values, RANK_ONLY_NOTE)


class RdkitAlertProvider:
    """Structural-alert counts from RDKit's bundled catalogs. Offline, ~ms, rank-only."""

    name = "rdkit-alerts"
    requires_network = False
    license_id = "MIT OR Apache-2.0"  # this code: smeltery's own (pyproject.toml)
    # Wrapped engine: RDKit is BSD-3-Clause (https://raw.githubusercontent.com/rdkit/rdkit/master/license.txt,
    # fetched 2026-10-10). The PAINS/Brenk/NIH catalogs are RDKit-bundled data whose own licence terms were
    # NOT checked, hence UNVERIFIED rather than a guess.
    engine_licenses = {"RDKit": "BSD-3-Clause"}
    data_license_id = "UNVERIFIED"
    _CATALOGS = {"pains_alerts": "PAINS", "brenk_alerts": "BRENK", "nih_alerts": "NIH"}

    def __init__(self) -> None:
        self._cats: dict | None = None

    def settings(self) -> dict:
        return {"catalogs": sorted(self._CATALOGS.values()), "network": False}

    def endpoint_units(self) -> dict[str, str]:
        return {k: "alert matches (count)" for k in self._CATALOGS}

    def endpoint_notes(self) -> dict[str, str]:
        return {k: f"{v} alert match count; {RANK_ONLY_NOTE}" for k, v in self._CATALOGS.items()}

    def _catalogs(self) -> dict:
        if self._cats is None:
            from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams

            self._cats = {}
            for endpoint, label in self._CATALOGS.items():
                params = FilterCatalogParams()
                params.AddCatalog(getattr(FilterCatalogParams.FilterCatalogs, label))
                self._cats[endpoint] = FilterCatalog(params)
        return self._cats

    def applicability(self, smiles: str) -> bool:
        from rdkit import Chem

        mol = Chem.MolFromSmiles(smiles)
        return mol is not None and len(Chem.GetMolFrags(mol)) == 1

    def predict(self, smiles: str) -> dict[str, float | None]:
        from rdkit import Chem

        if not self.applicability(smiles):
            return {k: None for k in self._CATALOGS}
        mol = Chem.MolFromSmiles(smiles)
        return {k: float(len(cat.GetMatches(mol))) for k, cat in self._catalogs().items()}
