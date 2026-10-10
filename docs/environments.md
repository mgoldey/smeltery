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

`smeltery.tiers.ForceField` has two paths with one contract: `mmff` (RDKit, core;
writes `E_mmff`, `E_mmff_relaxed`) and `openmm` (optional environment; writes
`E_openmm`, `E_openmm_relaxed`). Both relax a copy of each pose and never write
coordinates back, so a quantum tier after it scores the pose it was handed
(`tests/test_forcefield.py`, `tests/test_forcefield_openmm.py`, and dock -> FF ->
quantum with real Vina in `tests/test_docking_forcefield.py`, parametrised over both
paths; reintroducing the in-place write fails them).

### OpenMM force-field path: where ligand parameters come from

Decided on evidence, 2026-10-10. The route is **SMIRNOFF (OpenFF Sage
`openff_unconstrained-2.2.1.offxml`) through openmmforcefields'
`SMIRNOFFTemplateGenerator`, in a conda-forge environment**. It is not a pip extra:

| Route | Result |
|---|---|
| pip only: `openmm openmmforcefields rdkit` (openmmforcefields 0.15.1 sdist) | installs and imports, but `import openff` fails: no ligand parameter source. No `ambertools`, `openff-toolkit-base` or `openff-forcefields` project exists on PyPI (404); the PyPI `openff-toolkit`, `openff-interchange`, `openff-units`, `openff-utilities` releases are all yanked |
| GAFF2 via `GAFFTemplateGenerator` | needs AmberTools `antechamber` (conda) and openff-toolkit; same environment, no gain |
| micromamba + conda-forge (`environment-openff.yml`) | works end to end, measured below |

micromamba 2.9.0-0 (`micromamba-linux-64`) was fetched from the mamba-org/micromamba-releases
GitHub release; sha256 `366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3`
matched both the release's `.sha256` file and the API digest. The environment resolved
to python 3.11, openmm 8.6.1, openmmforcefields 0.16.0, openff-toolkit 0.18.0 (the
conda-forge build; a different artifact from the yanked PyPI upload), ambertools 26.0,
rdkit 2026.03.1 (about 3 GB download because conda-forge pulled a CUDA build of libtorch;
not pruned). Create it with `micromamba create -n smeltery-ff -f environment-openff.yml`;
`sqm` must be on PATH (activate the environment: openff-toolkit finds it there), and
smeltery's own `ferric` goes in with pip. Without it, `ForceField(path="openmm")`
raises `ForceFieldBackendMissing` naming this file, and the tests skip with the same
instruction. CI does not have the environment, so CI skips them: the OpenMM path is
exercised only where someone builds it.

Measured behaviour, same day, in that environment:

* Tests: `tests/test_forcefield.py` + `tests/test_forcefield_openmm.py`: 18 passed;
  `tests/test_docking_forcefield.py` (real Vina, both parametrisations): 2 passed.
  Mutations that each fail a test: no kJ -> kcal conversion, no convergence check,
  relaxed coordinates written back (also fails the docking test), atom-order check
  removed, `E_openmm` taken after relaxation.
* Units: OpenMM reports kJ/mol; divided by 4.184. A test rebuilds the system by hand
  and compares kJ directly.
* Platform: `Reference` (double precision). The `CPU` platform is single precision:
  at RMS-force tolerance 1 kJ/mol/nm it stalled on aspirin (once raising "did not
  converge" on a geometry that had converged a moment earlier), took about 160 ms, and
  its relaxed energy scattered by about 0.0004 kcal/mol; Reference was deterministic and
  about 6 ms. Convergence is judged by us (RMS force <= 0.1 kJ/mol/nm after
  `LocalEnergyMinimizer`), because that call does not report it.
* Charges: AM1-BCC via sqm, computed once per SMILES per tier instance. Fresh
  instances agreed exactly for benzoic acid and aspirin but differed by 0.016 kcal/mol
  for ibuprofen (conformer-dependent charges; cause not isolated). Not pinned to the
  pose.
* Parameter-assignment failure: `C[Se]C` raises `ForceFieldTypingError`; the cause is
  that sqm/AM1-BCC cannot handle selenium, not Sage lacking it (not isolated further).
* Warnings seen, not investigated: openff-interchange's "preset charges alongside
  virtual-site parameters" and a torch `reduce_op` deprecation.
* The vacuum, ligand-only energy is not comparable across formulas and no systematic
  floor is measured (`systematic_floor` is None).
* pip-installing vina/meeko/scipy/gemmi/pdb2pqr/posebusters into the conda env (for the
  docking test) upgraded numpy to 2.4.6, which conflicts with another package's pin in
  that environment (`proprep`); the tests passed regardless.

### Measured cost, one pose, `ForceField().run` (SMILES parse + MMFF94 minimize of a copy)

RDKit 2026.03.6, Python 3.11, one thread, box load average about 4.6 on 12 cores,
ETKDG seed 1 embedding, 50 repeats, 2026-10-10:

| Molecule | Atoms | Median | Range |
|---|---|---|---|
| benzoic acid | 15 | 1.8 ms | 1.8 - 8.8 ms |
| aspirin | 21 | 3.9 ms | 3.9 - 4.5 ms |
| ibuprofen | 33 | 12.4 ms | 12.2 - 13.7 ms |

#### Origin of the issue's "~9 ms at 21 atoms"

Found (ferric, read-only history search for "9 ms"): ferric commit `2e0eee64`
(2026-09-20) re-measured `tiers.tier2_forcefield` on aspirin, 21 atoms, n=9 after a
warm-up call, median 9.0 ms (min 8.8, max 10.3). That function is a different protocol
from this tier's: it parses SMILES, adds Hs, **embeds with ETKDGv3
(`useSmallRingTorsions`)** and runs `MMFFOptimizeMoleculeConfs(maxIters=2000)`, i.e.
embed + optimize, where `ForceField.run` relaxes a pose it is given. Reproduced here
(code copied from ferric `70f0cb3c^:tools/pipeline/tiers.py`), RDKit 2026.03.6,
2026-10-10, **load average about 36 on 12 cores** (not a quiet box):

| Molecule | n=9 median (min) | n=50 median (min) |
|---|---|---|
| benzoic acid (15) | 9.4 (8.3) ms | 9.0 (8.0) ms |
| aspirin (21) | 14.9 (12.0) ms | 15.1 (12.7) ms |
| ibuprofen (33) | 40.5 (36.5) ms | 40.1 (32.1) ms |

Aspirin is 1.7x the 9.0 ms figure (within the issue's factor of 2); the load makes
that ratio an upper bound on protocol agreement rather than a clean match. The
earlier 3.9 ms is a different quantity (relaxation only, no embedding). Under the same
load, `ForceField().run` measured 7.1 ms (aspirin, mmff) and the OpenMM path 13.3 ms
(cached parameters; median of 30). First call for a new SMILES: 2.3 s benzoic acid,
7.8 s aspirin, 48 s ibuprofen, dominated by AM1-BCC. Maxima of hundreds of ms to
seconds in those runs were load. Nothing was tuned toward 9 ms.

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
