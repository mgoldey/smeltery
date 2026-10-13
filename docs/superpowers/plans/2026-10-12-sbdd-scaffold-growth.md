# Scaffold-growth candidate generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `CandidateGenerator` protocol and a DiffSBDD-backed `ScaffoldGrowth` that regrows part of a parent molecule in a receptor pocket, repairs and filters the raw output, and hands `Candidate`s to the existing funnel.

**Architecture:** The model runs in a separate Python environment through a subprocess (`_sbdd_runner.py`); smeltery never imports torch. Raw output is repaired and filtered by pure-RDKit code (`sbdd_post.py`) that is tested without a GPU. A `pose_source` switch decides whether the model's geometry is carried into the funnel, which needs one new option on `PairedPoses`.

**Tech Stack:** Python 3.11, RDKit, numpy, pytest, ruff. External (not a dependency): DiffSBDD under Python 3.10 / torch 2.0.1.

**Spec:** `docs/superpowers/specs/2026-10-10-sbdd-scaffold-growth-design.md` (read it first; it holds the measured yields and the reasoning behind every choice below).

## Global Constraints

- smeltery runs on Python >=3.11,<3.13 with rdkit>=2024.3 and numpy>=1.26 as its only added dependencies here. **No torch, no new extra, no new dependency in `pyproject.toml`** (only a pytest marker is added).
- `src/smeltery/generate/` contains no molecule: none of `danuglipron`, `DANUGLIPRON`, `7LCJ`, `GLP1R`, `PF-06882961` may appear in executable code under `src/` (`tests/test_migration_rules.py`). Prose in docstrings and comments is allowed; fixtures are named neutrally.
- Core coordinates are exact: `PairedPoses` copies every atom the common-substructure search maps onto the parent from the parent's pose; a self-pair gives a per-pose ΔΔE of exactly 0.0, with or without the new option.
- Atom order is never assumed: poses are in the atom order of `Chem.AddHs(Chem.MolFromSmiles(candidate.smiles))`, and a test asserts it.
- Failures are recorded, not fatal: one bad sample is a `Rejection`, never an exception; a failed run is a `GeneratorError`.
- `len(candidates) + len(rejected)` equals the number of raw outputs, always.
- ruff: line length 120, rules E, F, I, B; CI runs `ruff check .` and `ruff format --check .` (ruff 0.16.10).
- No test in `tests/` may need a GPU, torch, or the external environment, except the one marked `gpu`, which is skipped unless four environment variables are set.
- Commit messages end with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## How to run things in this worktree

The working directory is `/home/matt/qc/smeltery/.claude/worktrees/generative-sbdd`. Tests use the main checkout's virtualenv with this worktree's source first on `PYTHONPATH`. Write paths literally; this session's shell guard rejects shell variables, `source`, heredocs, and `nice`/`time` combined with environment prefixes.

- Test: `PYTHONPATH=/home/matt/qc/smeltery/.claude/worktrees/generative-sbdd/src /home/matt/qc/smeltery/.venv/bin/python -m pytest -q -p no:cacheprovider <paths>`
- Lint: `/home/matt/qc/smeltery/.venv/bin/ruff check <paths>` and `/home/matt/qc/smeltery/.venv/bin/ruff format --check <paths>` (use `ruff format <paths>` to fix).

Below, `TEST` means the first command and `RUFF` means both lint commands, always with the paths written out. Run `ruff format` on the files you touched first, then `ruff check`; the formatter does not split long lines or strings, so wrap any line `ruff check` reports as E501 (over 120 characters) by hand.

## Review Focus

Failure modes the spec implies that no obvious test would exercise, most likely first. Each has a test in the task named.

1. A parent pose whose atom order differs from its SMILES (a pose copied from another tool) is accepted and silently mis-places atoms. Expected: `ValueError` before anything runs. (Task 2)
2. A scaffold atom carries a formal charge (an ammonium, a carboxylate). Expected: the charge survives into the repaired molecule. (Task 2)
3. An SDF record RDKit cannot read, or an empty SDF. Expected: one counted rejection, or zero raw outputs; never an exception. (Tasks 3 and 6)
4. Every sample is rejected. Expected: an empty candidate list and full rejection counts, not an error. (Task 6)
5. The same molecule is proposed twice, within one batch and across batches. Expected: the second is a `duplicate` rejection and the accounting still adds up. (Tasks 3 and 6)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/smeltery/generate/base.py` | create | `CandidateGenerator` protocol, `GenerationResult`, `Rejection`, `REASONS`, `GeneratorError` |
| `src/smeltery/generate/sbdd_post.py` | create | `ScaffoldSpec`, `build_scaffold`, `repair`, `smiles_and_pose`, `FilterConfig`, `process_samples` (pure RDKit) |
| `src/smeltery/generate/_sbdd_runner.py` | create | runs DiffSBDD under the external interpreter; stdlib plus torch/DiffSBDD only |
| `src/smeltery/generate/sbdd.py` | create | `ScaffoldGrowth`: writes inputs, runs the subprocess, batches, assembles `GenerationResult` |
| `src/smeltery/generate/__init__.py` | modify | export the new names |
| `src/smeltery/tiers.py` | modify | `PairedPoses.analogue_poses_given` |
| `docs/tiers/paired_poses.md` | modify | the ninth settings row and two prose lines (checked by `tests/test_tier_docs.py`) |
| `docs/generators.md` | create | how to set up the external environment and use the generator |
| `pyproject.toml` | modify | register the `gpu` pytest marker |
| `tests/data/sbdd/` | create | `grown_raw.sdf`, `scaffold.sdf`, `parent.sdf`, `README.md` |
| `tests/generate/conftest.py` | create | fixtures over the data above |
| `tests/generate/test_sbdd_base.py`, `test_sbdd_post_repair.py`, `test_sbdd_post_filters.py`, `test_sbdd_runner.py`, `test_sbdd_scaffold_growth.py`, `test_sbdd_gpu_smoke.py` | create | |
| `tests/test_paired_given_poses.py` | create | tests for the new `PairedPoses` option |

---

### Task 1: Generator protocol and result types

**Files:**
- Create: `src/smeltery/generate/base.py`
- Test: `tests/generate/test_sbdd_base.py`

**Interfaces:**
- Produces: `REASONS: tuple[str, ...]`; `class GeneratorError(RuntimeError)`; `@dataclass(frozen=True) class Rejection(index: int, reason: str, detail: str = "", smiles: str | None = None)` (raises `ValueError("unknown rejection reason ...")` for a reason outside `REASONS`); `@dataclass class GenerationResult(candidates: list[Candidate], rejected: list[Rejection], provenance: dict)` with property `n_raw -> int` and method `rejection_counts() -> dict[str, int]`; `class CandidateGenerator(Protocol)` with `name: str`, `settings() -> dict`, `generate(parent, scaffold, pocket, *, seed) -> GenerationResult`.

- [ ] **Step 1: Write the failing test**

```python
"""The generator protocol's result types."""

import pytest

from smeltery import Candidate
from smeltery.generate.base import REASONS, GenerationResult, GeneratorError, Rejection


def test_a_rejection_reason_must_come_from_the_closed_set():
    with pytest.raises(ValueError, match="unknown rejection reason"):
        Rejection(index=0, reason="because")
    assert Rejection(index=3, reason="duplicate").detail == ""
    assert all(isinstance(r, str) for r in REASONS)


def test_the_result_accounts_for_every_raw_output():
    result = GenerationResult(
        candidates=[Candidate("a", "C")],
        rejected=[Rejection(0, "duplicate"), Rejection(1, "duplicate"), Rejection(2, "disconnected", "2 fragments")],
        provenance={},
    )
    assert result.n_raw == 4
    assert result.rejection_counts() == {"duplicate": 2, "disconnected": 1}


def test_a_failed_run_is_a_runtime_error():
    assert issubclass(GeneratorError, RuntimeError)
```

- [ ] **Step 2: Run it and see it fail**

Run: `TEST tests/generate/test_sbdd_base.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'smeltery.generate.base'`.

- [ ] **Step 3: Implement**

```python
"""Candidate generators: the protocol, the result record, and the closed set of rejection reasons.

A generator proposes molecules and returns them as `Candidate`s for the funnel. Everything it
declines to return is kept as a `Rejection` with a reason from `REASONS`, so a caller can count
what was lost and why. `len(candidates) + len(rejected)` is the number of raw outputs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from os import PathLike
from typing import Protocol, Sequence

from ..model import Candidate

#: Why a raw output was not returned. Closed on purpose, so counts by reason are comparable between runs.
REASONS: tuple[str, ...] = (
    "scaffold_atom_mismatch",  # a scaffold atom could not be found in the output, or the fit was too loose
    "unsanitizable",  # RDKit could not sanitize the repaired molecule (or the record was unreadable)
    "disconnected",  # more than one fragment
    "scaffold_lost",  # the scaffold is not a substructure of the result
    "forbidden_substructure",  # matched a forbidden SMARTS; `detail` is its label
    "duplicate",  # the same canonical SMILES was already returned
    "size",  # outside the heavy-atom limits
    "atom_order",  # the pose could not be put in the candidate SMILES's atom order
)


class GeneratorError(RuntimeError):
    """A run failed: the interpreter would not start, the runner crashed, or its output was unusable."""


@dataclass(frozen=True)
class Rejection:
    index: int  # position of the raw output, counted across batches from 0
    reason: str
    detail: str = ""
    smiles: str | None = None  # best-effort SMILES of the repaired molecule; None before one exists

    def __post_init__(self) -> None:
        if self.reason not in REASONS:
            raise ValueError(f"unknown rejection reason {self.reason!r}; expected one of {REASONS}")


@dataclass
class GenerationResult:
    candidates: list[Candidate]
    rejected: list[Rejection]
    provenance: dict

    @property
    def n_raw(self) -> int:
        return len(self.candidates) + len(self.rejected)

    def rejection_counts(self) -> dict[str, int]:
        return dict(Counter(r.reason for r in self.rejected))


class CandidateGenerator(Protocol):
    """Proposes analogues of `parent` that keep the heavy atoms listed in `scaffold`."""

    name: str

    def settings(self) -> dict:
        """Everything that shaped the output, JSON-able, for the run record."""
        ...

    def generate(
        self, parent: Candidate, scaffold: Sequence[int], pocket: str | PathLike, *, seed: int
    ) -> GenerationResult:
        """`scaffold` indexes the parent's heavy atoms in SMILES order; `parent.poses[0]` is the bound pose."""
        ...
```

- [ ] **Step 4: Run it and see it pass**

Run: `TEST tests/generate/test_sbdd_base.py` then `RUFF src/smeltery/generate/base.py tests/generate/test_sbdd_base.py`
Expected: 3 passed; ruff clean (run `ruff format` on the two files if it reports changes).

- [ ] **Step 5: Commit**

```bash
git add src/smeltery/generate/base.py tests/generate/test_sbdd_base.py
git commit -m "feat(generate): CandidateGenerator protocol and result types" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Scaffold specification, repair, and SMILES-ordered poses

**Files:**
- Create: `src/smeltery/generate/sbdd_post.py` (first half; Task 3 appends to it)
- Create: `tests/data/sbdd/grown_raw.sdf`, `tests/data/sbdd/scaffold.sdf`, `tests/data/sbdd/parent.sdf`, `tests/data/sbdd/README.md`
- Create: `tests/generate/conftest.py`
- Test: `tests/generate/test_sbdd_post_repair.py`

**Interfaces:**
- Consumes: `smeltery.model.Pose`.
- Produces (all in `smeltery.generate.sbdd_post`):
  - `class RepairError(ValueError)`
  - `@dataclass(frozen=True) class ScaffoldSpec(parent: Chem.Mol, scaffold: Chem.Mol, kekule: Chem.Mol, indices: tuple[int, ...])` with property `coords -> np.ndarray` of shape `(len(indices), 3)`. `parent` is the heavy-atom parent with the bound conformer; `scaffold` is sanitized; `kekule` is the same graph with Kekulé bond orders; both have the parent's coordinates for the chosen atoms, in the order of `indices`.
  - `build_scaffold(smiles: str, indices: Sequence[int], pose: Pose) -> ScaffoldSpec`
  - `repair(raw: Chem.Mol, spec: ScaffoldSpec) -> tuple[Chem.Mol, list[int]]`: returns the repaired, rigidly fitted, still-unsanitized molecule and `mapping` (scaffold atom k -> output atom index); raises `RepairError`.
  - `smiles_and_pose(mol: Chem.Mol) -> tuple[str, Pose]`
  - private helpers used by tests: `_match_scaffold(pos, symbols, spec) -> list[int] | None`, constants `MATCH_TOL_ANG = 0.5`, `FIT_TOL_ANG = 0.3`.

- [ ] **Step 1: Add the fixture data**

The three SDF files are the real output of the 2026-10-10 GPU run described in the spec (a 41-heavy-atom parent bound pose, its 32-atom scaffold, and 16 raw samples). Copy them from the scratchpad of the session that produced them:

