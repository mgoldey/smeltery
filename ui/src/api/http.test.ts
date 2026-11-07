import { describe, expect, it } from "vitest";
import { describeReason } from "./http";

describe("describeReason", () => {
  it("keeps a specific server detail verbatim", () => {
    expect(describeReason(404, "Not Found", { detail: "no campaign abc" })).toBe("no campaign abc");
  });
  it("replaces generic fallbacks with an actionable sentence", () => {
    expect(describeReason(500, "Internal Server Error", null)).toContain("smeltery bench");
    expect(describeReason(409, "Conflict", {})).toContain("Reload");
  });
  it("renders FastAPI validation arrays as field: msg", () => {
    const detail = [
      { loc: ["body", "name"], msg: "Field required", type: "missing" },
      { loc: ["query", "n"], msg: "Input should be a valid integer" },
    ];
    expect(describeReason(422, "Unprocessable", { detail })).toBe(
      "name: Field required; query.n: Input should be a valid integer",
    );
  });
});
