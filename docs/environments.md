# Environments: what resolves under uv, and what needs pixi

Verified 2026-10-10 with `uv pip install --dry-run` into a fresh Python 3.11 venv
(uv resolution against PyPI). **A dry-run proves the dependency set resolves; it
does not prove the packages work together.** Re-verify before relying on it.

## Resolves under uv

| Package(s) | Result |
|---|---|
| `openmm rdkit vina meeko pdb2pqr posebusters ferric>=0.1.0rc6` (together) | resolves, 25 packages |
| `openmmforcefields` | resolves |
| `gufe` | resolves |
| `openmmml` | resolves (3 packages). It is the OpenMM-ML project (1.8). With `torchani` it installs and runs on CPU: see `docs/potentials.md` and the `ml-potential` extra |

## Does not resolve under uv

| Package | Why |
|---|---|
| `openff-toolkit` | the only PyPI release, 0.18.0, is yanked ("non-maintainer uploaded package to pypi ..."), so uv has nothing to install |

OpenFF therefore needs a conda-family environment (pixi). It is **not** a core or
extra dependency of smeltery, and nothing here adopts pixi: it is the route to
take if you need OpenFF parameters. `gufe` was evaluated and rejected as the funnel
core (a Protocol is a fixed two-state DAG with no population, ranking or cost
tracking); that it resolves says nothing about adopting it.

## Force-field tier

`smeltery.tiers.ForceField` has one path, `mmff` (RDKit, core). It relaxes a copy of
each pose and never writes coordinates back, so a quantum tier after it scores the
pose it was handed (`tests/test_forcefield.py`, and dock -> FF -> quantum with real
Vina in `tests/test_docking_forcefield.py`; reintroducing the in-place write fails
both). There is **no OpenMM path yet**: where ligand parameters would come from
(GAFF via openmmforcefields, or OpenFF via pixi) is undecided (issue #18).

### Measured cost, one pose, `ForceField().run` (SMILES parse + MMFF94 minimize of a copy)

RDKit 2026.03.6, Python 3.11, one thread, box load average about 4.6 on 12 cores,
ETKDG seed 1 embedding, 50 repeats, 2026-10-10:

| Molecule | Atoms | Median | Range |
|---|---|---|---|
| benzoic acid | 15 | 1.8 ms | 1.8 - 8.8 ms |
| aspirin | 21 | 3.9 ms | 3.9 - 4.5 ms |
| ibuprofen | 33 | 12.4 ms | 12.2 - 13.7 ms |

Issue #18 cites "~9 ms at 21 atoms" and asks for agreement within a factor of 2. The
source and protocol of that figure are not recorded in this repository, and 3.9 ms
is 2.3x below it (outside the factor-2 band). It was not tuned toward 9 ms. The
likely cause is a different protocol, but that is not established.