```bash
mkdir -p tests/data/sbdd
cp /tmp/claude-1000/-home-matt-qc-smeltery/419b5e9b-c5af-4ce6-8b90-ee6f942e8bc8/scratchpad/7lcj_grown.sdf tests/data/sbdd/grown_raw.sdf
cp /tmp/claude-1000/-home-matt-qc-smeltery/419b5e9b-c5af-4ce6-8b90-ee6f942e8bc8/scratchpad/scaffold.sdf tests/data/sbdd/scaffold.sdf
cp /tmp/claude-1000/-home-matt-qc-smeltery/419b5e9b-c5af-4ce6-8b90-ee6f942e8bc8/scratchpad/parent.sdf tests/data/sbdd/parent.sdf
```

If those files are gone, regenerate them with Task 7's smoke recipe (any 41-atom parent works; the pinned counts in Task 3 are specific to these files, so a regenerated set means re-measuring them).

Create `tests/data/sbdd/README.md`:

```markdown
# Scaffold-growth fixtures

Real DiffSBDD output, kept so the post-processing is tested against what the model actually produces.

- `parent.sdf`: a 41-heavy-atom ligand in its bound conformer (heavy atoms only).
- `scaffold.sdf`: 32 of those atoms, in the order the model was given them.
- `grown_raw.sdf`: 16 raw samples, unsanitized, in the model's own atom order.

Produced 2026-10-10 with DiffSBDD commit 5d0d38d, checkpoint `crossdocked_fullatom_cond.ckpt`
(SHA-256 07f86764bf569aafbc40a9c15fc02de8e2550437dd0f17f657eab3abe66c372c),
`--add_n_nodes 9 --n_samples 16 --center ligand --timesteps 50 --resamplings 20`, one GTX 1080,
RDKit 2022.09.5 in the model's environment.

Measured on these 16 under the repair in `smeltery.generate.sbdd_post`: 12 do not sanitize, 3 lose the
scaffold, 1 passes the default filters (index 12). That is one scaffold and one run, a regression
fixture, not a benchmark.
```

- [ ] **Step 2: Write the conftest**

`tests/generate/conftest.py`:

```python
"""Fixtures over the real scaffold-growth data in tests/data/sbdd."""

from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem

from smeltery import Candidate
from smeltery.generate.sbdd_post import build_scaffold, smiles_and_pose

DATA = Path(__file__).resolve().parents[1] / "data" / "sbdd"


@pytest.fixture(scope="session")
def raw_samples():
    """The 16 raw model outputs, unsanitized, exactly as written by the runner."""
    return [m for m in Chem.SDMolSupplier(str(DATA / "grown_raw.sdf"), sanitize=False)]


@pytest.fixture(scope="session")
def parent_candidate():
    mol = next(Chem.SDMolSupplier(str(DATA / "parent.sdf")))
    smiles, pose = smiles_and_pose(mol)
    return Candidate("parent", smiles, poses=[pose])


@pytest.fixture(scope="session")
def scaffold_indices(parent_candidate):
    """The scaffold's atoms as indices into the parent SMILES, found by position."""
    scaffold = next(Chem.SDMolSupplier(str(DATA / "scaffold.sdf")))
    n = Chem.MolFromSmiles(parent_candidate.smiles).GetNumAtoms()
    heavy = parent_candidate.poses[0].coords_ang[:n]
    return [int(np.linalg.norm(heavy - p, axis=1).argmin()) for p in scaffold.GetConformer().GetPositions()]


@pytest.fixture(scope="session")
def scaffold_spec(parent_candidate, scaffold_indices):
    return build_scaffold(parent_candidate.smiles, scaffold_indices, parent_candidate.poses[0])
```

- [ ] **Step 3: Write the failing tests**

`tests/generate/test_sbdd_post_repair.py`:

```python
"""Scaffold specification and repair of raw DiffSBDD output (pure RDKit; no GPU, no torch)."""

import re

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery import Pose
from smeltery.generate.sbdd_post import RepairError, _match_scaffold, build_scaffold, repair, smiles_and_pose

#: The one fixture sample that passes the default filters (tests/data/sbdd/README.md).
SURVIVOR = 12


def _pose(smiles: str) -> Pose:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=7) == 0
    return Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), np.array(mol.GetConformer().GetPositions()))


@pytest.mark.parametrize(
    "indices, message",
    [
        ([], "non-empty list of distinct"),
        ([1, 1], "non-empty list of distinct"),
        ([0, 9], "out of range"),
        ([-1, 0], "out of range"),
        ([0, 1, 2, 3], "nothing to grow"),
        ([0, 3], "one connected piece"),
    ],
)
def test_build_scaffold_rejects_a_bad_index_list(indices, message):
    with pytest.raises(ValueError, match=message):
        build_scaffold("CCCC", indices, _pose("CCCC"))


def test_build_scaffold_rejects_a_pose_in_another_atom_order():
    """Review focus 1: a pose from another tool is a different atom order, and would be mis-placed silently."""
    with pytest.raises(ValueError, match="does not start with the heavy atoms"):
        build_scaffold("CCO", [0, 1], _pose("OCC"))


def test_build_scaffold_keeps_formal_charges():
    """Review focus 2."""
    spec = build_scaffold("CC[NH3+]", [1, 2], _pose("CC[NH3+]"))
    assert [a.GetFormalCharge() for a in spec.kekule.GetAtoms()] == [0, 1]
    assert [a.GetFormalCharge() for a in spec.scaffold.GetAtoms()] == [0, 1]


def test_scaffold_coordinates_are_the_parents(parent_candidate, scaffold_spec, scaffold_indices):
    n = scaffold_spec.parent.GetNumAtoms()
    heavy = parent_candidate.poses[0].coords_ang[:n]
    assert np.array_equal(scaffold_spec.coords, heavy[scaffold_indices])
    assert scaffold_spec.scaffold.GetNumAtoms() == 32
    assert scaffold_spec.indices == tuple(scaffold_indices)


def test_smiles_and_pose_puts_the_pose_in_smiles_atom_order():
    mol = Chem.MolFromSmiles("C[C@H](N)c1ccccc1O")
    AllChem.EmbedMolecule(mol, randomSeed=3)  # heavy atoms only, in input order
    smiles, pose = smiles_and_pose(mol)
    reference = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert pose.symbols == tuple(a.GetSymbol() for a in reference.GetAtoms())
    order = [int(x) for x in re.findall(r"\d+", mol.GetProp("_smilesAtomOutputOrder"))]
    assert np.array_equal(pose.coords_ang[: mol.GetNumAtoms()], mol.GetConformer().GetPositions()[order])


def test_the_parent_fixture_pose_matches_its_smiles(parent_candidate):
    reference = Chem.AddHs(Chem.MolFromSmiles(parent_candidate.smiles))
    assert parent_candidate.poses[0].symbols == tuple(a.GetSymbol() for a in reference.GetAtoms())
    assert len(parent_candidate.poses[0].symbols) == 71


def _scaffold_bonds_present(mol, mapping, spec):
    return sum(
        mol.GetBondBetweenAtoms(mapping[b.GetBeginAtomIdx()], mapping[b.GetEndAtomIdx()]) is not None
        for b in spec.kekule.GetBonds()
    )


def _positions_and_symbols(mol):
    return np.asarray(mol.GetConformer().GetPositions()), [a.GetSymbol() for a in mol.GetAtoms()]


def test_the_model_drops_scaffold_bonds_and_repair_restores_them(raw_samples, scaffold_spec):
    total = scaffold_spec.kekule.GetNumBonds()
    missing_before = 0
    for raw in raw_samples:
        mapping = _match_scaffold(*_positions_and_symbols(raw), scaffold_spec)
        assert mapping is not None
        if _scaffold_bonds_present(raw, mapping, scaffold_spec) < total:
            missing_before += 1
        fixed, fixed_map = repair(raw, scaffold_spec)
        assert _scaffold_bonds_present(fixed, fixed_map, scaffold_spec) == total
    assert missing_before == 12  # measured: only 4 of the 16 raw samples had every scaffold bond


def test_repair_leaves_no_extra_bonds_between_scaffold_atoms(raw_samples, scaffold_spec):
    for raw in raw_samples:
        fixed, mapping = repair(raw, scaffold_spec)
        inside = set(mapping)
        between = sum(1 for b in fixed.GetBonds() if b.GetBeginAtomIdx() in inside and b.GetEndAtomIdx() in inside)
        assert between == scaffold_spec.kekule.GetNumBonds()


def test_repair_does_not_touch_bonds_the_model_placed_outside_the_scaffold(raw_samples, scaffold_spec):
    raw = raw_samples[SURVIVOR]
    fixed, mapping = repair(raw, scaffold_spec)
    inside = set(mapping)

    def outside(mol):
        return sorted(
            (
                min(b.GetBeginAtomIdx(), b.GetEndAtomIdx()),
                max(b.GetBeginAtomIdx(), b.GetEndAtomIdx()),
                str(b.GetBondType()),
            )
            for b in mol.GetBonds()
            if not (b.GetBeginAtomIdx() in inside and b.GetEndAtomIdx() in inside)
        )

    assert outside(fixed) == outside(raw)


def test_repair_fits_the_scaffold_onto_the_parent(raw_samples, scaffold_spec):
    fixed, mapping = repair(raw_samples[SURVIVOR], scaffold_spec)
    pos = fixed.GetConformer().GetPositions()[mapping]
    deviation = np.linalg.norm(pos - scaffold_spec.coords, axis=1)
    assert np.sqrt((deviation**2).mean()) < 0.1
    assert deviation.max() < 0.15


def test_repair_rejects_a_scaffold_atom_with_the_wrong_element(raw_samples, scaffold_spec):
    raw = Chem.RWMol(raw_samples[SURVIVOR])
    pos, _ = _positions_and_symbols(raw)
    raw.GetAtomWithIdx(int(np.linalg.norm(pos - scaffold_spec.coords[0], axis=1).argmin())).SetAtomicNum(15)
    with pytest.raises(RepairError, match="unique same-element"):
        repair(raw.GetMol(), scaffold_spec)


def test_repair_rejects_an_output_missing_a_scaffold_atom(raw_samples, scaffold_spec):
    raw = Chem.Mol(raw_samples[SURVIVOR])
    pos, _ = _positions_and_symbols(raw)
    raw.GetConformer().SetAtomPosition(
        int(np.linalg.norm(pos - scaffold_spec.coords[0], axis=1).argmin()), (50.0, 50.0, 50.0)
    )
    with pytest.raises(RepairError, match="unique same-element"):
        repair(raw, scaffold_spec)


def test_repair_rejects_a_scaffold_that_cannot_be_fitted_rigidly(raw_samples, scaffold_spec):
    raw = Chem.Mol(raw_samples[SURVIVOR])
    mapping = _match_scaffold(*_positions_and_symbols(raw), scaffold_spec)
    conf = raw.GetConformer()
    for k, j in enumerate(mapping):  # alternate +/-0.35 A: each atom stays matchable, the set is no longer rigid
        x, y, z = conf.GetAtomPosition(j)
        conf.SetAtomPosition(j, (x + (0.35 if k % 2 else -0.35), y, z))
    with pytest.raises(RepairError, match="rmsd"):
        repair(raw, scaffold_spec)
```

- [ ] **Step 4: Run them and see them fail**

