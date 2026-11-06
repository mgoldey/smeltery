import type { Page } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, fixture, IDX, newPipeline, openWorkspace, replaceToml, test } from "./bench";

type Doc = { toml: string; sha256: string; ok: boolean; issues: { level: string; stage: string | null; message: string }[] };
type Catalog = { methods: Record<string, { key: string; label: string }> };

async function doc(page: Page, name: string): Promise<Doc> {
  return (await (await page.request.get(`/api/pipelines/${name}`)).json()) as Doc;
}

async function catalog(page: Page): Promise<Catalog> {
  return (await (await page.request.get("/api/catalog")).json()) as Catalog;
}

/** The run's pipeline with an explicit, commented n_poses, so a Keep can be compared line by line. */
const withPoses = (t: string) => t.replace("\n[pocket]", "\n[poses]\nn_poses = 6  # poses per ligand\n\n[pocket]");

const step = (page: Page, key: string) => page.getByRole("navigation", { name: "Pipeline steps" }).locator(`[data-step="${key}"]`);

async function setParam(page: Page, name: string, value: string) {
  const input = page.locator(`[data-param="${name}"]`).getByRole("textbox");
  await input.fill(value);
  await input.press("Enter");
}

test("edit n_poses in place: old value shown, later steps marked, Keep writes one diff line", async ({ page }) => {
  const { name, toml: before } = await newPipeline(page, withPoses);
  await openWorkspace(page, name, "poses");
  await setParam(page, "n_poses", "8");

  const row = page.locator('[data-param="n_poses"]');
  await expect(row).toHaveAttribute("data-changed", "true");
  await expect(row.getByTestId("was")).toContainText("was 6");
  await expect(step(page, "poses")).toHaveAttribute("data-state", "changed");
  await expect(step(page, "measure")).toHaveAttribute("data-state", "stale");
  await expect(step(page, "cut")).toHaveAttribute("data-state", "stale");
  const diff = page.getByTestId("config-diff");
  await expect(diff.locator('[data-sign="-"]')).toHaveText(["- n_poses = 6  # poses per ligand"]);
  await expect(diff.locator('[data-sign="+"]')).toHaveText(["+ n_poses = 8  # poses per ligand"]);
  // the draft is not saved until Keep
  expect((await doc(page, name)).toml).toBe(before);

  await page.getByRole("region", { name: "Impact" }).getByRole("button", { name: "Keep" }).click();
  await expect(step(page, "poses")).not.toHaveAttribute("data-state", "changed");
  // Data source: the saved file over the API. It differs from before in exactly one line, comment kept.
  const after = (await doc(page, name)).toml;
  const a = before.split("\n");
  const b = after.split("\n");
  expect(b.length).toBe(a.length);
  const changed = a.map((line, i) => [line, b[i]!] as const).filter(([x, y]) => x !== y);
  expect(changed).toEqual([["n_poses = 6  # poses per ligand", "n_poses = 8  # poses per ligand"]]);
});

test("a draft with an error is saved and Run says why it is blocked", async ({ page }) => {
  // The run's record holds no pocket sites, so its pipeline has an error from the start.
  const { name } = await newPipeline(page, withPoses);
  await openWorkspace(page, name, "poses");
  await setParam(page, "n_poses", "7");
  await page.getByRole("region", { name: "Impact" }).getByRole("button", { name: "Keep" }).click();
  await expect(page.getByRole("region", { name: "Impact" })).toContainText("No edits to keep");

  const kept = await doc(page, name);
  expect(kept.toml).toContain("n_poses = 7");
  const errors = kept.issues.filter((i) => i.level === "error");
  expect(kept.ok).toBe(false);
  expect(errors.length).toBeGreaterThan(0);
  await expect(page.getByRole("button", { name: "Run pipeline" })).toBeDisabled();
  const why = page.getByTestId("run-blocked");
  await expect(why).toContainText(`blocked by ${errors.length} error`);
  for (const e of errors) await expect(why).toContainText(e.message);
});

