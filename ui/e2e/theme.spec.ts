import { expect, IDX, test } from "./bench";
import { cornerColour } from "./pixels";

/** sRGB relative luminance, 0 (black) to 1 (white). */
function luminance(rgb: string | [number, number, number]): number {
  const [r, g, b] = typeof rgb === "string" ? (rgb.match(/\d+(\.\d+)?/g) ?? []).slice(0, 3).map(Number) : rgb;
  const lin = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r!) + 0.7152 * lin(g!) + 0.0722 * lin(b!);
}

test.describe("dark mode", () => {
  test.use({ colorScheme: "dark" });

  test("every word in every figure is light enough to read on the dark page", async ({ page }) => {
    await page.goto(`/run/${IDX.a_resolved}/results`);
    for (const name of ["forest", "matrix", "per_pose"]) {
      await expect(page.locator(`[data-figure="${name}"] .main-svg`).first()).toBeVisible();
    }
    // Data source: the fill Plotly actually painted on each text node, not the figure JSON.
    const fills = await page
      .locator(".figure .main-svg text")
      .evaluateAll((els) => els.filter((e) => (e.textContent ?? "").trim()).map((e) => getComputedStyle(e).fill));
    expect(fills.length).toBeGreaterThan(10);
    const dark = fills.filter((f) => luminance(f) < 0.25);
    expect(dark, "text drawn in a light-theme colour").toEqual([]);
  });

  test("the 3D viewer's background is the dark panel, also after a structure loads", async ({ page }) => {
    await page.goto(`/run/${IDX.a_resolved}/poses`);
    const host = page.locator('[data-viewer="pose viewer"]');
    await expect.poll(async () => Number(await host.getAttribute("data-loaded")), { timeout: 30_000 }).toBeGreaterThanOrEqual(2);
    await expect.poll(async () => luminance(await cornerColour(host)), { timeout: 10_000 }).toBeLessThan(0.05);
  });
});