Run: `TEST tests/generate/test_sbdd_post_repair.py`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'smeltery.generate.sbdd_post'`.

- [ ] **Step 5: Implement**

`src/smeltery/generate/sbdd_post.py`:

```python
"""Post-processing of raw scaffold-growth output: match the scaffold, repair its bonds, filter, build candidates.

Pure RDKit and numpy: no torch, no GPU. The raw output comes from a model that sees atom types and
coordinates but never bond orders or valences, so two things hold of it (measured, see
docs/superpowers/specs/2026-10-10-sbdd-scaffold-growth-design.md):

* the atoms of each output are NOT in the order they were given (DiffSBDD's `batch_to_list` sorts them
  with an unstable `argsort`), so the scaffold is found by position, never by index;
* bonds are assigned from distances, so scaffold bonds go missing and spurious ones appear, and new atoms
  are often placed beside atoms that are already saturated in the scaffold.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from rdkit import Chem

from ..model import Pose

MATCH_TOL_ANG = 0.5  # a scaffold atom's output atom must lie within this of its input position
FIT_TOL_ANG = 0.3  # and the rigid fit of the whole scaffold must leave at most this rmsd


class RepairError(ValueError):
    """The output cannot be matched to the scaffold."""


@dataclass(frozen=True)
class ScaffoldSpec:
    """The fixed part of the parent: its graph, its coordinates, and the parent it was cut from."""

    parent: Chem.Mol  # heavy atoms, the bound conformer
    scaffold: Chem.Mol  # sanitized; atoms in `indices` order; the parent's coordinates
    kekule: Chem.Mol  # the same graph with Kekule bond orders and no aromatic flags
    indices: tuple[int, ...]  # heavy-atom indices into the parent SMILES

    @property
    def coords(self) -> np.ndarray:
        return np.asarray(self.scaffold.GetConformer().GetPositions(), dtype=float)


def _with_coords(mol: Chem.Mol, xyz: np.ndarray) -> Chem.Mol:
    out = Chem.Mol(mol)
    conf = Chem.Conformer(out.GetNumAtoms())
    for i, p in enumerate(xyz):
        conf.SetAtomPosition(i, (float(p[0]), float(p[1]), float(p[2])))
    out.RemoveAllConformers()
    out.AddConformer(conf, assignId=True)
    return out


def build_scaffold(smiles: str, indices: Sequence[int], pose: Pose) -> ScaffoldSpec:
    """Cut `indices` (heavy atoms of `smiles`, in SMILES order) out of the parent at its pose `pose`."""
    parent = Chem.MolFromSmiles(smiles)
    if parent is None:
        raise ValueError(f"unparseable SMILES: {smiles!r}")
    n = parent.GetNumAtoms()
    idx = tuple(int(i) for i in indices)
    if not idx or len(set(idx)) != len(idx):
        raise ValueError("scaffold must be a non-empty list of distinct heavy-atom indices")
    if min(idx) < 0 or max(idx) >= n:
        raise ValueError(f"scaffold index out of range for a molecule with {n} heavy atoms: {idx}")
    if len(idx) >= n:
        raise ValueError("scaffold covers every heavy atom; there is nothing to grow")
    if tuple(pose.symbols[:n]) != tuple(a.GetSymbol() for a in parent.GetAtoms()):
        raise ValueError("the parent pose does not start with the heavy atoms of the parent SMILES, in order")
    parent = _with_coords(parent, np.asarray(pose.coords_ang[:n], dtype=float))
    kek_parent = Chem.Mol(parent)
    Chem.Kekulize(kek_parent, clearAromaticFlags=True)
    where = {a: k for k, a in enumerate(idx)}
    rw = Chem.RWMol()
    for a in idx:
        src = kek_parent.GetAtomWithIdx(a)
        atom = Chem.Atom(src.GetAtomicNum())
        atom.SetFormalCharge(src.GetFormalCharge())
        rw.AddAtom(atom)
    for b in kek_parent.GetBonds():
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        if i in where and j in where:
            rw.AddBond(where[i], where[j], b.GetBondType())
    xyz = parent.GetConformer().GetPositions()
    kekule = _with_coords(rw.GetMol(), xyz[list(idx)])
    if len(Chem.GetMolFrags(kekule)) != 1:
        raise ValueError("the scaffold atoms are not one connected piece of the parent")
    scaffold = Chem.Mol(kekule)
    Chem.SanitizeMol(scaffold)
    return ScaffoldSpec(parent, scaffold, kekule, idx)


def _match_scaffold(pos: np.ndarray, symbols: Sequence[str], spec: ScaffoldSpec) -> list[int] | None:
    """Scaffold atom k -> the output atom nearest its input position; None unless that is unique and an element match."""
    sx = spec.coords
    out = []
    for k in range(len(sx)):
        d = np.linalg.norm(pos - sx[k], axis=1)
        j = int(d.argmin())
        if d[j] > MATCH_TOL_ANG or symbols[j] != spec.scaffold.GetAtomWithIdx(k).GetSymbol():
            return None
        out.append(j)
    return out if len(set(out)) == len(out) else None


def _kabsch(moving: np.ndarray, fixed: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Proper rotation `rot` and `shift` with `moving @ rot.T + shift` closest to `fixed`, and the rmsd left."""
    mc, fc = moving.mean(axis=0), fixed.mean(axis=0)
    u, _, vt = np.linalg.svd((moving - mc).T @ (fixed - fc))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rot = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    shift = fc - rot @ mc
    rmsd = float(np.sqrt((((moving @ rot.T + shift) - fixed) ** 2).sum(axis=1).mean()))
    return rot, shift, rmsd


def repair(raw: Chem.Mol, spec: ScaffoldSpec) -> tuple[Chem.Mol, list[int]]:
    """Fit the output onto the scaffold and make the scaffold's graph exactly the scaffold's.

    Adds scaffold bonds the model missed (with the scaffold's order), deletes bonds between two scaffold atoms
    that the scaffold does not have, and takes scaffold atoms' formal charges from the scaffold. Bonds the model
    placed anywhere else, and the orders of scaffold bonds it did place, are left alone: the model never saw bond
    orders, but forcing the scaffold's onto atoms the model crowded makes every one of them over-valent (0 of 16
    sanitized when measured), so valid output is rejected later, honestly, by sanitization.
    """
    pos = np.asarray(raw.GetConformer().GetPositions(), dtype=float)
    mapping = _match_scaffold(pos, [a.GetSymbol() for a in raw.GetAtoms()], spec)
    if mapping is None:
        raise RepairError(f"a scaffold atom has no unique same-element output atom within {MATCH_TOL_ANG} A")
    rot, shift, rmsd = _kabsch(pos[mapping], spec.coords)
    if rmsd > FIT_TOL_ANG:
        raise RepairError(f"the scaffold fits with rmsd {rmsd:.2f} A, above {FIT_TOL_ANG} A")
    rw = Chem.RWMol(raw)
    inside = set(mapping)
    want = {frozenset((mapping[b.GetBeginAtomIdx()], mapping[b.GetEndAtomIdx()])) for b in spec.kekule.GetBonds()}
    for b in list(rw.GetBonds()):
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        if i in inside and j in inside and frozenset((i, j)) not in want:
            rw.RemoveBond(i, j)
    for b in spec.kekule.GetBonds():
        i, j = mapping[b.GetBeginAtomIdx()], mapping[b.GetEndAtomIdx()]
        if rw.GetBondBetweenAtoms(i, j) is None:
            rw.AddBond(i, j, b.GetBondType())
    for k, j in enumerate(mapping):
        rw.GetAtomWithIdx(j).SetFormalCharge(spec.kekule.GetAtomWithIdx(k).GetFormalCharge())
    return _with_coords(rw.GetMol(), pos @ rot.T + shift), mapping


def smiles_and_pose(mol: Chem.Mol) -> tuple[str, Pose]:
    """Canonical SMILES of `mol` (heavy atoms, with a conformer) and a pose in the atom order of
    `Chem.AddHs(Chem.MolFromSmiles(smiles))`, the order `PairedPoses` and every tier read poses in.

    Raises ValueError if the order cannot be established or checked; never returns a pose in another order.
    """
    smiles = Chem.MolToSmiles(mol)
    order = [int(x) for x in re.findall(r"\d+", mol.GetProp("_smilesAtomOutputOrder"))]
    canon = Chem.MolFromSmiles(smiles)
    if canon is None or canon.GetNumAtoms() != mol.GetNumAtoms() or len(order) != mol.GetNumAtoms():
        raise ValueError(f"cannot rebuild {smiles!r} in SMILES atom order")
    if [mol.GetAtomWithIdx(o).GetSymbol() for o in order] != [a.GetSymbol() for a in canon.GetAtoms()]:
        raise ValueError(f"the atom order of {smiles!r} does not match the molecule it came from")
    xyz = np.asarray(mol.GetConformer().GetPositions(), dtype=float)[order]
    withh = Chem.AddHs(_with_coords(canon, xyz), addCoords=True)
    pose = Pose(
        tuple(a.GetSymbol() for a in withh.GetAtoms()),
        np.asarray(withh.GetConformer().GetPositions(), dtype=float),
    )
    reference = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if pose.symbols != tuple(a.GetSymbol() for a in reference.GetAtoms()):
        raise ValueError(f"the pose of {smiles!r} is not in the atom order of its SMILES with hydrogens added")
    if not np.array_equal(pose.coords_ang[: len(order)], xyz):
        raise ValueError(f"adding hydrogens moved a heavy atom of {smiles!r}")
    return smiles, pose
```

- [ ] **Step 6: Run the tests and lint**

Run: `TEST tests/generate/test_sbdd_post_repair.py` then `RUFF src/smeltery/generate/sbdd_post.py tests/generate/conftest.py tests/generate/test_sbdd_post_repair.py`
Expected: 18 passed (including the 6 parametrized cases); ruff clean. If `missing_before == 12` fails, stop and report the number: it means the fixture or RDKit differs from the 2026-10-10 measurement, and Task 3's pins depend on the same data.

- [ ] **Step 7: Commit**

```bash
git add src/smeltery/generate/sbdd_post.py tests/data/sbdd tests/generate/conftest.py tests/generate/test_sbdd_post_repair.py
git commit -m "feat(generate): scaffold specification and repair of raw growth output" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Filters, deduplication, and candidate building

**Files:**
- Modify: `src/smeltery/generate/sbdd_post.py` (append)
- Test: `tests/generate/test_sbdd_post_filters.py`

**Interfaces:**
- Consumes: Task 1's `Rejection`, Task 2's `ScaffoldSpec`, `repair`, `RepairError`, `smiles_and_pose`.
- Produces (in `smeltery.generate.sbdd_post`):
  - `DEFAULT_FORBIDDEN: dict[str, str]` = `{"peroxide": "[OX2][OX2]"}`; `SCAFFOLD_MATCH_MODES = ("exact", "topology", "off")`
  - `@dataclass class FilterConfig(require_connected: bool = True, scaffold_match: str = "exact", forbidden_smarts: dict[str, str] = <copy of DEFAULT_FORBIDDEN>, deduplicate: bool = True, min_heavy_atoms: int | None = None, max_heavy_atoms: int | None = None)`; `__post_init__` raises `ValueError` for an unknown `scaffold_match` or invalid SMARTS.
  - `first_forbidden(mol: Chem.Mol, forbidden: dict[str, str]) -> str | None`
  - `process_samples(raws, spec, config, *, pose_source="embed", name_prefix="sbdd", start_index=0, first_number=0, seen=None) -> tuple[list[Candidate], list[Rejection]]`. `raws` may contain `None` (an unreadable record). `seen` is a `set[str]` of canonical SMILES, mutated, shared across calls to deduplicate across batches. Candidate names are `f"{name_prefix}-{n:04d}"`, `n` counting from `first_number`. Filter order: sanitize, connected, scaffold, forbidden, duplicate, size.

- [ ] **Step 1: Write the failing tests**

`tests/generate/test_sbdd_post_filters.py`:

