/**
 * Q-005: the typecheck gate must cover the WHOLE frontend, not whichever files the globs happen to reach.
 *
 * `npm run typecheck` blocks CI, but a file outside both tsconfig `include` lists is simply never compiled, so it
 * passes by being invisible. That is how `scripts/gen-api.mjs` -- the script CI runs to prove `schema.d.ts` is
 * current -- went unchecked. This test compares what git tracks against what `tsc --listFiles` actually read, so a
 * new file added outside the globs fails here rather than going quietly unchecked.
 */
import { execFileSync } from "node:child_process";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const UI = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Source files git tracks under ui/, excluding generated and vendored ones. */
function tracked(): string[] {
  const out = execFileSync("git", ["ls-files", "*.ts", "*.tsx", "*.mjs", "*.js"], { cwd: UI, encoding: "utf8" });
  return out
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .filter((f) => !f.startsWith("node_modules/"))
    .filter((f) => f !== "src/api/schema.d.ts") // generated; checked as a dependency of the files that import it
    .sort();
}

/** Files `tsc` actually read for a project, relative to ui/. */
function compiled(project: string): string[] {
  const out = execFileSync("npx", ["tsc", "--noEmit", "--listFiles", "-p", project], {
    cwd: UI,
    encoding: "utf8",
    maxBuffer: 1 << 28,
  });
  return out
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.startsWith(UI) && !l.includes("node_modules"))
    .map((l) => relative(UI, l));
}

describe("the typecheck gate covers every source file", () => {
  it("leaves nothing tracked in ui/ outside both tsconfig projects", () => {
    const seen = new Set([...compiled("tsconfig.json"), ...compiled("tsconfig.node.json")]);
    const missed = tracked().filter((f) => !seen.has(f));
    expect(missed, `not typechecked by either tsconfig: ${missed.join(", ")}`).toEqual([]);
  });
}, 180_000);
