import type { Locator, Page } from "@playwright/test";
import { apiGet, expect, id, test } from "./bench";

/**
 * Deciding, in a browser: how far through it is, what the exit carries, and what a tie group looks like at the
 * point of choosing.
 *
 * All three were defects of legibility rather than of behaviour. Nothing said "n of N decided"; "Start the next
 * round" appeared on the first recorded decision with no sign of rows filled in and left; and the lede promised a
 * tie choice "needs a note" while the server requires one for the REASON `within_tie_choice`, not for the position.
 * A decision cannot be cleared -- there is no DELETE anywhere in the Bench API, by design, because the file is an
 * auditable judgement record -- so none of this blocks a click. It only stops the page from being quiet.
 *
 * Decisions persist for the life of the fixture server, and other specs record on these runs too, so every expected
 * count here is read from the API at the moment the test starts. Nothing is asserted against a constant.
 */

/**
 * The run's analogues and which of them already have a decision.
 *
 * `undecided` leaves the cut's survivors out: other specs on this fixture record on a survivor of a_resolved (it is
 * the natural "keep" to demonstrate), and a decision cannot be unmade, so writing to one here would change the state
 * those specs start from. Picking a non-survivor keeps these tests independent of run order.
 */
async function state(page: Page, run: string) {
  const d = await apiGet<{ analogues: string[]; groups: string[][]; survivors: string[] | null }>(
    page,
    `/api/runs/${run}`,
  );
  const made = await apiGet<{ candidate: string }[]>(page, `/api/runs/${run}/decisions`);
  const recorded = new Set(made.map((x) => x.candidate));
  const reserved = new Set(d.survivors ?? []);
  return {
    analogues: d.analogues,
    groups: d.groups,
    decided: d.analogues.filter((n) => recorded.has(n)),
    undecided: d.analogues.filter((n) => !recorded.has(n) && !reserved.has(n)),
  };
}

/**
 * The Reason select of one row, by its exact label.
 *
 * `getByLabel("Reason")` matches substrings, and once a note is required the note's own label reads
 * "Note (required for this reason)" -- so the loose form resolves to two elements as soon as the row holds
 * `within_tie_choice`. e2e/decisions.spec.ts avoids the same collision from the other side with /^Note/.
 */
function reasonOf(row: Locator): Locator {
  return row.getByLabel("Reason", { exact: true });
}

test("the page counts the analogues that have a recorded decision", async ({ page }) => {
  const run = id("a_resolved");
  const s = await state(page, run);
  test.skip(s.undecided.length === 0, "every analogue on this run is already decided");
  await page.goto(`/run/${run}/decisions`);
  await expect(page.getByTestId("decided-progress")).toContainText(`${s.decided.length} of ${s.analogues.length}`);

  const target = s.undecided[0]!;
  const row = page.locator(`[data-candidate="${target}"]`);
  await row.getByLabel("Status").selectOption("hold");
  await reasonOf(row).selectOption("synthesis");
  await row.getByRole("button", { name: `Record decision for ${target}` }).click();
  await expect(page.getByTestId("decided-progress")).toContainText(
    `${s.decided.length + 1} of ${s.analogues.length}`,
  );
});

test("a row typed into and left unrecorded is named, beside the exit, before it is lost", async ({ page }) => {
  const run = id("d_floor");
  const s = await state(page, run);
  test.skip(s.undecided.length < 2, "this test needs two analogues with no decision yet");
  const [first, second] = s.undecided;
  await page.goto(`/run/${run}/decisions`);

  // Record one, so the exit is offered at all: an empty next round is refused by the server.
  const recorded = page.locator(`[data-candidate="${first}"]`);
  await recorded.getByLabel("Status").selectOption("keep");
  await reasonOf(recorded).selectOption("cut_survivor");
  await recorded.getByRole("button", { name: `Record decision for ${first}` }).click();
  await expect(page.getByTestId("start-next-round")).toBeVisible();
  await expect(page.getByTestId("next-round-unrecorded")).toHaveCount(0);

  // Now fill a second row in and do NOT record it. That is the state the old page gave no sign of.
  const draft = page.locator(`[data-candidate="${second}"]`);
  await draft.getByLabel("Status").selectOption("reject");
  await expect(page.getByTestId("next-round-unrecorded")).toContainText(second!);
  await expect(page.getByTestId("next-round-unrecorded")).toContainText("not recorded");
  // Said, not enforced. Starting the round with a draft open is a legitimate thing to do, and there is no undo for
  // a decision, so the page must not be the thing that refuses.
  await expect(page.getByTestId("start-next-round")).toBeEnabled();
  // And recording it clears the warning rather than leaving a stale one.
  await reasonOf(draft).selectOption("liability");
  await draft.getByRole("button", { name: `Record decision for ${second}` }).click();
  await expect(page.getByTestId("next-round-unrecorded")).toHaveCount(0);
});

