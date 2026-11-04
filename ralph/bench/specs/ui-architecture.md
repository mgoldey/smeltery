# UI architecture

**Bench's interface is one React application, served by the Bench API, verified in a real browser.**

## Decisions (made; not open)

| Concern | Choice | Why |
|---|---|---|
| Framework | React 19 + TypeScript (strict) + Vite | Mol* and RDKit.js are web libraries; the same app serves the local tool now and a deployed service later. |
| Shell | Browser tab opened by `smeltery bench` | Electron would ship a second runtime and Chromium beside the Python server it still needs, and only fits a desktop. A Tauri shell (system webview, ~10 MB, Rust is already in the toolchain) is deferred, not built. |
| Data fetching | TanStack Query | Caching, polling for live task status, and retry/error states without hand-written effects. |
| Routing | React Router | `/bench`, `/run/:idx/*`, `/report/:idx`, `/campaigns/:id` |
| Accessible primitives | Radix UI (unstyled) | Keyboard and screen-reader behaviour for menus, dialogs and tabs, styled with Bench's own tokens. Not a component kit with its own look. |
| 3D | `molstar` npm package, plugin API | Renders the MolViewSpec state the API already builds (`scenes.pose_scene`). |
| 2D structures and atom picking | `@rdkit/rdkit` (RDKit.js) SVG depiction | Atoms are SVG elements carrying their index, so picking is deterministic and testable. |
| Plots | `plotly.js-dist-min`, rendering figure JSON built in Python | `gui.views.forest_figure`, `matrix_figure`, `per_pose_figure`, `compare_figure` already encode tie bands and the floor. The browser draws them; it never re-derives them. |
| TOML editing | CodeMirror 6 | |
| API types | Generated from FastAPI's OpenAPI schema with `openapi-typescript` | One source of truth for request and response shapes. |
| Unit tests | Vitest | |
| Browser tests | Playwright (`@playwright/test`, Chromium) | Every UI feature is checked as a user sees it. |

## Requirements

1. The frontend lives in `ui/` (its own `package.json`, `package-lock.json` committed). `npm run build`
   writes to `src/smeltery/bench/web/`, which is gitignored and built by CI and the wheel build.
2. The API serves `web/index.html` at `/` with a single-page fallback for any non-`/api` path, and
   the built assets under `/assets/`. All JSON stays under `/api/`.
3. `smeltery bench` with no built UI exits 2 with a message naming the command that builds it.
4. `python -m smeltery.bench.openapi` prints the OpenAPI JSON; `npm run gen:api` writes
   `ui/src/api/schema.d.ts` from it. CI regenerates it and fails if the committed file differs.
5. No string-built HTML routes remain in `app.py`. The Python side serves JSON and the built app.
6. The app works offline: every library is bundled by Vite. Browser tests block all non-localhost
   requests and fail if any is attempted.
7. Plot figures come from `GET /api/runs/{idx}/figures/{forest|matrix|per_pose}` returning
   `fig.to_plotly_json()` from the existing Python builders. The frontend passes them to Plotly
   unchanged, apart from theme colours.

## Testing contract
- Browser tests live in `ui/e2e/`. They start the real API through `ui/e2e/serve_fixture.py`, which
  builds run directories with `tests/gui_fixture.make_run_dir` (RESOLVED, UNRANKED, and INCOMPLETE
  with `results.cut` deleted) and serves them on a port from `BENCH_E2E_PORT`.
- A browser test reads expected values from the fixture's records or from the API's JSON. It never
  hard-codes a number the page is supposed to compute.

## Not in scope
The Tauri desktop shell, multi-user auth, and any server-side rendering.
