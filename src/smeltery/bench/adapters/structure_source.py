"""Structure source: local file + existing providers."""

from __future__ import annotations

from pathlib import Path

from smeltery.providers.structure import AfdbProvider, PdbProvider, is_pdb_id


class LocalStructureSource:
    def __init__(self, root: str | Path | None = None, allow_network: bool = False):
        self.root = Path(root) if root else Path.cwd() / ".smeltery_structures"
        self.root.mkdir(parents=True, exist_ok=True)
        self.allow_network = allow_network

    def _network(self, ref: str, allow_network: bool | None, refusal: dict[str, str]):
        """The one ladder for a ref that is not a local file: a PDB id, else an AFDB accession. Returns
        (provider, result); the provider is kept so a caller can read `last_error` when the result is None."""
        an = self.allow_network if allow_network is None else allow_network
        kind, prov = ("pdb", PdbProvider()) if is_pdb_id(ref) else ("afdb", AfdbProvider())
        if not an:
            raise PermissionError(refusal[kind])
        return prov, prov.predict(ref)

    def fetch(self, ref: str, *, allow_network: bool | None = None) -> Path:
        p = Path(ref)
        if p.exists():
            return p.resolve()
        prov, res = self._network(
            ref,
            allow_network,
            {
                "pdb": "network disabled: cannot fetch PDB id",
                "afdb": "network disabled: cannot fetch UniProt accession",
            },
        )
        if res is None:
            raise ValueError(getattr(prov, "last_error", None) or "fetch failed")
        out = self.root / f"{ref}.pdb"
        out.write_text(res.text)
        return out

    def fetch_result(self, ref: str, *, allow_network: bool | None = None):
        if Path(ref).exists():
            return PdbProvider().predict(ref)
        return self._network(ref, allow_network, {"pdb": "network disabled", "afdb": "network disabled"})[1]
