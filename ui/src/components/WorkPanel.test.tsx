import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunProgress, TaskInspector, WorkPanel } from "./WorkPanel";

// openapi-fetch binds fetch when the client is created, so the panel's data is faked at the client instead.
const tasksNow = vi.hoisted(() => ({ current: [] as unknown[] }));
// The panel reads `/api/tasks` and, for a run's result cell, `/api/runs`. One mock answering both with the task
// list made `runOfTask` walk task rows as if they were run records.
const posted = vi.hoisted(() => ({ calls: [] as { path: string; body: unknown }[] }));
vi.mock("../api/client", () => ({
  api: {
    GET: async (path: string) => ({ data: path === "/api/runs" ? [] : tasksNow.current }),
    POST: async (path: string, init?: { body?: unknown }) => {
      posted.calls.push({ path, body: init?.body });
      return { data: { ...base, id: "t2", name: "resumed", status: "queued" } };
    },
  },
  unwrap: async (p: Promise<{ data: unknown }>) => (await p).data,
}));

// `useCanWrite` is `known && !readOnly`, and `known` turns on only once /api/whoami has answered -- correct in the
// app (never offer a write control before knowing the Bench allows writes) but it means a write control is absent
// in a test until the capability is stated.
vi.mock("../api/capabilities", () => ({ useCanWrite: () => true }));

const base = { command: ["smeltery", "run"], cwd: "/x", status: "running", log_path: "", note: "" };

afterEach(cleanup);

describe("RunProgress", () => {
  it("draws a bar and 'n of N SCFs'", () => {
    render(<RunProgress progress={{ done: 5, total: 12, reused: 0 }} />);
    expect(screen.getByText("5 of 12 SCFs")).toBeTruthy();
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("value")).toBe("5");
    expect(bar.getAttribute("max")).toBe("12");
  });

  it("says how much of a resumed run was reused", () => {
    render(<RunProgress progress={{ done: 6, total: 10, reused: 6 }} />);
    expect(screen.getByTestId("run-progress").textContent).toContain("6 of 10 SCFs (6 reused from an earlier attempt)");
  });

  it("draws nothing without progress", () => {
    const { container } = render(<RunProgress progress={null} />);
    expect(container.innerHTML).toBe("");
  });
});

describe("WorkPanel", () => {
  it("shows the bar only on the task that has progress", async () => {
    const tasks = [
      { ...base, id: "a", name: "run alpha", progress: { done: 3, total: 8, reused: 0 } },
      { ...base, id: "b", name: "sleeper", progress: null },
    ];
    tasksNow.current = tasks;
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <WorkPanel selected={null} onSelect={() => {}} />
      </QueryClientProvider>,
    );
    await screen.findByText("run alpha");
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByText("3 of 8 SCFs")).toBeTruthy();
    expect(within(rows[1]!).queryByTestId("run-progress")).toBeNull();
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
  });
});

/**
 * A run that died must offer the way back to itself.
 *
 * The defect: the inspector's only control was Cancel task, meaningless once the task is over. The queue reports a
 * dead task failed and never retries it -- re-running is the user's call -- but `smeltery run` resumes a directory
 * it has already partly filled, reusing the SCFs that finished (`progress.reused` is that count). The directory
 * holds its own staged config, so it is the whole of what a resume needs; the user was left to copy the `--out`
 * path out of the command and re-run it in a shell.
 */
describe("the inspector on a run that ended badly", () => {
  const RUN = ["smeltery", "run", "/w/out/dan-20261114/config.toml", "--out", "/w/out/dan-20261114"];

  function show(task: Record<string, unknown>) {
    tasksNow.current = [{ ...base, id: "t1", name: "run Danuglipron", progress: null, ...task }];
    render(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <TaskInspector id="t1" onClose={() => {}} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  }

  beforeEach(() => {
    posted.calls = [];
  });

  it("resumes a failed run into the directory the run was already writing to", async () => {
    show({ status: "failed", command: RUN, returncode: 1 });
    const resume = await screen.findByTestId("resume-run");
    // It says what resuming buys, because the cost being avoided is re-running SCFs that already finished.
    expect(screen.getByTestId("resume-note").textContent).toMatch(/finished|already/i);
    resume.click();
    await vi.waitFor(() => expect(posted.calls).toHaveLength(1));
    expect(posted.calls[0]!.path).toBe("/api/runs/resume");
    expect(posted.calls[0]!.body).toEqual({ out: "/w/out/dan-20261114" });
  });

  it("offers it on a cancelled run too, which leaves the same half-finished directory", async () => {
    show({ status: "cancelled", command: RUN });
    expect(await screen.findByTestId("resume-run")).toBeTruthy();
  });

  it("does not offer it while the run is still going, nor for a task that is not a run", async () => {
    show({ status: "running", command: RUN });
    await screen.findByRole("heading", { name: "run Danuglipron" });
    expect(screen.queryByTestId("resume-run")).toBeNull();
    cleanup();
    // No --out: there is no directory to resume into, so no guess is made at one.
    show({ status: "failed", command: ["smeltery", "golden", "bundle"], returncode: 2 });
    await screen.findByRole("heading", { name: "run Danuglipron" });
    expect(screen.queryByTestId("resume-run")).toBeNull();
  });
});