```python
"""Filtering, deduplication and candidate building over the real fixture, with its measured counts pinned."""

from collections import Counter

import pytest
from rdkit import Chem

from smeltery.generate.sbdd_post import FilterConfig, first_forbidden, process_samples

SURVIVOR = 12


def _reasons(rejected):
    return dict(Counter(r.reason for r in rejected))


def test_default_filters_on_the_fixture_match_the_measurement(raw_samples, scaffold_spec):
    candidates, rejected = process_samples(raw_samples, scaffold_spec, FilterConfig())
    assert len(candidates) == 1
    assert _reasons(rejected) == {"unsanitizable": 12, "scaffold_lost": 3}
    assert len(candidates) + len(rejected) == len(raw_samples)


def test_topology_matching_admits_the_three_that_changed_the_scaffolds_bond_orders(raw_samples, scaffold_spec):
    candidates, rejected = process_samples(raw_samples, scaffold_spec, FilterConfig(scaffold_match="topology"))
    assert len(candidates) == 4
    assert _reasons(rejected) == {"unsanitizable": 12}


def test_with_every_optional_filter_off_only_sanitization_removes_samples(raw_samples, scaffold_spec):
    config = FilterConfig(require_connected=False, scaffold_match="off", forbidden_smarts={}, deduplicate=False)
    candidates, rejected = process_samples(raw_samples, scaffold_spec, config)
    assert len(candidates) == 4
    assert _reasons(rejected) == {"unsanitizable": 12}


def test_every_returned_candidate_is_connected_sanitized_and_contains_the_scaffold(raw_samples, scaffold_spec):
    candidates, _ = process_samples(raw_samples, scaffold_spec, FilterConfig())
    for cand in candidates:
        mol = Chem.MolFromSmiles(cand.smiles)
        assert mol is not None and len(Chem.GetMolFrags(mol)) == 1
        assert mol.HasSubstructMatch(scaffold_spec.scaffold)


def test_a_forbidden_substructure_rejects_with_its_label(raw_samples, scaffold_spec):
    config = FilterConfig(forbidden_smarts={"phosphorus": "[P]"})  # the survivor carries a phosphonate
    candidates, rejected = process_samples(raw_samples, scaffold_spec, config)
    assert candidates == []
    hit = [r for r in rejected if r.reason == "forbidden_substructure"]
    assert len(hit) == 1 and hit[0].detail == "phosphorus" and hit[0].index == SURVIVOR and hit[0].smiles


def test_the_default_forbidden_list_catches_a_peroxide_and_not_an_ether():
    from smeltery.generate.sbdd_post import DEFAULT_FORBIDDEN

    assert first_forbidden(Chem.MolFromSmiles("COOC"), DEFAULT_FORBIDDEN) == "peroxide"
    assert first_forbidden(Chem.MolFromSmiles("COC"), DEFAULT_FORBIDDEN) is None
    assert first_forbidden(Chem.MolFromSmiles("COOC"), {}) is None


def test_an_unreadable_record_is_one_counted_rejection(raw_samples, scaffold_spec):
    """Review focus 3."""
    candidates, rejected = process_samples([None, raw_samples[SURVIVOR]], scaffold_spec, FilterConfig())
    assert len(candidates) == 1
    assert [(r.index, r.reason) for r in rejected] == [(0, "unsanitizable")]
    assert "unreadable" in rejected[0].detail


def test_an_empty_input_is_an_empty_result(scaffold_spec):
    assert process_samples([], scaffold_spec, FilterConfig()) == ([], [])


def test_the_same_molecule_twice_is_a_duplicate(raw_samples, scaffold_spec):
    """Review focus 5, within a batch."""
    twice = [raw_samples[SURVIVOR], raw_samples[SURVIVOR]]
    candidates, rejected = process_samples(twice, scaffold_spec, FilterConfig())
    assert len(candidates) == 1 and _reasons(rejected) == {"duplicate": 1}
    candidates, rejected = process_samples(twice, scaffold_spec, FilterConfig(deduplicate=False))
    assert len(candidates) == 2 and rejected == []


def test_duplicates_are_found_across_calls_through_the_shared_seen_set(raw_samples, scaffold_spec):
    """Review focus 5, across batches; indices and names continue where the caller says."""
    seen: set[str] = set()
    first, _ = process_samples([raw_samples[SURVIVOR]], scaffold_spec, FilterConfig(), seen=seen)
    second, rejected = process_samples(
        [raw_samples[SURVIVOR]], scaffold_spec, FilterConfig(), seen=seen, start_index=10, first_number=len(first)
    )
    assert second == [] and [(r.index, r.reason) for r in rejected] == [(10, "duplicate")]
    assert len(seen) == 1


def test_a_candidate_that_fails_the_size_filter_does_not_poison_the_seen_set(raw_samples, scaffold_spec):
    seen: set[str] = set()
    _, rejected = process_samples([raw_samples[SURVIVOR]], scaffold_spec, FilterConfig(max_heavy_atoms=40), seen=seen)
    assert _reasons(rejected) == {"size": 1} and seen == set()
    _, rejected = process_samples([raw_samples[SURVIVOR]], scaffold_spec, FilterConfig(min_heavy_atoms=42))
    assert _reasons(rejected) == {"size": 1}


def test_candidates_are_named_in_order_from_the_given_number(raw_samples, scaffold_spec):
    candidates, _ = process_samples(
        raw_samples, scaffold_spec, FilterConfig(scaffold_match="topology"), name_prefix="g", first_number=7
    )
    assert [c.name for c in candidates] == ["g-0007", "g-0008", "g-0009", "g-0010"]


def test_embed_mode_returns_smiles_only_and_model_mode_adds_a_pose_in_smiles_order(raw_samples, scaffold_spec):
    embed, _ = process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="embed")
    assert embed[0].poses == []
    model, _ = process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="model")
    cand = model[0]
    assert cand.smiles == embed[0].smiles and len(cand.poses) == 1
    reference = Chem.AddHs(Chem.MolFromSmiles(cand.smiles))
    assert cand.poses[0].symbols == tuple(a.GetSymbol() for a in reference.GetAtoms())


def test_an_unknown_pose_source_is_refused(raw_samples, scaffold_spec):
    with pytest.raises(ValueError, match="pose_source"):
        process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="docked")


def test_filter_config_refuses_nonsense():
    with pytest.raises(ValueError, match="scaffold_match"):
        FilterConfig(scaffold_match="roughly")
    with pytest.raises(ValueError, match="not valid SMARTS"):
        FilterConfig(forbidden_smarts={"bad": "[C"})
    assert FilterConfig().forbidden_smarts == {"peroxide": "[OX2][OX2]"}
    assert FilterConfig().forbidden_smarts is not FilterConfig().forbidden_smarts  # not a shared mutable default
```

- [ ] **Step 2: Run them and see them fail**

Run: `TEST tests/generate/test_sbdd_post_filters.py`
Expected: FAIL, `ImportError: cannot import name 'FilterConfig'`.

- [ ] **Step 3: Implement**

Add these imports at the top of `sbdd_post.py` (merge with the existing ones): `from dataclasses import dataclass, field`, `from ..model import Candidate, Pose`, `from .base import Rejection`. Then append:

```python
DEFAULT_FORBIDDEN: dict[str, str] = {"peroxide": "[OX2][OX2]"}
SCAFFOLD_MATCH_MODES = ("exact", "topology", "off")
POSE_SOURCES = ("embed", "model")


@dataclass
class FilterConfig:
    """Which outputs are returned. Every filter is independent; sanitization is not one of them (no SMILES without it).

    `scaffold_match`: "exact" needs the scaffold as a substructure with its bond orders (a partially cut aromatic ring
    can be perceived differently in the product and fail); "topology" ignores bond orders and admits molecules that
    rearranged the scaffold's chemistry; "off" skips the check, and `PairedPoses` then needs a common core on its own.
    """

    require_connected: bool = True
    scaffold_match: str = "exact"
    forbidden_smarts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FORBIDDEN))
    deduplicate: bool = True
    min_heavy_atoms: int | None = None
    max_heavy_atoms: int | None = None

    def __post_init__(self) -> None:
        if self.scaffold_match not in SCAFFOLD_MATCH_MODES:
            raise ValueError(f"scaffold_match must be one of {SCAFFOLD_MATCH_MODES}, not {self.scaffold_match!r}")
        for label, smarts in self.forbidden_smarts.items():
            if Chem.MolFromSmarts(smarts) is None:
                raise ValueError(f"forbidden_smarts[{label!r}] is not valid SMARTS: {smarts!r}")


def first_forbidden(mol: Chem.Mol, forbidden: dict[str, str]) -> str | None:
    """The label of the first forbidden pattern `mol` contains, or None."""
    for label, smarts in forbidden.items():
        if mol.HasSubstructMatch(Chem.MolFromSmarts(smarts)):
            return label
    return None


def _scaffold_query(spec: ScaffoldSpec, mode: str) -> Chem.Mol | None:
    if mode == "off":
        return None
    if mode == "exact":
        return spec.scaffold
    params = Chem.AdjustQueryParameters.NoAdjustments()
    params.makeBondsGeneric = True
    return Chem.AdjustQueryProperties(spec.scaffold, params)


@dataclass(frozen=True)
class _Rejected:
    reason: str
    detail: str = ""
    smiles: str | None = None


def _classify(raw: Chem.Mol | None, spec: ScaffoldSpec, config: FilterConfig, query: Chem.Mol | None):
    """The repaired, sanitized molecule if it passes the structural filters, else a `_Rejected`."""
    if raw is None:
        return _Rejected("unsanitizable", "unreadable SDF record")
    try:
        mol, _ = repair(raw, spec)
    except RepairError as e:
        return _Rejected("scaffold_atom_mismatch", str(e))
    try:
        Chem.SanitizeMol(mol)
    except Exception as e:  # noqa: BLE001 - RDKit raises several exception types for the same condition
        return _Rejected("unsanitizable", (str(e).strip().splitlines() or ["sanitization failed"])[0])
    Chem.AssignStereochemistryFrom3D(mol)
    smiles = Chem.MolToSmiles(mol)
    n_frag = len(Chem.GetMolFrags(mol))
    if config.require_connected and n_frag != 1:
        return _Rejected("disconnected", f"{n_frag} fragments", smiles)
    if query is not None and not mol.HasSubstructMatch(query):
        return _Rejected("scaffold_lost", config.scaffold_match, smiles)
    label = first_forbidden(mol, config.forbidden_smarts)
    if label is not None:
        return _Rejected("forbidden_substructure", label, smiles)
    return mol


def process_samples(
    raws: Sequence[Chem.Mol | None],
    spec: ScaffoldSpec,
    config: FilterConfig,
    *,
    pose_source: str = "embed",
    name_prefix: str = "sbdd",
    start_index: int = 0,
    first_number: int = 0,
    seen: set[str] | None = None,
) -> tuple[list[Candidate], list[Rejection]]:
    """Repair, filter, deduplicate and name the raw outputs. Every input ends up as exactly one of the two lists."""
    if pose_source not in POSE_SOURCES:
        raise ValueError(f"pose_source must be one of {POSE_SOURCES}, not {pose_source!r}")
    seen = set() if seen is None else seen
    query = _scaffold_query(spec, config.scaffold_match)
    candidates: list[Candidate] = []
    rejected: list[Rejection] = []
    number = first_number
    for offset, raw in enumerate(raws):
        index = start_index + offset
        outcome = _classify(raw, spec, config, query)
        if isinstance(outcome, _Rejected):
            rejected.append(Rejection(index, outcome.reason, outcome.detail, outcome.smiles))
            continue
        smiles = Chem.MolToSmiles(outcome)
        if config.deduplicate and smiles in seen:
            rejected.append(Rejection(index, "duplicate", "", smiles))
            continue
        n_heavy = outcome.GetNumAtoms()
        if (config.min_heavy_atoms is not None and n_heavy < config.min_heavy_atoms) or (
            config.max_heavy_atoms is not None and n_heavy > config.max_heavy_atoms
        ):
            rejected.append(Rejection(index, "size", f"{n_heavy} heavy atoms", smiles))
            continue
        poses: list[Pose] = []
        if pose_source == "model":
            try:
                smiles, pose = smiles_and_pose(outcome)
            except ValueError as e:
                rejected.append(Rejection(index, "atom_order", str(e), smiles))
                continue
            poses = [pose]
        seen.add(smiles)
        candidates.append(Candidate(f"{name_prefix}-{number:04d}", smiles, poses=poses))
        number += 1
    return candidates, rejected
```

- [ ] **Step 4: Run the tests and lint**

Run: `TEST tests/generate/test_sbdd_post_filters.py tests/generate/test_sbdd_post_repair.py` then `RUFF src/smeltery/generate/sbdd_post.py tests/generate/test_sbdd_post_filters.py`
Expected: all pass; ruff clean. If a pinned count differs, do not edit the number: report it. The numbers are measurements from two RDKit versions (2022.09 and 2026.03).

- [ ] **Step 5: Commit**

```bash
git add src/smeltery/generate/sbdd_post.py tests/generate/test_sbdd_post_filters.py
git commit -m "feat(generate): optional upstream filters, deduplication and candidate building" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `PairedPoses.analogue_poses_given`

**Files:**
- Modify: `src/smeltery/tiers.py` (the `PairedPoses` dataclass and `_paired`; a constant after `NoCommonCoreError`)
- Modify: `docs/tiers/paired_poses.md`
- Test: `tests/test_paired_given_poses.py`

**Interfaces:**
- Consumes: a `Candidate` whose `poses[0]` is a `Pose` in the atom order of `AddHs(MolFromSmiles(candidate.smiles))`.
- Produces: field `PairedPoses.analogue_poses_given: bool = False`, reported by `settings()` under the same key; module constant `smeltery.tiers.GIVEN_POSE_CORE_TOL_ANG = 0.5`. Behaviour when the flag is set and the candidate has poses: its first pose is pose 0 of the pair with every mapped atom overwritten by the parent's pose-0 coordinate; poses 1..n-1 are built as before; pose count stays `len(parent_poses)`. `ValueError` if the given pose's symbols differ from the SMILES-with-H order, or any mapped heavy atom is more than 0.5 Å from the parent's pose-0 position. Unchanged when the flag is off.

- [ ] **Step 1: Write the failing tests**

`tests/test_paired_given_poses.py`:

```python
"""PairedPoses with analogue_poses_given: a given pose keeps its new atoms, the core is still an exact copy."""

import numpy as np
import pytest

from smeltery import Candidate, PointCharge, Pose, paired_delta
from smeltery.tiers import FieldInteraction, PairedPoses

ACID = "OC(=O)c1ccccc1"
FLUORO = "OC(=O)c1ccc(F)cc1"


def _default_run():
    parent, fluoro = Candidate("parent", ACID), Candidate("4-F", FLUORO)
    tier = PairedPoses(n_poses=3)
    tier.run([parent, fluoro], {"parent": parent})
    return parent, fluoro, tier.scaffold_maps["4-F"]


def _given_run(parent, given_pose):
    p2 = Candidate("parent", ACID, poses=list(parent.poses))
    a2 = Candidate("4-F", FLUORO, poses=[given_pose])
    tier = PairedPoses(parent_poses_given=True, analogue_poses_given=True)
    tier.run([p2, a2], {"parent": p2})
    return a2, tier.scaffold_maps["4-F"]


def _perturbed(fluoro, mapping, core_shift, new_shift):
    core = {a for a, _ in mapping}
    unmapped = [i for i in range(len(fluoro.poses[0].symbols)) if i not in core]
    xyz = np.array(fluoro.poses[0].coords_ang)
    xyz[sorted(core)] += core_shift
    xyz[unmapped] += new_shift
    return Pose(fluoro.poses[0].symbols, xyz), unmapped


