import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");
const ALLOWED = new Set(["styles/tokens.css", "api/schema.d.ts"]);
// A colour literal: #rgb[a] / #rrggbb[aa], or rgb()/hsl() with numbers.
const COLOUR = /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(\s*\d/;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

describe("colour discipline", () => {
  it("names colours only in styles/tokens.css", () => {
    const offenders = files(SRC)
      .filter((p) => /\.(tsx?|css)$/.test(p) && !p.endsWith(".test.ts"))
      .map((p) => relative(SRC, p))
      .filter((rel) => !ALLOWED.has(rel))
      .flatMap((rel) =>
        readFileSync(join(SRC, rel), "utf8")
          .split("\n")
          .map((line, i) => [rel, i + 1, line] as const)
          .filter(([, , line]) => COLOUR.test(line))
          .map(([r, n, line]) => `${r}:${n}: ${line.trim()}`),
      );
    expect(offenders).toEqual([]);
  });
});
