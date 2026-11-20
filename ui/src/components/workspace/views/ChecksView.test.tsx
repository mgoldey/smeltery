import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * The reviewed run showed "All 9 poses pass" in green for poses with 105-109 kcal/mol MMFF strain, because no strain
 * limit was set: PoseBusters passed them and nothing judged the strain. Without a limit the summary now says what
 * passed and that strain was not judged, and each strain cell says "not judged".
 */

type Limit = number | null;
let limit: Limit = null;

const gates = () => {
  const strain = [105.2, 107.9, 109.1];
  return {
    ran: ["posebusters", "strain_kcal"],
    max_strain_kcal: limit,
    raw: {},
    rows: strain.map((s, pose) => {
      const over = limit === null ? null : s > limit;
      return {
        candidate: "4-F",
        pose,
        posebusters: true,
        failed_checks: [],
        strain_kcal: s,
        over_strain: over,
        checked: true,
        ok: over !== true,
      };
    }),
  };
};

vi.mock("../../../api/client", async (orig) => {
  const real = await orig<typeof import("../../../api/client")>();
  const GET = async () => ({ data: gates(), response: new Response(null, { status: 200 }) });
  return { ...real, api: { GET } };
});

const { ChecksView, strainNotJudged } = await import("./ChecksView");

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const step = {} as Parameters<typeof ChecksView>[0]["step"];
  return render(
    <QueryClientProvider client={qc}>
      <ChecksView pipeline="p" toml="" step={step} sourceRun="r1" onToml={() => {}} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  limit = null;
});

describe("Pose checks never call unjudged strain a pass", () => {
  it("qualifies the summary and marks every strain cell not judged when no limit is set", async () => {
    renderView();
    await waitFor(() => expect(screen.getByTestId("checks-summary")).toBeTruthy());
    const summary = screen.getByTestId("checks-summary").textContent ?? "";
    expect(summary).toContain("All 3 poses pass PoseBusters · strain not judged (no limit set).");
    expect(summary).not.toMatch(/All 3 poses pass\./);
    expect(screen.getAllByTestId("strain-not-judged")).toHaveLength(3);
    // the number itself is still shown in full
    expect(screen.getByRole("table", { name: "Gate results" }).textContent).toContain("109.10");
  });

  it("keeps today's wording when a limit judged the strain", async () => {
    limit = 200;
    renderView();
    await waitFor(() => expect(screen.getByTestId("checks-summary")).toBeTruthy());
    const summary = screen.getByTestId("checks-summary").textContent ?? "";
    expect(summary).toContain("All 3 poses pass.");
    expect(summary).toContain("Strain limit 200 kcal/mol.");
    expect(summary).not.toContain("not judged");
    expect(screen.queryByTestId("strain-not-judged")).toBeNull();
  });

  it("strainNotJudged: a recorded strain with no limit, and nothing else", () => {
    const base = { candidate: "a", pose: 0, posebusters: true, failed_checks: [], checked: true, ok: true };
    expect(strainNotJudged({ ...base, strain_kcal: 5, over_strain: null })).toBe(true);
    expect(strainNotJudged({ ...base, strain_kcal: 5, over_strain: false })).toBe(false);
    expect(strainNotJudged({ ...base, strain_kcal: null, over_strain: null })).toBe(false);
  });
});
