"""Single-substituent-change analysis for the PLB series (issue #26).

Lives under benchmarks/, not src/smeltery/: the repository rule (tests/generate) is that RDKit's MCS is called
only from smeltery.tiers, where it builds pairs. This module only COUNTS which ligands differ at one site, to
choose a reference ligand and fill the suitability table; it builds no pair and moves no coordinates.
"""

from __future__ import annotations

from typing import Any

from smeltery.benchmark import mol_from_smiles

# Operational definition of "single-substituent change" (see `single_site_change`).
SINGLE_SITE_MAX_ATOMS = 8
MCS_TIMEOUT_S = 2


def _components(mol, atoms: set[int]) -> int:
    seen: set[int] = set()
    n = 0
    for a in atoms:
        if a in seen:
            continue
        n += 1
        stack = [a]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(nb.GetIdx() for nb in mol.GetAtomWithIdx(x).GetNeighbors() if nb.GetIdx() in atoms)
    return n


def single_site_change(smiles_a: str, smiles_b: str) -> dict[str, Any]:
    """Is b a single-site change from a?  Operational definition, via RDKit MCS on heavy atoms.

    MCS: element-exact atoms, order-exact bonds (aromatic matches aromatic), ring-complete, ring-to-ring.
    `single_site` iff the atoms of a outside the MCS form <= 1 connected piece, the atoms of b outside it form
    <= 1 connected piece, each piece has <= SINGLE_SITE_MAX_ATOMS heavy atoms, at least one side is non-empty
    (heavy-atom-identical pairs, e.g. stereoisomers, are reported as `identical_heavy_graph`), and the MCS
    search did not time out. It does not check that the two pieces attach at the same MCS atom.
    """
    from rdkit import Chem
    from rdkit.Chem import rdFMCS

    a = Chem.RemoveHs(mol_from_smiles(smiles_a))
    b = Chem.RemoveHs(mol_from_smiles(smiles_b))
    if abs(a.GetNumAtoms() - b.GetNumAtoms()) > SINGLE_SITE_MAX_ATOMS:
        # Exact shortcut, not an approximation: the larger molecule then has more than the allowed number of
        # atoms outside any common substructure. The MCS is skipped.
        return {
            "single_site": False,
            "identical_heavy_graph": False,
            "timed_out": False,
            "mcs_atoms": None,
            "unmatched_parent_atoms": None,
            "unmatched_analogue_atoms": None,
        }
    res = rdFMCS.FindMCS(
        [a, b],
        timeout=MCS_TIMEOUT_S,
        atomCompare=rdFMCS.AtomCompare.CompareElements,
        bondCompare=rdFMCS.BondCompare.CompareOrder,
        ringMatchesRingOnly=True,
        completeRingsOnly=True,
    )
    q = Chem.MolFromSmarts(res.smartsString) if res.numAtoms else None
    ma = set(a.GetSubstructMatch(q)) if q is not None else set()
    mb = set(b.GetSubstructMatch(q)) if q is not None else set()
    ua = set(range(a.GetNumAtoms())) - ma
    ub = set(range(b.GetNumAtoms())) - mb
    ca, cb = _components(a, ua), _components(b, ub)
    ident = not ua and not ub
    ok = (
        not res.canceled
        and not ident
        and ca <= 1
        and cb <= 1
        and len(ua) <= SINGLE_SITE_MAX_ATOMS
        and len(ub) <= SINGLE_SITE_MAX_ATOMS
    )
    return {
        "single_site": bool(ok),
        "identical_heavy_graph": ident,
        "timed_out": bool(res.canceled),
        "mcs_atoms": int(res.numAtoms),
        "unmatched_parent_atoms": len(ua),
        "unmatched_analogue_atoms": len(ub),
    }


def neighbour_counts(smiles: list[str]) -> list[int]:
    """For each ligand, how many OTHER ligands are a single-site change from it."""
    n = len(smiles)
    counts = [0] * n
    for i in range(n):
        for j in range(i + 1, n):
            if single_site_change(smiles[i], smiles[j])["single_site"]:
                counts[i] += 1
                counts[j] += 1
    return counts
