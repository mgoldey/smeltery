import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Two defects on this page, both about a judgement the app described more confidently than it supported.
 *
 * Deciding had no progress and a partial exit: nothing said how many of the analogues had been judged, and "Start
 * the next round" appeared on the first recorded decision with no sign that other rows had been filled in and left.
 * A decision cannot be cleared -- there is no DELETE anywhere in the Bench API, by design -- so the remedy is
 * legibility, not an undo.
 *
 * And a tie group was described more strictly than it is enforced: the lede promised a tie choice "needs a note",
 * while the server requires one for the REASON `within_tie_choice` (or `other`) and takes `cut_survivor` inside a
 * tie group without comment. The row now says who it is tied with and points at the honest reason, while leaving
 * every other reason recordable -- a liability can break a tie.
 */

const RUN = "r1";
const ANALOGUES = ["4-F", "4-Cl", "4-Br"];

/** The fixture cut: 4-F alone, then {4-Cl, 4-Br} unresolved from each other. */
const DETAIL = {
  id: RUN,
  path: "/runs/out/run-abc.json",
  out_dir: "/runs/out",
  campaign: "halogens",
  digest: "abc123def456",
  parent: "benzoic",
  candidates: { benzoic: "OC(=O)c1ccccc1", "4-F": "F", "4-Cl": "Cl", "4-Br": "Br" },
  analogues: ANALOGUES,
  groups: [["4-F"], ["4-Cl", "4-Br"]],
  survivors: ["4-F"],
  floor: 0,
  floor_source: "config",
  floor_statement: "",
  z: 2,
  keep: 1,
  unranked: false,
  has_pose_output: true,
  n_poses: {},
  unit: "kcal/mol",
  verdict: { state: "RESOLVED", winner: "4-F", floor: 0, missing: [] },
  decisions_in: null,
};

const REASONS = ["cut_survivor", "within_tie_choice", "liability", "synthesis", "pose_implausible", "novelty", "other"];
const FULL_VOCAB = { statuses: ["keep", "reject", "hold"], reasons: REASONS, note_required: ["within_tie_choice", "other"] };

/** The served vocabulary. It comes from `smeltery.decisions.REASONS`, so one test narrows it. */
let VOCAB: { statuses: string[]; reasons: string[]; note_required: string[] } = FULL_VOCAB;

/** Decisions the server has; each test sets it before rendering. */
let onServer: { candidate: string; status: string; reason: string; note: string; user: string; time: string }[] = [];

const posted: unknown[] = [];

vi.mock("../../components/Structure2D", () => ({ Structure2D: () => <span /> }));

vi.mock("../../api/client", async (orig) => {
  const real = await orig<typeof import("../../api/client")>();
  const GET = async (path: string) => {
    const data =
      path === "/api/whoami"
        ? { user: "mg", root: "/r", read_only: false }
        : path === "/api/runs/{run_id}"
          ? DETAIL
          : path === "/api/decisions/vocabulary"
            ? VOCAB
            : path === "/api/runs/{run_id}/decisions"
              ? onServer
              : path === "/api/runs/{run_id}/ddE"
                ? { rows: [], floor: 0, floor_source: "config", z: 2, keep: 1, unit: "kcal/mol", verdict: DETAIL.verdict }
                : [];
    return { data, response: new Response(null, { status: 200 }) };
  };
  const POST = async (path: string, init: { body?: unknown }) => {
    posted.push({ path, body: init.body });
    return { data: {}, response: new Response(null, { status: 200 }) };
  };
  return { ...real, api: { GET, POST } };
});

const { Decisions } = await import("./Decisions");

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/run/${RUN}/decisions`]}>
        <Routes>
          <Route path="/run/:runId/decisions" element={<Decisions />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** A row's controls, by the candidate it is about. */
function row(name: string) {
  const tr = document.querySelector(`[data-candidate="${name}"]`);
  if (!tr) throw new Error(`no row for ${name}`);
  return {
    tr: tr as HTMLElement,
    status: tr.querySelector(`#status-${name}`) as HTMLSelectElement,
    reason: tr.querySelector(`#reason-${name}`) as HTMLSelectElement,
    note: tr.querySelector(`#note-${name}`) as HTMLInputElement,
  };
}

function decision(candidate: string, status: string, reason: string) {
  return { candidate, status, reason, note: "", user: "mg", time: "2026-01-01T00:00:00Z" };
}

beforeEach(() => {
  onServer = [];
  posted.length = 0;
  VOCAB = FULL_VOCAB;
});
afterEach(cleanup);

describe("deciding says how far through it is", () => {
  it("counts the analogues with a recorded decision against how many there are", async () => {
    onServer = [decision("4-F", "keep", "cut_survivor")];
    renderPage();
    await waitFor(() => expect(screen.getByTestId("decided-progress")).toBeTruthy());
    expect(screen.getByTestId("decided-progress").textContent).toContain(`1 of ${ANALOGUES.length}`);
  });

  it("says none are recorded when none are, rather than saying nothing", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("decided-progress")).toBeTruthy());
    expect(screen.getByTestId("decided-progress").textContent).toContain(`0 of ${ANALOGUES.length}`);
  });

  it("names the rows that are filled in but never recorded", async () => {
    onServer = [decision("4-F", "keep", "cut_survivor")];
    renderPage();
    await waitFor(() => expect(row("4-Cl").status).toBeTruthy());
    fireEvent.change(row("4-Cl").status, { target: { value: "reject" } });
    await waitFor(() => expect(screen.getByTestId("decided-progress").textContent).toContain("4-Cl"));
    expect(screen.getByTestId("decided-progress").textContent).toContain("not recorded");
  });
});

