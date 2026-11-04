import type { Locator } from "@playwright/test";
import { PNG } from "pngjs";

/** How many distinct colours a screenshot of `el` contains. A blank or uniform canvas has one or two. */
export async function distinctColours(el: Locator): Promise<number> {
  const png = PNG.sync.read(await el.screenshot());
  const seen = new Set<number>();
  for (let i = 0; i < png.data.length; i += 4 * 7) {
    seen.add((png.data[i]! << 16) | (png.data[i + 1]! << 8) | png.data[i + 2]!);
    if (seen.size > 64) break;
  }
  return seen.size;
}

/** The colour of the pixel `inset` px in from the top-left of `el`, as [r, g, b]. */
export async function cornerColour(el: Locator, inset = 6): Promise<[number, number, number]> {
  const png = PNG.sync.read(await el.screenshot());
  const i = (inset * png.width + inset) * 4;
  return [png.data[i]!, png.data[i + 1]!, png.data[i + 2]!];
}
