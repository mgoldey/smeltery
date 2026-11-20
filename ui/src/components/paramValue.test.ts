import { describe, expect, it } from "vitest";
import { paramText, parseParam, parseVec3 } from "./paramValue";

describe("parseParam", () => {
  it("reads each kind, and empty is unset", () => {
    expect(parseParam("int", " 8 ")).toBe(8);
    expect(parseParam("float", "1e-10")).toBe(1e-10);
    expect(parseParam("bool", "false")).toBe(false);
    expect(parseParam("vec3", "1, 2 3")).toEqual([1, 2, 3]);
    expect(parseParam("int_list", "1 2,3")).toEqual([1, 2, 3]);
    expect(parseParam("str_list", "A, B")).toEqual(["A", "B"]);
    expect(parseParam("str", "pbe")).toBe("pbe");
    expect(parseParam("int", "")).toBeNull();
  });
  it("drops empty list pieces rather than reading them as zero", () => {
    expect(parseParam("int_list", "1,2,")).toEqual([1, 2]);
    expect(parseParam("int_list", "1 2")).toEqual([1, 2]);
  });
  it("accepts a whitespace-separated vec3", () => {
    expect(parseParam("vec3", "1 2 3")).toEqual([1, 2, 3]);
  });
  it("accepts any integral number as an int", () => {
    expect(parseParam("int", "1e3")).toBe(1000);
    expect(parseParam("int", "5.0")).toBe(5);
  });
  it("refuses what is not of the kind", () => {
    expect(() => parseParam("int", "8.5")).toThrow("not an integer");
    expect(() => parseParam("float", "lots")).toThrow("not a number");
    expect(() => parseParam("bool", "yes")).toThrow("not true or false");
    expect(() => parseParam("vec3", "1 2")).toThrow("three numbers");
    expect(() => parseParam("int_list", "1,x")).toThrow("not an integer");
  });
});

describe("parseVec3", () => {
  it("is three numbers or undefined", () => {
    expect(parseVec3("1,2,3")).toEqual([1, 2, 3]);
    expect(parseVec3("")).toBeUndefined();
    expect(parseVec3("1 2")).toBeUndefined();
    expect(parseVec3("1 2 z")).toBeUndefined();
  });
});

describe("paramText", () => {
  it("is empty for unset and comma-joined for lists", () => {
    expect(paramText(null)).toBe("");
    expect(paramText(undefined)).toBe("");
    expect(paramText([1, 2])).toBe("1, 2");
    expect(paramText(false)).toBe("false");
  });
});
