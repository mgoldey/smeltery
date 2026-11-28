import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Attrition, type AttritionData, lostByReason, lostSentence } from "./Attrition";

afterEach(cleanup);

const GEN = {
  generator: "fake",
  n_raw: 10,
  n_returned: 4,
  by_reason: [
    { reason: "duplicate", count: 5 },
    { reason: "size", count: 1 },
    { reason: "disconnected", count: 0 },
  ],
  rejected_total: 6,
  rejected_listed: 1,
  rejected: [{ index: 2, reason: "size", detail: "", smiles: "C" }],
};

const DATA = (generation: AttritionData["generation"]): AttritionData => ({ stages: [], generation });

describe("Attrition", () => {
  it("renders nothing for a run with no generation step", () => {
    const { container } = render(<Attrition data={DATA(null)} />);
    expect(container.innerHTML).toBe("");
  });

  it("lists only reasons that lost molecules, most first, and says how many were listed", () => {
    expect(lostByReason(GEN).map((r) => r.reason)).toEqual(["duplicate", "size"]);
    expect(lostSentence(GEN)).toBe("6 of 10 raw outputs were rejected; 4 reached the funnel.");
    render(<Attrition data={DATA(GEN)} />);
    expect(screen.getByRole("table", { name: "Rejections by reason" })).toBeTruthy();
    expect(screen.getByText(/1 of 6 listed/)).toBeTruthy();
    expect(screen.getByText("C")).toBeTruthy();
  });
});