test("the exit counts what it carries against what it leaves undecided", async ({ page }) => {
  const run = id("b_unranked");
  const s = await state(page, run);
  test.skip(s.undecided.length === 0, "every analogue on this run is already decided");
  await page.goto(`/run/${run}/decisions`);
  const target = s.undecided[0]!;
  const row = page.locator(`[data-candidate="${target}"]`);
  await row.getByLabel("Status").selectOption("keep");
  await reasonOf(row).selectOption("within_tie_choice");
  await row.getByLabel(/^Note/).fill("closest to the parent scaffold");
  await row.getByRole("button", { name: `Record decision for ${target}` }).click();

  const carries = page.getByTestId("next-round-carries");
  await expect(carries).toBeVisible();
  const decided = s.decided.length + 1;
  await expect(carries).toContainText(decided === 1 ? "one analogue" : `${decided} analogues`);
  const left = s.analogues.length - decided;
  if (left > 0) await expect(carries).toContainText("undecided");
  else await expect(carries).toContainText("Every analogue");
});

test("a tied row names who it is tied with, and points at the reason that is honest about it", async ({ page }) => {
  const run = id("d_floor");
  const s = await state(page, run);
  const group = s.groups.find((g) => g.length > 1); // the run's own tie groups, never a constant
  test.skip(!group, "this run's cut has no tie group");
  const [tied, other] = group!;
  await page.goto(`/run/${run}/decisions`);

  await expect(page.getByTestId(`group-${tied}`)).toContainText("tied with");
  await expect(page.getByTestId(`group-${tied}`)).toContainText(other!);

  const row = page.locator(`[data-candidate="${tied}"]`);
  await row.getByLabel("Status").selectOption("keep");
  // `cut_survivor` inside a tie group says the cut chose this one, which is exactly what did not happen. The server
  // takes it and will go on taking it, so the page names the mismatch instead of refusing the click.
  await reasonOf(row).selectOption("cut_survivor");
  await expect(page.getByTestId(`tie-reason-${tied}`)).toBeVisible();
  await expect(row.getByRole("button", { name: `Record decision for ${tied}` })).toBeEnabled();

  // The honest reason is one click away, and it is the one the server asks a note for.
  await page.getByTestId(`tie-reason-${tied}`).getByRole("button", { name: "Call it a tie choice" }).click();
  await expect(reasonOf(row)).toHaveValue("within_tie_choice");
  await expect(row.getByRole("button", { name: `Record decision for ${tied}` })).toBeDisabled();
  await expect(page.getByTestId(`needs-note-${tied}`)).toBeVisible();

  // A reason of the chemist's own stays recordable on a tied row: a liability really can break a tie.
  await reasonOf(row).selectOption("liability");
  await expect(page.getByTestId(`tie-reason-${tied}`)).toBeVisible();
  await row.getByRole("button", { name: `Record decision for ${tied}` }).click();
  await expect(page.getByTestId(`recorded-${tied}`)).toContainText("liability");
});

test("a row alone in its group is never described as tied", async ({ page }) => {
  const run = id("a_resolved");
  const s = await state(page, run);
  const alone = s.groups.find((g) => g.length === 1 && s.analogues.includes(g[0]!))?.[0];
  test.skip(!alone, "this run's cut has no single-member analogue group");
  await page.goto(`/run/${run}/decisions`);
  await expect(page.getByTestId(`group-${alone}`)).not.toContainText("tied with");
  const row = page.locator(`[data-candidate="${alone}"]`);
  await row.getByLabel("Status").selectOption("keep");
  await reasonOf(row).selectOption("cut_survivor");
  await expect(page.getByTestId(`tie-reason-${alone}`)).toHaveCount(0);
});

test("Provenance says this round carried no decisions in, and shows the verdict beside the record", async ({ page }) => {
  // The fixture runs name no `[decisions]` file, so this is the "nothing carried in" state -- which must be said in
  // words rather than left as a blank. The populated states are covered at the API in tests/bench.
  const run = id("a_resolved");
  const detail = await apiGet<{ decisions_in: unknown }>(page, `/api/runs/${run}`);
  expect(detail.decisions_in).toBeNull();
  await page.goto(`/run/${run}/provenance`);
  await expect(page.getByTestId("carried-none")).toContainText("carried no decisions in");
  await expect(page.getByTestId("carried-in")).toHaveCount(0);
  // The reading, beside the record it was read from.
  await expect(page.getByLabel("Verdict")).toBeVisible();
});