def test_the_flag_is_a_reported_setting_and_defaults_off():
    assert PairedPoses().analogue_poses_given is False
    assert PairedPoses(analogue_poses_given=True).settings()["analogue_poses_given"] is True


def test_a_given_pose_keeps_its_new_atoms_and_copies_the_core_from_the_parent():
    parent, fluoro, mapping = _default_run()
    given, unmapped = _perturbed(fluoro, mapping, core_shift=0.01, new_shift=np.array([0.3, 0.0, 0.0]))
    assert unmapped, "the fluorine must lie outside the common core"
    a2, mapping2 = _given_run(parent, given)
    assert mapping2 == mapping
    pose0 = a2.poses[0].coords_ang
    for a, p in mapping:
        assert np.array_equal(pose0[a], parent.poses[0].coords_ang[p])
    assert np.array_equal(pose0[unmapped], given.coords_ang[unmapped])
    assert len(a2.poses) == len(parent.poses)


def test_the_other_poses_are_built_as_without_the_option():
    parent, fluoro, mapping = _default_run()
    given, _ = _perturbed(fluoro, mapping, core_shift=0.0, new_shift=np.array([0.3, 0.0, 0.0]))
    a2, _ = _given_run(parent, given)
    for i in range(1, len(parent.poses)):
        assert np.array_equal(a2.poses[i].coords_ang, fluoro.poses[i].coords_ang)


def test_a_given_pose_in_another_atom_order_is_refused():
    parent, fluoro, _ = _default_run()
    pose = fluoro.poses[0]
    with pytest.raises(ValueError, match="atom order"):
        _given_run(parent, Pose(tuple(reversed(pose.symbols)), pose.coords_ang))


def test_a_given_pose_whose_core_is_not_on_the_parent_is_refused():
    parent, fluoro, mapping = _default_run()
    given, _ = _perturbed(fluoro, mapping, core_shift=np.array([1.0, 0.0, 0.0]), new_shift=0.0)
    with pytest.raises(ValueError, match="sits"):
        _given_run(parent, given)


def test_without_the_flag_a_given_pose_is_ignored():
    parent, fluoro, _ = _default_run()
    p2 = Candidate("parent", ACID, poses=list(parent.poses))
    junk = Pose(tuple(reversed(fluoro.poses[0].symbols)), fluoro.poses[0].coords_ang)
    a2 = Candidate("4-F", FLUORO, poses=[junk])
    PairedPoses(parent_poses_given=True).run([p2, a2], {"parent": p2})
    assert np.array_equal(a2.poses[0].coords_ang, fluoro.poses[0].coords_ang)


def test_self_pair_with_given_poses_copies_every_coordinate():
    parent = Candidate("parent", ACID)
    PairedPoses(n_poses=3).run([parent], {"parent": parent})
    twin = Candidate("twin", ACID, poses=list(parent.poses))
    PairedPoses(parent_poses_given=True, analogue_poses_given=True).run([parent, twin], {"parent": parent})
    assert [p.coords_ang.tolist() for p in twin.poses] == [p.coords_ang.tolist() for p in parent.poses]


@pytest.mark.needs_ferric
def test_self_pair_with_given_poses_gives_exactly_zero_ddE():
    parent = Candidate("parent", ACID)
    PairedPoses(n_poses=2).run([parent], {"parent": parent})
    twin = Candidate("twin", ACID, poses=list(parent.poses))
    PairedPoses(parent_poses_given=True, analogue_poses_given=True).run([parent, twin], {"parent": parent})
    field = [PointCharge(1.0, (3.0, 0.0, 0.0)), PointCharge(-0.5, (0.0, 3.5, 0.0))]
    FieldInteraction().run([parent, twin], {"field": field})
    assert paired_delta(parent, twin, "dE_int").per_pose == (0.0, 0.0)
    assert all(abs(v) > 1e-3 for v in parent.per_pose["dE_int"])  # not vacuous: the field does something
