import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.BENCH_E2E_PORT ?? 8799);
const python = process.env.BENCH_PYTHON ?? "../.venv/bin/python";

// Each run builds the app, then serves it over freshly built fixture runs (e2e/serve_fixture.py).
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npm run build && BENCH_E2E_PORT=${port} ${python} e2e/serve_fixture.py`,
    url: `http://127.0.0.1:${port}/healthz`,
    timeout: 180_000,
    reuseExistingServer: false,
  },
});
