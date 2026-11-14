import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The lineage panel is reachable on a read-only Bench: Results has no `NeedsWrite` guard, and it should not have
 * one -- tracing a number is a read. But both of its buttons call `useOpenRunPipeline`, which POSTs
 * `/api/pipelines` when the pipeline does not exist yet, and `--read-only` refuses every unsafe method in one
 * middleware. So on a read-only server those two buttons could only ever produce a 403. They are now wrapped in
 * `Writable`; the lineage itself must stay readable beside them.
 */

let readOnly = false;

const LINEAGE = {
  candidate: "lig-7",
  parent: "parent-0",
  quantity: "ddE",
  mean: -1.25,
  sem: 0.3,
  n: 2,
  poses: [
    { pose: 1, value: -3.5, parent_value: -2.25, difference: -1.25 },
    { pose: 2, value: -3.0, parent_value: -1.75, difference: -1.25 },
  ],
  steps: [{ stage: "measure", kind: "rhf", settings: { basis: "sto-3g" }, seconds: 12.0 }],
  field: { n_charges: 42, provenance: null },
  engine: {},
  smeltery: null,
  digest: "abcdef0123456789",
  config: null,
  calcs: null,
  started_utc: "2026-01-01T00:00:00Z",
  missing: ["config"],
};

/** Both GETs the panel's tree makes: the lineage itself, and the whoami that `Writable` reads. */
vi.mock("../api/client", async (orig) => {
  const real = await orig<typeof import("../api/client")>();
  const GET = async (path: string) => {
    const data =
      path === "/api/whoami"
        ? { user: "mg", root: "/r", read_only: readOnly }
        : path === "/api/runs/{run_id}/lineage"
          ? LINEAGE
          : [];
    return { data, response: new Response(null, { status: 200 }) };
  };
  return { ...real, api: { GET } };
});

const { LineageInspector } = await import("./LineageInspector");

function renderPanel(ro: boolean) {
  readOnly = ro;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <LineageInspector runId="7" candidate="lig-7" onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const OPEN = "Open the pipeline that made this";
const RERUN = "Change a setting and re-run";

afterEach(() => {
  cleanup();
});

describe("the lineage panel on a read-only server", () => {
  it("offers neither button that would open the pipeline editor", async () => {
    renderPanel(true);
    // Wait for the lineage to arrive: before it does, nothing below the heading renders at all, so an
    // assertion made too early would pass without proving anything about the guard.
    expect(await screen.findByRole("table", { name: "Per-pose values" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: OPEN })).toBeNull();
    expect(screen.queryByRole("button", { name: RERUN })).toBeNull();
  });

  it("still shows the lineage it exists to show, and no stray empty controls row", async () => {
    const { container } = renderPanel(true);
    const poses = await screen.findByRole("table", { name: "Per-pose values" });
    expect(poses.querySelectorAll("tbody tr")).toHaveLength(LINEAGE.poses.length);
    expect(screen.getByRole("region", { name: "Made by" })).toBeTruthy();
    expect(screen.getByText(/the mean over 2 paired poses/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
    expect(container.querySelectorAll("p.controls")).toHaveLength(0);
  });

  it("offers both on a writable server", async () => {
    renderPanel(false);
    expect(await screen.findByRole("button", { name: OPEN })).toBeTruthy();
    expect(screen.getByRole("button", { name: RERUN })).toBeTruthy();
  });
});
