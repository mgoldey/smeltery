import { test as base, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export interface FixtureRun {
  idx: number;
  name: string;
  path: string;
  out_dir: string;
  campaign: string;
  digest: string;
}
export interface Fixture {
  root: string;
  runs: FixtureRun[];
  study: string;
  pdb: string;
}

/** What e2e/serve_fixture.py built: the source of every expected value in these tests. */
export function fixture(): Fixture {
  const here = dirname(fileURLToPath(import.meta.url));
  return JSON.parse(readFileSync(resolve(here, ".fixture.json"), "utf8")) as Fixture;
}

export function run(name: "a_resolved" | "b_unranked" | "c_incomplete"): FixtureRun {
  const r = fixture().runs.find((x) => x.name === name);
  if (!r) throw new Error(`no fixture run ${name}`);
  return r;
}

/** Every test runs offline: a request to any host other than localhost is aborted, and fails the test. */
export const test = base.extend<{ noExternal: string[] }>({
  noExternal: [
    async ({ page }, use) => {
      const external: string[] = [];
      await page.route("**/*", (route) => {
        const host = new URL(route.request().url()).hostname;
        if (host === "127.0.0.1" || host === "localhost") return route.continue();
        external.push(route.request().url());
        return route.abort();
      });
      await use(external);
      expect(external, "the app requested an external origin").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
