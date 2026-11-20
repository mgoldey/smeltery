"""What can be said about each candidate before anything runs: it parses, its charge, its electron parity, STO-3G
cover, and the core it shares with the parent. RDKit only; fast enough to run on every edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from rdkit import Chem

from smeltery.cost import STO3G_NBF
from smeltery.gates import mol_with_h
from smeltery.gui.pipeline import Pipeline
from smeltery.model import Candidate
from smeltery.tiers import NoCommonCoreError, PairedPoses, net_charge, parity_problem


@dataclass
class LigandFacts:
    """One candidate's checks (`api.ligands.LigandCheck` field for field)."""

    name: str
    smiles: str
    is_parent: bool
    smiles_ok: bool
    formal_charge: int | None
    electrons: int | None
    electrons_even: bool | None
    sto3g_ok: bool | None
    sto3g_missing: list[str] = field(default_factory=list)
    core_atoms: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class LigandsFacts:
    parent: str
    candidates: list[LigandFacts]
    problems: list[str]  # problems with the list as a whole, e.g. a parent that is not a candidate


class _NoCore(Exception):
    """Why a candidate has no core with the parent. Raised so `lru_cache` never stores a failure: only a core found
    is cached, and a timeout or a crash is tried again on the next check."""


@lru_cache(maxsize=512)
def _core_found(parent_smiles: str, smiles: str) -> tuple[int, ...]:
    """The analogue atoms `PairedPoses` copies from the parent: its own scaffold map, not a second MCS, with the
    run's own default search time so the check and the run agree. Pure in its two SMILES, so cached."""
    tier = PairedPoses(n_poses=1)
    parent = Candidate("parent", parent_smiles)
    try:
        tier.run([parent, Candidate("candidate", smiles)], {"parent": Candidate("parent", parent_smiles)})
    except NoCommonCoreError as e:
        if "timed out" in str(e):
            raise _NoCore(
                f"the common-core search with the parent timed out after {tier.mcs_timeout_s} s; "
                "a run searches the same way and would stop here too"
            ) from e
        raise _NoCore(str(e).removeprefix("candidate: ")) from e
    except Exception as e:  # noqa: BLE001 - one bad molecule is a row's problem, never a 500 for the table
        raise _NoCore(f"cannot pair with the parent: {e}") from e
    return tuple(sorted(a for a, _ in tier.scaffold_maps["candidate"]))


def _core(parent_smiles: str, name: str, smiles: str) -> tuple[list[int], str | None]:
    try:
        return list(_core_found(parent_smiles, smiles)), None
    except _NoCore as e:
        return [], f"{name}: {e}"


def check(name: str, smiles: str, parent: Candidate, parent_ok: bool) -> LigandFacts:
    """One candidate's checks. `parent_ok` false skips the core search (the list-level problem says why, once)."""
    is_parent = name == parent.name
    try:
        molh = mol_with_h(smiles) if smiles.strip() else None
    except ValueError:
        molh = None
    if molh is None:
        return LigandFacts(
            name=name,
            smiles=smiles,
            is_parent=is_parent,
            smiles_ok=False,
            formal_charge=None,
            electrons=None,
            electrons_even=None,
            sto3g_ok=None,
            sto3g_missing=[],
            core_atoms=[],
            problems=[f"{name}: RDKit cannot read the SMILES {smiles!r}"],
        )
    charge = net_charge(smiles)
    electrons = sum(a.GetAtomicNum() for a in molh.GetAtoms()) - charge  # the CLI's _check_parity, exactly
    even = electrons % 2 == 0
    missing = sorted({a.GetSymbol() for a in molh.GetAtoms() if a.GetSymbol() not in STO3G_NBF})
    problems = []
    if parity := parity_problem(name, electrons, charge):
        problems.append(parity)
    if missing:
        problems.append(f"{name} contains {', '.join(missing)}, which ferric's bundled STO-3G does not cover")
    if is_parent:
        core: list[int] = list(range(molh.GetNumAtoms()))
    elif not parent_ok:
        core = []  # the list-level problem says why, once
    else:
        core, why = _core(parent.smiles, name, smiles)
        if why:
            problems.append(why)
    return LigandFacts(
        name=name,
        smiles=smiles,
        is_parent=is_parent,
        smiles_ok=True,
        formal_charge=charge,
        electrons=electrons,
        electrons_even=even,
        sto3g_ok=not missing,
        sto3g_missing=missing,
        core_atoms=core,
        problems=problems,
    )


def check_ligands(p: Pipeline) -> LigandsFacts:
    """Every candidate of `p` checked, plus the problems with the list as a whole."""
    parent_smiles = p.candidates.get(p.parent, "")
    parent = Candidate(p.parent, parent_smiles)
    parent_ok = bool(parent_smiles.strip()) and Chem.MolFromSmiles(parent_smiles) is not None
    problems = []
    if p.candidates and p.parent not in p.candidates:
        problems.append(f"the parent {p.parent!r} is not among the candidates, so no core can be found")
    elif p.candidates and not parent_ok:
        problems.append(f"the parent {p.parent!r} has a SMILES that cannot be read, so no core can be found")
    return LigandsFacts(
        parent=p.parent,
        candidates=[check(n, s, parent, parent_ok) for n, s in p.candidates.items()],
        problems=problems,
    )


def export_records(p: Pipeline) -> list[dict]:
    """The candidates as export rows: name, SMILES, role and the RDKit-only checks (the core search is not a column,
    so it is skipped)."""
    parent = Candidate(p.parent, p.candidates.get(p.parent, ""))
    out = []
    for name, smiles in p.candidates.items():
        c = check(name, smiles, parent, parent_ok=False)
        out.append(
            {
                "name": name,
                "smiles": smiles,
                "role": "parent" if c.is_parent else "analogue",
                "formal_charge": c.formal_charge,
                "electrons": c.electrons,
                "electrons_even": c.electrons_even,
                "sto3g_ok": c.sto3g_ok,
            }
        )
    return out
