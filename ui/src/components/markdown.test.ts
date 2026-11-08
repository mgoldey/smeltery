import { describe, expect, it } from "vitest";
import { isSafeUrl, parseInline, parseMarkdown } from "./markdown";

describe("parseMarkdown", () => {
  it("keeps a <script> tag as plain text", () => {
    expect(parseMarkdown("<script>alert(1)</script>")).toEqual([
      { t: "p", children: [{ t: "text", text: "<script>alert(1)</script>" }] },
    ]);
    expect(parseInline("**<img src=x onerror=1>**")).toEqual([
      { t: "strong", children: [{ t: "text", text: "<img src=x onerror=1>" }] },
    ]);
  });

  it("does not linkify javascript:, data: or protocol-relative targets, but keeps their text", () => {
    for (const bad of ["javascript:alert(1)", "JaVaScRiPt:x", "data:text/html,x", "//evil.example/x", "/\\evil.com", "/\\/evil.com", "./a\\b"]) {
      expect(isSafeUrl(bad)).toBe(false);
      const out = parseInline(`[click](${bad})`);
      expect(out.some((n) => n.t === "link")).toBe(false);
      expect(out).toEqual([{ t: "text", text: `[click](${bad})` }]);
    }
    expect(parseInline("[a](https://x.org/p?q=1)")).toEqual([
      { t: "link", href: "https://x.org/p?q=1", children: [{ t: "text", text: "a" }] },
    ]);
    expect(parseInline("[t](https://en.wikipedia.org/wiki/Foo_(bar))")).toEqual([
      { t: "link", href: "https://en.wikipedia.org/wiki/Foo_(bar)", children: [{ t: "text", text: "t" }] },
    ]);
    expect(isSafeUrl("/runs/3")).toBe(true);
    expect(isSafeUrl("#entry-1")).toBe(true);
  });

  it("nests emphasis inside bold and bold inside emphasis", () => {
    expect(parseInline("**a *b* c**")).toEqual([
      { t: "strong", children: [{ t: "text", text: "a " }, { t: "em", children: [{ t: "text", text: "b" }] }, { t: "text", text: " c" }] },
    ]);
    expect(parseInline("*a **b** c*")).toEqual([
      { t: "em", children: [{ t: "text", text: "a " }, { t: "strong", children: [{ t: "text", text: "b" }] }, { t: "text", text: " c" }] },
    ]);
  });

  it("leaves code spans and fenced blocks unformatted", () => {
    expect(parseInline("`**x** [a](b)`")).toEqual([{ t: "code", text: "**x** [a](b)" }]);
    expect(parseMarkdown("```\n# not a heading\n**no**\n- nor a list\n```\nafter")).toEqual([
      { t: "code", text: "# not a heading\n**no**\n- nor a list" },
      { t: "p", children: [{ t: "text", text: "after" }] },
    ]);
  });

  it("reads headings, lists and paragraphs, and an unterminated fence runs to the end", () => {
    const b = parseMarkdown("# T\n\npara one\nstill one\n\n- a\n- b\n\n1. x\n2. y\n\n#### too deep\n```\nopen");
    expect(b.map((x) => x.t)).toEqual(["heading", "p", "list", "list", "p", "code"]);
    expect(b[3]).toMatchObject({ ordered: true });
    expect(b[1]).toEqual({ t: "p", children: [{ t: "text", text: "para one\nstill one" }] });
    expect(b[5]).toEqual({ t: "code", text: "open" });
  });

  it("leaves unmatched markers as literal text", () => {
    expect(parseInline("2 * 3 and `open and **bold")).toEqual([{ t: "text", text: "2 * 3 and `open and **bold" }]);
  });
});