test("unparseable TOML in the pane blocks a step edit with the line number", async ({ page }) => {
  const { name, toml } = await newPipeline(page, withPoses);
  await openWorkspace(page, name, "poses");
  const broken = `${toml.trimEnd()}\nbad = = 1\n`;
  const line = broken.split("\n").indexOf("bad = = 1") + 1;
  await replaceToml(page, broken);
  await expect(page.getByTestId("toml-error")).toContainText(`line ${line}`);
  await expect(page.locator('[data-param="n_poses"]').getByRole("textbox")).toBeDisabled();
  // nothing was saved
  expect((await doc(page, name)).toml).toBe(toml);
});

test("a step whose value differs from the workspace default says so", async ({ page }) => {
  const put = (defaults: Record<string, unknown>) => page.request.put("/api/methods/paired_poses", { data: { defaults } });
  expect((await put({ n_poses: 12 })).status()).toBe(200);
  try {
    const { name } = await newPipeline(page, withPoses);
    await openWorkspace(page, name, "poses");
    await expect(page.locator('[data-param="n_poses"]').getByTestId("workspace-default")).toHaveText("workspace default 12");
    await expect(page.locator('[data-param="seed"]').getByTestId("workspace-default")).toHaveCount(0);
  } finally {
    await put({});
  }
});

test("a check is added and removed with the keyboard alone, and the TOML follows", async ({ page }) => {
  const label = (await catalog(page)).methods["gfn2"]!.label; // the TOML gate `xtb` is the gfn2 method
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "checks");
  const pane = page.getByTestId("toml-pane");
  const add = page.getByRole("button", { name: `Add ${label}` });
  await add.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-method="gfn2"]')).toBeVisible();
  await expect(pane).toContainText("xtb = true");
  await expect(step(page, "checks")).toHaveAttribute("data-state", "changed");
  const remove = page.getByRole("button", { name: `Remove ${label}` });
  await remove.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-method="gfn2"]')).toHaveCount(0);
  await expect(pane).not.toContainText("xtb = true");
});

test("editing the TOML redraws the steps, and every issue shows on its own step", async ({ page }) => {
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "poses");
  // Two independent errors in two steps, plus a gate the drawing must pick up.
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
  await expect(step(page, "checks")).not.toHaveAttribute("data-state", "off");
  // both steps need input at once
  await expect(step(page, "poses")).toHaveAttribute("data-state", "needs_input");
  await expect(step(page, "cut")).toHaveAttribute("data-state", "needs_input");
  const errors = (key: string) => page.locator(`[data-panel="${key}"]`).locator('.issue[data-level="error"]');
  await expect(errors("poses").filter({ hasText: "n_poses" })).toHaveCount(1);
  await step(page, "cut").click();
  await expect(errors("cut").filter({ hasText: "keep" })).toHaveCount(1);
});

test("switching the measurement from RHF to KS-DFT: one click, and the TOML follows", async ({ page }) => {
  const cat = await catalog(page); // data source: the labels the catalog gives the two tiers
  const rhf = cat.methods["field_interaction"]!.label;
  const pbe = cat.methods["field_dft_interaction"]!.label;
  const { name } = await newPipeline(page);
  await openWorkspace(page, name, "measure");
  const pane = page.getByTestId("toml-pane");
  const panel = page.locator('[data-panel="measure"]');
  await expect(panel.locator('[data-method="field_interaction"]')).toBeVisible();
  // RHF is required unless replaced: it has no Remove, only the alternative's Use
  await expect(page.getByRole("button", { name: `Remove ${rhf}` })).toHaveCount(0);
  await page.getByRole("button", { name: `Use ${pbe}` }).click();
  await expect(panel.locator('[data-method="field_dft_interaction"]')).toBeVisible();
  await expect(panel.locator('[data-method="field_interaction"]')).toHaveCount(0);
  await expect(pane).toContainText("[field_dft_interaction]");
  await expect(pane).not.toContainText("[field_interaction]");
  await expect(step(page, "measure")).toHaveAttribute("data-state", "changed");
  // the PBE knobs are editable like any step's
  await expect(panel.locator('[data-param="functional"]').getByRole("textbox")).toBeEditable();
  await expect(page.getByRole("button", { name: `Use ${rhf}` })).toBeVisible();
});

