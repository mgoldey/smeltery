import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "./bench";
import { routes } from "./routes";

for (const path of routes()) {
  test(`axe finds no serious or critical violations on ${path}`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    const bad = result.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`);
    expect(bad).toEqual([]);
  });
}

test("dark mode redefines the tokens", async ({ page }) => {
  const ground = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--ground").trim().toLowerCase());
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/bench");
  expect(await ground()).toBe("#f7f8f8");
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await ground()).toBe("#15181a");
});

test("every numeric cell uses tabular figures", async ({ page }) => {
  for (const path of routes()) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const styles = await page
      .locator(".num")
      .evaluateAll((els) => els.map((e) => getComputedStyle(e).fontVariantNumeric));
    for (const s of styles) expect(s, path).toContain("tabular-nums");
  }
});
