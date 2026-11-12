import { expect, id, test } from "./bench";

/** Which files a page pulls down. The heavy libraries (Mol*, RDKit's wasm, Plotly) must cost nothing until a view
 * that draws with them opens. */
const HEAVY =
  /RDKit|plotly|molstar|mol-plugin|context-|parser-|behavior-|mvs-data/i;

async function assetsRequested(
  page: import("@playwright/test").Page,
  path: string,
  settle: () => Promise<void>,
) {
  const seen: { url: string; bytes: number }[] = [];
  page.on("response", async (r) => {
    const u = new URL(r.url());
    if (!u.pathname.startsWith("/assets/")) return;
    const body = await r.body().catch(() => Buffer.alloc(0));
    seen.push({ url: u.pathname, bytes: body.length });
  });
  await page.goto(path);
  await settle();
  await page.waitForLoadState("networkidle");
  return seen;
}

test("the landing page does not download Mol*, RDKit or Plotly", async ({
  page,
}, info) => {
  const seen = await assetsRequested(page, "/", async () => {
    await expect(page.getByRole("main")).toBeVisible();
  });
  const total = seen.reduce((n, s) => n + s.bytes, 0);
  info.annotations.push({
    type: "landing-bytes",
    description: `${total} bytes in ${seen.length} files: ${seen.map((s) => `${s.url}=${s.bytes}`).join(" ")}`,
  });
  console.log(
    `LANDING ${total} bytes: ${seen.map((s) => `${s.url}=${s.bytes}`).join(" ")}`,
  );
  expect(seen.filter((s) => HEAVY.test(s.url)).map((s) => s.url)).toEqual([]);
  expect(total).toBeLessThan(700_000);
});

test("opening the pose view does download the Mol* chunks", async ({
  page,
}) => {
  const seen = await assetsRequested(
    page,
    `/run/${id("a_resolved")}/poses`,
    async () => {
      await expect(page.locator("[data-viewer]")).toBeVisible();
    },
  );
  console.log(
    `POSES ${seen.reduce((n, s) => n + s.bytes, 0)} bytes: ${seen.map((s) => `${s.url}=${s.bytes}`).join(" ")}`,
  );
  expect(seen.some((s) => /context-|parser-|behavior-/.test(s.url))).toBe(true);
});

test("opening a report downloads Plotly and the RDKit wasm, which the landing page did not", async ({
  page,
}) => {
  const seen = await assetsRequested(
    page,
    `/report/${id("a_resolved")}`,
    async () => {
      await page
        .locator(".js-plotly-plot")
        .first()
        .waitFor({ timeout: 30_000 });
    },
  );
  console.log(
    `REPORT ${seen.reduce((n, s) => n + s.bytes, 0)} bytes: ${seen.map((s) => `${s.url}=${s.bytes}`).join(" ")}`,
  );
  expect(seen.some((s) => /plotly-cartesian/.test(s.url))).toBe(true);
  expect(seen.some((s) => /RDKit.*\.wasm/i.test(s.url))).toBe(true);
});

test("the server caches hashed assets for good and never caches index.html that way", async ({
  page,
}) => {
  const html = await page.request.get("/bench");
  expect(html.headers()["cache-control"]).toBe("no-cache");
  const asset = /\/assets\/index-[^"]+\.js/.exec(await html.text())?.[0];
  expect(asset).toBeTruthy();
  expect((await page.request.get(asset!)).headers()["cache-control"]).toContain(
    "immutable",
  );
});
