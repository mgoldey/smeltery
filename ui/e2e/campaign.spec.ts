import { expect, fixture, test } from "./bench";

type Row = { step: string; upstream: string[] };
type Cell = { label: string; step: string; state: string };

test("a study's DAG has one node per step and one edge per upstream link", async ({ page }) => {
  const study = fixture().study;
  // Data source: the API's dag rows, from Study.dag().
  const rows = (await (await page.request.get(`/api/studies/${study}/dag`)).json()) as Row[];
  const edges = rows.flatMap((r) => r.upstream.map((u) => `${u}->${r.step}`));
  expect(edges.length).toBeGreaterThan(1);
  await page.goto("/bench");
  await page.getByRole("link", { name: study }).click();
  await expect(page.locator("[data-step]")).toHaveCount(rows.length);
  for (const r of rows) await expect(page.locator(`[data-step="${r.step}"]`)).toBeVisible();
  const drawn = await page.locator("[data-edge]").evaluateAll((els) => els.map((e) => e.getAttribute("data-edge")).sort());
  expect(drawn).toEqual([...edges].sort());
});

test("every cell says cached or to run, as text", async ({ page }) => {
  const study = fixture().study;
  const cells = (await (await page.request.get(`/api/studies/${study}/cells`)).json()) as Cell[];
  await page.goto(`/studies/${encodeURIComponent(study)}`);
  for (const c of cells) {
    await expect(page.locator(`[data-cell="${c.label}|${c.step}"]`)).toHaveText(c.state === "cached" ? "cached" : "to run");
  }
});

test("a study that is not configured shows the API's message inline", async ({ page }) => {
  await page.goto("/studies/nope.nothing");
  await expect(page.getByRole("alert")).toContainText("nope.nothing");
});
