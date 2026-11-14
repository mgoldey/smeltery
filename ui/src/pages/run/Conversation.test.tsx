/**
 * The chemist/colleague conversation, and what a read-only Bench may say about it.
 *
 * Three defects are covered, each one a page telling the reader something that is not true of their server:
 *
 * 1. Proposals instructed a reader on a `--read-only` Bench to "tick the ones you agree with" and showed them the
 *    boxes to do it with, while the only button that consumes a tick is wrapped in `Writable` and therefore absent.
 *    The queue itself must stay readable: a published Bench exists to be read.
 * 2. The Proposals tab looked identical whether a colleague had written five proposals or none, so the chemist had
 *    no signal that anything was waiting, and comments were readable only from the Report page the chemist does not
 *    visit.
 * 3. The Report page let a colleague propose and then went quiet about it. There is no status on a proposal and no
 *    route to set one, so the honest fix is prose that says where it landed and that nothing reports back.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Proposals } from "./Proposals";
import { RunLayout } from "./RunLayout";

// The default 1s is a race, not a budget: vitest runs this file beside twenty others, each with its own jsdom, and
// a query that settles in 20ms unloaded can take longer than that when the box is saturated. Waiting longer costs
// nothing when the assertion is going to pass.
configure({ asyncUtilTimeout: 10_000 });

let readOnly = false;
let proposals: unknown[] = [];
let comments: unknown[] = [];

const RUNS = [{ id: "r1", path: "/r/one", campaign: "demo", digest: "d".repeat(64), n_candidates: 2 }];

vi.mock("../../api/client", async (orig) => {
  const real = await orig<typeof import("../../api/client")>();
  const GET = async (path: string) => {
    const data =
      path === "/api/whoami"
        ? { user: "mg", root: "/r", read_only: readOnly }
        : path === "/api/runs"
          ? RUNS
          : path === "/api/proposals"
            ? proposals
            : path === "/api/runs/{run_id}/comments"
              ? comments
              : [];
    return { data, response: new Response(null, { status: 200 }) };
  };
  return { ...real, api: { GET } };
});

/** The 2D depiction loads RDKit from a wasm bundle, which jsdom has no business running. */
vi.mock("../../components/Structure2D", () => ({
  Structure2D: ({ smiles }: { smiles: string }) => <span data-testid="structure">{smiles}</span>,
}));

function proposal(smiles: string) {
  return {
    id: `p-${smiles}`,
    run: "d".repeat(64),
    smiles,
    reason: "substitution",
    note: "cheaper than the chloro",
    candidate: "",
    author: "bench-chemist",
    created_at: "2026-03-01T10:00:00Z",
    status: "pending",
  };
}

function comment(body: string) {
  return { id: `c-${body}`, anchor: "run", author: "bench-chemist", body, created_at: "2026-03-01T11:00:00Z" };
}

function renderAt(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/run/r1/proposals"]}>
        <Routes>
          <Route path="/run/:runId/proposals" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  readOnly = false;
  proposals = [proposal("Cc1ccccc1")];
  comments = [];
});

afterEach(() => {
  cleanup();
});

describe("the proposals queue on a read-only Bench", () => {
  it("is readable, and instructs nothing that cannot be done", async () => {
    readOnly = true;
    renderAt(<Proposals />);
    // The queue is the point: the proposal and everything said about it stay on the page.
    await waitFor(() => expect(screen.getByRole("table", { name: "Review queue" })).toBeTruthy());
    expect(screen.getAllByText("Cc1ccccc1").length).toBeGreaterThan(0); // the depiction and the SMILES cell
    expect(screen.getByText("cheaper than the chloro")).toBeTruthy();
    expect(screen.getByText("bench-chemist")).toBeTruthy();

    // What goes is every instruction and control that needs a write.
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/Tick the ones you agree with/i)).toBeNull();
    expect(screen.queryByText(/Tick a proposal to add it/i)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Take these into the next round" })).toBeNull();
    expect(screen.queryByTestId("take-proposals")).toBeNull();
    // And the page says why, rather than leaving a table with no explanation of the missing verb.
    expect(screen.getByText(/This Bench is\s+read-only/)).toBeTruthy();
  });

  it("does not send a reader to the report to propose, where the form is hidden too", async () => {
    readOnly = true;
    proposals = [];
    renderAt(<Proposals />);
    await waitFor(() => expect(screen.getByTestId("empty-state")).toBeTruthy());
    expect(screen.getByTestId("empty-state").textContent).toContain("Nothing has been proposed");
    expect(screen.queryByRole("link", { name: "Open the report" })).toBeNull();
  });

  it("on a writable Bench the boxes, the button and the report link are all there", async () => {
    renderAt(<Proposals />);
    await waitFor(() => expect(screen.getByTestId("take-proposals")).toBeTruthy());
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);
    expect(screen.getByText(/Tick the ones you agree with/)).toBeTruthy();
    cleanup();
    proposals = [];
    renderAt(<Proposals />);
    await waitFor(() => expect(screen.getByRole("link", { name: "Open the report" })).toBeTruthy());
  });
});

describe("what a colleague sent back", () => {
  it("shows the run's comments on a run tab, not only on the report", async () => {
    comments = [comment("the nitro worries me")];
    renderAt(<Proposals />);
    await waitFor(() => expect(screen.getByText("the nitro worries me")).toBeTruthy());
    // Inside the run tab's own Discussion section, not somewhere incidental.
    expect(screen.getByRole("region", { name: "Discussion" }).textContent).toContain("the nitro worries me");
  });

  it("counts proposals and comments on the Proposals tab", async () => {
    comments = [comment("one"), comment("two")];
    proposals = [proposal("Cc1ccccc1"), proposal("CCO")];
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/run/r1/proposals"]}>
          <Routes>
            <Route path="/run/:runId" element={<RunLayout />}>
              <Route path="proposals" element={<p>the queue</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("proposals-waiting").textContent).toBe("4"));
    expect(screen.getByRole("link", { name: "Proposals, 4 waiting" })).toBeTruthy();
  });

  it("says nothing on the tab when nothing is waiting", async () => {
    proposals = [];
    comments = [];
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/run/r1/proposals"]}>
          <Routes>
            <Route path="/run/:runId" element={<RunLayout />}>
              <Route path="proposals" element={<p>the queue</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByRole("link", { name: "Proposals" })).toBeTruthy());
    expect(screen.queryByTestId("proposals-waiting")).toBeNull();
  });
});
