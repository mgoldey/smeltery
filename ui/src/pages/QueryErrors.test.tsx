/**
 * A failed fetch must not look like an empty result.
 *
 * The defect these cover: a page gated on `q.data` renders nothing when `q` errors, so the reader sees a verdict
 * over blank space and concludes the run has no analogues, no candidates and no poses. Each test fails the one
 * endpoint a section depends on and asserts a visible message naming the failure.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/http";
import { Report } from "./Report";
import { Results } from "./run/Results";

/**
 * Which GETs fail with a 500 for the current test, by exact route — or, for the figures route, by figure name.
 * Exact, because `/api/runs/{run_id}` is a prefix of `/api/runs/{run_id}/verdict`, and failing the verdict too
 * would hide the very section the test is about behind VerdictBanner's own alert.
 */
let failing: string[] = [];

const VERDICT = { state: "RESOLVED", missing: [], floor: 0.5 };
const RUN = {
  unit: "kcal/mol",
  floor_statement: "Differences below 0.5 kcal/mol are inside the floor.",
  floor_source: "L01",
  groups: [["parent"], ["an1"]],
  analogues: ["an1"],
  n_poses: { an1: 1 },
  survivors: ["an1"],
  keep: 1,
  z: 1.96,
  candidates: ["parent", "an1"],
  poses: {},
};
const DDE = {
  unit: "kcal/mol",
  z: 1.96,
  rows: [
    { candidate: "an1", smiles: "CC", mean: -1.2, sem: 0.3, lo: -1.8, hi: -0.6, n: 8, group: 0, survivor: true },
  ],
};

vi.mock("../api/client", async (orig) => {
  const real = await orig<typeof import("../api/client")>();
  const body = (path: string): unknown => {
    if (path.endsWith("/verdict")) return VERDICT;
    if (path.endsWith("/ddE")) return DDE;
    if (path.endsWith("/figures/{name}")) return { data: [], layout: {} };
    if (path === "/api/runs/{run_id}") return RUN;
    if (path === "/api/whoami") return { user: "mg", root: "/r", read_only: true };
    if (path === "/api/limits") return { measured: [], unmeasured: [] };
    return [];
  };
  const GET = async (path: string, init?: { params?: { path?: Record<string, string> } }) => {
    // Figures share one route, so a test that fails "forest" names the figure, not the route.
    const key = path.endsWith("/figures/{name}") ? (init?.params?.path?.name ?? "") : path;
    if (failing.includes(key))
      throw new ApiError(500, "The server hit an error (500). Check the terminal running `smeltery bench`.");
    return { data: body(path), response: new Response(null, { status: 200 }) };
  };
  return { ...real, api: { GET } };
});

function renderAt(ui: React.ReactNode, path: string, route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  failing = [];
  // `useScheme` reads prefers-color-scheme; jsdom has no matchMedia.
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Results when a query fails", () => {
  it("says the ΔΔE table could not be read instead of rendering nothing under the verdict", async () => {
    failing = ["/api/runs/{run_id}/ddE"];
    renderAt(<Results />, "/run/r1/results", "/run/:runId/results");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/paired ΔΔE table could not be read/i);
    expect(alert.textContent).toMatch(/500/);
    // The error must not be dressed as an empty result.
    expect(screen.queryByTestId("empty-state")).toBeNull();
  });

  it("still renders the table when only a figure fails, and names the figure", async () => {
    failing = ["forest"];
    renderAt(<Results />, "/run/r1/results", "/run/:runId/results");
    const table = await screen.findByRole("table", { name: "Paired ΔΔE" });
    expect(table).toBeTruthy();
    expect(screen.getByRole("main").textContent).toMatch(/forest plot could not be drawn/i);
    expect(screen.queryByRole("alert")).toBeNull(); // one missing figure is not a page-level failure
  });
});

describe("Report when a query fails", () => {
  it("says the candidates could not be listed instead of dropping the section", async () => {
    failing = ["/api/runs/{run_id}/ddE"];
    renderAt(<Report />, "/report/r1", "/report/:runId");
    const section = await screen.findByRole("region", { name: "Candidates" });
    const banner = section.querySelector('[role="alert"]');
    expect(banner?.textContent).toMatch(/paired ΔΔE table could not be read/i);
    expect(screen.queryByTestId("mean-an1")).toBeNull(); // nothing is invented in its place
  });

  it("says the run record could not be read instead of dropping the floor statement and the poses", async () => {
    failing = ["/api/runs/{run_id}"];
    renderAt(<Report />, "/report/r1", "/report/:runId");
    const banner = await screen.findByText((_t, el) => {
      if (el?.getAttribute("role") !== "alert") return false;
      return /run record could not be read/i.test(el.textContent ?? "");
    });
    expect(banner).toBeTruthy();
    const poses = await screen.findByRole("region", { name: "Poses" });
    expect(poses.textContent).toMatch(/poses could not be loaded/i);
    expect(screen.queryByTestId("floor-statement")).toBeNull();
  });
});
