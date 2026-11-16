/** How fast the 3D viewer and the figures actually are, measured rather than assumed.
 *
 * Why this exists: "the 3D and plots are weak in a browser" is a reasonable suspicion and an expensive thing to act
 * on -- replacing Mol* with a native viewer is weeks of work. Mol* draws through WebGL, the same GPU path a native
 * viewer uses, so a slow scene is more likely our setup (how much of the receptor we ask for, which representation,
 * how many redraws) than the browser. These numbers say which.
 *
 * Not a pass/fail gate: the thresholds are loose, and the point is the printed report. Run with BENCH_PERF=1.
 */
import { expect, IDX, test } from "./bench";

const RUN_PERF = process.env.BENCH_PERF === "1";

type Timing = { label: string; ms: number; detail?: string };
const timings: Timing[] = [];

function record(label: string, ms: number, detail?: string) {
  timings.push({ label, ms, detail });
}

test.describe(() => {
  test.skip(!RUN_PERF, "set BENCH_PERF=1 to measure viewer and figure performance");

  test.afterAll(() => {
    const rows = timings.map((t) => `  ${t.label.padEnd(44)} ${String(Math.round(t.ms)).padStart(6)} ms${t.detail ? `   ${t.detail}` : ""}`);
    console.log(`\nviewer / figure timings (this machine, this fixture)\n${rows.join("\n")}\n`);
  });

  test("the pose viewer's time to first structure, and to an interactive frame", async ({ page }) => {
    await page.goto(`/run/${IDX.a_resolved}/poses`);
    const host = page.locator('[data-viewer="pose viewer"]');
    const t0 = Date.now();
    await expect.poll(async () => Number(await host.getAttribute("data-loaded")), { timeout: 60_000 }).toBeGreaterThanOrEqual(2);
    record("pose viewer: structures loaded", Date.now() - t0);

    // Frame rate while the camera moves: the number a user feels when they drag the scene.
    const fps = await page.evaluate(async () => {
      const canvas = document.querySelector('[data-viewer="pose viewer"] canvas') as HTMLCanvasElement | null;
      if (!canvas) return null;
      let frames = 0;
      const start = performance.now();
      return await new Promise<number>((resolve) => {
        const tick = () => {
          frames += 1;
          if (performance.now() - start >= 1000) return resolve(frames);
          requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
    });
    record("pose viewer: frames in 1 s (idle)", 0, fps === null ? "no canvas" : `${fps} fps`);
    expect(fps, "the viewer should reach a usable frame rate").not.toBeNull();
  });

  test("how much of the receptor the pocket scene asks the GPU to draw", async ({ page }) => {
    await page.goto(`/structure`);
    const host = page.locator("[data-viewer]").first();
    const t0 = Date.now();
    await expect.poll(async () => Number(await host.getAttribute("data-loaded")), { timeout: 60_000 }).toBeGreaterThanOrEqual(1);
    record("pocket scene: loaded", Date.now() - t0);

    // WebGL draw calls and buffer sizes are the cost; a native viewer would pay the same for the same scene.
    const gl = await page.evaluate(() => {
      const canvas = document.querySelector("[data-viewer] canvas") as HTMLCanvasElement | null;
      if (!canvas) return null;
      const ctx = (canvas.getContext("webgl2") ?? canvas.getContext("webgl")) as WebGLRenderingContext | null;
      return {
        width: canvas.width,
        height: canvas.height,
        dpr: window.devicePixelRatio,
        renderer: ctx ? String(ctx.getParameter(ctx.RENDERER)) : "none",
      };
    });
    record("pocket scene: canvas", 0, gl ? `${gl.width}x${gl.height} @${gl.dpr}x on ${gl.renderer}` : "no canvas");
  });

  test("how long the figures take to draw", async ({ page }) => {
    const t0 = Date.now();
    await page.goto(`/run/${IDX.b_unranked}/results`);
    await expect(page.locator('[data-figure="forest"] .main-svg').first()).toBeVisible();
    record("results page: first figure visible", Date.now() - t0);

    const t1 = Date.now();
    for (const name of ["forest", "matrix", "per_pose"]) {
      await expect(page.locator(`[data-figure="${name}"] .main-svg`).first()).toBeVisible();
    }
    record("results page: all three figures", Date.now() - t1);

    const nodes = await page.locator(".figure .main-svg *").count();
    record("results page: SVG nodes drawn", 0, `${nodes} nodes`);
  });
});
