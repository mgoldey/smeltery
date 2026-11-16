import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "./bench";

/** Data source: the run record committed with the prebuilt pipeline, read from the repository, not through the API. */
const RUN = resolve(dirname(fileURLToPath(import.meta.url)), "../../src/smeltery/prebuilt/benzoic-pbe/run");
type Rec = { results: { per_pose: Record<string, { dE_int: number[] }> } };
const committed = (): Rec => {
  const f = readdirSync(RUN).find((n) => /^run-.*\.json$/.test(n));
  return JSON.parse(readFileSync(resolve(RUN, f!), "utf8")) as Rec;
};

test("start from benzoic-pbe: every step is ready and step 5 shows the committed numbers", async ({ page }) => {
  const name = `pbe-${Date.now().toString(36)}`;
  await page.goto("/bench");
  const card = page.getByRole("group", { name: "Examples you can copy" });
  // Data source: the API. One card per non-variant tier, and each variant folded under the example it varies —
  // not one row per prebuilt, which is what made five alphabetical peers out of one example and four others.
  const listed = (await (await page.request.get("/api/prebuilt")).json()) as { tier: string }[];
  const variants = listed.filter((p) => p.tier === "variant");
  await expect(card.locator("[data-prebuilt]")).toHaveCount(listed.length - variants.length);
  await expect(card.locator("[data-prebuilt-variant]")).toHaveCount(variants.length);
  // benzoic-rhf is THE example: its card comes first and carries the badge.
  await expect(card.locator("[data-prebuilt]").first()).toHaveAttribute("data-prebuilt", "benzoic-rhf");
  await expect(card.locator('[data-prebuilt="benzoic-rhf"] .start-best')).toHaveText("easiest");
  const row = card.locator('[data-prebuilt-variant="benzoic-pbe"]');
  await expect(row).toContainText("Resolves to the same winner by a different method");
  await expect(card.locator('[data-prebuilt="docking"]')).toContainText("Does not resolve");
  await row.getByLabel("Name of your copy").fill(name);
  await row.getByRole("button", { name: "Start from benzoic-pbe" }).click();

  await expect(page).toHaveURL(new RegExp(`/pipelines/${name}\\?run=[0-9a-f]+-[0-9a-f]+&step=measure`));
  const steps = page.getByRole("navigation", { name: "Pipeline steps" }).locator("[data-step]");
  await expect(steps).toHaveCount(6);
  for (const s of await steps.all()) {
    const state = await s.getAttribute("data-state");
    expect(["ready", "off"], `step ${await s.getAttribute("data-step")}`).toContain(state);
  }
  await expect(page.locator('[data-step="measure"]')).toHaveAttribute("data-state", "ready");

  const fig = page.locator('[data-figure="per_pose_values"]');
  await expect(fig.locator(".scatterlayer .trace")).not.toHaveCount(0);
  const plotted = await fig.evaluate((gd) =>
    Object.fromEntries((gd as unknown as { data: { name: string; y: number[] }[] }).data.map((t) => [t.name, t.y])),
  );
  const rec = committed().results.per_pose;
  for (const cand of Object.keys(rec)) {
    const want = rec[cand]!.dE_int;
    const key = cand in plotted ? cand : `${cand} (parent)`;
    expect(plotted[key], `${cand} is plotted`).toBeDefined();
    plotted[key]!.forEach((y, i) => expect(y).toBeCloseTo(want[i]!, 6));
  }
});
