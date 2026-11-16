import { expect, fixture, newPipeline, test } from "./bench";

/**
 * Two findings about losing track of a run (docs/user-flows.md, pain points 5 and 6).
 *
 * 5. A launch's "Open the result" lived only in the Launch component's state, so "Follow it in Work" -- the link
 *    meant to take you to the run -- destroyed it, and neither the Work table nor the task inspector linked to
 *    what the task had produced. Runs were then picked out of a table whose rows carried no directory and no
 *    pipeline, where two rounds of one campaign differ only by digest.
 * 6. A pipeline link carried no `?run=`, so the Poses, Checks, dE_int and Cut views all reported that the step
 *    had not run -- including for a copied example that ships with a finished run.
 */

test("a run is listed with the directory it went to and the pipeline it belongs to", async ({ page }) => {
  // Data source: the API's own run list, narrowed to the five runs serve_fixture.py built. The server keeps every
  // run for its lifetime and several specs launch their own, so a whole-list assertion passes alone and fails
  // after any of them -- this test's first version did exactly that. The fixture's runs are the ones whose claim
  // is being made (they share a campaign, so campaign alone cannot name them); another spec's run is not.
  const mine = new Set(fixture().runs.map((r) => r.path));
  const all = (await (await page.request.get("/api/runs")).json()) as { id: string; path: string; campaign: string }[];
  const runs = all.filter((r) => mine.has(r.path));
  expect(runs, "the fixture's own runs are listed").toHaveLength(mine.size);
  expect(new Set(runs.map((r) => r.campaign)).size, "the fixture runs share a campaign").toBe(1);
  await page.goto("/bench");
  const table = page.getByRole("region", { name: "Runs" }).locator("table.grid");
  await expect(table).toBeVisible();
  // Every run says which directory it is, and those names are all different: that is what tells them apart.
  const allWhere = await table.locator("[data-cell='where']").evaluateAll((cs) => cs.map((c) => c.textContent ?? ""));
  // The table lists every run the server has, so keep the cells that name one of the fixture's directories.
  const dirs = runs.map((r) => r.path.split("/").slice(-3, -1).join("/"));
  const where = allWhere.filter((w) => dirs.some((d) => w.includes(d)));
  expect(where).toHaveLength(runs.length);
  // The fixture's runs all sit in a folder named `out`, so the label has to reach past it to the folder that
  // does name the run -- the same reason the campaign alone was not enough.
  for (const r of runs) {
    const dir = r.path.split("/").slice(-3, -1).join("/");
    expect(where.some((w) => w.includes(dir)), `${dir} is listed`).toBe(true);
  }
  expect(new Set(where).size).toBe(runs.length);
  // These runs were not made by a pipeline, and the table says so rather than naming one it guessed.
  await expect(table.locator("[data-cell='pipeline']").first()).toContainText("none recorded");
});

test("the Work panel and the task inspector link to the run the task produced", async ({ page }) => {
  // A launchable pipeline: the starting one has no pocket charge, which blocks Run (see launch.spec.ts).
  const { name } = await newPipeline(page, (t) =>
    `${t}\n[[pocket.site]]\nq = 1.0\natom = 3\nfrom_atom = 4\ndistance = 3.0\n`,
  );
  const dir = `e2e-identity-${Date.now().toString(36)}`;
  const r = await page.request.post(`/api/pipelines/${name}/launch`, { data: { out: `out/${dir}` } });
  expect(r.status(), await r.text()).toBe(201);
  const task = (await r.json()) as { id: string; command: string[] };
  const id = task.id;
  // Data source: the task the queue recorded. `--out` is the absolute directory the run was told to write into,
  // and it is the only thing the launch and the Work panel have in common (`Task.run_id` is a Temporal id).
  const out = task.command[task.command.indexOf("--out") + 1]!;
  expect(out.endsWith(`/out/${dir}`), out).toBe(true);
  try {
    await page.goto("/bench");
    const row = page.locator(`[data-task="${id}"]`);
    // Before the record exists there is no run to link, and the row says where it is going instead of nothing.
    await expect(row.getByTestId("task-out")).toContainText(dir, { timeout: 15_000 });

    // The link appears once the record is there. The run is faked rather than waited out: a real SCF takes
    // minutes, and what is under test is the Work panel's end of the launch-to-result link.
    const fake = { id: "fake-run-id", path: `${out}/run-abcdef012345.json`, campaign: "GUI Fixture", digest: "abcdef0123456789" };
    await page.route("**/api/runs", async (route) => {
      const got = await route.fetch();
      route.fulfill({ json: [...((await got.json()) as unknown[]), fake] });
    });
    await page.reload();
    await expect(row.getByTestId("task-run")).toHaveAttribute("href", `/run/${fake.id}/results`, { timeout: 15_000 });

    // And the inspector -- where someone sits while a run finishes -- offers it as a control, not just a cell.
    await row.getByRole("button", { name: /Show task/ }).first().click();
    await expect(page.getByTestId("inspector-run")).toHaveAttribute("href", `/run/${fake.id}/results`);
  } finally {
    await page.request.post(`/api/tasks/${id}/cancel`); // do not leave a real SCF running
  }
});

