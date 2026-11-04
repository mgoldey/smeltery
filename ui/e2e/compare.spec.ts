import { expect, IDX, test } from "./bench";

test("choosing two runs draws the comparison figure and the API's caption", async ({ page }) => {
  const want = (await (await page.request.get(`/api/compare?a=${IDX.a_resolved}&b=${IDX.d_floor}`)).json()) as { caption: string };
  await page.goto("/compare");
  await page.getByLabel("Arm A").selectOption(String(IDX.a_resolved));
  await page.getByLabel("Arm B").selectOption(String(IDX.d_floor));
  await expect(page.getByTestId("compare-caption")).toHaveText(want.caption);
  await expect(page.locator('[data-figure="compare"] .main-svg').first()).toBeVisible();
});

test("the same run in both arms is refused, and nothing is drawn", async ({ page }) => {
  await page.goto("/compare");
  await page.getByLabel("Arm A").selectOption(String(IDX.a_resolved));
  await page.getByLabel("Arm B").selectOption(String(IDX.a_resolved));
  await expect(page.getByRole("alert")).toContainText("both arms");
  await expect(page.locator('[data-figure="compare"]')).toHaveCount(0);
});
