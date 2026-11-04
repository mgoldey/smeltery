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

/** Run indices, fixed by construction: serve_fixture.py lists runs in path order, and the names sort a < b < c.
 * Usable when tests are collected, before the server has written .fixture.json; shell.spec.ts checks them. */
export const IDX = { a_resolved: 0, b_unranked: 1, c_incomplete: 2, d_floor: 3, e_noposes: 4 } as const;

export function run(name: keyof typeof IDX): FixtureRun {
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
