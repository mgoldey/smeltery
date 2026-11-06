import { describe, expect, it } from "vitest";
import { visible } from "./ConfigDiff";
import { pipelineName } from "./openFromRun";
import { parse } from "./ParamRow";

describe("ParamRow.parse", () => {
  it("reads each knob kind, and empty is unset", () => {
    expect(parse("int", " 8 ")).toBe(8);
    expect(parse("float", "1e-10")).toBe(1e-10);
    expect(parse("vec3", "1, 2 3")).toEqual([1, 2, 3]);
    expect(parse("int_list", "1 2,3")).toEqual([1, 2, 3]);
    expect(parse("str_list", "A, B")).toEqual(["A", "B"]);
    expect(parse("str", "pbe")).toBe("pbe");
    expect(parse("int", "")).toBeNull();
  });
  it("refuses what is not of the kind", () => {
    expect(() => parse("int", "8.5")).toThrow("not an integer");
    expect(() => parse("float", "lots")).toThrow("not a number");
    expect(() => parse("vec3", "1 2")).toThrow("three numbers");
  });
});

describe("ConfigDiff.visible", () => {
  const ctx = (line: string) => ({ sign: " ", line });
  it("keeps changed lines with one line of context and marks the gaps", () => {
    const diff = [ctx("a"), ctx("b"), ctx("c"), { sign: "-", line: "n = 6" }, { sign: "+", line: "n = 8" }, ctx("d"), ctx("e")];
    expect(visible(diff)).toEqual([ctx("c"), diff[3], diff[4], ctx("d")]);
    const two = [{ sign: "+", line: "x" }, ctx("a"), ctx("b"), ctx("c"), { sign: "+", line: "y" }];
    expect(visible(two)).toEqual([two[0], ctx("a"), null, ctx("c"), two[4]]);
  });
  it("shows nothing when nothing changed", () => {
    expect(visible([ctx("a"), ctx("b")])).toEqual([]);
  });
});

describe("pipelineName", () => {
  it("is a valid store name made from the campaign and digest", () => {
    const n = pipelineName({ idx: 3, campaign: "GUI Fixture!", digest: "ABCDEF0123456789" });
    expect(n).toBe("gui-fixture-abcdef01");
    expect(n).toMatch(/^[a-z0-9][a-z0-9_-]{0,63}$/);
    expect(pipelineName({ idx: 3, campaign: null, digest: null })).toBe("run-run3");
  });
});

describe("parseAnalogues", () => {
  it("reads name and SMILES per line and needs at least one", async () => {
    const { parseAnalogues } = await import("../../pages/BenchHome");
    expect(parseAnalogues("4-F  OC(=O)c1ccc(F)cc1\n\n4-Cl OC(=O)c1ccc(Cl)cc1")).toEqual({
      ok: true,
      candidates: { "4-F": "OC(=O)c1ccc(F)cc1", "4-Cl": "OC(=O)c1ccc(Cl)cc1" },
      why: "",
    });
    expect(parseAnalogues("").ok).toBe(false);
    expect(parseAnalogues("4-F").why).toContain("give a name and a SMILES");
  });
});
