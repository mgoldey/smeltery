import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, id, test } from "./bench";

const RUN = () => id("a_resolved");

test("the report leads with the verdict, then the plot, then the candidates", async ({ page }) => {
  await page.goto(`/report/${RUN()}`);
  await expect(page.locator('[data-figure="forest"] .main-svg').first()).toBeVisible();
  const order = await page.evaluate(() => {
    const at = (sel: string) => {
      const el = document.querySelector(sel);
      return el ? [...document.querySelectorAll("*")].indexOf(el) : -1;
    };
    return { verdict: at(".verdict"), plot: at('[data-figure="forest"]'), candidates: at('[aria-label="Candidates"]') };
  });
  expect(order.verdict).toBeGreaterThanOrEqual(0);
  expect(order.verdict).toBeLessThan(order.plot);
  expect(order.plot).toBeLessThan(order.candidates);
});

test("a comment on a candidate appears in its thread with the author from Identity", async ({ page }) => {
  const who = ((await (await page.request.get("/api/whoami")).json()) as { user: string }).user;
  await page.goto(`/report/${RUN()}`);
  const thread = page.locator('[data-thread="candidate:4-F"]');
  await thread.getByLabel("Comment on 4-F").fill("nitro on an aniline: we will not make this");
  await thread.getByRole("button", { name: "Add comment" }).click();
  await expect(thread.locator(".comments li")).toContainText("nitro on an aniline");
  await expect(thread.locator(".comment-author")).toHaveText(who);
  const stored = (await (await page.request.get(`/api/runs/${RUN()}/comments`)).json()) as { anchor: string; body: string; author: string }[];
  expect(stored.find((c) => c.body.startsWith("nitro on an aniline"))).toMatchObject({ anchor: "candidate:4-F", author: who });
});

test("nothing on the report edits the method or starts work", async ({ page }) => {
  test.slow(); // every select and button is exercised, and each pose switch reloads the 3D viewer
  const forbidden: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (r.method() !== "GET" && (u.pathname.startsWith("/api/pipeline") || u.pathname === "/api/tasks" || u.pathname.includes("/launch"))) {
      forbidden.push(`${r.method()} ${u.pathname}`);
    }
  });
  await page.goto(`/report/${RUN()}`);
  await expect(page.locator('[data-figure="forest"] .main-svg').first()).toBeVisible();
  // Exercise every control on the page: every enabled button, and every select.
  for (const sel of await page.locator("main select").all()) {
    const values = await sel.locator("option").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value));
    for (const v of values) await sel.selectOption(v);
  }
  for (const ta of await page.locator("main textarea").all()) await ta.fill("checking controls");
  // Controls can disable or disappear once used (a submitted comment form); click each that is still enabled.
  for (const b of await page.locator("main button").all()) {
    if ((await b.count()) && (await b.isVisible()) && (await b.isEnabled())) await b.click({ timeout: 5_000 });
  }
  await page.waitForLoadState("networkidle");
  expect(forbidden).toEqual([]);
});

test("the report downloads as a file holding the run's numbers, referencing only its pinned libraries", async ({
  page,
}) => {
  // The take-away is a real download now. It used to be a button that wrote a file onto the server and printed
  // the path, which only helps someone with a shell on that machine.
  await page.goto(`/report/${RUN()}`);
  await expect(page.locator('[data-testid^="mean-"]').first()).toBeVisible();
  const means = await page.locator('[data-testid^="mean-"]').allInnerTexts(); // data source: the page as displayed
  expect(means.length).toBeGreaterThan(0);

  const start = page.waitForEvent("download");
  await page.getByTestId("download-report").click();
  const download = await start;
  expect(download.suggestedFilename()).toMatch(/^report-[0-9a-f]{12}\.html$/);
  const saved = await download.path();
  const html = readFileSync(saved).toString("utf8");

  for (const m of means) expect(html, m).toContain(m);

  // The archived copy is the same bytes as the download, so what the chemist saves is what the notebook keeps.
  // Both are built here, back to back: the report embeds the run's comments and decisions, so a write by any
  // other test between two builds legitimately changes the file, and comparing across that gap would be
  // asserting the report ignores the discussion it is supposed to carry.
  const snap = (await (await page.request.post(`/api/runs/${RUN()}/snapshot`)).json()) as {
    path: string;
    sha256: string;
    external: string[];
  };
  const again = await page.request.get(`/api/runs/${RUN()}/report.html`);
  expect(createHash("sha256").update(Buffer.from(await again.body())).digest("hex")).toBe(snap.sha256);

  const urls = new Set(html.match(/https?:\/\/[^\s"'<>)]+/g) ?? []);
  for (const u of urls) expect(snap.external, u).toContain(u);
});

test("the report states the floor and lists what the result cannot tell you", async ({ page }) => {
  await page.goto(`/report/${RUN()}`);
  await expect(page.getByTestId("floor-statement")).toContainText("The floor is");
  const section = page.getByRole("region", { name: "What this result cannot tell you" });
  await expect(section.locator("details")).not.toHaveAttribute("open", "");
  await section.locator("summary").click();
  await expect(section.getByRole("cell", { name: "L1", exact: true })).toBeVisible();
  await expect(section.getByRole("cell", { name: "U1", exact: true })).toBeVisible();
});
