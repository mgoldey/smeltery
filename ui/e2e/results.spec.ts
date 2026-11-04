import type { Page } from "@playwright/test";
import { expect, IDX, test } from "./bench";

type Fig = { data: { mode?: string; y?: unknown[] }[]; layout: { shapes?: { name?: string }[] } };

async function api<T>(page: Page, path: string): Promise<T> {
  return (await (await page.request.get(path)).json()) as T;
}

/** The figure as Plotly actually holds it in the page (Plotly attaches data/layout to the graph div). */
async function rendered(page: Page, name: string): Promise<Fig> {
  const el = page.locator(`[data-figure="${name}"]`);
  await expect(el.locator(".main-svg").first()).toBeVisible();
  return el.evaluate((d) => ({ data: (d as unknown as Fig).data, layout: (d as unknown as Fig).layout }));
}

test("the forest plot has one marker per analogue, from the API's table", async ({ page }) => {
  const table = await api<{ rows: { candidate: string }[] }>(page, `/api/runs/${IDX.a_resolved}/ddE`);
  await page.goto(`/run/${IDX.a_resolved}/results`);
  await rendered(page, "forest");
  await expect(page.locator('[data-figure="forest"] .scatterlayer .point')).toHaveCount(table.rows.length);
  for (const r of table.rows) await expect(page.getByRole("button", { name: `Trace ${r.candidate}`, exact: true })).toBeVisible();
});

test("a non-zero floor draws its band around each group boundary", async ({ page }) => {
  const run = await api<{ floor: number }>(page, `/api/runs/${IDX.d_floor}`);
  expect(run.floor).toBeGreaterThan(0); // data source: the fixture run's recorded floor
  await page.goto(`/run/${IDX.d_floor}/results`);
  const fig = await rendered(page, "forest");
  const bands = (fig.layout.shapes ?? []).filter((s) => s.name?.startsWith("floor-band"));
  expect(bands.length).toBeGreaterThan(0);
  const drawn = await page.locator('[data-figure="forest"] .shapelayer path').count();
  expect(drawn).toBe((fig.layout.shapes ?? []).length);
});

test("tie groups carry text labels, in the plot and the table", async ({ page }) => {
  await page.goto(`/run/${IDX.a_resolved}/results`);
  await rendered(page, "forest");
  await expect(page.locator('[data-figure="forest"]').getByText(/tie group 2/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "tie group 2 (no order)" }).first()).toBeVisible();
});

test("the matrix shown is the funnel's resolved matrix", async ({ page }) => {
  const want = await api<{ names: string[]; matrix: boolean[][] }>(page, `/api/runs/${IDX.a_resolved}/resolved`);
  await page.goto(`/run/${IDX.a_resolved}/results`);
  await rendered(page, "matrix");
  const z = await page
    .locator('[data-figure="matrix"]')
    .evaluate((d) => (d as unknown as { data: { z: number[][] }[] }).data[0]?.z ?? []);
  expect(z.map((row) => row.map((v) => Boolean(v)))).toEqual(want.matrix);
});

test("the per-pose plot draws one line per analogue pose pair", async ({ page }) => {
  const rows = await api<{ candidate: string; pose: number }[]>(page, `/api/runs/${IDX.a_resolved}/per_pose`);
  await page.goto(`/run/${IDX.a_resolved}/results`);
  await rendered(page, "per_pose");
  // One drawn line per (analogue, pose) row of the API's per-pose table.
  await expect(page.locator('[data-figure="per_pose"] .scatterlayer .trace')).toHaveCount(rows.length);
});

test("an incomplete run shows no plots, only what it lacks", async ({ page }) => {
  await page.goto(`/run/${IDX.c_incomplete}/results`);
  await expect(page.getByRole("region", { name: "Verdict" })).toContainText("Incomplete");
  await expect(page.locator("[data-figure]")).toHaveCount(0);
});
