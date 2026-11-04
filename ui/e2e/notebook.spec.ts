import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, run, test } from "./bench";

test("a notebook entry records attached runs by the sha256 of their record", async ({ page }) => {
  const r = run("a_resolved");
  const sha = createHash("sha256").update(readFileSync(r.path)).digest("hex"); // data source: the record file itself
  await page.goto("/bench");
  await page.getByLabel("Campaign name").fill(`nb-${Date.now()}`);
  await page.getByRole("button", { name: "Start notebook" }).click();
  await expect(page).toHaveURL(/\/campaigns\//);
  await page.getByLabel("Title", { exact: true }).fill("First pass");
  await page.getByLabel("Entry", { exact: true }).fill("Ran the funnel on the fixture.");
  await page.getByRole("checkbox").nth(r.idx).check();
  await page.getByRole("button", { name: "Add entry" }).click();
  await expect(page.locator(`[data-artefact="${sha}"]`)).toBeVisible();
});

test("a correction supersedes; the old entry stays readable and marked; nothing offers an edit", async ({ page }) => {
  await page.goto("/bench");
  await page.getByLabel("Campaign name").fill(`nb2-${Date.now()}`);
  await page.getByRole("button", { name: "Start notebook" }).click();
  await page.getByLabel("Title", { exact: true }).fill("Floor is 0.5");
  await page.getByLabel("Entry", { exact: true }).fill("Original claim.");
  await page.getByRole("button", { name: "Add entry" }).click();
  await page.getByRole("button", { name: "Write a correction to Floor is 0.5" }).click();
  await page.getByLabel("Title", { exact: true }).fill("Floor is 1.0");
  await page.getByLabel("Entry", { exact: true }).fill("Corrected.");
  await page.getByRole("button", { name: "Add correction" }).click();
  const old = page.locator(".entry", { hasText: "Original claim." });
  await expect(old).toHaveClass(/superseded/);
  await expect(old).toContainText("superseded by");
  await expect(old.locator(".entry-body")).toHaveText("Original claim.");
  await expect(page.getByRole("button", { name: /edit/i })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: /edit/i })).toHaveCount(0);
});

test("axe finds no serious or critical violations on a notebook with entries", async ({ page }) => {
  const { default: AxeBuilder } = await import("@axe-core/playwright");
  const c = (await (await page.request.post("/api/campaigns", { data: { name: `a11y-${Date.now()}` } })).json()) as { id: string };
  await page.request.post(`/api/campaigns/${c.id}/entries`, { data: { title: "one", body: "b", runs: [0] } });
  await page.goto(`/campaigns/${c.id}`);
  await expect(page.locator(".entry")).toHaveCount(1);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
});
