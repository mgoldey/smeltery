import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, newPipeline, openWorkspace, test } from "./bench";

/** Data source: a pocket PDB committed with the prebuilt docking pipeline. */
const PDB = resolve(dirname(fileURLToPath(import.meta.url)), "../../src/smeltery/prebuilt/docking/pocket_pep5.pdb");

test("a PDB chosen from this computer is copied into the pipeline and named in the config", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "active_site", { unfoldToml: false });
  const view = page.locator('[data-view="active_site"]');
  await view.getByRole("radio", { name: "A protein structure" }).check();

  await view.getByLabel("a PDB file: choose a file").setInputFiles(PDB);
  // The file lands in the pipeline folder and the field names it by its plain name, not the path it came from.
  await expect(view.getByLabel(/^or a path/)).toHaveValue("pocket_pep5.pdb");
  const folder = (await (await page.request.get(`/api/pipelines/${name}`)).json()).folder as string;
  expect(readFileSync(join(folder, "pocket_pep5.pdb"), "utf8")).toBe(readFileSync(PDB, "utf8"));
  // It opened straight away: the view shows the file's own sha256.
  await expect(view.getByTestId("active-site-sha")).toBeVisible();

  // A second pipeline offers the file it already holds, instead of only an upload.
  await expect(view.getByLabel("a PDB file: a file already in this pipeline")).toContainText("pocket_pep5.pdb");
});

test("the Method column offers a picker exactly where a setting names a file", async ({ page }) => {
  const { name } = await newPipeline(page, (t) => `${t}\n[structure]\nspec = "x.pdb"\npocket_cutoff = 8.0\ncenter = [0.0, 0.0, 0.0]\n`);
  await openWorkspace(page, name, "active_site", { unfoldToml: false });
  const method = page.getByRole("region", { name: "Method" });
  await expect(method.locator('[data-param="spec"]').getByRole("button", { name: /Choose file/ })).toBeVisible();
  await expect(method.locator('[data-param="pocket_cutoff"]').getByRole("button", { name: /Choose file/ })).toHaveCount(0);

  await method.locator('[data-param="spec"]').getByLabel("file: choose a file").setInputFiles(PDB);
  await expect(method.locator('[data-param="spec"]').getByRole("textbox")).toHaveValue("pocket_pep5.pdb");
  const kept = (await (await page.request.get(`/api/pipelines/${name}`)).json()).toml as string;
  expect(kept).not.toContain("pocket_pep5.pdb"); // an edit is a draft until Keep
});

test("the structure field starts from what the config already names", async ({ page }) => {
  const { name } = await newPipeline(page, (t) => `${t}\n[structure]\nspec = "already.pdb"\npocket_cutoff = 9.5\ncenter = [0.0, 0.0, 0.0]\n`);
  await openWorkspace(page, name, "active_site", { unfoldToml: false });
  const view = page.locator('[data-view="active_site"]');
  // Seeded from [structure] spec, so the View and the Method column name one structure, not two.
  await expect(view.getByLabel(/^or a path/)).toHaveValue("already.pdb");
  await expect(page.getByRole("region", { name: "Method" }).locator('[data-param="spec"]').getByRole("textbox")).toHaveValue(
    "already.pdb",
  );
});

test("the home page leads with one numbered way to start", async ({ page }) => {
  await page.goto("/bench");
  const start = page.getByRole("region", { name: "Start here" });
  await expect(start.getByRole("heading", { level: 3 })).toHaveCount(3);
  await expect(start.locator('[data-way="prebuilt"]')).toContainText("easiest");
  // "Start here" comes before the work you already have.
  const order = await page.locator("h1, h2").allInnerTexts();
  expect(order.indexOf("Start here")).toBeLessThan(order.indexOf("Your work"));
});

test("picking a file in the Method column updates the View's field, so one structure is named once", async ({ page }) => {
  const { name } = await newPipeline(page, (t) => `${t}\n[structure]\nspec = "already.pdb"\npocket_cutoff = 9.5\ncenter = [0.0, 0.0, 0.0]\n`);
  await openWorkspace(page, name, "active_site", { unfoldToml: false });
  const view = page.locator('[data-view="active_site"]');
  await expect(view.getByLabel(/^or a path/)).toHaveValue("already.pdb");

  // The Method column writes [structure] spec; the View must follow it rather than keep the value it mounted with.
  await page.getByRole("region", { name: "Method" }).locator('[data-param="spec"]').getByLabel("file: choose a file").setInputFiles(PDB);
  await expect(view.getByLabel(/^or a path/)).toHaveValue("pocket_pep5.pdb");
});
