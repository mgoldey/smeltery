import { expect, fixture, newPipeline, openWorkspace, test } from "./bench";

const toml = (page: import("@playwright/test").Page) => page.getByRole("textbox", { name: "Pipeline TOML" });

test("hand-placed to structure: the TOML gains [structure] and [docking], and the preview counts the field", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "active_site");
  const view = page.locator('[data-view="active_site"]');
  await expect(view).toHaveAttribute("data-choice-now", "hand_placed");

  await view.getByLabel("Path, PDB id or UniProt accession").fill(fixture().pdb);
  await view.getByRole("button", { name: "Open structure" }).click();
  await expect(view.getByTestId("active-site-sha")).toBeVisible();
  await view.getByLabel("Reference ligand").selectOption("LIG");
  await view.getByLabel("Pocket cutoff (Å)").fill("12");
  await view.getByRole("button", { name: "Use as active site" }).click();

  await expect(view).toHaveAttribute("data-choice-now", "structure");
  await expect(toml(page)).toContainText("[structure]");
  await expect(toml(page)).toContainText("reference_ligand");
  await expect(toml(page)).toContainText("[docking]");

  // Data source: the preview route itself, asked for the same draft text.
  const text = await toml(page).innerText();
  const want = (await (await page.request.post("/api/pipelines/pocket_preview", { data: { toml: text, pipeline: name } })).json()) as {
    n_charges: number;
  };
  await expect(view.getByTestId("n-charges")).toHaveText(String(want.n_charges));
});

test("a preview that cannot run says what is missing instead of a number", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "active_site");
  await page.route("**/api/pipelines/pocket_preview", (route) =>
    route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "`pdb2pqr30` is not on PATH" }) }),
  );
  const view = page.locator('[data-view="active_site"]');
  await view.getByLabel("Path, PDB id or UniProt accession").fill(fixture().pdb);
  await view.getByRole("button", { name: "Open structure" }).click();
  await view.getByLabel("Reference ligand").selectOption("LIG");
  await view.getByRole("button", { name: "Use as active site" }).click();
  await expect(view.getByTestId("preview-error")).toContainText("pdb2pqr30");
});

test("switching back to hand-placed removes [structure] and [docking]", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "active_site");
  const view = page.locator('[data-view="active_site"]');
  await view.getByLabel("Path, PDB id or UniProt accession").fill(fixture().pdb);
  await view.getByRole("button", { name: "Open structure" }).click();
  await view.getByLabel("Reference ligand").selectOption("LIG");
  await view.getByRole("button", { name: "Use as active site" }).click();
  await expect(view).toHaveAttribute("data-choice-now", "structure");
  await view.getByLabel("Hand-placed charges").check();
  await expect(view).toHaveAttribute("data-choice-now", "hand_placed");
  await expect(toml(page)).not.toContainText("[structure]");
  await expect(toml(page)).not.toContainText("[docking]");
});
