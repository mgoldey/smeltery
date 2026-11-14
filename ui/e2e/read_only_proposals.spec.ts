/**
 * A `--read-only` Bench must not instruct an impossible action, and must still be readable.
 *
 * The defect: Proposals rendered a tick box per row, a select-all box, and a lede saying "Tick the ones you agree
 * with and start the next round", while the only button that consumes a tick is wrapped in `Writable` and so is
 * absent on a read-only server. The reader was told to do something the page had hidden the means of doing. Its
 * empty state then sent them to the Report page to propose, where the propose form is hidden too.
 *
 * This file runs only against a read-only server (`smeltery bench --read-only`), which the repo's own fixture is
 * not, so it skips unless `/api/whoami` says `read_only`. Start one with e2e/serve_fixture.py behind a server
 * whose `read_only` is on, point BENCH_E2E_PORT at it, and run this file.
 */
import { expect, id, test } from "./bench";

test.beforeEach(async ({ page }) => {
  const who = (await (await page.request.get("/api/whoami")).json()) as { read_only: boolean };
  test.skip(!who.read_only, "needs a Bench served with --read-only");
});

test("the queue is readable, and nothing on it instructs a write", async ({ page }) => {
  const run = id("a_resolved");
  // Proposals are stored per run; this server refuses the POST that would make one, so the queue may be empty.
  // Both states are asserted: whichever this server is in, the page must not describe a control it is hiding.
  await page.goto(`/run/${run}/proposals`);
  await expect(page.getByRole("heading", { name: "Proposals" })).toBeVisible();
  await expect(page.getByTestId("read-only-badge")).toBeVisible();

  // The instruction and the control it describes are both gone.
  await expect(page.getByText("Tick the ones you agree with")).toHaveCount(0);
  await expect(page.getByText("Tick a proposal to add it")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Take these into the next round" })).toHaveCount(0);
  await expect(page.getByTestId("take-proposals")).toHaveCount(0);
  await expect(page.locator("main input[type=checkbox]")).toHaveCount(0);

  // And the page says why, rather than leaving the reader to infer it from an absence.
  await expect(page.locator("main")).toContainText("read-only");

  const queue = page.getByRole("table", { name: "Review queue" });
  if (await queue.count()) {
    // Whatever colleagues proposed stays legible: a published Bench exists to be read.
    await expect(queue.locator("tbody tr").first()).toBeVisible();
  } else {
    // The empty state must not send them to the report to propose: that form is hidden there too. Scoped to the
    // empty state, because the Discussion section below legitimately links to the report -- to READ the comments
    // anchored to a candidate or a pose, which is not a write and is not hidden.
    const empty = page.getByTestId("empty-state");
    await expect(empty).toContainText("Nothing has been proposed");
    await expect(empty.getByRole("link")).toHaveCount(0);
  }
});

test("the report offers no propose form and promises no follow-up", async ({ page }) => {
  await page.goto(`/report/${id("a_resolved")}`);
  await expect(page.getByTestId("floor-statement")).toBeVisible(); // the report itself still reads
  await expect(page.getByRole("region", { name: "Propose a candidate" })).toHaveCount(0);
  // The note about where a proposal lands belongs to the form; with no form there is nothing to explain.
  await expect(page.getByTestId("proposal-afterwards")).toHaveCount(0);
});

test("the Proposals tab is still offered, because the queue can be read", async ({ page }) => {
  await page.goto(`/run/${id("a_resolved")}/results`);
  const tabs = page.getByRole("navigation", { name: "Run views" });
  await expect(tabs.getByRole("link", { name: /^Proposals/ })).toBeVisible();
  await expect(tabs.getByRole("link", { name: "Decisions" })).toHaveCount(0); // that tab only writes
});
