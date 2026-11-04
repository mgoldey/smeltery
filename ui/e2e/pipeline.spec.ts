import type { Page } from "@playwright/test";
import { expect, IDX, test } from "./bench";

type Catalog = {
  rungs: { name: string; methods: string[] }[];
  methods: Record<string, { key: string; label: string; wired: boolean; why_unwired: string; quantity: string | null; implicit: boolean }>;
};

async function catalog(page: Page): Promise<Catalog> {
  return (await (await page.request.get("/api/catalog")).json()) as Catalog;
}

async function openEditor(page: Page) {
  await page.goto(`/run/${IDX.a_resolved}/pipeline`);
  await expect(page.getByRole("region", { name: "Stages" }).locator("[data-stage]").first()).toBeVisible();
}

async function replaceToml(page: Page, text: string) {
  const pane = page.getByRole("textbox", { name: "Pipeline TOML" });
  await pane.click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.press("Delete");
  await page.keyboard.insertText(text);
}

test("the palette groups methods by rung, and an unwired tier shows why it cannot be added", async ({ page }) => {
  const cat = await catalog(page);
  await openEditor(page);
  for (const r of cat.rungs.filter((r) => r.methods.some((k) => !cat.methods[k]?.implicit))) {
    await expect(page.getByRole("region", { name: `Rung ${r.name}` })).toBeVisible();
  }
  const qmmm = cat.methods["qmmm"]!;
  expect(qmmm.wired).toBe(false);
  const row = page.locator('[data-method="qmmm"]');
  await expect(row).toContainText(qmmm.why_unwired);
  await expect(row.getByRole("button")).toHaveCount(0);
});

test("no palette group mixes quantities: rigour is never one ladder across quantities", async ({ page }) => {
  const cat = await catalog(page); // data source: each method's quantity, as the catalog reports it
  await openEditor(page);
  const groups = page.locator(".quantity-group");
  const n = await groups.count();
  expect(n).toBeGreaterThan(0);
  for (let i = 0; i < n; i++) {
    const g = groups.nth(i);
    const q = (await g.getAttribute("data-quantity")) ?? "";
    for (const key of await g.locator("[data-method]").evaluateAll((els) => els.map((e) => e.getAttribute("data-method")))) {
      expect(cat.methods[key!]!.quantity ?? "", `${key} in group ${q}`).toBe(q);
    }
  }
});

test("a stage is added and removed with the keyboard alone, and the TOML follows", async ({ page }) => {
  const cat = await catalog(page);
  const label = cat.methods["gfn2"]!.label; // the TOML gate `xtb` is the gfn2 stage
  await openEditor(page);
  const add = page.getByRole("button", { name: `Add ${label}` });
  await add.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-stage="gfn2"]')).toBeVisible();
  await expect(page.getByTestId("toml-pane")).toContainText("xtb = true");
  const remove = page.getByRole("button", { name: `Remove ${label}` });
  await remove.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-stage="gfn2"]')).toHaveCount(0);
  await expect(page.getByTestId("toml-pane")).not.toContainText("xtb = true");
});

test("a knob edit updates the TOML pane", async ({ page }) => {
  await openEditor(page);
  await page.getByRole("button", { name: /Edit Paired poses knobs/ }).click();
  const n = page.getByLabel("n_poses");
  await n.fill("8");
  await n.press("Enter");
  await expect(page.getByTestId("toml-pane")).toContainText("n_poses = 8");
});

test("editing the TOML updates the stages, and every issue is listed at once", async ({ page }) => {
  await openEditor(page);
  const original = (await page.getByTestId("toml-pane").innerText()).trim();
  expect(original).toContain("[cut]");
  // Two independent errors in two stages, plus a gate the visual list must pick up.
  await replaceToml(
    page,
    [
      'campaign = "e2e"',
      'parent = "benzoic"',
      "",
      "[candidates]",
      'benzoic = "OC(=O)c1ccccc1"',
      '4-F = "OC(=O)c1ccc(F)cc1"',
      "",
      "[poses]",
      'n_poses = "lots"',
      "",
      "[[pocket.site]]",
      "q = 1.0",
      "atom = 1",
      "from_atom = 2",
      "distance = 3.0",
      "",
      "[cut]",
      'keep = "many"',
      "floor = 0.0",
      "",
      "[gates]",
      "xtb = true",
    ].join("\n"),
  );
  await expect(page.locator('[data-stage="gfn2"]')).toBeVisible();
  const errors = page.getByRole("region", { name: "Issues" }).locator('.issue[data-level="error"]');
  await expect.poll(() => errors.count()).toBeGreaterThanOrEqual(2);
  await expect(errors.filter({ hasText: "keep" })).toHaveCount(1);
  await expect(errors.filter({ hasText: "n_poses" })).toHaveCount(1);
});

test("switching the measurement from RHF to KS-DFT: remove RHF, add PBE, and the TOML follows", async ({ page }) => {
  const cat = await catalog(page); // data source: the labels the catalog gives the two tiers
  const rhf = cat.methods["field_interaction"]!.label;
  const pbe = cat.methods["field_dft_interaction"]!.label;
  await openEditor(page);
  const stages = page.getByRole("region", { name: "Stages" });
  const toml = page.getByTestId("toml-pane");
  await expect(page.getByRole("button", { name: `Add ${pbe}` })).toBeDisabled(); // not while RHF is placed
  await page.getByRole("button", { name: `Remove ${rhf}` }).click();
  await expect(stages.locator('[data-stage="field_interaction"]')).toHaveCount(0);
  // with no measurement, the issue names both choices
  await expect(page.getByRole("region", { name: "Issues" })).toContainText("'field_dft_interaction'");
  await page.getByRole("button", { name: `Add ${pbe}` }).click();
  await expect(stages.locator('[data-stage="field_dft_interaction"]')).toHaveCount(1);
  await expect(toml).toContainText("[field_dft_interaction]");
  await expect(toml).not.toContainText("[field_interaction]");
  // the PBE knobs are editable like any stage's
  await page.getByRole("button", { name: `Edit ${pbe} knobs` }).click();
  await expect(page.getByLabel("functional", { exact: false }).first()).toBeVisible();
});
