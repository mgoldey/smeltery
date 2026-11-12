import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunProgress, WorkPanel } from "./WorkPanel";

// openapi-fetch binds fetch when the client is created, so the panel's data is faked at the client instead.
const tasksNow = vi.hoisted(() => ({ current: [] as unknown[] }));
vi.mock("../api/client", () => ({
  api: { GET: async () => ({ data: tasksNow.current }) },
  unwrap: async (p: Promise<{ data: unknown }>) => (await p).data,
}));

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