```

- [ ] **Step 2: Run them and see them fail**

Run: `TEST tests/test_paired_given_poses.py`
Expected: FAIL, `TypeError: PairedPoses.__init__() got an unexpected keyword argument 'analogue_poses_given'` (most tests) .

- [ ] **Step 3: Implement**

In `src/smeltery/tiers.py`, after `class NoCommonCoreError ...` add:

```python
#: How far (Å) a mapped heavy atom of a GIVEN analogue pose may sit from the parent's pose 0. A sanity check against
#: a mis-mapped core (a flipped ring is over 1 Å off), not a test of exactness: the core is copied exactly afterwards.
GIVEN_POSE_CORE_TOL_ANG = 0.5
```

In the `PairedPoses` docstring, before the sentence beginning "The MCS gate REFUSES", add:

```
    With `analogue_poses_given=True`, an analogue that already carries poses keeps its FIRST pose as pose 0 of the
    pair (a generator's geometry for the new atoms). Every mapped atom of that pose is still overwritten with the
    parent's pose-0 coordinate, so the core remains an exact copy and the self-pair anchor holds; the pose is refused
    (ValueError) if its atom order is not that of the SMILES with hydrogens added, or a mapped heavy atom sits more
    than `GIVEN_POSE_CORE_TOL_ANG` from the parent's pose 0. Poses 1..n-1 are built as above, and a given pose is
    never relaxed, whatever `relax_unmapped` says.

```

Add the field after `relax_unmapped: bool = False`:

```python
    analogue_poses_given: bool = False
```

Add `"analogue_poses_given",` to the key tuple in `settings()` after `"relax_unmapped",`.

In `_paired`, replace

```python
        sym = tuple(a.GetSymbol() for a in amol.GetAtoms())
        poses = []
        for i, pxyz in enumerate(parent_poses):
            if len(mapping) == amol.GetNumAtoms():
```

with

```python
        sym = tuple(a.GetSymbol() for a in amol.GetAtoms())
        given = cand.poses[0] if self.analogue_poses_given and cand.poses else None
        if given is not None:
            self._check_given_pose(cand, given, amol, sym, mapping, parent_poses[0])
        poses = []
        for i, pxyz in enumerate(parent_poses):
            if i == 0 and given is not None:
                xyz = np.array(given.coords_ang, dtype=float)
                for a, p in mapping:  # exact copy of every mapped atom, as in the default path
                    xyz[a] = pxyz[p]
                poses.append(Pose(sym, xyz))
                continue
            if len(mapping) == amol.GetNumAtoms():
```

and add this method after `_paired` (before `_relax_unmapped`):

```python
    def _check_given_pose(
        self,
        cand: Candidate,
        given: Pose,
        amol: Chem.Mol,
        sym: tuple[str, ...],
        mapping: list[tuple[int, int]],
        parent_pose0: np.ndarray,
    ) -> None:
        if tuple(given.symbols) != sym:
            raise ValueError(f"{cand.name}: the given pose's atom order does not match the SMILES (with H added)")
        heavy = [(a, p) for a, p in mapping if amol.GetAtomWithIdx(a).GetAtomicNum() > 1]
        worst = max(float(np.linalg.norm(given.coords_ang[a] - parent_pose0[p])) for a, p in heavy)
        if worst > GIVEN_POSE_CORE_TOL_ANG:
            raise ValueError(
                f"{cand.name}: a core atom of the given pose sits {worst:.2f} A from the parent's pose 0 "
                f"(limit {GIVEN_POSE_CORE_TOL_ANG} A); the pose is not on the parent's core"
            )
```

- [ ] **Step 4: Update the tier page**

In `docs/tiers/paired_poses.md`: change "reports these eight keys" to "reports these nine keys", add the row after `relax_unmapped`:

```
| `analogue_poses_given` | `False` |
```

Add to **Computes** (after the analogue-pose bullet):

```
- With `analogue_poses_given=True`, an analogue that already has poses keeps its first pose as pose 0 of the pair: its non-core atoms are used as given, and every core atom is still copied exactly from parent pose *0*. Poses 1 onward are built as above.
```

Add to **Not licensed to claim**:

```
- That a given analogue pose is a bound pose. Only its atom order and the position of its core are checked (each mapped heavy atom within 0.5 Å of parent pose 0); the non-core atoms are taken as supplied.
```

(The page tests reject history-narrating words such as "now", "previously", "newly"; avoid them.)

- [ ] **Step 5: Run the tests, the docs and registry tests, and lint**

Run: `TEST tests/test_paired_given_poses.py tests/test_anchors.py tests/test_paired_relax.py tests/test_tier_docs.py tests/test_tier_registry.py` then `RUFF src/smeltery/tiers.py tests/test_paired_given_poses.py`
Expected: all pass (the `needs_ferric` test runs, since the main venv has ferric). If `test_tier_docs.py` objects to the page, read its message: it names the page and line.

- [ ] **Step 6: Commit**

```bash
git add src/smeltery/tiers.py docs/tiers/paired_poses.md tests/test_paired_given_poses.py
git commit -m "feat(tiers): PairedPoses.analogue_poses_given keeps a generator's geometry for the new atoms" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The runner

**Files:**
- Create: `src/smeltery/generate/_sbdd_runner.py`
- Test: `tests/generate/test_sbdd_runner.py`

**Interfaces:**
- Produces: a script run as `<python> _sbdd_runner.py <request.json>`. Request keys (all required except `add_n_nodes`): `diffsbdd_dir, checkpoint, pdb, ref_ligand, fix_atoms, out_sdf, out_report, n_samples, timesteps, resamplings, center, seed`, and `add_n_nodes` (int or null). Exit status 0 on success, 1 if the run failed (the report then has `"ok": false` and an `"error"` traceback, also printed to stderr), 2 for a bad command line or request. On success the report has: `ok, n_raw, wall_time_s, seed, checkpoint_sha256, diffsbdd_commit, diffsbdd_dirty, torch, cuda, device, cuda_free_mib, patches`. Importable module functions `load_request(path)`, `sha256_of(path)`, `git_info(repo) -> (commit | None, dirty: bool)`, `main(argv) -> int`; top-level imports are stdlib only.

- [ ] **Step 1: Write the failing tests**

`tests/generate/test_sbdd_runner.py`:

```python
"""The runner's contract, tested without torch: bad input, failure reporting, helpers, and independence from smeltery."""

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "src" / "smeltery" / "generate" / "_sbdd_runner.py"

GOOD_REQUEST = {
    "diffsbdd_dir": "/nonexistent/diffsbdd",
    "checkpoint": "/nonexistent.ckpt",
    "pdb": "/nonexistent.pdb",
    "ref_ligand": "/nonexistent.sdf",
    "fix_atoms": "/nonexistent_scaffold.sdf",
    "n_samples": 2,
    "add_n_nodes": 3,
    "timesteps": 5,
    "resamplings": 1,
    "center": "ligand",
    "seed": 1,
}


def _load_module():
    spec = importlib.util.spec_from_file_location("sbdd_runner_under_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args):
    return subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True, text=True)


def test_the_runner_imports_nothing_from_smeltery():
    tree = ast.parse(RUNNER.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
        if isinstance(node, ast.ImportFrom) and node.level:
            pytest.fail("the runner must not use relative imports: it runs outside the smeltery package")
    assert "smeltery" not in roots


def test_a_missing_argument_is_a_usage_error():
    proc = _run()
    assert proc.returncode == 2 and "usage" in proc.stderr.lower()


def test_a_request_missing_keys_names_them(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"seed": 1}))
    proc = _run(str(request))
    assert proc.returncode == 2
    assert "out_sdf" in proc.stderr and "checkpoint" in proc.stderr


def test_a_failed_run_writes_a_report_with_the_traceback_and_exits_1(tmp_path):
    request = tmp_path / "request.json"
    report = tmp_path / "report.json"
    request.write_text(json.dumps({**GOOD_REQUEST, "out_sdf": str(tmp_path / "out.sdf"), "out_report": str(report)}))
    proc = _run(str(request))
    assert proc.returncode == 1
    written = json.loads(report.read_text())
    assert written["ok"] is False and "Traceback" in written["error"]
    assert written["error"].strip().splitlines()[-1] in proc.stderr


def test_sha256_of_matches_hashlib(tmp_path):
    import hashlib

    path = tmp_path / "blob"
    path.write_bytes(b"x" * 3_000_000)
    assert _load_module().sha256_of(path) == hashlib.sha256(b"x" * 3_000_000).hexdigest()


def test_git_info_reports_the_commit_and_whether_tracked_files_changed(tmp_path):
    def git(*a):
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *a],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (tmp_path / "f.txt").write_text("one")
    git("add", "f.txt")
    git("commit", "-q", "-m", "m")
    module = _load_module()
    commit, dirty = module.git_info(tmp_path)
    assert commit and len(commit) == 40 and dirty is False
    (tmp_path / "f.txt").write_text("two")
    assert module.git_info(tmp_path)[1] is True
    (tmp_path / "untracked.txt").write_text("x")  # an untracked file does not make the checkout dirty
    git("checkout", "--", "f.txt")
    assert module.git_info(tmp_path)[1] is False
    assert module.git_info(tmp_path / "missing") == (None, False)
```

- [ ] **Step 2: Run them and see them fail**

Run: `TEST tests/generate/test_sbdd_runner.py`
Expected: FAIL, the runner file does not exist (`FileNotFoundError` in `RUNNER.read_text()` / the import helper).

- [ ] **Step 3: Implement**

`src/smeltery/generate/_sbdd_runner.py`:

```python
"""Run DiffSBDD scaffold inpainting. Executed by the EXTERNAL Python (the one with torch and DiffSBDD installed).

    <python> _sbdd_runner.py request.json

smeltery never imports this file, and it imports nothing from smeltery: it must run under Python 3.10 with
torch 2.0.1, outside the smeltery environment. Top-level imports are stdlib only; torch, RDKit and DiffSBDD are
imported inside `run`, so the request and report plumbing can be tested without them.

Two things in DiffSBDD's `inpaint.py` are fixed at runtime rather than by editing the checkout, so its commit
hash stays the upstream one. Both are listed under "patches" in the report:

* `utils.batch_to_list` indexes a CPU tensor with a CUDA mask and fails on GPU after sampling;
* `inpaint.prepare_substructure` reads a module global `model` that exists only when `inpaint.py` runs as a script.

Exit status: 0 success, 1 the run failed (the report says why), 2 bad command line or request.
"""

import hashlib
import json
import random
import subprocess
import sys
import time
import traceback
from pathlib import Path

REQUIRED = (
    "diffsbdd_dir",
    "checkpoint",
    "pdb",
    "ref_ligand",
    "fix_atoms",
    "out_sdf",
    "out_report",
    "n_samples",
    "timesteps",
    "resamplings",
    "center",
    "seed",
)


def load_request(path):
    request = json.loads(Path(path).read_text())
    missing = [k for k in REQUIRED if k not in request]
    if missing:
        raise ValueError(f"request is missing {missing}")
    return request


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_info(repo):
    """(HEAD commit or None, whether a TRACKED file differs from HEAD). Untracked files do not count."""

    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        except OSError:
            return None

    head = git("rev-parse", "HEAD")
    if head is None or head.returncode != 0:
        return None, False
    status = git("status", "--porcelain", "--untracked-files=no")
    return head.stdout.strip(), bool(status is not None and status.stdout.strip())


def run(request):
    started = time.time()
    repo = Path(request["diffsbdd_dir"])
    sys.path.insert(0, str(repo))
    import numpy as np
    import torch
    from rdkit import Chem

    seed = int(request["seed"])
    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)

    import inpaint
    import utils
    from lightning_modules import LigandPocketDDPM

    patches = []
    original_batch_to_list = utils.batch_to_list

    def batch_to_list(data, batch_mask):
        return original_batch_to_list(data, batch_mask.to(data.device))

    utils.batch_to_list = batch_to_list
    patches.append("utils.batch_to_list: batch_mask moved to data.device")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = LigandPocketDDPM.load_from_checkpoint(request["checkpoint"], map_location=device).to(device)
    inpaint.model = model
    patches.append("inpaint.model: prepare_substructure reads this module global")

    molecules = inpaint.inpaint_ligand(
        model,
        request["pdb"],
        int(request["n_samples"]),
        request["ref_ligand"],
        [request["fix_atoms"]],
        add_n_nodes=request.get("add_n_nodes"),
        center=request["center"],
        sanitize=False,
        largest_frag=False,
        relax_iter=0,
        timesteps=int(request["timesteps"]),
        resamplings=int(request["resamplings"]),
    )
    writer = Chem.SDWriter(str(request["out_sdf"]))
    writer.SetKekulize(False)  # the molecules are unsanitized
    for molecule in molecules:
        writer.write(molecule)
    writer.close()

    commit, dirty = git_info(repo)
    return {
        "ok": True,
        "n_raw": len(molecules),
        "wall_time_s": round(time.time() - started, 3),
        "seed": seed,
        "checkpoint_sha256": sha256_of(request["checkpoint"]),
        "diffsbdd_commit": commit,
        "diffsbdd_dirty": dirty,
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "cuda_free_mib": round(torch.cuda.mem_get_info()[0] / 2**20) if device == "cuda" else None,
        "patches": patches,
    }


def main(argv):
    if len(argv) != 2:
        print("usage: _sbdd_runner.py request.json", file=sys.stderr)
        return 2
    try:
        request = load_request(argv[1])
    except Exception as exc:  # noqa: BLE001 - any unreadable request is a usage error
        print(f"bad request: {exc}", file=sys.stderr)
        return 2
    try:
        report, status = run(request), 0
    except BaseException:  # noqa: BLE001 - CUDA out-of-memory and import errors must reach the report too
        report, status = {"ok": False, "error": traceback.format_exc()}, 1
    Path(request["out_report"]).write_text(json.dumps(report, indent=2, sort_keys=True))
    if status:
        print(report["error"], file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run the tests and lint**

Run: `TEST tests/generate/test_sbdd_runner.py` then `RUFF src/smeltery/generate/_sbdd_runner.py tests/generate/test_sbdd_runner.py`
Expected: 6 passed; ruff clean. (The "failed run" test passes without torch because the import error is the failure it exercises.)

- [ ] **Step 5: Commit**

```bash
git add src/smeltery/generate/_sbdd_runner.py tests/generate/test_sbdd_runner.py
git commit -m "feat(generate): runner script for DiffSBDD scaffold inpainting" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `ScaffoldGrowth`

**Files:**
- Create: `src/smeltery/generate/sbdd.py`
- Test: `tests/generate/test_sbdd_scaffold_growth.py`

**Interfaces:**
- Consumes: Task 1 types, Task 2 `build_scaffold`, Task 3 `FilterConfig` and `process_samples`, Task 5's request/report contract.
- Produces: `@dataclass class ScaffoldGrowth` with fields `python: str`, `diffsbdd_dir: str`, `checkpoint: str`, `n_samples: int = 16`, `add_n_nodes: int | None = None`, `timesteps: int = 50`, `resamplings: int = 20`, `center: str = "ligand"`, `device: str | None = None` (value for `CUDA_VISIBLE_DEVICES`; None leaves it as it is), `pose_source: str = "embed"`, `filters: FilterConfig`, `target_accepted: int | None = None`, `max_batches: int = 1`, `name_prefix: str = "sbdd"`, `keep_workdir: str | None = None`, `timeout_s: float | None = None`, `runner: str | None = None` (override the packaged runner; for tests), `name = "sbdd_scaffold_growth"`. Methods `settings() -> dict` and `generate(parent, scaffold, pocket, *, seed) -> GenerationResult`. `generate` runs one subprocess per batch with seed `seed + batch`; with `target_accepted` set it runs up to `max_batches`, stopping once that many are accepted; otherwise exactly one. Provenance keys: `generator, seed, n_batches, n_raw, n_accepted, rejection_counts, wall_time_s, runs` (`runs` is the list of runner reports). Defaults of 50 timesteps and 20 resamplings are `inpaint.py`'s own.

- [ ] **Step 1: Write the failing tests**

`tests/generate/test_sbdd_scaffold_growth.py`:

```python
"""ScaffoldGrowth end to end against a fake runner that replays the real fixture; no GPU, no torch."""

import json
import sys
import tempfile
from pathlib import Path

import pytest

from smeltery.generate.base import GeneratorError
from smeltery.generate.sbdd import ScaffoldGrowth
from smeltery.generate.sbdd_post import FilterConfig

DATA = Path(__file__).resolve().parents[1] / "data" / "sbdd"

FAKE_RUNNER = """
import json, os, shutil, sys
request = json.load(open(sys.argv[1]))
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "crash":
    print("boom: something broke", file=sys.stderr)
    sys.exit(3)
if mode == "garbage_report":
    open(request["out_report"], "w").write("not json")
    shutil.copy(FIXTURE, request["out_sdf"])
    sys.exit(0)
if mode != "no_sdf":
    shutil.copy(FIXTURE if mode != "empty" else EMPTY, request["out_sdf"])
n_raw = 16 if mode not in ("empty", "wrong_count") else (0 if mode == "empty" else 99)
json.dump({
    "ok": True, "n_raw": n_raw, "wall_time_s": 0.1, "seed": request["seed"],
    "checkpoint_sha256": "abc", "diffsbdd_commit": "deadbeef", "diffsbdd_dirty": False,
    "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"), "patches": [],
    "cwd": os.getcwd(),
}, open(request["out_report"], "w"))
"""


@pytest.fixture
def fake_runner(tmp_path):
    empty = tmp_path / "empty.sdf"
    empty.write_text("")
    path = tmp_path / "fake_runner.py"
    path.write_text(f"FIXTURE = {str(DATA / 'grown_raw.sdf')!r}\nEMPTY = {str(empty)!r}\n" + FAKE_RUNNER)
    return str(path)


@pytest.fixture
def files(tmp_path):
    ckpt = tmp_path / "model.ckpt"
    ckpt.write_bytes(b"x")
    repo = tmp_path / "diffsbdd"
    repo.mkdir()
    pocket = tmp_path / "pocket.pdb"
    pocket.write_text("END\n")
    return {"checkpoint": str(ckpt), "diffsbdd_dir": str(repo), "pocket": str(pocket)}


def _generator(fake_runner, files, **kw):
    return ScaffoldGrowth(
        python=sys.executable,
        runner=fake_runner,
        diffsbdd_dir=files["diffsbdd_dir"],
        checkpoint=files["checkpoint"],
        **kw,
    )


def test_a_run_returns_the_measured_split_and_accounts_for_every_output(
    fake_runner, files, parent_candidate, scaffold_indices
):
    gen = _generator(fake_runner, files)
    result = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=11)
    assert len(result.candidates) == 1 and result.n_raw == 16
    assert result.rejection_counts() == {"unsanitizable": 12, "scaffold_lost": 3}
    assert result.candidates[0].name == "sbdd-0000" and result.candidates[0].poses == []
    prov = result.provenance
    assert prov["n_batches"] == 1 and prov["n_raw"] == 16 and prov["n_accepted"] == 1 and prov["seed"] == 11
    assert prov["rejection_counts"] == {"unsanitizable": 12, "scaffold_lost": 3}
    assert prov["runs"][0]["checkpoint_sha256"] == "abc" and prov["runs"][0]["diffsbdd_dirty"] is False
    json.dumps(prov)  # provenance goes into the run record, so it must be JSON-able


def test_model_poses_are_carried_when_asked(fake_runner, files, parent_candidate, scaffold_indices):
    gen = _generator(fake_runner, files, pose_source="model")
    cand = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1).candidates[0]
    assert len(cand.poses) == 1


def test_settings_describe_everything_that_shaped_the_output(fake_runner, files):
    gen = _generator(fake_runner, files, n_samples=8, add_n_nodes=9, filters=FilterConfig(scaffold_match="topology"))
    s = gen.settings()
    assert s["n_samples"] == 8 and s["add_n_nodes"] == 9 and s["timesteps"] == 50 and s["resamplings"] == 20
    assert s["filters"]["scaffold_match"] == "topology" and s["pose_source"] == "embed"
    json.dumps(s)


def test_the_runner_gets_the_inputs_it_needs(fake_runner, files, parent_candidate, scaffold_indices, tmp_path):
    keep = tmp_path / "kept"
    gen = _generator(fake_runner, files, keep_workdir=str(keep), n_samples=5, add_n_nodes=9)
    gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=21)
    request = json.loads((keep / "request_0.json").read_text())
    assert request["n_samples"] == 5 and request["add_n_nodes"] == 9 and request["seed"] == 21
    assert request["pdb"] == str(Path(files["pocket"]).resolve())
    assert request["timesteps"] == 50 and request["resamplings"] == 20 and request["center"] == "ligand"
    scaffold_sdf = (keep / "scaffold.sdf").read_text()
    parent_sdf = (keep / "parent.sdf").read_text()
    assert scaffold_sdf.splitlines()[3].split()[0] == "32" and parent_sdf.splitlines()[3].split()[0] == "41"
    report = json.loads((keep / "report_0.json").read_text())
    assert report["cwd"] == str(Path(files["diffsbdd_dir"]).resolve())  # DiffSBDD is run from its own directory


def test_the_device_sets_cuda_visible_devices_and_none_leaves_it_alone(
    fake_runner, files, parent_candidate, scaffold_indices, monkeypatch
):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    off = _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert off.provenance["runs"][0]["cuda_visible_devices"] is None
    on = _generator(fake_runner, files, device="1").generate(
        parent_candidate, scaffold_indices, files["pocket"], seed=1
    )
    assert on.provenance["runs"][0]["cuda_visible_devices"] == "1"


def test_every_sample_rejected_is_a_result_not_an_error(fake_runner, files, parent_candidate, scaffold_indices):
    """Review focus 4."""
    gen = _generator(fake_runner, files, filters=FilterConfig(forbidden_smarts={"phosphorus": "[P]"}))
    result = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert result.candidates == [] and result.n_raw == 16
    assert result.rejection_counts() == {"unsanitizable": 12, "scaffold_lost": 3, "forbidden_substructure": 1}


def test_an_empty_sdf_is_zero_raw_outputs(fake_runner, files, parent_candidate, scaffold_indices, monkeypatch):
    """Review focus 3."""
    monkeypatch.setenv("FAKE_MODE", "empty")
    result = _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert result.candidates == [] and result.rejected == [] and result.provenance["n_raw"] == 0


def test_batches_continue_until_the_target_and_deduplicate_across_them(
    fake_runner, files, parent_candidate, scaffold_indices
):
    """Review focus 5, across batches. The fake replays the same 16 samples every time."""
    gen = _generator(fake_runner, files, target_accepted=3, max_batches=3)
    result = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=100)
    assert len(result.candidates) == 1 and result.n_raw == 48
    assert result.rejection_counts()["duplicate"] == 2
    assert [r["seed"] for r in result.provenance["runs"]] == [100, 101, 102]
    assert result.provenance["n_batches"] == 3
    assert sorted(r.index for r in result.rejected) == [i for i in range(48) if i != 12]


def test_sampling_stops_as_soon_as_the_target_is_reached(fake_runner, files, parent_candidate, scaffold_indices):
    gen = _generator(fake_runner, files, target_accepted=1, max_batches=5)
    result = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert result.provenance["n_batches"] == 1


def test_without_a_target_exactly_one_batch_runs(fake_runner, files, parent_candidate, scaffold_indices):
    gen = _generator(fake_runner, files, max_batches=4)
    assert gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1).provenance["n_batches"] == 1


@pytest.mark.parametrize(
    "mode, message",
    [("crash", "status 3"), ("no_sdf", "no SDF"), ("garbage_report", "readable report"), ("wrong_count", "99")],
)
def test_a_failed_run_is_a_generator_error_with_the_reason(
    fake_runner, files, parent_candidate, scaffold_indices, monkeypatch, mode, message
):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(GeneratorError, match=message):
        _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)


def test_the_runner_stderr_tail_is_in_the_error(fake_runner, files, parent_candidate, scaffold_indices, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    with pytest.raises(GeneratorError, match="boom: something broke"):
        _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)


def test_an_interpreter_that_cannot_start_is_a_generator_error(files, parent_candidate, scaffold_indices, tmp_path):
    not_executable = tmp_path / "python"
    not_executable.write_text("")
    not_executable.chmod(0o755)  # exists and passes `which`, but is not a program the OS can run
    gen = ScaffoldGrowth(python=str(not_executable), diffsbdd_dir=files["diffsbdd_dir"], checkpoint=files["checkpoint"])
    with pytest.raises(GeneratorError, match="could not start"):
        gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)


def test_no_working_directory_is_left_behind_after_success_or_failure(
    fake_runner, files, parent_candidate, scaffold_indices, monkeypatch, tmp_path
):
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    monkeypatch.setenv("FAKE_MODE", "crash")
    with pytest.raises(GeneratorError):
        _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert list(root.iterdir()) == []


def test_misconfiguration_is_refused_before_anything_runs(fake_runner, files, parent_candidate, scaffold_indices):
    gen = _generator(fake_runner, files)
    with pytest.raises(ValueError, match="pocket not found"):
        gen.generate(parent_candidate, scaffold_indices, "/nonexistent.pdb", seed=1)
    with pytest.raises(ValueError, match="out of range"):
        gen.generate(parent_candidate, [0, 999], files["pocket"], seed=1)
    from smeltery import Candidate

    with pytest.raises(ValueError, match="bound pose"):
        gen.generate(Candidate("p", parent_candidate.smiles), scaffold_indices, files["pocket"], seed=1)
    for bad in (
        {"pose_source": "docked"},
        {"max_batches": 0},
        {"n_samples": 0},
        {"checkpoint": "/nonexistent.ckpt"},
        {"diffsbdd_dir": "/nonexistent_dir"},
        {"python": "/nonexistent/python"},
    ):
        kw = {"diffsbdd_dir": files["diffsbdd_dir"], "checkpoint": files["checkpoint"], "python": sys.executable, **bad}
        with pytest.raises(ValueError):
            ScaffoldGrowth(runner=fake_runner, **kw).generate(
                parent_candidate, scaffold_indices, files["pocket"], seed=1
            )
```

- [ ] **Step 2: Run them and see them fail**

Run: `TEST tests/generate/test_sbdd_scaffold_growth.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'smeltery.generate.sbdd'`.

- [ ] **Step 3: Implement**

`src/smeltery/generate/sbdd.py`:

```python
"""`ScaffoldGrowth`: DiffSBDD scaffold inpainting behind the `CandidateGenerator` protocol.

DiffSBDD runs in its own Python environment (it needs Python 3.10 and torch 2.0.1; smeltery needs RDKit >= 2024.3 on
Python 3.11+), so this class talks to it through `_sbdd_runner.py` in a subprocess with JSON and SDF files. See
docs/generators.md for setting the environment up and docs/superpowers/specs/2026-10-10-sbdd-scaffold-growth-design.md
for the measured yield: on the scaffold it was developed against, about 1 raw sample in 16 survived, so `target_accepted`
and the rejection counts matter more than the model's headline speed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from os import PathLike
from pathlib import Path

from rdkit import Chem

from ..model import Candidate
from .base import GenerationResult, GeneratorError, Rejection
from .sbdd_post import POSE_SOURCES, FilterConfig, build_scaffold, process_samples

RUNNER = Path(__file__).with_name("_sbdd_runner.py")
STDERR_TAIL_CHARS = 2000


def _write_sdf(path: Path, mol: Chem.Mol) -> None:
    writer = Chem.SDWriter(str(path))
    writer.write(mol)
    writer.close()


def _read_records(path: Path) -> list[Chem.Mol | None]:
    """Every record of an SDF, unsanitized, with None for one RDKit cannot parse.

    A run that produced no molecules leaves an empty file, which `SDMolSupplier` refuses to open (OSError), so an
    empty file is read as no records.
    """
    if path.stat().st_size == 0:
        return []
    return list(Chem.SDMolSupplier(str(path), sanitize=False, removeHs=False))


@dataclass
class ScaffoldGrowth:
    python: str  # the interpreter that has torch and DiffSBDD's dependencies
    diffsbdd_dir: str  # a DiffSBDD checkout
    checkpoint: str  # a conditional checkpoint, e.g. crossdocked_fullatom_cond.ckpt
    n_samples: int = 16  # samples per batch
    add_n_nodes: int | None = None  # atoms to add to the scaffold; None lets the model choose
    timesteps: int = 50  # inpaint.py's own defaults; the training value (500) with 1 resampling did worse
    resamplings: int = 20
    center: str = "ligand"
    device: str | None = None  # CUDA_VISIBLE_DEVICES for the runner; None leaves the environment as it is
    pose_source: str = "embed"  # "embed": SMILES only; "model": also carry the model's geometry as pose 0
    filters: FilterConfig = field(default_factory=FilterConfig)
    target_accepted: int | None = None  # keep sampling batches until this many are accepted (or max_batches)
    max_batches: int = 1  # only used with target_accepted
    name_prefix: str = "sbdd"
    keep_workdir: str | None = None  # keep request, report and SDF files here; None uses a temp dir that is removed
    timeout_s: float | None = None  # per batch
    runner: str | None = None  # replace the packaged runner (tests)
    name: str = "sbdd_scaffold_growth"

    def settings(self) -> dict:
        return {
            "generator": "diffsbdd-inpaint",
            "python": self.python,
            "diffsbdd_dir": self.diffsbdd_dir,
            "checkpoint": self.checkpoint,
            "n_samples": self.n_samples,
            "add_n_nodes": self.add_n_nodes,
            "timesteps": self.timesteps,
            "resamplings": self.resamplings,
            "center": self.center,
            "device": self.device,
            "pose_source": self.pose_source,
            "target_accepted": self.target_accepted,
            "max_batches": self.max_batches,
            "name_prefix": self.name_prefix,
            "filters": asdict(self.filters),
        }

    def generate(
        self, parent: Candidate, scaffold: Sequence[int], pocket: str | PathLike, *, seed: int
    ) -> GenerationResult:
        self._validate(pocket)
        if not parent.poses:
            raise ValueError(
                f"{parent.name}: the parent needs its bound pose as poses[0] to fix the scaffold's coordinates"
            )
        spec = build_scaffold(parent.smiles, scaffold, parent.poses[0])
        own_dir = self.keep_workdir is None
        workdir = Path(tempfile.mkdtemp(prefix="smeltery-sbdd-")) if own_dir else Path(self.keep_workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            return self._generate(spec, str(Path(pocket).resolve()), seed, workdir)
        finally:
            if own_dir:
                shutil.rmtree(workdir, ignore_errors=True)

    def _validate(self, pocket: str | PathLike) -> None:
        if self.pose_source not in POSE_SOURCES:
            raise ValueError(f"pose_source must be one of {POSE_SOURCES}, not {self.pose_source!r}")
        if self.n_samples < 1 or self.max_batches < 1:
            raise ValueError("n_samples and max_batches must be at least 1")
        for label, path in (("pocket", pocket), ("checkpoint", self.checkpoint), ("diffsbdd_dir", self.diffsbdd_dir)):
            if not Path(path).exists():
                raise ValueError(f"{label} not found: {path}")
        if shutil.which(self.python) is None:
            raise ValueError(f"python not found or not executable: {self.python}")

    def _generate(self, spec, pocket: str, seed: int, workdir: Path) -> GenerationResult:
        started = time.time()
        reference, fixed = workdir / "parent.sdf", workdir / "scaffold.sdf"
        _write_sdf(reference, spec.parent)
        _write_sdf(fixed, spec.scaffold)
        n_batches = self.max_batches if self.target_accepted is not None else 1
        candidates: list[Candidate] = []
        rejected: list[Rejection] = []
        seen: set[str] = set()
        runs: list[dict] = []
        n_raw = 0
        for batch in range(n_batches):
            report = self._run_batch(workdir, batch, seed + batch, pocket, reference, fixed)
            raws = _read_records(workdir / f"grown_{batch}.sdf")
            if len(raws) != report["n_raw"]:
                raise GeneratorError(f"the runner reported {report['n_raw']} samples but the SDF holds {len(raws)}")
            got, lost = process_samples(
                raws,
                spec,
                self.filters,
                pose_source=self.pose_source,
                name_prefix=self.name_prefix,
                start_index=n_raw,
                first_number=len(candidates),
                seen=seen,
            )
            n_raw += len(raws)
            candidates += got
            rejected += lost
            runs.append(report)
            if self.target_accepted is not None and len(candidates) >= self.target_accepted:
                break
        result = GenerationResult(candidates, rejected, {})
        result.provenance = {
            "generator": self.name,
            "seed": seed,
            "n_batches": len(runs),
            "n_raw": n_raw,
            "n_accepted": len(candidates),
            "rejection_counts": result.rejection_counts(),
            "wall_time_s": round(time.time() - started, 3),
            "runs": runs,
        }
        return result

    def _run_batch(self, workdir: Path, batch: int, seed: int, pocket: str, reference: Path, fixed: Path) -> dict:
        out_sdf = workdir / f"grown_{batch}.sdf"
        out_report = workdir / f"report_{batch}.json"
        request = workdir / f"request_{batch}.json"
        request.write_text(
            json.dumps(
                {
                    "diffsbdd_dir": str(Path(self.diffsbdd_dir).resolve()),
                    "checkpoint": str(Path(self.checkpoint).resolve()),
                    "pdb": pocket,
                    "ref_ligand": str(reference),
                    "fix_atoms": str(fixed),
                    "out_sdf": str(out_sdf),
                    "out_report": str(out_report),
                    "n_samples": self.n_samples,
                    "add_n_nodes": self.add_n_nodes,
                    "timesteps": self.timesteps,
                    "resamplings": self.resamplings,
                    "center": self.center,
                    "seed": seed,
                },
                indent=2,
            )
        )
        env = dict(os.environ)
        if self.device is not None:
            env["CUDA_VISIBLE_DEVICES"] = str(self.device)
        try:
            proc = subprocess.run(
                [self.python, self.runner or str(RUNNER), str(request)],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(Path(self.diffsbdd_dir).resolve()),
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise GeneratorError(f"the runner did not finish within {self.timeout_s} s") from exc
        except OSError as exc:
            raise GeneratorError(f"could not start {self.python!r}: {exc}") from exc
        if proc.returncode != 0:
            raise GeneratorError(f"the runner exited with status {proc.returncode}: {proc.stderr[-STDERR_TAIL_CHARS:]}")
        try:
            report = json.loads(out_report.read_text())
        except (OSError, ValueError) as exc:
            raise GeneratorError("the runner left no readable report") from exc
        if not report.get("ok"):
            raise GeneratorError(f"the runner reported a failure: {str(report.get('error'))[-STDERR_TAIL_CHARS:]}")
        if not out_sdf.exists():
            raise GeneratorError("the runner reported success but wrote no SDF")
        return report
```

- [ ] **Step 4: Run the tests and lint**

Run: `TEST tests/generate/test_sbdd_scaffold_growth.py` then `RUFF src/smeltery/generate/sbdd.py tests/generate/test_sbdd_scaffold_growth.py`
Expected: all pass; ruff clean. Two details to check if a test fails: (1) the message in `test_a_failed_run...` must match exactly the strings in `_run_batch` ("status 3", "no SDF" appears in "wrote no SDF", "readable report", and "99" in the count-mismatch message); (2) `shutil.which` accepts an absolute path to an existing executable, which is why `sys.executable` passes validation.

- [ ] **Step 5: Commit**

```bash
git add src/smeltery/generate/sbdd.py tests/generate/test_sbdd_scaffold_growth.py
git commit -m "feat(generate): ScaffoldGrowth, DiffSBDD scaffold inpainting behind a subprocess boundary" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Exports, docs, the GPU smoke test, and the real run

**Files:**
- Modify: `src/smeltery/generate/__init__.py`
- Modify: `pyproject.toml` (marker)
- Create: `docs/generators.md`
- Test: `tests/generate/test_sbdd_gpu_smoke.py`

**Interfaces:**
- Produces: `smeltery.generate` exports `CandidateGenerator, GenerationResult, GeneratorError, Rejection, ScaffoldGrowth, FilterConfig`; pytest marker `gpu`; the GPU smoke test reads `SMELTERY_SBDD_PYTHON`, `SMELTERY_SBDD_DIR`, `SMELTERY_SBDD_CKPT`, `SMELTERY_SBDD_POCKET` (all required) and `SMELTERY_SBDD_DEVICE` (optional).

- [ ] **Step 1: Exports**

In `src/smeltery/generate/__init__.py` add the import lines `from .base import CandidateGenerator, GenerationResult, GeneratorError, Rejection`, `from .sbdd import ScaffoldGrowth`, `from .sbdd_post import FilterConfig` (keep the file's existing import ordering for ruff's isort rule), and add the six names to `__all__`. Update the module docstring's first paragraph with one sentence: "`ScaffoldGrowth` is a structure-based generator over DiffSBDD (see docs/generators.md)." Then run `TEST tests/generate` to confirm the existing generate tests still pass (a test may assert `__all__`; update it if so).

- [ ] **Step 2: Register the marker**

In `pyproject.toml`, under `[tool.pytest.ini_options]` `markers`, add after the `needs_ferric` entry:

```toml
    "gpu: needs the external DiffSBDD environment and a GPU; skipped unless SMELTERY_SBDD_PYTHON, SMELTERY_SBDD_DIR, SMELTERY_SBDD_CKPT and SMELTERY_SBDD_POCKET are set",
```

- [ ] **Step 3: The smoke test**

`tests/generate/test_sbdd_gpu_smoke.py`:

```python
"""One real run through the external DiffSBDD environment. Skipped unless the four variables below are set."""

import os

import pytest

from smeltery.generate import ScaffoldGrowth

REQUIRED = ("SMELTERY_SBDD_PYTHON", "SMELTERY_SBDD_DIR", "SMELTERY_SBDD_CKPT", "SMELTERY_SBDD_POCKET")

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(
        any(k not in os.environ for k in REQUIRED),
        reason="set " + ", ".join(REQUIRED) + " to run the real-model smoke test",
    ),
]