test("a run's Pipeline tab opens the workspace on the pipeline made from that run", async ({ page }) => {
  await page.goto(`/run/${IDX.a_resolved}/pipeline`);
  await expect(page).toHaveURL(new RegExp(`/pipelines/[a-z0-9_-]+\\?run=${IDX.a_resolved}`));
  const name = new URL(page.url()).pathname.split("/").pop()!;
  expect((await page.request.get(`/api/pipelines/${name}`)).status()).toBe(200);
  await expect(page.getByRole("navigation", { name: "Pipeline steps" }).locator("[data-step]")).toHaveCount(6);
  // opening it again reopens the same pipeline rather than failing on the taken name
  await page.goto(`/run/${IDX.a_resolved}/pipeline`);
  await expect(page).toHaveURL(new RegExp(`/pipelines/${name}\\?run=`));
});

test("Discard puts back only the selected step; the other step's edit stays in the draft", async ({ page }) => {
  const { name, toml } = await newPipeline(page, withPoses);
  await openWorkspace(page, name, "poses");
  await setParam(page, "n_poses", "8");
  await expect(step(page, "poses")).toHaveAttribute("data-state", "changed");
  await step(page, "cut").click();
  await setParam(page, "z", "3");
  await expect(step(page, "cut")).toHaveAttribute("data-state", "changed");
  await step(page, "poses").click();
  await page.getByRole("button", { name: "Discard Paired poses edits" }).click();

  const pane = page.getByTestId("toml-pane");
  await expect(pane).toContainText("n_poses = 6  # poses per ligand"); // the kept line, comment and all
  await expect(page.locator('[data-param="n_poses"]').getByRole("textbox")).toHaveValue("6");
  await expect(page.locator('[data-param="n_poses"]')).not.toHaveAttribute("data-changed", "true");
  await expect(step(page, "cut")).toHaveAttribute("data-state", "changed");
  await expect(pane).toContainText("z = 3");
  // the draft was never saved
  expect((await doc(page, name)).toml).toBe(toml);
});

test("a blank pipeline starts from the home page and lands on its ligands", async ({ page }) => {
  const name = `blank-${Date.now().toString(36)}`;
  await page.goto("/bench");
  const form = page.getByRole("form", { name: "New blank pipeline" });
  await form.getByLabel("Pipeline name").fill(name);
  await form.getByLabel("Campaign").fill("acids");
  await form.getByLabel("Parent name").fill("benzoic");
  await form.getByLabel("Parent SMILES").fill("OC(=O)c1ccccc1");
  await form.getByLabel(/Analogues/).fill("4-F OC(=O)c1ccc(F)cc1\n4-Cl OC(=O)c1ccc(Cl)cc1");
  await form.getByRole("button", { name: "New blank pipeline" }).click();
  await expect(page).toHaveURL(new RegExp(`/pipelines/${name}\\?step=ligands$`));
  const inputs = page.locator('[data-panel="ligands"]').getByRole("region", { name: "Inputs" });
  await expect(inputs).toContainText("benzoic (parent), 4-F, 4-Cl");
  await expect(inputs).toContainText("campaign acids");
  // Data source: the saved config over the API holds what was entered.
  const kept = await doc(page, name);
  expect(kept.toml).toContain('campaign = "acids"');
  expect(kept.toml).toContain('4-Cl = "OC(=O)c1ccc(Cl)cc1"');
});

