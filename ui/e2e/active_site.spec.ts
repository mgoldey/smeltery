import type { Page } from "@playwright/test";
import { expect, fixture, newPipeline, openWorkspace, test } from "./bench";

const SITE =
  "\n[[pocket.site]]\natom = 1\nfrom_atom = 0\nq = 1.0\ndistance = 3.0\n";
const siteCount = (t: string) =>
  (t.match(/\[\[pocket\.site\]\]/g) ?? []).length;

/** Click "Use as active site", answering the drop-charges confirmation when the fixture pipeline has sites. */
async function useStructure(page: Page) {
  const view = page.locator('[data-view="active_site"]');
  await view.getByRole("button", { name: "Use as active site" }).click();
  const yes = view.getByRole("button", { name: /^Drop \d+ and switch$/ });
  if (await yes.isVisible({ timeout: 500 }).catch(() => false))
    await yes.click();
}

const toml = (page: import("@playwright/test").Page) =>
  page.getByRole("textbox", { name: "Pipeline TOML" });

test("hand-placed to structure: the TOML gains [structure] and [docking], and the preview counts the field", async ({
  page,
}) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "active_site");
  const view = page.locator('[data-view="active_site"]');
  await expect(view).toHaveAttribute("data-choice-now", "hand_placed");

  await view.getByLabel("A protein structure").check();
  await view
    .getByLabel("Path, PDB id or UniProt accession")
    .fill(fixture().pdb);
  await view.getByRole("button", { name: "Open structure" }).click();
  await expect(view.getByTestId("active-site-sha")).toBeVisible();
  await view.getByLabel("Reference ligand").selectOption("LIG");
  await view.getByLabel("Pocket cutoff (Å)").fill("12");
  await useStructure(page);

  await expect(view).toHaveAttribute("data-choice-now", "structure");
  await expect(toml(page)).toContainText("[structure]");
  await expect(toml(page)).toContainText("reference_ligand");
  await expect(toml(page)).toContainText("[docking]");

  // Data source: the preview route itself, asked for the same draft text.
  const text = await toml(page).innerText();
  const want = (await (
    await page.request.post("/api/pipelines/pocket_preview", {
      data: { toml: text, pipeline: name },
    })
  ).json()) as {
    n_charges: number;
  };
  await expect(view.getByTestId("n-charges")).toHaveText(
    String(want.n_charges),
  );
});

test("a preview that cannot run says what is missing instead of a number", async ({
  page,
}) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "active_site");
  await page.route("**/api/pipelines/pocket_preview", (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ reason: "`pdb2pqr30` is not on PATH" }),
    }),
  );
  const view = page.locator('[data-view="active_site"]');
  await view.getByLabel("A protein structure").check();
  await view
    .getByLabel("Path, PDB id or UniProt accession")
    .fill(fixture().pdb);
  await view.getByRole("button", { name: "Open structure" }).click();
  await view.getByLabel("Reference ligand").selectOption("LIG");
  await useStructure(page);
  await expect(view.getByTestId("preview-error")).toContainText("pdb2pqr30");
});

test("switching back to hand-placed removes [structure] and [docking]", async ({
  page,
}) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "active_site");
  const view = page.locator('[data-view="active_site"]');
  await view.getByLabel("A protein structure").check();
  await view
    .getByLabel("Path, PDB id or UniProt accession")
    .fill(fixture().pdb);
  await view.getByRole("button", { name: "Open structure" }).click();
  await view.getByLabel("Reference ligand").selectOption("LIG");
  await useStructure(page);
  await expect(view).toHaveAttribute("data-choice-now", "structure");
  await view.getByLabel("Hand-placed charges").check();
  await view.getByRole("button", { name: "Use hand-placed charges" }).click();
  await expect(view).toHaveAttribute("data-choice-now", "hand_placed");
  await expect(toml(page)).not.toContainText("[structure]");
  await expect(toml(page)).not.toContainText("[docking]");
});

test("switching away from hand-placed charges asks first, says how many, and Cancel keeps them", async ({
  page,
}) => {
  const { name, toml: kept } = await newPipeline(page, (t) => t + SITE);
  const n = siteCount(kept);
  expect(n).toBeGreaterThan(0);
  await openWorkspace(page, name, "active_site");
  const view = page.locator('[data-view="active_site"]');
  await view.getByLabel("A protein structure").check();
  await view
    .getByLabel("Path, PDB id or UniProt accession")
    .fill(fixture().pdb);
  await view.getByRole("button", { name: "Open structure" }).click();
  await view.getByLabel("Reference ligand").selectOption("LIG");
  await view.getByRole("button", { name: "Use as active site" }).click();

  const ask = view.getByTestId("confirm-switch");
  await expect(ask).toContainText(`${n} hand-placed charge`);
  await expect(toml(page)).not.toContainText("[structure]"); // nothing written yet
  await ask.getByRole("button", { name: "Cancel" }).click();
  await expect(ask).toHaveCount(0);
  expect(siteCount(await toml(page).innerText())).toBe(n);

  await view.getByRole("button", { name: "Use as active site" }).click();
  await ask.getByRole("button", { name: `Drop ${n} and switch` }).click();
  await expect(toml(page)).toContainText("[structure]");
  expect(siteCount(await toml(page).innerText())).toBe(0);
});
