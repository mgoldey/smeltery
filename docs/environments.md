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
| `openmmml` | resolves (3 packages; not checked that it is the OpenMM-ML project) |

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

## PoseBusters on Python 3.12 (issue #68)

Verified 2026-10-10, Linux x86_64, throwaway venvs from PyPI wheels, one thread.

**Fault.** With uv-managed CPython 3.12.11+ (3.12.11 and 3.12.12 tested) and 3.13.5+,
`posebusters_check` segfaulted. It is not PoseBusters-specific logic, scipy or pandas:
`np.array([Point3D(1,2,3), Point3D(1,2,3)])` alone reproduces it with numpy + rdkit.
PoseBusters 0.6.5 hits it once, at `posebusters/modules/flatness.py:115`
(`np.array([conf.GetAtomPosition(i) ...])`).

**Mechanism (established by gdb backtrace and one controlled bypass):** NumPy iterates
each `Point3D` until RDKit raises the end-of-sequence `IndexError` from C++
(`RDGeom::point3dGetItem`). RDKit wheels with boost 1.85 route every C++ throw through
`libboost_stacktrace_from_exception`'s `__cxa_allocate_exception`, which calls
`_Unwind_Backtrace`; the fault is in libgcc_s inside that call. An `LD_PRELOAD` shim
sending `__cxa_allocate_exception` to libstdc++'s own makes the same script pass.
Throwing from pure-Python frames is fine (`Point3D(1,2,3)[3]`, `list(p)`); only a throw
beneath NumPy's C frames faults. **Not established:** why the unwinder faults on
those interpreters (suspect: missing/odd unwind info in the interpreter or NumPy frames
of that python-build-standalone build); no upstream report was checked.

| Interpreter | rdkit | numpy | result |
|---|---|---|---|
| 3.11.13, 3.11.14 (uv) | 2026.03.6 | 2.4.x | pass |
| 3.12.0, .4, .6, .8, .9, .10 (uv) | 2026.03.6 | 2.4.4 | pass |
| 3.12.3 (Ubuntu system python) | 2026.03.6 | 2.5.3 | pass |
| 3.12.11 (uv) | 2026.03.6 | 2.4.4 | segfault |
| 3.12.12 (uv) | 2026.03.6 | 1.26.4, 2.0.2, 2.1.3, 2.2.6, 2.3.5, 2.4.4, 2.5.3 | segfault (every numpy) |
| 3.13.5, 3.13.9 (uv) | 2026.03.6 | 2.4.4 | segfault |
| 3.12.12 (uv) | 2026.03.1, 2025.09.x, 2025.03.x, 2024.09.x, 2024.03.2/.5/.6 | 2.2.6 | segfault |
| 3.12.12 (uv) | 2024.03.1 (boost 1.78, no from_exception hook) | 2.2.6 | pass |

Not varied: pandas, scipy (not installed in the reproducer), posebusters version (only
0.6.5), other 3.12 patch releases (.1, .2, .5, .7 not run), other OS/arch.

**Outcome.** No version pin is sound (only the 2024.03.1 rdkit passes). `smeltery.gates`
instead replaces `flatness._get_coords` with a `GetPositions()` read of the same
coordinates (no exception thrown, so the hook never runs). With it,
`tests/test_posebusters_gate.py` passes on 3.12.12 and 3.11.14, and the full suite
passes on 3.12.12 (425 passed, 24 skipped). The `posebusters-py312` CI job guards it.
Other code that lets RDKit throw a C++ exception beneath NumPy frames can still fault on
the affected interpreters; smeltery's own `GetAtomPosition` uses are pure-Python
(`list(p)`, `tuple(p)`) and were not seen to fault.