test("a broken methods.toml is reported as itself, not as unparseable pipeline TOML", async ({ page }) => {
  const dir = join(fixture().root, ".smeltery", "bench");
  const file = join(dir, "methods.toml");
  const before = existsSync(file) ? readFileSync(file, "utf8") : null;
  mkdirSync(dir, { recursive: true });
  writeFileSync(file, "[paired_poses\n");
  try {
    const { name } = await newPipeline(page);
    await page.goto(`/pipelines/${name}`);
    await expect(page.getByTestId("steps-error")).toContainText("methods.toml");
    await expect(page.getByTestId("toml-error")).toHaveCount(0);
  } finally {
    if (before === null) rmSync(file);
    else writeFileSync(file, before);
  }
});

test.describe("night mode", () => {
  test.use({ colorScheme: "dark" });

  test("night mode: every workspace surface uses token colours", async ({ page }) => {
    const { name } = await newPipeline(page, withPoses);
    await openWorkspace(page, name, "poses");
    await setParam(page, "n_poses", "9"); // a changed row, a stale step, a diff and the impact strip all render
    await expect(page.locator('[data-param="n_poses"]')).toHaveAttribute("data-changed", "true");
    await expect(page.getByTestId("config-diff")).toBeVisible();
    await expect(page.getByTestId("impact-memory")).toBeVisible();
    // Data source: the browser's own computed styles, against every token tokens.css defines, resolved the same way.
    const { tokens, used } = await page.evaluate(() => {
      const names = new Set<string>();
      const walk = (rules: CSSRuleList) => {
        for (const r of Array.from(rules)) {
          if (r instanceof CSSStyleRule && r.selectorText === ":root")
            for (const p of Array.from(r.style)) if (p.startsWith("--")) names.add(p);
          if ("cssRules" in r) walk((r as CSSGroupingRule).cssRules);
        }
      };
      for (const s of Array.from(document.styleSheets)) walk(s.cssRules);
      const probe = document.createElement("div");
      document.body.appendChild(probe);
      const tokens = [...names].map((n) => {
        probe.style.color = `var(${n})`;
        return getComputedStyle(probe).color;
      });
      probe.remove();
      const root = document.querySelector('[data-testid="workspace"]')!;
      // A colour counts where it is painted: text and backgrounds always, a border or outline only when drawn,
      // fill and stroke only on SVG (on HTML elements they are inherited defaults that paint nothing).
      const used: string[] = [];
      for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
        const cs = getComputedStyle(el);
        const tag = `${el.tagName.toLowerCase()}.${(el.getAttribute("class") ?? "").split(" ")[0]}`;
        const add = (p: string) => used.push(`${p} ${cs.getPropertyValue(p)} ${tag}`);
        add("color");
        add("background-color");
        for (const side of ["top", "right", "bottom", "left"])
          if (cs.getPropertyValue(`border-${side}-style`) !== "none" && cs.getPropertyValue(`border-${side}-width`) !== "0px")
            add(`border-${side}-color`);
        if (cs.outlineStyle !== "none" && cs.outlineWidth !== "0px") add("outline-color");
        if (el instanceof SVGElement)
          for (const p of ["fill", "stroke"]) {
            const v = cs.getPropertyValue(p);
            if (v !== "none" && !v.startsWith("url(")) add(p);
          }
      }
      return { tokens: [...tokens, "rgba(0, 0, 0, 0)"], used };
    });
    expect(tokens.length).toBeGreaterThan(10);
    const allowed = new Set(tokens);
    const outside = used.filter((u) => !allowed.has(u.split(" ").slice(1, -1).join(" ")));
    expect([...new Set(outside)]).toEqual([]);
  });
});

test("the selected step shows its view", async ({ page }) => {
  const { name } = await newPipeline(page, withPoses);
  await openWorkspace(page, name, "poses");
  await expect(page.locator('[data-panel="poses"] [data-view="poses"]')).toBeVisible();
  await step(page, "cut").click();
  await expect(page.locator('[data-panel="cut"] [data-view="cut"]')).toBeVisible();
});
