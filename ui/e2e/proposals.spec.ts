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

test("a proposal the reviewer agrees with goes into the next round's candidates", async ({ page }) => {
  // The other half of the loop: a bench chemist proposes from the report, the computational chemist takes it into
  // the next round. Until this existed, the Proposals tab could only be read, so agreeing meant copying the SMILES
  // out of Bench and hand-editing a TOML.
  await page.goto(`/report/${IDX.a_resolved}`);
  const form = page.getByRole("region", { name: "Propose a candidate" });
  await form.getByLabel("SMILES").fill("OC(=O)c1ccc(C)cc1");
  await form.getByLabel("Reason").selectOption("substitution");
  await form.getByLabel("Note").fill("cheaper than the chloro");
  await form.getByRole("button", { name: "Propose" }).click();
  await expect(form.getByRole("status")).toContainText("waiting for review");

  await page.goto(`/run/${IDX.a_resolved}/proposals`);
  const row = page.locator('[data-proposal="OC(=O)c1ccc(C)cc1"]');
  await expect(row).toBeVisible();
  // Nothing is taken until it is ticked: the button is the judgement, not the page load.
  await expect(page.getByTestId("take-proposals")).toBeDisabled();
  await row.getByRole("checkbox").check();
  await page.getByTestId("take-proposals").click();
  await page.waitForURL(/\/pipelines\/.*step=ligands/, { timeout: 20_000 });

  // The molecule is really in the new pipeline's chemistry, and the page says it was added.
  const name = page.url().match(/pipelines\/([^?]+)/)![1];
  const doc = await (await page.request.get(`/api/pipelines/${name}`)).json();
  expect(doc.toml).toContain("OC(=O)c1ccc(C)cc1");
  expect(doc.notes.some((n: string) => n.includes("added") && n.includes("proposed"))).toBe(true);
  await expect(page.getByRole("note").filter({ hasText: "proposed" })).toBeVisible();
});
