import { expect, IDX, test } from "./bench";
import { distinctColours } from "./pixels";

test("Mol* draws the API's scene: the canvas is not a uniform colour", async ({ page }) => {
  await page.goto(`/run/${IDX.a_resolved}/poses`);
  const host = page.locator("[data-viewer]");
  await expect(host).toBeVisible();
  // Mol* itself reports the candidate and the muted parent as loaded structures...
  await expect.poll(async () => Number(await host.getAttribute("data-loaded")), { timeout: 30_000 }).toBeGreaterThanOrEqual(2);
  // ...and they are actually on screen: a framed molecule is many colours, an empty canvas one or two.
  await expect.poll(() => distinctColours(host), { timeout: 30_000 }).toBeGreaterThan(20);
});

test("changing candidate and pose requests that scene, and the pairing is stated", async ({ page }) => {
  // Data source: the run's analogues, parent and pose counts from the API.
  const d = (await (await page.request.get(`/api/runs/${IDX.a_resolved}`)).json()) as {
    analogues: string[];
    parent: string;
    n_poses: Record<string, number>;
  };
  const target = d.analogues[d.analogues.length - 1]!;
  const lastPose = (d.n_poses[target] ?? 1) - 1;
  await page.goto(`/run/${IDX.a_resolved}/poses`);
  const wait = page.waitForRequest(
    (r) => r.url().includes("/scene?") && r.url().includes(`candidate=${encodeURIComponent(target)}`) && r.url().includes(`pose=${lastPose}`),
  );
  await page.getByLabel("Candidate").selectOption(target);
  await page.getByLabel("Pose", { exact: true }).selectOption(String(lastPose));
  await wait;
  await expect(page.getByTestId("pairing")).toContainText(`Pose ${lastPose} of ${target} is paired to pose ${lastPose} of ${d.parent}`);
});

test("a run without pose output says so instead of showing an empty canvas", async ({ page }) => {
  await page.goto(`/run/${IDX.e_noposes}/poses`);
  await expect(page.getByRole("alert")).toContainText("predates pose output");
  await expect(page.locator("[data-viewer]")).toHaveCount(0);
});

test("the Poses page links the run's PyMOL session download", async ({ page }) => {
  await page.goto(`/run/${IDX.a_resolved}/poses`);
  const link = page.getByRole("link", { name: "Download PyMOL session" });
  await expect(link).toHaveAttribute("href", `/api/runs/${IDX.a_resolved}/pymol.zip`);
});
