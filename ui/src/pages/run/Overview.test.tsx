import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * Provenance never said what the round carried in.
 *
 * `results.decisions_in` -- the decisions file, its sha256 and the candidates rejected before the run -- has been in
 * the record since `[decisions]` existed, and this page showed neither it nor the verdict. So a round built from the
 * previous round's judgement looked identical to one built from nothing, and the only trace of what had been dropped
 * was a comment inside the config text at the bottom of the page.
 *
 * Three states, kept apart: no `[decisions]` carried nothing in; a file that rejected nobody was read and excluded
 * no one; a file that rejected candidates names them.
 */

type Carried = { file: string; sha256: string; excluded: string[] } | null;

let carried: Carried = null;
/** The detail read fails (as it does when the record has no cut). */
let incomplete = false;
/** ...and the verdict reports INCOMPLETE, which is the case the verdict banner already states. */
let verdictIncomplete = false;

const SHA = "0123456789abcdef".repeat(4);

const DETAIL = () => ({
  id: "r1",
  path: "/runs/out/run-abc.json",
  out_dir: "/runs/out",
  campaign: "halogens",
  digest: "abc123def4567890",
  parent: "benzoic",
  candidates: { benzoic: "OC(=O)c1ccccc1", "4-F": "F" },
  analogues: ["4-F"],
  groups: [["4-F"]],
  survivors: ["4-F"],
  floor: 0,
  floor_source: "config [cut] floor",
  floor_statement: "",
  z: 2,
  keep: 1,
  unranked: false,
  has_pose_output: true,
  n_poses: { "4-F": 4 },
  unit: "kcal/mol",
  verdict: { state: "RESOLVED", winner: "4-F", floor: 0, missing: [] },
  decisions_in: carried,
});

vi.mock("../../api/client", async (orig) => {
  const real = await orig<typeof import("../../api/client")>();
  const GET = async (path: string) => {
    if (incomplete && path === "/api/runs/{run_id}") {
      return { error: { reason: "the record has no cut" }, response: new Response(null, { status: 409 }) };
    }
    const data =
      path === "/api/whoami"
        ? { user: "mg", root: "/r", read_only: false }
        : path === "/api/runs/{run_id}"
          ? DETAIL()
          : path === "/api/runs/{run_id}/verdict"
            ? verdictIncomplete
              ? { state: "INCOMPLETE", winner: null, floor: null, missing: ["cut"] }
              : DETAIL().verdict
            : path === "/api/runs/{run_id}/dropped_poses"
              ? { pose_index: {}, dropped: [] }
              : path === "/api/runs/{run_id}/config"
                ? { file: null, sha256: null, text: null, reason: "no config was staged for this run" }
                : [];
    return { data, response: new Response(null, { status: 200 }) };
  };
  return { ...real, api: { GET } };
});

const { Overview } = await import("./Overview");

/** `stopped`: the detail read fails. `resolvedVerdict`: ...but the verdict is readable and not INCOMPLETE, so the
 * failure is one the verdict cannot account for. */
function renderPage(c: Carried, { stopped = false, resolvedVerdict = false } = {}) {
  carried = c;
  incomplete = stopped;
  verdictIncomplete = stopped && !resolvedVerdict;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/run/r1/provenance"]}>
        <Routes>
          <Route path="/run/:runId/provenance" element={<Overview />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe("Provenance says what this round carried in", () => {
  it("names the decisions file, its digest and the candidates it excluded", async () => {
    renderPage({ file: "decisions-abc123def456.json", sha256: SHA, excluded: ["4-Cl", "4-Br"] });
    await waitFor(() => expect(screen.getByTestId("carried-in")).toBeTruthy());
    const text = screen.getByTestId("carried-in").textContent ?? "";
    expect(text).toContain("decisions-abc123def456.json");
    expect(text).toContain(SHA.slice(0, 12));
    const excluded = screen.getByTestId("carried-excluded").textContent ?? "";
    expect(excluded).toContain("4-Cl");
    expect(excluded).toContain("4-Br");
  });

  it("says a run with no [decisions] carried nothing in, rather than leaving a blank", async () => {
    renderPage(null);
    await waitFor(() => expect(screen.getByTestId("carried-none")).toBeTruthy());
    expect(screen.getByTestId("carried-none").textContent).toContain("carried no decisions in");
    expect(screen.queryByTestId("carried-in")).toBeNull();
  });

  it("keeps 'read a file and rejected nobody' apart from 'there was no file'", async () => {
    renderPage({ file: "d.json", sha256: SHA, excluded: [] });
    await waitFor(() => expect(screen.getByTestId("carried-in")).toBeTruthy());
    expect(screen.getByTestId("carried-excluded-none").textContent).toContain("rejected nobody");
    expect(screen.queryByTestId("carried-excluded")).toBeNull();
    expect(screen.queryByTestId("carried-none")).toBeNull();
  });

  it("shows the run's verdict, so the archived page carries the reading as well as the record", async () => {
    renderPage(null);
    await waitFor(() => expect(screen.getByLabelText("Verdict")).toBeTruthy());
    expect(screen.getByLabelText("Verdict").textContent).toContain("Keep 4-F");
  });

  it("states an incomplete run once, in the verdict's wording, not twice in two", async () => {
    // Both the verdict banner and this page's own error banner knew how to say INCOMPLETE, in different words. The
    // fact is still stated -- it must be -- but by the one component the whole app uses for it.
    renderPage(null, { stopped: true });
    await waitFor(() => expect(screen.getByLabelText("Verdict")).toBeTruthy());
    expect(screen.getByLabelText("Verdict").textContent).toContain("Incomplete");
    expect(screen.getByLabelText("Verdict").textContent).toContain("cut");
    expect(screen.queryAllByRole("alert")).toEqual([]);
  });

  it("still says plainly when the record cannot be read for a reason the verdict does not explain", async () => {
    // The read failure that is not an incomplete run has to keep its own banner: a page that renders nothing and
    // says nothing reads as "this run has no provenance".
    renderPage(null, { stopped: true, resolvedVerdict: true });
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toContain("cannot be read");
  });
});
