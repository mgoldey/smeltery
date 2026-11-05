import type { Page } from "@playwright/test";
import { expect, IDX, newPipeline, openWorkspace, test } from "./bench";

async function parentOf(page: Page) {
  return ((await (await page.request.get(`/api/runs/${IDX.a_resolved}`)).json()) as { parent: string }).parent;
}

test("the anchor: every depicted atom is the element the run's pose file has at that index", async ({ page }) => {
  // Data source: the parent's pose SDF, read by the API (pose_atoms) -- not the SMILES, which would be circular.
  const parent = await parentOf(page);
  const fromSdf = (
    (await (await page.request.get(`/api/runs/${IDX.a_resolved}/pose_atoms?candidate=${encodeURIComponent(parent)}`)).json()) as {
      symbols: string[];
    }
  ).symbols;
  await openWorkspace(page, (await newPipeline(page)).name, "active_site");
  const atoms = page.locator("[data-atom-index]");
  await expect(atoms).toHaveCount(fromSdf.length);
  const depicted = await atoms.evaluateAll((els) =>
    els.map((e) => [Number(e.getAttribute("data-atom-index")), e.getAttribute("data-element")] as const),
  );
  for (const [i, el] of depicted) expect(el, `atom ${i}`).toBe(fromSdf[i]);
});

test("clicking two atoms and confirming writes one [[pocket.site]] with those indices", async ({ page }) => {
  await openWorkspace(page, (await newPipeline(page)).name, "active_site");
  const pane = page.getByTestId("toml-pane");
  await expect(pane).toContainText("[cut]");
  const before = ((await pane.innerText()).match(/\[\[pocket\.site\]\]/g) ?? []).length;
  await page.locator('[data-atom-index="3"]').click();
  await page.locator('[data-atom-index="4"]').click();
  await expect(page.getByTestId("site-selection")).toHaveText("Charge beyond atom 3, pointing away from atom 4.");
  await page.getByLabel("Charge q (e)").fill("-1.0");
  await page.getByLabel("Distance (Å)").fill("3.5");
  await page.getByRole("button", { name: "Place site" }).click();
  await expect.poll(async () => ((await pane.innerText()).match(/\[\[pocket\.site\]\]/g) ?? []).length).toBe(before + 1);
  const text = await pane.innerText();
  const block = text.slice(text.lastIndexOf("[[pocket.site]]"));
  expect(block).toMatch(/atom = 3\b/);
  expect(block).toMatch(/from_atom = 4\b/);
  expect(block).toMatch(/q = -1\.0\b/);
  expect(block).toMatch(/distance = 3\.5\b/);
});

test("atoms are keyboard operable, and the provisional 3D view is labelled and loaded", async ({ page }) => {
  await openWorkspace(page, (await newPipeline(page)).name, "active_site");
  const a = page.locator('[data-atom-index="5"]');
  await a.focus();
  await page.keyboard.press("Enter");
  await expect(a).toHaveAttribute("aria-pressed", "true");
  const viewer = page.locator('[data-viewer="provisional geometry"]');
  await expect(viewer).toBeVisible();
  await expect.poll(async () => Number(await viewer.getAttribute("data-loaded")), { timeout: 30_000 }).toBeGreaterThanOrEqual(1);
});
