import { expect, newPipeline, openWorkspace, test } from "./bench";

const row = (page: import("@playwright/test").Page, name: string) => page.locator(`[data-candidate="${name}"]`);

test("each candidate shows its checks, and the bromo analogue names the element STO-3G lacks", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "ligands");
  await expect(page.getByRole("table", { name: "Candidates" })).toBeVisible();
  const br = row(page, "4-Br");
  await expect(br.locator('[data-check="sto3g_ok"]')).toContainText("no STO-3G for Br");
  await expect(br.locator('[data-check="electrons_even"]')).toContainText("even");
  await expect(row(page, "4-F").locator('[data-check="sto3g_ok"]')).toContainText("covered");
  // the data source for the core is the API (PairedPoses' scaffold map); the drawing carries it
  const api = await (await page.request.post("/api/pipelines/ligands", { data: { toml: (await (await page.request.get(`/api/pipelines/${name}`)).json()).toml } })).json();
  const f = api.candidates.find((c: { name: string }) => c.name === "4-F");
  await expect(row(page, "4-F").locator(".structure2d")).toHaveAttribute("data-core", f.core_atoms.join(","));
  await expect(page.getByTestId("ligands-problems")).toContainText("Br");
});

test("add, rename, change a SMILES and pick the parent write the text; removing the parent is refused", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "ligands");
  await page.getByLabel("New candidate name").fill("4-Me");
  await page.getByLabel("New candidate SMILES").fill("OC(=O)c1ccc(C)cc1");
  await page.getByRole("button", { name: "Add candidate" }).click();
  await expect(row(page, "4-Me")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Pipeline TOML" })).toContainText('"4-Me"');

  const smiles = page.getByLabel("SMILES of 4-Me");
  await smiles.fill("OC(=O)c1ccc(CC)cc1");
  await smiles.press("Enter");
  await expect(page.getByLabel("SMILES of 4-Me")).toHaveValue("OC(=O)c1ccc(CC)cc1");

  const nm = page.getByLabel("Name of 4-Me");
  await nm.fill("4-Et");
  await nm.press("Enter");
  await expect(row(page, "4-Et")).toBeVisible();

  await page.getByRole("button", { name: "Remove benzoic" }).click();
  await expect(page.getByTestId("ligands-error")).toContainText("parent");
  await expect(row(page, "benzoic")).toBeVisible();

  await row(page, "4-F").getByRole("radio").check();
  await expect(row(page, "4-F")).toHaveAttribute("data-parent", "true");
  await page.getByRole("button", { name: "Remove benzoic" }).click();
  await expect(row(page, "benzoic")).toHaveCount(0);
});