def test_a_real_run_completes_and_reports(parent_candidate, scaffold_indices):
    gen = ScaffoldGrowth(
        python=os.environ["SMELTERY_SBDD_PYTHON"],
        diffsbdd_dir=os.environ["SMELTERY_SBDD_DIR"],
        checkpoint=os.environ["SMELTERY_SBDD_CKPT"],
        n_samples=4,
        add_n_nodes=9,
        device=os.environ.get("SMELTERY_SBDD_DEVICE"),
        pose_source="model",
    )
    result = gen.generate(parent_candidate, scaffold_indices, os.environ["SMELTERY_SBDD_POCKET"], seed=1)
    run = result.provenance["runs"][0]
    print(
        f"\nSMOKE n_raw={result.n_raw} accepted={len(result.candidates)} rejected={result.rejection_counts()} "
        f"wall={run['wall_time_s']}s device={run['device']} free_mib={run['cuda_free_mib']}"
    )
    # A run that accepts nothing is a result: the measured yield on this scaffold is about 1 in 16.
    assert result.n_raw == 4
    assert len(run["checkpoint_sha256"]) == 64 and run["diffsbdd_commit"]
    assert run["diffsbdd_dirty"] is False, (
        "the DiffSBDD checkout has local changes; the runner patches at runtime instead"
    )
