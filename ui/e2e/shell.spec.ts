import { expect, fixture, IDX, run, test } from "./bench";

test("the bench lists every fixture run with its digest", async ({ page }) => {
  await page.goto("/bench");
  const runs = fixture().runs;
  // Two fixtures share inputs (resolved, and the same run with its cut deleted), so they share a digest.
  for (const digest of new Set(runs.map((r) => r.digest.slice(0, 12)))) {
    const n = runs.filter((r) => r.digest.startsWith(digest)).length;
    await expect(page.getByRole("cell", { name: digest, exact: true })).toHaveCount(n);
  }
});

test("the context bar shows the run's campaign and input digest", async ({ page }) => {
  const r = run("a_resolved"); // data source: the record serve_fixture.py wrote, via .fixture.json
  await page.goto(`/run/${r.id}`);
  await expect(page).toHaveURL(new RegExp(`/run/${r.id}/results$`));
  await expect(page.getByTestId("context-campaign")).toHaveText(r.campaign);
  await expect(page.getByTestId("context-digest")).toHaveText(r.digest.slice(0, 12));
});

test("an incomplete run says what it is missing instead of rendering blank", async ({ page }) => {
  const r = run("c_incomplete");
  await page.goto(`/run/${r.id}/overview`);
  await expect(page.getByRole("alert")).toContainText("incomplete");
  await expect(page.getByRole("alert")).toContainText("cut");
});

test("an unknown address is a page that says so", async ({ page }) => {
  await page.goto("/no/such/place");
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
});

test("the fixture indices used at collection time match what the server built", () => {
  for (const r of fixture().runs) expect(IDX[r.name as keyof typeof IDX]).toBe(r.idx);
});

test("the app shell renders under the error boundary, and its fallback stays out of the way", async ({ page }) => {
  // Forcing a throw in the real app would need a test-only hook; the fallback itself is covered by ErrorBoundary.test.tsx.
  const r = run("a_resolved");
  await page.goto(`/run/${r.idx}`);
  await expect(page.getByTestId("context-campaign")).toBeVisible();
  await expect(page.getByTestId("error-boundary")).toHaveCount(0);
});

test("a run id addresses the same record whatever its position, and the old integer link still opens it", async ({
  page,
}) => {
  const r = run("d_floor"); // not position 0, so an id/position mix-up would show
  const byId = (await (await page.request.get(`/api/runs/${r.id}`)).json()) as { path: string; id: string };
  expect(byId.path).toBe(r.path);
  const byPosition = await page.request.get(`/api/runs/${r.idx}`);
  expect(((await byPosition.json()) as { path: string }).path).toBe(r.path);
  expect(byPosition.headers()["deprecation"]).toBe("true"); // it works, and it says it is the old form
  expect(byPosition.headers()["link"]).toBe(`</api/runs/${r.id}>; rel="canonical"`);
  // The page the app itself links to is the id one: every run link on Bench is /run/<an id>, never /run/<n>.
  await page.goto("/bench");
  await expect(page.locator('a[href^="/run/"]').first()).toBeVisible(); // the page is its own chunk: wait for it
  const hrefs = await page.locator('a[href^="/run/"]').evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  expect(hrefs).toContain(`/run/${r.id}`);
  expect(hrefs.filter((h) => /^\/run\/\d+$/.test(h ?? ""))).toEqual([]);
});
