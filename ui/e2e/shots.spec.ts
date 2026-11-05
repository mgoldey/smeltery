/** Documentation screenshots, light and dark, written to docs/img/.
 *
 * Not assertions about pixels: these produce the figures docs/bench.md and the README show, from the same fixture
 * runs every other spec uses, so a committed screenshot is a page that really rendered rather than a drawing. Run
 * them on purpose (`npx playwright test e2e/shots.spec.ts`); `npm run test` skips them, because writing files into
 * the repo is not something a check should do.
 */
import { expect, IDX, PIPELINE, test } from "./bench";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), "../../docs/img");
const RUN_SHOTS = process.env.BENCH_SHOTS === "1";

test.describe(() => {
  test.skip(!RUN_SHOTS, "set BENCH_SHOTS=1 to write screenshots into docs/img/");
  test.beforeAll(() => mkdirSync(OUT, { recursive: true }));

  /** Each page, in both themes. `ready` must settle before the shot: a half-drawn figure is worse than none. */
  const pages: { file: string; path: string; ready: (page: import("@playwright/test").Page) => Promise<void> }[] = [
    {
      file: "bench-workspace",
      path: `/pipelines/${PIPELINE}`,
      ready: async (page) => {
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expect(page.locator("[data-step]").first()).toBeVisible();
      },
    },
    {
      file: "bench-methods",
      path: "/methods",
      ready: async (page) => {
        await expect(page.getByRole("heading", { name: "Methods", level: 1 })).toBeVisible();
        await expect(page.getByRole("region", { name: "Method field_interaction" })).toBeVisible();
      },
    },
    {
      file: "bench-prebuilt",
      path: "/bench",
      ready: async (page) => {
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expect.poll(async () => page.locator("[data-prebuilt]").count()).toBeGreaterThan(0);
      },
    },
    {
      file: "bench-results",
      path: `/run/${IDX.b_unranked}/results`,
      ready: async (page) => {
        await expect(page.locator('[data-figure="forest"] .main-svg').first()).toBeVisible();
      },
    },
    {
      // The lineage panel is opened from the results table, so the shot shows both: a number and what made it.
      file: "bench-lineage",
      path: `/run/${IDX.a_resolved}/results`,
      ready: async (page) => {
        const trace = page.getByRole("button", { name: /^Trace / }).first();
        await expect(trace).toBeVisible();
        await trace.click();
        await expect(page.getByRole("complementary", { name: "Lineage" })).toBeVisible();
      },
    },
  ];

  for (const theme of ["light", "dark"] as const) {
    test.describe(`${theme} theme`, () => {
      test.use({ colorScheme: theme });
      for (const { file, path, ready } of pages) {
        test(`${file} (${theme})`, async ({ page }) => {
          await page.goto(path);
          await ready(page);
          await page.waitForTimeout(400); // let figure animations settle; the shot is a still
          const name = theme === "light" ? `${file}.png` : `${file}-dark.png`;
          await page.screenshot({ path: resolve(OUT, name), fullPage: false });
        });
      }
    });
  }
});
