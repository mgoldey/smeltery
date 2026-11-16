/**
 * The notebook's attached runs, and what the Report page promises a colleague.
 *
 * Two defects are covered:
 *
 * 1. A notebook entry listed its attached runs as bare 64-character hashes with nothing to click, so the record
 *    keeper's archive had no way back into the app. The hash is the sha256 of the run record's *bytes*, which is
 *    deliberately not the run's id, and no route serves that mapping -- so the link is earned by reading the
 *    archived record back and matching its `input_digest` against the one `GET /api/runs` reports. A hash that
 *    matches nothing served here gets no link, because a wrong link is worse than a hash.
 * 2. The Report page let a colleague propose and said "waiting for review", which reads as a promise that someone
 *    will come back to them. Nothing does: a proposal has no status and no route to set one.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Notebook } from "./Notebook";
import { Report } from "./Report";

// The default 1s is a race, not a budget: vitest runs this file beside twenty others, each with its own
// jsdom, and a query that settles in 20ms unloaded can take longer than that when the box is saturated.
configure({ asyncUtilTimeout: 10_000 });

const RUN_DIGEST = "a".repeat(64);
/** The hash of the record's bytes: a different hash from the run's own digest, which is the whole difficulty. */
const RECORD_SHA = "b".repeat(64);
/** A record archived from a run this Bench no longer lists. */
const ORPHAN_SHA = "c".repeat(64);

let entries: unknown[] = [];
/** What `/api/notebook/artifacts/<sha>` answers with, by sha. A missing key 404s. */
let archived: Record<string, unknown> = {};

const RUNS = [{ id: "r1", path: "/r/one", campaign: "demo", digest: RUN_DIGEST, n_candidates: 2 }];

vi.mock("../api/client", async (orig) => {
  const real = await orig<typeof import("../api/client")>();
  const GET = async (path: string) => {
    const data =
      path === "/api/whoami"
        ? { user: "mg", root: "/r", read_only: false }
        : path === "/api/runs"
          ? RUNS
          : path === "/api/campaigns/{campaign_id}"
            ? { id: "c1", name: "A campaign", description: "", created_at: "", n_entries: 1, n_runs: 1, updated_at: "" }
            : path === "/api/campaigns/{campaign_id}/entries"
              ? entries
              : path === "/api/runs/{run_id}/verdict"
                ? { state: "RESOLVED", missing: [], floor: 0.5 }
                : path === "/api/runs/{run_id}"
                  ? {
                      id: "r1", path: "/r/one", out_dir: "/r/one/out", campaign: "demo", digest: RUN_DIGEST,
                      parent: "parent", candidates: { parent: "c1ccccc1" }, analogues: [], groups: [["parent"]],
                      survivors: ["parent"], floor: 0.5, floor_source: "L01",
                      floor_statement: "The floor is 0.5 kcal/mol.", z: 1.96, keep: 1, n_poses: {}, poses: {},
                      unit: "kcal/mol",
                    }
                  : path === "/api/limits"
                    ? { measured: [], unmeasured: [] }
                    : [];
    return { data, response: new Response(null, { status: 200 }) };
  };
  return { ...real, api: { GET } };
});

vi.mock("../components/Structure2D", () => ({
  Structure2D: ({ smiles }: { smiles: string }) => <span>{smiles}</span>,
}));
vi.mock("../components/PlotlyFigure", () => ({ PlotlyFigure: () => <div /> }));
vi.mock("../components/PoseExplorer", () => ({ PoseExplorer: () => <div /> }));

function entry(artefacts: string[]) {
  return {
    id: "e1",
    campaign_id: "c1",
    title: "First pass",
    body: "Ran the funnel.",
    author: "mg",
    created_at: "2026-03-01T10:00:00Z",
    artefacts,
    files: [],
    supersedes: null,
    superseded_by: null,
  };
}

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
  entries = [entry([RECORD_SHA])];
  archived = { [RECORD_SHA]: { input_digest: RUN_DIGEST } };
  // `useScheme` reads prefers-color-scheme; jsdom has no matchMedia.
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
  // The artifact route serves bytes, so the page fetches it directly rather than through the typed client.
  vi.stubGlobal("fetch", async (url: string) => {
    const sha = String(url).split("/").pop()?.split("?")[0] ?? "";
    if (!(sha in archived)) return new Response("no stored file", { status: 404 });
    return new Response(JSON.stringify(archived[sha]), { status: 200 });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a notebook entry's attached runs", () => {
  it("link to the run whose record was archived", async () => {
    renderAt(<Notebook />, "/campaigns/c1", "/campaigns/:id");
    const link = await waitFor(() => screen.getByRole("link", { name: RECORD_SHA }));
    expect(link.getAttribute("href")).toBe("/run/r1/results");
    // The hash stays on the page: it is the record of what was attached, and the link is a convenience over it.
    expect(link.textContent).toBe(RECORD_SHA);
    // Named, so one of several attached runs can be told from another.
    expect(screen.getByRole("list", { name: "Attached runs" }).textContent).toContain("demo");
  });

  it("stay a bare hash, with the archived bytes offered, when no run here has that record", async () => {
    entries = [entry([ORPHAN_SHA])];
    archived = { [ORPHAN_SHA]: { input_digest: "f".repeat(64) } };
    renderAt(<Notebook />, "/campaigns/c1", "/campaigns/:id");
    await waitFor(() => expect(screen.getByText(/no run with this record is served here/)).toBeTruthy());
    expect(screen.queryByRole("link", { name: ORPHAN_SHA })).toBeNull();
    expect(screen.getByRole("link", { name: "download the archived record" })).toBeTruthy();
  });

  it("claims nothing either way when the archived record cannot be read", async () => {
    entries = [entry([ORPHAN_SHA])];
    archived = {}; // the read 404s
    renderAt(<Notebook />, "/campaigns/c1", "/campaigns/:id");
    await waitFor(() => expect(screen.getByText("First pass")).toBeTruthy());
    expect(screen.getByText(ORPHAN_SHA)).toBeTruthy();
    expect(screen.queryByRole("link", { name: ORPHAN_SHA })).toBeNull();
    expect(screen.queryByText(/no run with this record is served here/)).toBeNull();
  });
});

describe("what the report tells a colleague who proposes", () => {
  it("names where the proposal lands and says nothing reports back", async () => {
    renderAt(<Report />, "/report/r1", "/report/:runId");
    const note = await waitFor(() => screen.getByTestId("proposal-afterwards"));
    expect(note.textContent).toContain("Nothing here reports back");
    expect(note.querySelector("a")?.getAttribute("href")).toBe("/run/r1/proposals");
  });
});
