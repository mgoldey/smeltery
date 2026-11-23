import { apiGet, expect, id, test } from "./bench";

test("the reason list is exactly the core's vocabulary", async ({ page }) => {
  const vocab = await apiGet<{ reasons: string[]; statuses: string[] }>(page, "/api/decisions/vocabulary");
  await page.goto(`/run/${id("a_resolved")}/decisions`);
  const row = page.locator("[data-candidate]").first();
  // the options arrive with the vocabulary query, so poll rather than read the select once
  const values = (label: string) => () =>
    row.getByLabel(label).locator("option").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value).filter(Boolean));
  await expect.poll(values("Reason")).toEqual(vocab.reasons);
  await expect.poll(values("Status")).toEqual(vocab.statuses);
});

test("a tie choice without a note is refused with the core's message, and nothing is written", async ({ page }) => {
  // The server stays the authority on the rule. The page no longer offers the click that would hit it -- Record is
  // held while the note is blank (see the test below) -- so this asks the route directly, the way a script or an
  // older client would, and checks the refusal still names the reason and writes nothing.
  const before = await apiGet<unknown[]>(page, `/api/runs/${id("d_floor")}/decisions`);
  const d = await apiGet<{ groups: string[][] }>(page, `/api/runs/${id("d_floor")}`);
  const tied = d.groups.find((g) => g.length > 1)![0]!; // data source: the run's own tie groups
  const r = await page.request.post(`/api/runs/${id("d_floor")}/decisions`, {
    data: { candidate: tied, status: "keep", reason: "within_tie_choice", note: "   " }, // whitespace is blank: the core strips it
  });
  expect(r.status(), await r.text()).toBe(400);
  expect(await r.text()).toContain("needs a note");
  expect(await apiGet<unknown[]>(page, `/api/runs/${id("d_floor")}/decisions`)).toEqual(before);
});

test("the server's refusal still reaches the page when it is the one to refuse", async ({ page }) => {
  // The hold on Record is only about not offering a doomed click; the red server-error path has to stay wired, or a
  // refusal the client cannot predict (an unknown candidate, a file the core will not write) would land silently.
  const d = await apiGet<{ analogues: string[] }>(page, `/api/runs/${id("d_floor")}`);
  const target = d.analogues[0]!;
  await page.goto(`/run/${id("d_floor")}/decisions`);
  const row = page.locator(`[data-candidate="${target}"]`);
  await row.getByLabel("Status").selectOption("keep");
  await row.getByLabel("Reason").selectOption("cut_survivor"); // no note required, so the client lets it through
  await page.route(`**/api/runs/*/decisions`, (route) =>
    route.request().method() === "POST"
      // `reason` is the field the app's own error reader keeps verbatim (api/http.ts describeReason).
      ? route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ reason: "the core refused this one" }) })
      : route.continue(),
  );
  await row.getByRole("button", { name: `Record decision for ${target}` }).click();
  await expect(page.getByTestId(`refusal-${target}`)).toContainText("the core refused this one");
});

test("a decision recorded with the keyboard alone lands in the decisions file with the user from Identity", async ({ page }) => {
  const who = await apiGet<{ user: string }>(page, "/api/whoami");
  const d = await apiGet<{ survivors: string[] }>(page, `/api/runs/${id("a_resolved")}`);
  const kept = d.survivors[0]!;
  await page.goto(`/run/${id("a_resolved")}/decisions`);
  const row = page.locator(`[data-candidate="${kept}"]`);
  // Keyboard only: the first status is keep and the first reason is cut_survivor (smeltery.decisions order).
  await row.getByLabel("Status").focus();
  await page.keyboard.press("ArrowDown");
  await expect(row.getByLabel("Status")).toHaveValue("keep");
  await page.keyboard.press("Tab");
  await expect(row.getByLabel("Reason")).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(row.getByLabel("Reason")).toHaveValue("cut_survivor");
  await page.keyboard.press("Tab"); // note
  await page.keyboard.press("Tab"); // Record
  await expect(row.getByRole("button", { name: `Record decision for ${kept}` })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId(`recorded-${kept}`)).toContainText(`by ${who.user}`);
  const after = await apiGet<{ candidate: string; status: string; reason: string; user: string }[]>(page, `/api/runs/${id("a_resolved")}/decisions`);
  expect(after.find((x) => x.candidate === kept)).toMatchObject({ status: "keep", reason: "cut_survivor", user: who.user });
});

