# AGENTS.md

How to build, test and check smeltery. Commands only; progress lives in `ralph/bench/features.json`
and `ralph/bench/NOTES.md`, never here.

## Environment
- Python: `.venv/bin/python` (3.12). ferric is installed there; Bench tests do not need it.
- Install a package: `VIRTUAL_ENV=.venv uv pip install --no-cache <pkg>`. If `uv.lock` changed and
  you did not mean it to, `git checkout -- uv.lock`.
- Prefix anything heavy: `nice -n 19 taskset -c 0,1 env OPENBLAS_NUM_THREADS=1 RAYON_NUM_THREADS=2 OMP_NUM_THREADS=1`.
- Kill processes by PID (`ps`, then `kill <pid>`), never `pkill -f`.

## Tests: run the smallest set that answers the question
The full suite takes ~30 minutes. **Do not run it unless a feature's `"full_suite": true` says so.**

1. While working: the feature's own `verify` command.
2. Before committing, the **bench gate** (~30 s):
   `.venv/bin/python -m pytest tests/bench tests/test_study_plan_cells.py -q -p no:randomly`
3. Plus the tests for any core file you touched:

   | You changed | Also run |
   |---|---|
   | `src/smeltery/cli.py` | `tests/test_cli.py tests/test_run_outputs.py tests/test_decisions.py tests/test_golden_path_smoke.py` |
   | `src/smeltery/study/` | `tests/test_study.py tests/test_study_plan_cells.py` |
   | `src/smeltery/decisions.py` | `tests/test_decisions.py` |
   | `src/smeltery/gui/` | `tests/test_gui_*.py` |
   | `src/smeltery/tasks.py`, `dashboard.py` | `tests/test_dashboard_tasks.py` |
   | `src/smeltery/funnel.py` | `tests/test_funnel.py` |

4. Full suite, only for milestone features:
   `nice -n 19 taskset -c 0,1 env OPENBLAS_NUM_THREADS=1 RAYON_NUM_THREADS=2 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ -q -p no:randomly`
   Baseline: 1649 passed, 45 skipped. `tests/test_danuglipron_halogen.py::test_a_failed_scf_keeps_the_inflight_results_and_is_never_retried`
   is a known flake (passes alone); any other failure is yours.

Never use `pytest -x` when checking for regressions: it hides every failure after the first.

## Frontend (`ui/`)
- Install: `cd ui && npm ci`. Browser for tests: `npx playwright install chromium` (cached builds may
  already be in `~/.cache/ms-playwright`).
- While working on a UI feature: `cd ui && npx playwright test e2e/<file>.spec.ts` (only that file).
- Before committing a UI change: `cd ui && npm run typecheck && npm run test && npm run build`.
- After changing any API route or response: `cd ui && npm run gen:api`, then commit
  `ui/src/api/schema.d.ts`.
- The full browser suite (`cd ui && npx playwright test`) runs only for milestone features.
- `src/smeltery/bench/web/` is build output: never edit or commit it.

## Gates (all must pass before a commit)
```
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
uvx ty@0.0.96 check
<the bench gate, plus the table above for core files you touched>
```
`ruff check` passing does not mean `ruff format --check` passes. Run both.

## Layout facts that cause mistakes
- `tests/` is not a package; tests import helpers bare (`from gui_fixture import make_run_dir`).
  `tests/bench/` is a package (`from tests.bench.fakes import ...`).
- `tests/gui_fixture.make_run_dir(root, unranked=False)` writes a contract-shaped run dir: four
  candidates, a tie group, pose SDFs. Use it rather than hand-writing records.
- Do not edit `experiments/` or `docs/archive/`; they are frozen records.
- Bench code: `src/smeltery/bench/{ports,adapters,use_cases}/`, `app.py`, `verdict.py`,
  `provisional.py`. There is exactly one home for each use case: `use_cases/`.

## Git
- Commit only the paths you changed: `git add <paths>`. Never `git add -A` or `git add .`.
- Do not commit `.ralph/`, `.opencode/`, `.smeltery/`, `*.sqlite`, `ui/node_modules/`,
  `src/smeltery/bench/web/`, or anything under `benchmarks/`.
- Do not commit `scripts/temporal_ddE.py`; its changes belong to unrelated work.