describe("the exit says what it carries, and what it leaves", () => {
  it("names the recorded analogues and the undecided ones separately", async () => {
    onServer = [decision("4-F", "keep", "cut_survivor")];
    renderPage();
    await waitFor(() => expect(screen.getByTestId("next-round-carries")).toBeTruthy());
    const text = screen.getByTestId("next-round-carries").textContent ?? "";
    expect(text).toContain("one analogue");
    expect(text).toContain("2 are");
    expect(text).toContain("undecided");
  });

  it("warns, beside the button, that an unrecorded row is about to be left out", async () => {
    onServer = [decision("4-F", "keep", "cut_survivor")];
    renderPage();
    await waitFor(() => expect(row("4-Br").status).toBeTruthy());
    expect(screen.queryByTestId("next-round-unrecorded")).toBeNull();
    fireEvent.change(row("4-Br").status, { target: { value: "hold" } });
    await waitFor(() => expect(screen.getByTestId("next-round-unrecorded")).toBeTruthy());
    const warn = screen.getByTestId("next-round-unrecorded").textContent ?? "";
    expect(warn).toContain("4-Br");
    expect(warn).toContain("not recorded");
    // Legibility, not a block: the exit stays available. A decision cannot be cleared, so refusing the click here
    // would trap a user who meant exactly this.
    expect((screen.getByTestId("start-next-round") as HTMLButtonElement).disabled).toBe(false);
  });

  it("says every analogue is decided once they are", async () => {
    onServer = ANALOGUES.map((n) => decision(n, "keep", "cut_survivor"));
    renderPage();
    await waitFor(() => expect(screen.getByTestId("next-round-carries")).toBeTruthy());
    expect(screen.getByTestId("next-round-carries").textContent).toContain("Every analogue");
    expect(screen.queryByTestId("next-round-unrecorded")).toBeNull();
  });
});

describe("a tie group is visible where the choice is made", () => {
  it("names the other members on a tied row, and nothing on a row that is alone", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("group-4-Cl")).toBeTruthy());
    expect(screen.getByTestId("group-4-Cl").textContent).toContain("tied with 4-Br");
    expect(screen.getByTestId("group-4-F").textContent).not.toContain("tied with");
  });

  it("points a tied row at the reason that names the judgement, without refusing the others", async () => {
    renderPage();
    await waitFor(() => expect(row("4-Cl").status).toBeTruthy());
    fireEvent.change(row("4-Cl").status, { target: { value: "keep" } });
    fireEvent.change(row("4-Cl").reason, { target: { value: "cut_survivor" } });
    await waitFor(() => expect(screen.getByTestId("tie-reason-4-Cl")).toBeTruthy());
    // The honest reason is offered as a control, not just described.
    const record = screen.getByLabelText("Record decision for 4-Cl") as HTMLButtonElement;
    expect(record.disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Call it a tie choice" }));
    await waitFor(() => expect(row("4-Cl").reason.value).toBe("within_tie_choice"));
    // And that reason does require a note, which the existing hold already enforces.
    expect((screen.getByLabelText("Record decision for 4-Cl") as HTMLButtonElement).disabled).toBe(true);
  });

  it("says nothing about ties on a row that is alone in its group", async () => {
    renderPage();
    await waitFor(() => expect(row("4-F").status).toBeTruthy());
    fireEvent.change(row("4-F").status, { target: { value: "keep" } });
    fireEvent.change(row("4-F").reason, { target: { value: "cut_survivor" } });
    await waitFor(() => expect(screen.getByTestId("decided-progress").textContent).toContain("4-F"));
    expect(screen.queryByTestId("tie-reason-4-F")).toBeNull();
  });

  it("drops the guidance if the server's vocabulary no longer has the tie reason", async () => {
    // The reason list is `smeltery.decisions.REASONS`, served, never a constant in this page. If the core renames
    // or removes `within_tie_choice`, the honest thing is to say nothing rather than offer a dead option.
    VOCAB = { ...FULL_VOCAB, reasons: REASONS.filter((r) => r !== "within_tie_choice"), note_required: ["other"] };
    renderPage();
    await waitFor(() => expect(row("4-Cl").status).toBeTruthy());
    fireEvent.change(row("4-Cl").status, { target: { value: "keep" } });
    fireEvent.change(row("4-Cl").reason, { target: { value: "cut_survivor" } });
    await waitFor(() => expect(screen.getByTestId("decided-progress").textContent).toContain("4-Cl"));
    expect(screen.queryByTestId("tie-reason-4-Cl")).toBeNull();
    // The tie itself is still named: that fact does not depend on any reason existing.
    expect(screen.getByTestId("group-4-Cl").textContent).toContain("tied with 4-Br");
  });

  it("leaves a non-tie reason recordable on a tied row: a liability really can break a tie", async () => {
    renderPage();
    await waitFor(() => expect(row("4-Br").status).toBeTruthy());
    fireEvent.change(row("4-Br").status, { target: { value: "reject" } });
    fireEvent.change(row("4-Br").reason, { target: { value: "liability" } });
    fireEvent.click(screen.getByLabelText("Record decision for 4-Br"));
    await waitFor(() => expect(posted.length).toBe(1));
    expect(posted[0]).toMatchObject({
      path: "/api/runs/{run_id}/decisions",
      body: { candidate: "4-Br", status: "reject", reason: "liability" },
    });
  });
});
