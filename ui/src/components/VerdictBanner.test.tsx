import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The banner headlined "Keep 4-F" in the largest type on the page for a run where both analogues scored worse than
 * the parent and no systematic bound was applied. The headline stays the cut's decision; under it the banner now
 * carries the run's floor statement and, when no kept analogue beats the parent, says so.
 *
 * ΔΔE is analogue minus parent (`funnel.paired_delta`): the parent is the reference at 0 with no row, and lower is
 * better (the cut keeps the lowest).
 */

const STATEMENT = "The floor is 0 kcal/mol: no systematic bound was applied, and a ranking may sit inside a systematic error.";

type Row = { candidate: string; mean: number; survivor: boolean | null };
let state: "RESOLVED" | "UNRANKED" | "INCOMPLETE" = "RESOLVED";
let survivors: string[] = ["4-F"];
let rows: Row[] = [];

const row = (r: Row, group: number) => ({
  ...r,
  smiles: "C",
  sem: 0.2,
  n: 4,
  per_pose: [r.mean],
  lo: r.mean - 0.4,
  hi: r.mean + 0.4,
  unpaired_sem: null,
  group,
});

vi.mock("../api/client", async (orig) => {
  const real = await orig<typeof import("../api/client")>();
  const verdict = () => ({ state, winner: state === "RESOLVED" ? survivors[0] : null, floor: 0, missing: [] });
  const GET = async (path: string) => {
    const data =
      path === "/api/runs/{run_id}/verdict"
        ? verdict()
        : path === "/api/runs/{run_id}"
          ? {
              id: "r1",
              survivors: state === "RESOLVED" ? survivors : null,
              floor: 0,
              floor_source: "config [cut] floor",
              floor_statement: STATEMENT,
              z: 2,
              keep: survivors.length,
              unit: "kcal/mol",
              verdict: verdict(),
            }
          : path === "/api/runs/{run_id}/ddE"
            ? { rows: rows.map(row), floor: 0, floor_source: "config", keep: 1, z: 2, unit: "kcal/mol", verdict: verdict() }
            : null;
    return { data, response: new Response(null, { status: 200 }) };
  };
  return { ...real, api: { GET } };
});

const { VerdictBanner, keptNoBetterThanParent } = await import("./VerdictBanner");

function renderBanner(props: { floorStatement?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <VerdictBanner runId="r1" {...props} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  state = "RESOLVED";
  survivors = ["4-F"];
  rows = [];
});

describe("the verdict banner carries its caveats", () => {
  it("puts the floor statement under a resolved headline, which is unchanged", async () => {
    rows = [{ candidate: "4-F", mean: -1.2, survivor: true }];
    renderBanner();
    await waitFor(() => expect(screen.getByTestId("verdict-floor-statement")).toBeTruthy());
    expect(screen.getByTestId("verdict-floor-statement").textContent).toBe(STATEMENT);
    expect(screen.getByLabelText("Verdict").querySelector(".verdict-headline")?.textContent).toBe("Keep 4-F");
    // the analogue is below the parent (negative ΔΔE): nothing to caveat
    expect(screen.queryByTestId("verdict-not-better")).toBeNull();
  });

  it("puts the floor statement under an unranked headline too", async () => {
    state = "UNRANKED";
    renderBanner();
    await waitFor(() => expect(screen.getByTestId("verdict-floor-statement")).toBeTruthy());
    expect(screen.getByLabelText("Verdict").textContent).toContain("Does not resolve");
  });

  it("has no floor statement when there is no result", async () => {
    state = "INCOMPLETE";
    renderBanner();
    await waitFor(() => expect(screen.getByLabelText("Verdict").textContent).toContain("Incomplete"));
    expect(screen.queryByTestId("verdict-floor-statement")).toBeNull();
  });

  it("leaves the floor statement to a page that prints its own", async () => {
    rows = [{ candidate: "4-F", mean: 1.81, survivor: true }];
    renderBanner({ floorStatement: false });
    await waitFor(() => expect(screen.getByTestId("verdict-not-better")).toBeTruthy());
    expect(screen.queryByTestId("verdict-floor-statement")).toBeNull();
  });

  it("says a kept analogue that scored worse than the parent did not beat it (the reviewed run)", async () => {
    rows = [
      { candidate: "4-F", mean: 1.81, survivor: true },
      { candidate: "4-Cl", mean: 5.02, survivor: false },
    ];
    renderBanner();
    await waitFor(() => expect(screen.getByTestId("verdict-not-better")).toBeTruthy());
    const text = screen.getByTestId("verdict-not-better").textContent ?? "";
    expect(text).toContain("4-F does not score better than the parent");
    expect(text).toContain("+1.81 kcal/mol");
    expect(text).toContain("lower is better");
    expect(text).not.toContain("4-Cl"); // only the kept set is the banner's claim
    expect(screen.getByLabelText("Verdict").querySelector(".verdict-headline")?.textContent).toBe("Keep 4-F");
  });
});

describe("keptNoBetterThanParent", () => {
  const r = (candidate: string, mean: number) => row({ candidate, mean, survivor: true }, 0);
  it("is the kept rows when every one is at or above the parent's 0", () => {
    expect(keptNoBetterThanParent(["a", "b"], [r("a", 0), r("b", 2), r("c", -3)])?.map((x) => x.candidate)).toEqual(["a", "b"]);
  });
  it("is null when any kept analogue is below the parent", () => {
    expect(keptNoBetterThanParent(["a", "b"], [r("a", -0.1), r("b", 2)])).toBeNull();
  });
  it("is null rather than a guess when a kept name has no row, or nothing is kept", () => {
    expect(keptNoBetterThanParent(["a"], [])).toBeNull();
    expect(keptNoBetterThanParent([], [r("a", 1)])).toBeNull();
  });
});
