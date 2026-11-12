import { describe, expect, it } from "vitest";
import { describeReason } from "./http";

describe("describeReason", () => {
  it("keeps a specific server detail verbatim", () => {
    expect(describeReason(404, "Not Found", { reason: "no campaign abc" })).toBe("no campaign abc");
  });
  it("replaces generic fallbacks with an actionable sentence", () => {
    expect(describeReason(500, "Internal Server Error", null)).toContain("smeltery bench");
    expect(describeReason(409, "Conflict", {})).toContain("Reload");
  });
  it("treats a detail equal to the status text as generic", () => {
    expect(describeReason(404, "Not Found", { reason: "Not Found" })).toContain("Not found.");
  });
  it("reads the reason of a validation problem, not its structured errors", () => {
    const body = { reason: "name: Field required", errors: [{ loc: ["body", "name"], msg: "Field required" }] };
    expect(describeReason(422, "Unprocessable", body)).toBe("name: Field required");
  });
  it("falls back when the body is not a Problem", () => {
    expect(describeReason(422, "Unprocessable", { detail: "x" })).toContain("rejected");
  });
});
