import { readFileSync } from "node:fs";
import { expect, IDX, run, test } from "./bench";

type Record_ = { results: { per_pose: Record<string, { dE_int: number[] }> } };
const STEPS = (page: import("@playwright/test").Page) => page.getByRole("navigation", { name: "Pipeline steps" }).locator("[data-step]");

/** The pipeline serve_fixture.py makes from run a_resolved ("e2e"), opened on one step with the run as its source. */
async function open(page: import("@playwright/test").Page, step: string, withRun = true) {
  await page.goto(`/pipelines/e2e?${withRun ? `run=${IDX.a_resolved}&` : ""}step=${step}`);
  await expect(STEPS(page)).toHaveCount(6);
}

test("measure: clicking pose 3 shows benzoic and 4-F values that match the record", async ({ page }) => {
  // Data source: the run record on disk, read here directly (results.per_pose), not through the API.
  const rec = JSON.parse(readFileSync(run("a_resolved").path, "utf8")) as Record_;
  const pose = 3;
  const parentV = rec.results.per_pose["benzoic"]!.dE_int[pose]!;
  const fV = rec.results.per_pose["4-F"]!.dE_int[pose]!;

  await open(page, "measure");
  const fig = page.locator('[data-figure="per_pose_values"]');
  await expect(fig.locator(".scatterlayer .trace")).not.toHaveCount(0);
  // A real click on the 4-F marker of pose 3, at the pixel Plotly drew it.
  const at = await fig.evaluate(
    (gd, args) => {
      const g = gd as unknown as {
        _fullLayout: { xaxis: { _offset: number; l2p: (v: number) => number }; yaxis: { _offset: number; l2p: (v: number) => number } };
        data: { name: string; x: number[]; y: number[] }[];
      };
      const t = g.data.find((d) => d.name === args.name)!;
      const box = gd.getBoundingClientRect();
      return {
        x: box.left + g._fullLayout.xaxis._offset + g._fullLayout.xaxis.l2p(t.x[args.pose]!),
        y: box.top + g._fullLayout.yaxis._offset + g._fullLayout.yaxis.l2p(t.y[args.pose]!),
      };
    },
    { name: "4-F", pose },
  );
  await page.mouse.move(at.x - 3, at.y - 3);
  await page.mouse.move(at.x, at.y);
  await page.mouse.click(at.x, at.y);

  const picked = page.getByTestId("picked");
  await expect(picked).toHaveAttribute("data-candidate", "4-F");
  await expect(picked).toHaveAttribute("data-pose", String(pose));
  const table = picked.getByRole("table", { name: "Values at the selected pose" });
  await expect(table.locator('[data-ligand="benzoic"] [data-col="value"]')).toHaveText(parentV.toFixed(3));
  await expect(table.locator('[data-ligand="4-F"] [data-col="value"]')).toHaveText(fV.toFixed(3));
  await expect(table.locator('[data-ligand="4-F"] [data-col="paired"]')).toHaveText((fV - parentV).toFixed(3));
  await expect(picked.locator("[data-viewer]")).toBeVisible();
  await picked.getByRole("button", { name: "SCF log" }).click();
  // The fixture's calc log is empty: the view says so rather than showing nothing.
  await expect(page.getByTestId("scf-log")).toContainText("no line for 4-F pose 3");
});

test("poses: the scene is requested with the core marked, and the caption says which atoms are solid", async ({ page }) => {
  const maps = (await (await page.request.get(`/api/runs/${IDX.a_resolved}/pose_maps`)).json()) as { maps: Record<string, number[][]> };
  const target = "4-F";
  const n = new Set(maps.maps[target]!.map((p) => p[0])).size;
  const wait = page.waitForRequest((r) => r.url().includes("/scene?") && r.url().includes("core=true") && r.url().includes("candidate=4-F"));
  await open(page, "poses");
  await page.getByLabel("Ligand").selectOption(target);
  await wait;
  await expect(page.getByTestId("core-caption")).toContainText(`${n} atoms of ${target}`);
  await expect(page.locator('[data-viewer="poses step"]')).toBeVisible();
});

test("checks: a run with no gates says so; without a run the view says what a run would show", async ({ page }) => {
  await open(page, "checks");
  await expect(page.getByTestId("no-gates")).toContainText("No gate ran");
  await open(page, "checks", false);
  await expect(page.getByTestId("not-run")).toContainText("pass or fail");
});

test("cut: the forest and the matrix are the Results page's own figures", async ({ page }) => {
  await open(page, "cut");
  await expect(page.locator('[data-figure="forest"]')).toBeVisible();
  await expect(page.locator('[data-figure="matrix"]')).toBeVisible();
  await open(page, "cut", false);
  await expect(page.getByTestId("not-run")).toBeVisible();
});
