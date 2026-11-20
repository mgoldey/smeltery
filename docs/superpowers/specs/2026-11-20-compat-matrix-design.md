# Meta-install and compatibility matrix: a pinned set of all three, and a CI-made table of ferric versions

Date: 2026-11-20. Roadmap task 46 (cross-repo). Status: built.

Task text: "Meta-install and compatibility matrix. A pinned compatible set of all three, plus a CI-generated table of
which ferric versions each smeltery release was tested against."

## Audit: what existed at e33c879

| Question | Finding |
|---|---|
| How is ferric declared? | `ferric>=0.1.0rc10` (a pre-release floor, no cap), from PyPI; `[tool.uv.sources]` is empty (a checkout or git rev is a commented-out recipe). uv.lock pins 0.1.0rc10. |
| Which ferric does CI test? | Only the locked one: every job is `uv sync --no-sources` (0.1.0rc10). release.yml installs the built wheel with plain pip, so it gets the NEWEST ferric (0.1.0rc12 today) -- but only on a tag, and nothing records which. |
| Which tests touch the real engine? | Those marked `needs_ferric` (21 files). The release workflow runs `test_funnel.py` + `test_anchors.py` + `examples/mwe_benzoic.py` from the installed wheel; the golden path (`test_golden_path_smoke.py`) drives `smeltery run` end to end. The cross-server test uses the FAKE engine (`tests/cross_server/smeltery_fake_mcp.py`) and says nothing about ferric. |
| What is on PyPI? | ferric 0.1.0rc3..rc12 (no rc9 files), smeltery 0.0.1 and 0.1.0 (0.1.0, 2026-11-09, has neither the `mcp` nor the `attestation` extra), attestation 0.2.0..0.5.0 (0.5.0 pins `mcp==1.28.1`; main pins `mcp==2.3.0` since 8c67578), attestation-schema 0.1.0 only, mcp 2.3.0. |
| What is unreleased? | attestation 0.5.1 (main's pyproject says 0.5.1; the repository's `v0.5.1` tag points at the `schema-v0.1.0` commit, whose pyproject still says 0.5.0, and nothing was published from it) and attestation-schema 0.2.0 (main; attestation main requires `>=0.2.0`). attestation main moved from `mcp==1.28.1` to `mcp==2.3.0` at 8c67578 (its PR #68), which this stack pins. |
| Can one environment hold all three? | **No, for one reason.** mcp no longer conflicts (smeltery's `mcp` extra needs `>=2.3`; attestation main pins `==2.3.0` since 8c67578; PyPI's 0.5.0 still pins 1.28.1). attestation-schema does: smeltery's `attestation` extra caps it `<0.2`, attestation main needs `>=0.2.0`, which is not on PyPI. The `cross-server` job already installs attestation into its own `uv tool` environment. |

## Decisions

### The pinned set is a file, not an extra

`compat/stack.toml` names the set and says, per member, where it comes from and why; `compat/constraints.txt` is its
smeltery-environment half as a pip/uv constraints file, GENERATED from stack.toml (`scripts/compat_matrix.py
constraints --write`, `--check` in CI and in a test).

A `smeltery[stack]` extra was rejected: it could only pull `attestation` into smeltery's own environment, which the
boundary rule forbids (smeltery depends on attestation-schema, never attestation) and which the attestation-schema
conflict above makes impossible anyway. A constraints file constrains without adding a dependency, so it is the honest shape: the
one-liner is `uv pip install -c <raw constraints at REF> 'smeltery[mcp,attestation] @ git+...@REF'`, and attestation
goes into its own environment at the commit stack.toml pins.

Two environments, stated in the file:

* **smeltery-env**: ferric 0.1.0rc10, attestation-schema 0.1.0, mcp 2.3.0 (PyPI), smeltery from git at the same ref
  the constraints are read from. PyPI's smeltery 0.1.0 is not this stack's smeltery: it predates both extras.
* **attestation-env**: attestation from git at a FULL commit (8c67578, main today, version 0.5.1), which brings
  attestation-schema 0.2.0 from the same checkout and mcp 2.3.0 (the same mcp as smeltery-env). PyPI's 0.5.0 is not usable: it predates the ledger's
  core-record reader and `runs.status`, which is what reads smeltery's output.

Why ferric 0.1.0rc10 and not the newest: it is what uv.lock pins, so **every** `uv sync` job in test.yml tests exactly
the pinned set. tests/test_compat_stack.py keeps each smeltery-env pin equal to uv.lock's and inside the specifier
pyproject declares; the matrix (below) is where newer ferric versions are shown to pass. Moving the pin is one PR:
stack.toml, `uv lock -P ferric==X`, the regenerated constraints.

How the pinned set is verified:

1. tests/test_compat_stack.py (every PR): pins == uv.lock, pins satisfy pyproject, constraints generated, attestation
   pinned by full SHA and absent from smeltery's dependencies.
2. A new `stack` job in test.yml (every PR): installs smeltery from the checkout with `-c compat/constraints.txt` into
   a fresh venv (not `uv sync`, which would read uv.lock and hide a bad constraints file), asserts the installed
   versions equal the pins, installs attestation at the pinned rev with `uv tool install`, and runs
   `tests/cross_server` against that exact pair with `SMELTERY_REQUIRE_CROSS_SERVER=1`. The existing `cross-server`
   job keeps tracking attestation main (its purpose: do the two heads still fit); `stack` proves the pin.
3. Run locally on 2026-11-20 (the same steps): versions as pinned, cross-server transcript passed against 6e2b14d
   (1 passed, 14 s) and again after the pin moved to 8c67578 (1 passed, 10 s).

### The matrix: one variable at a time

`scripts/compat_matrix.py run --ferric SPEC` makes one CELL per (smeltery, ferric): a fresh `uv venv`, smeltery (the
checkout, or `--smeltery smeltery==0.1.0` with `--source` a checkout of that tag) with the `posebusters` extra, the
ferric under test as an `--override` (so a version below the floor can be shown, labelled so), and **every other
dependency constrained to that smeltery tree's own uv.lock** (`uv export`). The last part is measured, not
decorative: the first trial let dependencies float, rdkit moved past the lock, and golden-path gate G1 refused
butanol instead of propanol -- a failure with nothing to do with ferric. With the lock exported as constraints the
same cell passes.

Checks per cell, from a copy of the tree's `tests/`, `docs/` and `examples/` so the INSTALLED package is imported:

* `install`: ferric installs and imports, smeltery imports from site-packages; ferric's version and build stamp are
  recorded.
* `tests`: `test_funnel.py`, `test_anchors.py`, `test_interaction_energy.py`, `test_golden_path_smoke.py` (minus the
  ten-fold gate-mutation sweep, which tests smeltery's gates, not ferric), `test_cost.py` and the `basis_gaps` test of
  `test_plb_measure.py`. Each calls the real engine. ~25 s. The last two are there because of a measurement: with only
  the first four, every cell down to ferric 0.1.0rc6 passed, which would have said the declared floor (0.1.0rc10) was
  arbitrary. It is not (b401d10: rc8's bundled STO-3G stopped at Ar); those two tests fail on rc8 and pass from rc10,
  so the table now shows where the floor comes from.
* `example`: `examples/mwe_benzoic.py` runs and its run record names the installed ferric (the build stamp's commit
  when the wheel has one, else the version) with a VERIFIED provenance.

A cell is `pass`, `fail` or `not-installable`. Rejected: the full `needs_ferric` suite (many files need other extras,
pdb2pqr or GPUs; minutes per cell, and a failure there is usually not about ferric), and the cross-server test (fake
engine).

### Where the table lives, and why CI does not commit it

`render` merges cells into the JSON block embedded at the end of docs/compatibility.md (newest cell per smeltery
version x ferric label x Python minor wins) and rewrites the whole page from that data. The page is therefore a pure
function of its data block: tests/test_compat_matrix.py pins the format line by line on fixed cells and fails if the
committed page differs from the render of its own data (a hand edit, or a stale generator).

The `compat` workflow runs weekly, on demand (`workflow_dispatch`, with extra ferric specs, e.g. one below the floor)
and on every `v*` tag (that release's row). It lists ferric from PyPI (every release the floor admits) plus `main`,
runs one cell per job, and a final job renders the merged page and uploads it with the raw cells as the
`compatibility` artifact; it fails if a released ferric that the floor admits did not pass. ferric `main` is
ferric's newest nightly wheel (ferric's wheels.yml, artifact `wheels-cp310-abi3`, kept 3 days), because building it
needs libint2, which the runner lacks; if the download fails the cell is recorded `not-installable` rather than
silently dropped. UNVERIFIED: that `github.token` may download another repository's artifacts.

**CI never commits.** No workflow here pushes to main; a bot commit would skip the review every change to a docs page
gets, and on a protected branch would need a token with write access stored for it. Refreshing the committed page is
`render cells/*.json --write` on the downloaded artifact, in an ordinary PR. The tag run makes the artifact at release
time, so the release's row exists the day it is cut.

## What was produced locally (2026-11-20)

The committed docs/compatibility.md, made by `scripts/compat_matrix.py` at fd21a12 (the commit before the page; the
next commit only stopped cells recording the local wheel's machine path) on Python 3.11.15, linux-x86_64:

| smeltery | ferric 0.1.0rc6 | rc7 | rc8 | rc10 | rc11 | rc12 | main (2f484e7) |
|---|---|---|---|---|---|---|---|
| 0.1.1.dev114+gfd21a12d8 (this tree) | FAIL (below floor) | FAIL (below floor) | FAIL (below floor) | pass | pass | pass | pass |
| 0.1.0 (PyPI wheel, tests from the v0.1.0 tag) | -- | -- | FAIL (below floor) | pass | pass | pass | pass |

Every below-floor failure is exactly the two basis tests (STO-3G past Ar), the floor's documented reason; the
example's run record states a VERIFIED ferric provenance in every cell. ferric main is a wheel built here from
2f484e7 (`maturin build --release`; Rust, libint2 2.13.1 and OpenBLAS are present in this sandbox), not the nightly
wheel CI will download. Each cell takes ~30-60 s (install from a warm uv cache, ~100 tests, the example).

The pinned stack was checked the way the `stack` job does it: the constraints file read back from the pushed ref
(byte-identical), `uv pip install -c ... 'smeltery[mcp,attestation] @ git+...@<ref>'` resolved ferric 0.1.0rc10,
attestation-schema 0.1.0, mcp 2.3.0 and `smeltery mcp --list-tools` listed the four tools; attestation installed at
8c67578 reported attestation 0.5.1, attestation-schema 0.2.0, mcp 2.3.0; the cross-server transcript passed against
that pair (as it had against the earlier pin 6e2b14d, with mcp 1.28.1). (Passing the constraints as an https URL to uv/pip failed in this sandbox only because of its TLS proxy;
the file was fetched with curl instead.)

## Left open

* attestation is on PyPI only at 0.5.0; once 0.5.1 (and attestation-schema 0.2.0) are published, stack.toml's
  attestation-env can move to PyPI pins, and once smeltery's `attestation` extra admits schema 0.2 the two
  environments can become one (mcp already agrees at 2.3.0). Neither is true today and the stack says so.
* smeltery 0.1.0 on PyPI has no `mcp`/`attestation` extras; the next smeltery release lets the one-liner use PyPI.
* The matrix is Linux x86_64 and one Python (3.11). ferric now ships macOS arm64 and Windows wheels; smeltery does
  not claim those platforms.