test("a pipeline that has a run of its own is linked with it, so its step views have something to read", async ({
  page,
}) => {
  // A copy of an example ships a finished run under the copy's own folder: exactly the case where the step views
  // used to say "open this pipeline from a run (or run it)" about a run that was already there.
  const name = `ident-${Date.now().toString(36)}`;
  await page.goto("/bench");
  const card = page.getByRole("group", { name: "Examples you can copy" });
  await page.locator("#prebuilt-name-benzoic-rhf").fill(name);
  await card.getByRole("button", { name: "Start from benzoic-rhf" }).click();
  await expect(page).toHaveURL(new RegExp(`/pipelines/${name}\\?run=`));

  // Data source: the API, for the id of the run that copy now holds.
  const runs = (await (await page.request.get("/api/runs")).json()) as { id: string; path: string }[];
  const mine = runs.find((r) => r.path.includes(`/pipelines/${name}/`))!;
  expect(mine, "the copy has a run of its own").toBeTruthy();

  // The pipeline list links to it with that run, not bare.
  await page.goto("/bench");
  const link = page.locator(`[data-pipeline="${name}"]`).first();
  await expect(link).toHaveAttribute("href", `/pipelines/${name}?run=${mine.id}`);

  // And following it lands on a step view that reads the run, instead of one saying the step has not run.
  await link.click();
  await expect(page.getByRole("navigation", { name: "Pipeline steps" }).locator("[data-step]")).toHaveCount(6);
  await page.locator('[data-step="measure"]').click();
  await expect(page.getByTestId("not-run")).toHaveCount(0);
  await expect(page.locator('[data-figure="per_pose_values"]')).toBeVisible({ timeout: 20_000 });
});

test("Methods' 'Used by' carries the pipeline's run too", async ({ page }) => {
  // The same bare link, from the other page that offers it.
  const name = `ident-m-${Date.now().toString(36)}`;
  await page.goto("/bench");
  const card = page.getByRole("group", { name: "Examples you can copy" });
  await page.locator("#prebuilt-name-benzoic-rhf").fill(name);
  await card.getByRole("button", { name: "Start from benzoic-rhf" }).click();
  await expect(page).toHaveURL(new RegExp(`/pipelines/${name}\\?run=`));
  const runs = (await (await page.request.get("/api/runs")).json()) as { id: string; path: string }[];
  const mine = runs.find((r) => r.path.includes(`/pipelines/${name}/`))!;

  await page.goto("/methods");
  const used = page.locator(`[data-fact="used-by"] [data-pipeline="${name}"]`).first();
  await expect(used).toHaveAttribute("href", `/pipelines/${name}?run=${mine.id}`);
});

test("the arm and start-from pickers name which run of a campaign each option is", async ({ page }) => {
  const runs = (await (await page.request.get("/api/runs")).json()) as { id: string; path: string }[];
  // The folder that names the run: the fixture's own `out` holds no name, so the one above it does.
  const dirOf = (p: string) => p.split("/").slice(-3, -1).join("/");
  const named = (labels: string[]) => labels.filter((l) => l !== "choose a run");

  /** Every real option names a run directory, and no two options read the same. */
  const check = async (picker: string, labels: string[]) => {
    expect(named(labels).length, `${picker} offers runs`).toBeGreaterThan(1);
    for (const l of named(labels)) {
      expect(runs.some((r) => l.includes(dirOf(r.path))), `${picker}: "${l}" names a run directory`).toBe(true);
    }
    expect(new Set(labels).size, `${picker} has no two identical options`).toBe(labels.length);
  };

  /** A picker's option texts, once its runs have arrived (the placeholder alone, or nothing, is too early). */
  const options = async (label: string) => {
    const sel = page.getByLabel(label).locator("option");
    await expect(sel).not.toHaveCount(0);
    await expect(sel).not.toHaveCount(1);
    return sel.evaluateAll((os) => os.map((o) => o.textContent ?? ""));
  };

  await page.goto("/compare");
  // Each option carries its run directory, so two rounds of one campaign are not two identical lines.
  await check("Arm A", await options("Arm A"));

  await page.goto("/bench");
  await check("Start from run", await options("Start from run"));
});
