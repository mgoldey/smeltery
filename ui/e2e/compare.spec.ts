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
