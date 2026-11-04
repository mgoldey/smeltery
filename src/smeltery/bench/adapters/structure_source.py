"""Structure source: local file + existing providers."""

from __future__ import annotations

from pathlib import Path

from smeltery.providers.structure import AfdbProvider, PdbProvider


class LocalStructureSource:
    def __init__(self, root: str | Path | None = None, allow_network: bool = False):
        self.root = Path(root) if root else Path.cwd() / ".smeltery_structures"
        self.root.mkdir(parents=True, exist_ok=True)
        self.allow_network = allow_network

    def fetch(self, ref: str, *, allow_network: bool | None = None) -> Path:
        an = self.allow_network if allow_network is None else allow_network
        p = Path(ref)
        if p.exists():
            return p.resolve()
        if len(ref) == 4 and ref.isalnum():
            if not an:
                raise PermissionError("network disabled: cannot fetch PDB id")
            prov = PdbProvider()
            res = prov.predict(ref)
            if res is None:
                raise ValueError(prov.last_error or "fetch failed")
            out = self.root / f"{ref}.pdb"
            out.write_text(res.text)
            return out
        # UniProt / AFDB
        if not an:
            raise PermissionError("network disabled: cannot fetch UniProt accession")
        prov = AfdbProvider()
        res = prov.predict(ref)
        if res is None:
            raise ValueError(getattr(prov, "last_error", None) or "fetch failed")
        out = self.root / f"{ref}.pdb"
        out.write_text(res.text)
        return out

    def fetch_result(self, ref: str, *, allow_network: bool | None = None):
        an = self.allow_network if allow_network is None else allow_network
        p = Path(ref)
        if p.exists():
            prov = PdbProvider()
            return prov.predict(str(p))
        if len(ref) == 4 and ref.isalnum():
            if not an:
                raise PermissionError("network disabled")
            return PdbProvider().predict(ref)
        if not an:
            raise PermissionError("network disabled")
        return AfdbProvider().predict(ref)
