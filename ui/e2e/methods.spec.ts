import type { Page } from "@playwright/test";
import { expect, test } from "./bench";

type Method = {
  key: string;
  label: string;
  cost_model: string;
  floor: number | null;
  params: { name: string; kind: string; help: string; workspace_default: unknown }[];
};

async function methods(page: Page): Promise<Method[]> {
  return (await (await page.request.get("/api/methods")).json()) as Method[]; // data source: the API's own list
}

// The server is shared by every spec: leave the workspace without defaults.
test.afterEach(async ({ page }) => {
  for (const m of await methods(page)) {
    if (m.params.some((p) => p.workspace_default !== null)) await page.request.put(`/api/methods/${m.key}`, { data: { defaults: {} } });
  }
});

test("every method has a section and every setting says what it does", async ({ page }) => {
  const ms = await methods(page);
  await page.goto("/methods");
  await expect(page.getByRole("heading", { name: "Methods", level: 1 })).toBeVisible();
  for (const m of ms) {
    const sec = page.getByRole("region", { name: `Method ${m.key}` });
    await expect(sec.getByRole("heading", { name: m.label })).toBeVisible();
    await expect(sec.locator("[data-param]")).toHaveCount(m.params.length);
    for (const p of m.params) {
      expect(p.help.trim(), `${m.key}.${p.name}`).not.toBe("");
      await expect(sec.locator(`[data-param="${p.name}"] [data-cell="help"]`)).toHaveText(p.help);
    }
  }
});

test("a model that cannot say shows 'cannot say', never a number", async ({ page }) => {
  await page.goto("/methods");
  const docking = page.getByRole("region", { name: "Method docking" });
  await expect(docking.locator('[data-fact="cost"]')).toContainText("cannot say");
  const floor = docking.locator('[data-fact="floor"]');
  await expect(floor).toContainText("unmeasured");
  await expect(floor).not.toContainText(/\d/);
});

test("a workspace default is saved, survives a reload, and can be cleared", async ({ page }) => {
  await page.goto("/methods");
  const sec = page.getByRole("region", { name: "Method field_dft_interaction" });
  const box = sec.getByLabel("field_dft_interaction basis workspace default");
  await box.fill("def2-svp");
  await sec.getByRole("button", { name: /Save defaults/ }).click();
  await expect.poll(async () => (await methods(page)).find((m) => m.key === "field_dft_interaction")!.params.find((p) => p.name === "basis")!.workspace_default).toBe("def2-svp");

  await page.reload();
  const again = page.getByRole("region", { name: "Method field_dft_interaction" }).getByLabel("field_dft_interaction basis workspace default");
  await expect(again).toHaveValue("def2-svp");
  // the code's own default is still shown beside it
  await expect(page.locator('[data-method="field_dft_interaction"] [data-param="basis"] [data-cell="default"]')).toHaveText("sto-3g");

  await again.fill("");
  await page.getByRole("region", { name: "Method field_dft_interaction" }).getByRole("button", { name: /Save defaults/ }).click();
  await expect.poll(async () => (await methods(page)).find((m) => m.key === "field_dft_interaction")!.params.find((p) => p.name === "basis")!.workspace_default).toBe(null);
});

test("a default the setting would refuse says why and stores nothing", async ({ page }) => {
  await page.goto("/methods");
  const sec = page.getByRole("region", { name: "Method cut" });
  await sec.getByLabel("cut z workspace default").fill("wide");
  await sec.getByRole("button", { name: /Save defaults/ }).click();
  await expect(sec.getByRole("alert")).toContainText("z must be a number");
  expect((await methods(page)).find((m) => m.key === "cut")!.params.every((p) => p.workspace_default === null)).toBe(true);
});
