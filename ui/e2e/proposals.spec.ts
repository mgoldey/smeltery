import { expect, IDX, test } from "./bench";

test("a proposal from the report reaches the review queue and starts no work", async ({ page }) => {
  const count = async () => ((await (await page.request.get("/api/tasks")).json()) as unknown[]).length;
  const before = await count(); // data source: the task queue itself
  const posted: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/tasks") posted.push(r.url());
  });
  await page.goto(`/report/${IDX.a_resolved}`);
  const form = page.getByRole("region", { name: "Propose a candidate" });
  await form.getByLabel("SMILES").fill("Cc1ccccc1");
  await expect(form.getByTestId("propose-preview").locator("svg")).toBeVisible(); // the 2D preview renders first
  await form.getByLabel("Reason").selectOption("synthesis");
  await form.getByLabel("Note").fill("try the methyl instead");
  await form.getByRole("button", { name: "Propose" }).click();
  await expect(form.getByRole("status")).toContainText("waiting for review");

  await page.goto(`/run/${IDX.a_resolved}/proposals`);
  await expect(page.locator('[data-proposal="Cc1ccccc1"]')).toContainText("try the methyl instead");
  expect(await count()).toBe(before);
  expect(posted).toEqual([]);
});

test("an unparseable SMILES is refused with the core's reason", async ({ page }) => {
  await page.goto(`/report/${IDX.a_resolved}`);
  const form = page.getByRole("region", { name: "Propose a candidate" });
  await form.getByLabel("SMILES").fill("C1CC");
  await form.getByLabel("Reason").selectOption("synthesis");
  await form.getByRole("button", { name: "Propose" }).click();
  await expect(form.getByRole("alert")).toContainText("SMILES");
});
