import { describe, expect, it } from "vitest";
import { runByRef } from "./client";
import type { RunSummary } from "./client";

const runs = [
  { id: "aaaaaaaaaaaa-111111", path: "/root/a/run-aaaaaaaaaaaa.json" },
  { id: "bbbbbbbbbbbb-222222", path: "/root/b/run-bbbbbbbbbbbb.json" },
] as RunSummary[];

describe("runByRef", () => {
  it("matches a stable id wherever it sits in the listing", () => {
    expect(runByRef(runs, "bbbbbbbbbbbb-222222")?.path).toBe("/root/b/run-bbbbbbbbbbbb.json");
    // The whole point: prepending a run must not change which record an id names.
    const withEarlier = [{ id: "cccccccccccc-333333", path: "/root/0/run-c.json" } as RunSummary, ...runs];
    expect(runByRef(withEarlier, "bbbbbbbbbbbb-222222")?.path).toBe("/root/b/run-bbbbbbbbbbbb.json");
  });

  it("still reads an old all-digits link as a position, as the API does", () => {
    expect(runByRef(runs, "0")?.id).toBe("aaaaaaaaaaaa-111111");
    expect(runByRef(runs, "1")?.id).toBe("bbbbbbbbbbbb-222222");
  });

  it("is undefined for an unknown reference, an out-of-range position, or nothing to look in", () => {
    expect(runByRef(runs, "deadbeefcafe-999999")).toBeUndefined();
    expect(runByRef(runs, "7")).toBeUndefined();
    expect(runByRef(undefined, "0")).toBeUndefined();
    expect(runByRef(runs, undefined)).toBeUndefined();
  });
});