```

- [ ] **Step 4: The docs**

Create `docs/generators.md` with these sections (write them in full; keep to what was verified):

1. **What it is.** `smeltery.generate.ScaffoldGrowth` regrows the part of a parent molecule outside a fixed scaffold, inside a receptor pocket, with DiffSBDD's inpainting; the output is repaired, filtered and returned as `Candidate`s. One short example: build a `ScaffoldGrowth`, call `generate(parent, scaffold, pocket, seed=...)`, put `[parent, *result.candidates]` through `PairedPoses(parent_poses_given=True, analogue_poses_given=True)` when `pose_source="model"`, and put `gen.settings()` and `result.provenance` into `RunRecord.inputs`.
2. **The external environment.** DiffSBDD does not run in smeltery's environment. The recipe verified on 2026-10-10 on this machine (two GTX 1080s, CUDA 13 driver): `uv venv --python 3.10 <dir>`, then `uv pip install --python <dir>/bin/python "torch==2.0.1" --index-url https://download.pytorch.org/whl/cu118`, then `numpy==1.23.5 scipy==1.10.1 pytorch-lightning==1.8.4 torchmetrics==0.11.4 rdkit==2022.9.5 biopython==1.79 networkx pandas==1.5.3 seaborn imageio pyyaml tqdm wandb==0.13.1 protobuf==3.20.3 matplotlib openbabel-wheel`, then `torch-scatter==2.1.1 -f https://data.pyg.org/whl/torch-2.0.0+cu118.html`. Clone `https://github.com/arneschneuing/DiffSBDD` (verified at commit `5d0d38d`) and download `crossdocked_fullatom_cond.ckpt` from Zenodo record 8183747. The checkpoint is a pickle and loads code: load it only in that environment, from that source. Pascal GPUs run torch's `sm_60` kernels.
3. **Settings and what they cost.** The defaults (50 timesteps, 20 resamplings) are `inpaint.py`'s; a 16-sample batch took 213 s on one GTX 1080. The runner passes `CUDA_VISIBLE_DEVICES` from `device`.
4. **Rejection reasons.** A table of `REASONS` with one line each, and the filter order (sanitize, connected, scaffold, forbidden, duplicate, size).
5. **Measured yield, honestly.** One scaffold (32 of 41 heavy atoms fixed, 9 added), 16 samples: 12 unsanitizable, 3 lost the scaffold, 1 accepted, and that one is chemically implausible; at the training step count with one resampling, 0 of 16. State plainly that this setup is a poor proposer on that scaffold, that `scaffold_match="topology"` admits 4 but they include molecules that absorbed the carboxylic acid into a ring, and that a yield study is the next piece of work. No claim beyond that.
6. **Not claimed.** The model's pose is one geometry, not a bound pose; the funnel pairs ensembles, so the standard error mixes the model pose with jittered ones.

Do not use the words "now", "previously", "newly" or "no longer" (they are pointless in a page of current facts).

- [ ] **Step 5: Run everything that does not need the GPU**

Run:
- `TEST tests/generate tests/test_paired_given_poses.py tests/test_tier_docs.py tests/test_tier_registry.py tests/test_migration_rules.py tests/test_anchors.py tests/test_paired_relax.py tests/test_wheel_smoke.py`
- `RUFF .` (the whole repository, as CI does)

Expected: all pass or skip (the GPU smoke test skips); ruff clean. `tests/test_migration_rules.py` must pass: it is the check that no campaign name leaked into `src/`.

Then the full suite once, in the background (it can be long): `TEST tests` with `run_in_background`, and read its tail when it finishes. CI allows 90 minutes.

- [ ] **Step 6: The real GPU run**

Set the four variables and run the smoke test (the external environment and checkout are at `/home/matt/qc/external`; `venv-sbdd` is the interpreter environment; the pocket is the 407-residue receptor file in `experiments/danuglipron/data/c9_danuglipron/`). First revert the local edit to DiffSBDD's `utils.py` so the checkout is clean: `git -C /home/matt/qc/external/DiffSBDD checkout -- utils.py`; the runner patches at runtime instead. Then:

```
SMELTERY_SBDD_PYTHON=/home/matt/qc/external/venv-sbdd/bin/python SMELTERY_SBDD_DIR=/home/matt/qc/external/DiffSBDD SMELTERY_SBDD_CKPT=/home/matt/qc/external/DiffSBDD/checkpoints/crossdocked_fullatom_cond.ckpt SMELTERY_SBDD_POCKET=/home/matt/qc/smeltery/.claude/worktrees/generative-sbdd/experiments/danuglipron/data/c9_danuglipron/7LCJ_pocket.pdb SMELTERY_SBDD_DEVICE=0 PYTHONPATH=/home/matt/qc/smeltery/.claude/worktrees/generative-sbdd/src /home/matt/qc/smeltery/.venv/bin/python -m pytest -q -s -p no:cacheprovider tests/generate/test_sbdd_gpu_smoke.py
```

Expected: 1 passed, and a `SMOKE ...` line with `n_raw=4`, the accepted count, the rejection counts, wall time and free GPU memory. Record that line in `docs/generators.md` under "Measured yield" as a second, independent data point (4 samples, `pose_source="model"`). If the test fails because `diffsbdd_dirty` is true, the revert in this step was skipped. If it fails inside the runner, the `GeneratorError` carries the traceback.

- [ ] **Step 7: Commit and report**

```bash
git add src/smeltery/generate/__init__.py pyproject.toml docs/generators.md tests/generate/test_sbdd_gpu_smoke.py
git commit -m "feat(generate): export the generator API, document it, and add the GPU smoke test" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Report to the owner: the smoke line, the full-suite result, and the open item that matters most, which is that the yield on the measured scaffold is about 1 in 16 and a yield study is the next piece of work. Do not push or open a pull request without being asked.

---

## Self-review

**Spec coverage.** Protocol and result types: Task 1. Subprocess boundary and runner with runtime patches: Tasks 5 and 6. Match, repair, Kabsch fit, sanitize, optional filters in the spec's order, `scaffold_match` modes, deduplication, size, sampling budget: Tasks 2, 3, 6. Pose switch and `analogue_poses_given` with the 0.5 Å check, no relaxation, anchor preserved: Tasks 3 (model poses) and 4. Atom-order invariant: Tasks 2 and 3. Provenance in `settings()` and `result.provenance`, with `record.py` unchanged: Task 6. Error handling: run-level `GeneratorError`, sample-level `Rejection`, misconfiguration `ValueError`: Task 6. Testing (fixture pins, fake runner, anchors, boundary tests, GPU smoke, tier docs): Tasks 2 to 7. Acceptance criteria 1 to 7: 1 and 2 in Tasks 3 and 6; 3 in Task 3; 4 in Tasks 3 and 4; 5 in Tasks 5 and 6 (report fields) and Task 7 (assertions on the real run); 6 in Task 7 Step 5; 7 in Task 7 Step 6.

**Deliberate departures from the spec text, all recorded there:** poses are fitted rigidly, not overwritten, in the generator (exactness comes from `PairedPoses`); hydrogens are added without relaxation; filter order puts duplicate before size so a size-rejected molecule never enters the seen set.

**Type consistency.** `ScaffoldSpec` fields (`parent, scaffold, kekule, indices`) and `.coords` are used identically in Tasks 2, 3, 6. `process_samples` keyword names (`pose_source, name_prefix, start_index, first_number, seen`) match between Task 3's definition and Task 6's call. Runner report keys in Task 5 (`ok, n_raw, wall_time_s, seed, checkpoint_sha256, diffsbdd_commit, diffsbdd_dirty, ...`) match what Task 6 reads (`ok`, `n_raw`) and what Task 7 asserts. `FilterConfig` fields match across Tasks 3, 6, 7. `POSE_SOURCES` is defined once, in Task 3, and imported by Task 6.

**Known soft spots, flagged for the reviewer.** (1) The pinned counts (12 / 3 / 1, 4 under topology, 12 samples missing scaffold bonds) were measured under RDKit 2022.09.5 and 2026.03.6 and agree; a different RDKit may move them, and the plan says to report, not edit. (2) The fake-runner template in Task 6 contains one dead line to delete (noted under the test file). (3) `test_wheel_smoke.py` may build a wheel and could be slow or need network; if it cannot run offline, run it in CI only and say so.
