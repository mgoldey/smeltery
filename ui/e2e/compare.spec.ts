import { expect, id, test } from "./bench";

test("choosing two runs draws the comparison figure and the API's caption", async ({ page }) => {
  const want = (await (await page.request.get(`/api/compare?a=${id("a_resolved")}&b=${id("d_floor")}`)).json()) as { caption: string };
  await page.goto("/compare");
  await page.getByLabel("Arm A").selectOption(id("a_resolved"));
  await page.getByLabel("Arm B").selectOption(id("d_floor"));
  await expect(page.getByTestId("compare-caption")).toHaveText(want.caption);
  await expect(page.locator('[data-figure="compare"] .main-svg').first()).toBeVisible();
});

test("the same run in both arms is refused, and nothing is drawn", async ({ page }) => {
  await page.goto("/compare");
  await page.getByLabel("Arm A").selectOption(id("a_resolved"));
  await page.getByLabel("Arm B").selectOption(id("a_resolved"));
  await expect(page.getByRole("alert")).toContainText("both arms");
  await expect(page.locator('[data-figure="compare"]')).toHaveCount(0);
});

test("with fewer than two finished runs the page says what to do and links to Bench", async ({ page }) => {
  await page.route("**/api/runs", (route) => route.fulfill({ json: [] }));
  await page.goto("/compare");
  const empty = page.getByTestId("empty-state");
  await expect(empty).toContainText("at least two finished runs");
  await empty.getByRole("link", { name: "Go to Bench" }).click();
  await expect(page).toHaveURL(/\/bench$/);
});

test("a run links into a comparison already holding it, and each arm links back", async ({ page }) => {
  // Compare used to be reachable only from the sidebar, with the arms in component state: there was no way in
  // from a run you were reading, and no way back out to the runs being compared.
  await page.goto(`/run/${id("a_resolved")}/results`);
  await page.getByTestId("go-compare").click();
  await expect(page).toHaveURL(new RegExp(`/compare\\?a=${id("a_resolved")}`));
  await expect(page.getByLabel("Arm A")).toHaveValue(id("a_resolved")); // arrived already holding this run

  await page.getByLabel("Arm B").selectOption(id("d_floor"));
  await expect(page.getByTestId("compare-caption")).toBeVisible();
  // The arms are in the URL, so the comparison is a thing you can send to someone.
  await expect(page).toHaveURL(new RegExp(`b=${id("d_floor")}`));

  await page.getByTestId("open-arm-b").click();
  await expect(page).toHaveURL(new RegExp(`/run/${id("d_floor")}/results`));
});

test("a comparison opened from a link needs no clicking to draw", async ({ page }) => {
  await page.goto(`/compare?a=${id("a_resolved")}&b=${id("d_floor")}`);
  await expect(page.getByTestId("compare-caption")).toBeVisible();
  await expect(page.locator('[data-figure="compare"] .main-svg').first()).toBeVisible();
});
