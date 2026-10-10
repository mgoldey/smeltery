"""Structure providers: where a receptor model comes from, and what it may claim.

A `StructureProvider` turns a spec (a path, a PDB id, a UniProt accession) into a
`StructureResult`: the coordinates PLUS everything a downstream reader needs to
judge them -- whether the structure is EXPERIMENTAL or PREDICTED, per-residue
confidence for predictions, the licence and the attribution string that licence
requires. A provider that cannot answer returns `None`; it never fabricates a
structure, and the reason is kept in `provider.last_error`.

The EXPERIMENTAL/PREDICTED label cannot be forged: a result carrying confidence
or a weights source is a prediction, and `StructureResult` refuses to be
constructed as EXPERIMENTAL with either.

Network access goes through an injectable `fetch(url) -> bytes`, so tests run on
committed fixtures and the live path is a separate, opt-in test.

Licensing (verified 2026-10-05, alphafold.ebi.ac.uk/download): AlphaFold DB is
CC BY 4.0 and permits commercial use; attribution is required and is carried in
every AFDB result.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

StructureKind = Literal["EXPERIMENTAL", "PREDICTED"]

AFDB_API = "https://alphafold.ebi.ac.uk/api/prediction/{accession}"
RCSB_PDB = "https://files.rcsb.org/download/{pdb_id}.pdb"
AFDB_ATTRIBUTION = (
    "AlphaFold Protein Structure Database (EMBL-EBI), CC BY 4.0. "
    "Jumper et al. 2021 Nature 596:583; Varadi et al. 2024 Nucleic Acids Res 52:D368."
)
_UNIPROT_RE = re.compile(r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})(-\d+)?$")
_PDB_ID_RE = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


@dataclass(frozen=True)
class StructureResult:
    """A structure with its provenance. `text` is PDB-format coordinates."""

    text: str
    kind: StructureKind
    source: str
    license_id: str | None = None
    attribution: str | None = None
    #: Per-residue (residue number, pLDDT) for predictions; None for experiments.
    plddt: tuple[tuple[int, float], ...] | None = None
    weights_source: str | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ("EXPERIMENTAL", "PREDICTED"):
            raise ValueError(f"kind must be EXPERIMENTAL or PREDICTED, got {self.kind!r}")
        if not self.text.strip():
            raise ValueError("empty structure text")
        if self.kind == "EXPERIMENTAL" and (self.plddt is not None or self.weights_source is not None):
            raise ValueError(
                "a structure with pLDDT confidence or a weights source is a prediction; "
                "it cannot be labelled EXPERIMENTAL"
            )
        if self.kind == "PREDICTED" and not (self.license_id and self.attribution):
            raise ValueError("a predicted structure must carry its licence id and attribution string")

    @property
    def mean_plddt(self) -> float | None:
        return None if not self.plddt else sum(v for _, v in self.plddt) / len(self.plddt)

    def to_dict(self) -> dict:
        """JSON-able record entry (coordinates omitted: record the digest, not the file)."""
        import hashlib

        return {
            "kind": self.kind,
            "source": self.source,
            "license_id": self.license_id,
            "attribution": self.attribution,
            "weights_source": self.weights_source,
            "n_residues_with_plddt": 0 if not self.plddt else len(self.plddt),
            "mean_plddt": self.mean_plddt,
            "sha256": hashlib.sha256(self.text.encode()).hexdigest(),
            "metadata": self.metadata,
        }


@runtime_checkable
class StructureProvider(Protocol):
    name: str

    def predict(self, spec: str) -> StructureResult | None:
        """The structure for `spec`, or None if this provider cannot answer (see `last_error`)."""
        ...

    def settings(self) -> dict: ...


def plddt_from_pdb(text: str) -> tuple[tuple[int, float], ...]:
    """Per-residue pLDDT from a predicted-model PDB: AlphaFold stores it in the B-factor column.

    One value per residue, read from the CA atom (every residue of a model has one).
    """
    out = []
    for line in text.splitlines():
        if line.startswith("ATOM") and line[12:16].strip() == "CA":
            out.append((int(line[22:26]), float(line[60:66])))
    return tuple(out)


def _urlopen_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "smeltery"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed https hosts only
        return r.read()


class PdbProvider:
    """Experimental structures: a local PDB file, or a 4-character PDB id fetched from RCSB."""

    name = "pdb"
    license_id = "MIT OR Apache-2.0"  # this code: smeltery's own (pyproject.toml)
    # DATA licence: RCSB says "data files contained in the PDB archive are available under the CC0 1.0 Universal
    # (CC0 1.0) Public Domain Dedication" (https://www.rcsb.org/pages/policies, fetched 2026-10-10). That covers
    # ids fetched from RCSB; a local .pdb file carries whatever licence its owner gave it (results leave it None).
    data_license_id = "CC0-1.0"

    def __init__(self, fetch: Callable[[str], bytes] = _urlopen_bytes) -> None:
        self._fetch = fetch
        self.last_error: str | None = None

    def settings(self) -> dict:
        return {"provider": self.name, "rcsb_url": RCSB_PDB}

    def predict(self, spec: str) -> StructureResult | None:
        self.last_error = None
        path = Path(spec)
        if path.suffix.lower() == ".pdb" or path.exists():
            if not path.is_file():
                self.last_error = f"no such file: {spec}"
                return None
            text, source = path.read_text(), f"file:{path.name}"
        elif _PDB_ID_RE.match(spec):
            url = RCSB_PDB.format(pdb_id=spec.upper())
            try:
                text, source = self._fetch(url).decode(), url
            except (urllib.error.URLError, OSError) as e:
                self.last_error = f"could not fetch {url}: {e}"
                return None
        else:
            self.last_error = f"{spec!r} is neither a .pdb path nor a PDB id"
            return None
        if not any(line.startswith(("ATOM", "HETATM")) for line in text.splitlines()):
            self.last_error = f"{source}: no ATOM/HETATM records"
            return None
        return StructureResult(text=text, kind="EXPERIMENTAL", source=source)


class AfdbProvider:
    """Predicted structures from the AlphaFold Protein Structure Database, by UniProt accession."""

    name = "afdb"
    license_id = "MIT OR Apache-2.0"  # this code: smeltery's own (pyproject.toml)
    # DATA licence: "Data is available for academic and commercial use, under a CC-BY-4.0 licence"
    # (https://alphafold.ebi.ac.uk/download, fetched 2026-10-10). The data, not the code, is CC-BY-4.0.
    data_license_id = "CC-BY-4.0"

    def __init__(self, fetch: Callable[[str], bytes] = _urlopen_bytes) -> None:
        self._fetch = fetch
        self.last_error: str | None = None

    def settings(self) -> dict:
        return {"provider": self.name, "api": AFDB_API, "license_id": "CC-BY-4.0"}

    def predict(self, spec: str) -> StructureResult | None:
        self.last_error = None
        if not _UNIPROT_RE.match(spec):
            self.last_error = f"{spec!r} is not a UniProt accession"
            return None
        api = AFDB_API.format(accession=spec)
        try:
            entries = json.loads(self._fetch(api))
            entry = entries[0]
            pdb_url = entry["pdbUrl"]
            text = self._fetch(pdb_url).decode()
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError) as e:
            self.last_error = f"AFDB lookup for {spec} failed: {type(e).__name__}: {e}"
            return None
        plddt = plddt_from_pdb(text)
        if not plddt:
            self.last_error = f"{pdb_url}: no CA atoms, so no pLDDT to carry"
            return None
        return StructureResult(
            text=text,
            kind="PREDICTED",
            source=pdb_url,
            license_id="CC-BY-4.0",
            attribution=AFDB_ATTRIBUTION,
            plddt=plddt,
            weights_source="AlphaFold DB precomputed model (no local weights)",
            metadata={
                "uniprot": spec,
                "model_version": entry.get("latestVersion"),
                "entry_id": entry.get("entryId"),
            },
        )
