import type { Page } from "@playwright/test";
import { expect, IDX, test } from "./bench";

async function verdictOf(page: Page, idx: number) {
  // Data source: the API's verdict for this run, not a constant in the test.
  const r = await page.request.get(`/api/runs/${idx}/verdict`);
  return (await r.json()) as { state: string; winner: string | null; floor: number | null; missing: string[] };
}

async function bannerIsLargestText(page: Page) {
  const { banner, max } = await page.evaluate(() => {
    const size = (e: Element) => parseFloat(getComputedStyle(e).fontSize);
    const headline = document.querySelector(".verdict-headline");
    const all = [...document.querySelectorAll("body *")].filter((e) => (e as HTMLElement).innerText?.trim());
    return { banner: headline ? size(headline) : 0, max: Math.max(...all.map(size)) };
  });
  expect(banner).toBeGreaterThan(0);
  expect(banner).toBe(max);
}

test("RESOLVED names the candidates the cut keeps", async ({ page }) => {
  const v = await verdictOf(page, IDX.a_resolved);
  expect(v.state).toBe("RESOLVED");
  const survivors = ((await (await page.request.get(`/api/runs/${IDX.a_resolved}`)).json()) as { survivors: string[] }).survivors;
  await page.goto(`/run/${IDX.a_resolved}/results`);
  const banner = page.getByRole("region", { name: "Verdict" });
  for (const s of survivors) await expect(banner).toContainText(s);
  await bannerIsLargestText(page);
});

test("UNRANKED says it does not resolve, gives the floor, and names no winner", async ({ page }) => {
  const v = await verdictOf(page, IDX.b_unranked);
  expect(v.state).toBe("UNRANKED");
  await page.goto(`/run/${IDX.b_unranked}/results`);
  const banner = page.getByRole("region", { name: "Verdict" });
  await expect(banner).toContainText("Does not resolve");
  await expect(banner).toContainText(String(v.floor));
  await expect(page.locator("body")).not.toContainText(/winner/i);
  await bannerIsLargestText(page);
});

test("INCOMPLETE lists what the record is missing and is never blank", async ({ page }) => {
  const v = await verdictOf(page, IDX.c_incomplete);
  expect(v.state).toBe("INCOMPLETE");
  await page.goto(`/run/${IDX.c_incomplete}/results`);
  const banner = page.getByRole("region", { name: "Verdict" });
  await expect(banner).toContainText("Incomplete");
  for (const m of v.missing) await expect(banner).toContainText(m);
  await bannerIsLargestText(page);
});
