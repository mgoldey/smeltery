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
  await page.goto(`/run/${r.idx}`);
  await expect(page).toHaveURL(new RegExp(`/run/${r.idx}/results$`));
  await expect(page.getByTestId("context-campaign")).toHaveText(r.campaign);
  await expect(page.getByTestId("context-digest")).toHaveText(r.digest.slice(0, 12));
});

test("an incomplete run says what it is missing instead of rendering blank", async ({ page }) => {
  const r = run("c_incomplete");
  await page.goto(`/run/${r.idx}/overview`);
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
