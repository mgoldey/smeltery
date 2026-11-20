import { describe, expect, it } from "vitest";
import { day, stamp } from "./time";

describe("stamp", () => {
  it("renders the UTC minute with its zone", () => {
    expect(stamp("2026-03-01T10:05:09Z")).toBe("2026-03-01 10:05 UTC");
  });
  it("reads a string without a trailing Z as UTC", () => {
    expect(stamp("2026-03-01T10:05:09")).toBe("2026-03-01 10:05 UTC");
  });
  it("accepts fractional seconds", () => {
    expect(stamp("2026-03-01T23:59:59.987Z")).toBe("2026-03-01 23:59 UTC");
  });
  it("converts an explicit offset to UTC", () => {
    expect(stamp("2026-03-01T01:30:00+02:00")).toBe("2026-02-28 23:30 UTC");
  });
  it("shows what it cannot read as given", () => {
    expect(stamp("")).toBe("");
    expect(stamp("soon")).toBe("soon");
  });
});

describe("day", () => {
  it("is the UTC calendar day", () => {
    expect(day("2026-03-01T23:59:59Z")).toBe("2026-03-01");
    expect(day("2026-03-01T23:59:59.5")).toBe("2026-03-01");
    expect(day("2026-03-01")).toBe("2026-03-01");
  });
});