test("a recorded decision can start the next round, and the new pipeline references it", async ({ page }) => {
  // The loop this app existed to support: decide, then carry the decision into the next run. Before this, the only
  // route was to leave Bench and hand-edit [decisions] file = "..." into a TOML.
  const d = await apiGet<{ analogues: string[] }>(page, `/api/runs/${id("a_resolved")}`);
  const target = d.analogues[0];
  await page.goto(`/run/${id("a_resolved")}/decisions`);
  const row = page.locator(`tbody tr`).filter({ hasText: target }).first();
  await row.getByLabel("Status").selectOption("reject");
  await row.getByLabel("Reason").selectOption("liability");
  await row.getByRole("button", { name: `Record decision for ${target}` }).click();
  await expect(page.getByTestId("start-next-round")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("start-next-round").click();
  // Lands in the workspace on the new pipeline's candidates.
  await expect(page).toHaveURL(/\/pipelines\/.*step=ligands/, { timeout: 20_000 });
  // The new config really references the copied decisions file, which is the whole point, and says in its header
  // what it carried. The TOML is folded away by default, so open it before looking.
  await page.getByRole("group").getByText("Edit as TOML", { exact: true }).click();
  const toml = page.getByLabel("Pipeline TOML");
  await expect(toml).toContainText("[decisions]", { timeout: 20_000 });
  await expect(toml).toContainText(`${target} was rejected`);
  // Written into the config, not just into one API response, so it survives a reload.
  await page.reload();
  await page.getByRole("group").getByText("Edit as TOML", { exact: true }).click();
  await expect(page.getByLabel("Pipeline TOML")).toContainText(`${target} was rejected`);
});

test("a tie choice holds Record until a note is typed, and says which reason is waiting", async ({ page }) => {
  // The page's own lede promises a tie choice "needs a note saying why", and the core refuses one without it. Before
  // this, Record stayed live and the only way to learn the rule was a red server error.
  const d = await apiGet<{ groups: string[][] }>(page, `/api/runs/${id("d_floor")}`);
  const tied = d.groups.find((g) => g.length > 1)![0]!; // data source: the run's own tie groups
  await page.goto(`/run/${id("d_floor")}/decisions`);
  const row = page.locator(`[data-candidate="${tied}"]`);
  const record = row.getByRole("button", { name: `Record decision for ${tied}` });
  await row.getByLabel("Status").selectOption("keep");
  await row.getByLabel("Reason").selectOption("within_tie_choice");
  await expect(record).toBeDisabled();
  // Disabled alone is its own defect: the reason has to be on screen.
  await expect(page.getByTestId(`needs-note-${tied}`)).toContainText("needs a note saying why");
  // Whitespace is blank to the core, which strips the note, so it must be blank here too.
  await row.getByLabel(/^Note/).fill("   ");
  await expect(record).toBeDisabled();
  await row.getByLabel(/^Note/).fill("closest to the parent scaffold");
  await expect(record).toBeEnabled();
  await expect(page.getByTestId(`needs-note-${tied}`)).toHaveCount(0);
  // And it really records: the hold was about not offering a doomed click, not about blocking the action.
  await record.click();
  await expect(page.getByTestId(`recorded-${tied}`)).toContainText("within_tie_choice");
});

test("a bulk tie choice holds Apply until a note is typed, and says it would refuse every selected candidate", async ({ page }) => {
  // Worse than one row: the core applies a bulk decision all-or-nothing, so one blank note refused all of them.
  const d = await apiGet<{ analogues: string[] }>(page, `/api/runs/${id("d_floor")}`);
  const two = d.analogues.slice(0, 2);
  await page.goto(`/run/${id("d_floor")}/decisions`);
  for (const n of two) await page.getByLabel(`Select ${n}`).check();
  const bar = page.getByRole("group", { name: "Bulk decision" });
  const apply = bar.getByRole("button", { name: "Apply to selected" });
  await bar.getByLabel("Status").selectOption("keep");
  await bar.getByLabel("Reason").selectOption("within_tie_choice");
  await expect(apply).toBeDisabled();
  await expect(page.getByTestId("bulk-needs-note")).toContainText(`refuse all ${two.length}`);
  await bar.getByLabel(/^Note/).fill(" ");
  await expect(apply).toBeDisabled();
  await bar.getByLabel(/^Note/).fill("both sit inside the tie group");
  await expect(apply).toBeEnabled();
});
