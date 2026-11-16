import type { Page } from "@playwright/test";
import { expect, id, IDX, test } from "./bench";

type Lineage = {
  candidate: string;
  parent: string;
  poses: { pose: number; value: number; parent_value: number; difference: number }[];
  steps: { stage: string; kind: string; seconds: number | null }[];
  missing: string[];
  digest: string;
};

async function get<T>(page: Page, path: string): Promise<T> {
  return (await (await page.request.get(path)).json()) as T;
}

test("tracing a candidate shows the API's per-pose values and what made them", async ({ page }) => {
  const table = await get<{ rows: { candidate: string }[] }>(page, `/api/runs/${IDX.a_resolved}/ddE`);
  const name = table.rows[0]!.candidate;
  const want = await get<Lineage>(page, `/api/runs/${IDX.a_resolved}/lineage?candidate=${encodeURIComponent(name)}`);
  expect(want.poses.length).toBeGreaterThan(0);

  await page.goto(`/run/${IDX.a_resolved}/results`);
  await page.getByRole("button", { name: `Trace ${name}`, exact: true }).click();

  const panel = page.getByRole("complementary", { name: "Lineage" });
  await expect(panel).toBeVisible();
  const rows = panel.getByRole("table", { name: "Per-pose values" }).locator("tbody tr");
  await expect(rows).toHaveCount(want.poses.length);
  for (const [i, p] of want.poses.entries()) {
    const cells = rows.nth(i).locator("td");
    await expect(cells.nth(0)).toHaveText(String(p.pose));
    await expect(cells.nth(1)).toHaveText(p.value.toFixed(2));
    await expect(cells.nth(2)).toHaveText(p.parent_value.toFixed(2));
    await expect(cells.nth(3)).toHaveText(p.difference.toFixed(2));
  }

  const made = panel.getByRole("region", { name: "Made by" });
  for (const s of want.steps) await expect(made).toContainText(s.stage);
  await expect(made).toContainText(want.digest.slice(0, 12));
  // The fixture runs record no config: it is named as absent, not left blank.
  expect(want.missing).toContain("config");
  await expect(made.getByTestId("made-config")).toContainText("not recorded for this run");

  await panel.getByRole("button", { name: "Close" }).click();
  await expect(panel).toHaveCount(0);
});

test("tracing another candidate switches the panel to it", async ({ page }) => {
  const table = await get<{ rows: { candidate: string }[] }>(page, `/api/runs/${IDX.a_resolved}/ddE`);
  const name = table.rows[1]!.candidate;
  await page.goto(`/run/${IDX.a_resolved}/results`);
  await page.getByRole("button", { name: `Trace ${name}`, exact: true }).click();
  await expect(page.getByRole("complementary", { name: "Lineage" })).toContainText(name);
});

test("the lineage opens the pipeline that made the number, and 'change a setting' opens its ΔE_int step", async ({ page }) => {
  const table = await get<{ rows: { candidate: string }[] }>(page, `/api/runs/${IDX.a_resolved}/ddE`);
  const name = table.rows[0]!.candidate;
  await page.goto(`/run/${IDX.a_resolved}/results`);
  await page.getByRole("button", { name: `Trace ${name}`, exact: true }).click();
  const panel = page.getByRole("complementary", { name: "Lineage" });
  await panel.getByRole("button", { name: "Open the pipeline that made this" }).click();
  await expect(page).toHaveURL(new RegExp(`/pipelines/[a-z0-9_-]+\\?run=${id("a_resolved")}$`));
  const pipeline = new URL(page.url()).pathname.split("/").pop()!;
  // Data source: the pipeline the API now holds was made from this run (its campaign and candidates).
  const doc = await get<{ toml: string }>(page, `/api/pipelines/${pipeline}`);
  const run = await get<{ campaign: string }>(page, `/api/runs/${IDX.a_resolved}`);
  expect(doc.toml).toContain(`campaign = "${run.campaign}"`);
  expect(doc.toml).toContain(name);

  await page.goto(`/run/${IDX.a_resolved}/results`);
  await page.getByRole("button", { name: `Trace ${name}`, exact: true }).click();
  await panel.getByRole("button", { name: "Change a setting and re-run" }).click();
  await expect(page).toHaveURL(new RegExp(`/pipelines/${pipeline}\\?run=${id("a_resolved")}&step=measure$`));
  const measure = page.getByRole("navigation", { name: "Pipeline steps" }).locator('[data-step="measure"]');
  await expect(measure).toHaveAttribute("aria-current", "step");
  await expect(page.locator('[data-panel="measure"]')).toBeVisible();
});

test("Provenance says plainly when the run kept no config, rather than showing one that never ran", async ({
  page,
}) => {
  // Provenance named the config file and its hash but never showed the text. These fixture runs record no config
  // at all, which is the case that matters most: a rebuilt config would read as provenance while being a guess.
  await page.goto(`/run/${id("a_resolved")}/provenance`);
  const config = page.getByRole("region", { name: "Config" });
  await expect(config).toBeVisible();
  await expect(config.getByTestId("config-absent")).toContainText("does not name a config");
  await expect(config.getByTestId("config-text")).toHaveCount(0); // nothing invented to fill the space
});
