import { describe, expect, it } from "vitest";
import { type PerPoseRow, perPoseCsv } from "./PerPoseTable";
import { convergenceSummary } from "./ScfLog";

describe("perPoseCsv", () => {
  it("quotes fields holding commas or quotes and keeps full precision", () => {
    const rows: PerPoseRow[] = [{ candidate: 'a,"b"', pose: 2, position: 0, parent_position: 0, value: -1.23456789, parent_value: -1, paired: -0.23456789 }];
    const lines = perPoseCsv(rows, "kcal/mol").split("\r\n");
    expect(lines[0]).toBe("Candidate,Pose,ΔE_int (kcal/mol),Parent ΔE_int (kcal/mol),Paired ΔΔE (kcal/mol)");
    expect(lines[1]).toBe('"a,""b""",2,-1.23456789,-1,-0.23456789');
  });
});

describe("convergenceSummary", () => {
  it("counts legs not rungs, and leaves outcome lines out", () => {
    const lines = [
      { leg: "vacuum", rung: 0, converged: false },
      { leg: "vacuum", rung: 1, converged: true },
      { leg: "field", field_model: "ff", rung: 0, converged: true },
      { leg: "field", field_model: "ff", outcome: "ok" },
    ];
    expect(convergenceSummary(lines)).toBe("2 legs, 2 converged (3 SCF attempts)");
  });
});
