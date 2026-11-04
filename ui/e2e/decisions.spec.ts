import type { Page } from "@playwright/test";
import { expect, IDX, test } from "./bench";

async function get<T>(page: Page, path: string): Promise<T> {
  return (await (await page.request.get(path)).json()) as T;
}

test("the reason list is exactly the core's vocabulary", async ({ page }) => {
  const vocab = await get<{ reasons: string[]; statuses: string[] }>(page, "/api/decisions/vocabulary");
  await page.goto(`/run/${IDX.a_resolved}/decisions`);
  const row = page.locator("[data-candidate]").first();
  const reasons = await row.getByLabel("Reason").locator("option").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value).filter(Boolean));
  expect(reasons).toEqual(vocab.reasons);
  const statuses = await row.getByLabel("Status").locator("option").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value).filter(Boolean));
  expect(statuses).toEqual(vocab.statuses);
});

test("a tie choice without a note is refused with the core's message, and nothing is written", async ({ page }) => {
  const before = await get<unknown[]>(page, `/api/runs/${IDX.d_floor}/decisions`);
  const d = await get<{ groups: string[][] }>(page, `/api/runs/${IDX.d_floor}`);
  const tied = d.groups.find((g) => g.length > 1)![0]!; // data source: the run's own tie groups
  await page.goto(`/run/${IDX.d_floor}/decisions`);
  const row = page.locator(`[data-candidate="${tied}"]`);
  await row.getByLabel("Status").selectOption("keep");
  await row.getByLabel("Reason").selectOption("within_tie_choice");
  await row.getByRole("button", { name: `Record decision for ${tied}` }).click();
  await expect(page.getByTestId(`refusal-${tied}`)).toContainText("needs a note");
  expect(await get<unknown[]>(page, `/api/runs/${IDX.d_floor}/decisions`)).toEqual(before);
});

test("a decision recorded with the keyboard alone lands in the decisions file with the user from Identity", async ({ page }) => {
  const who = await get<{ user: string }>(page, "/api/whoami");
  const d = await get<{ survivors: string[] }>(page, `/api/runs/${IDX.a_resolved}`);
  const kept = d.survivors[0]!;
  await page.goto(`/run/${IDX.a_resolved}/decisions`);
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
  const after = await get<{ candidate: string; status: string; reason: string; user: string }[]>(page, `/api/runs/${IDX.a_resolved}/decisions`);
  expect(after.find((x) => x.candidate === kept)).toMatchObject({ status: "keep", reason: "cut_survivor", user: who.user });
});
