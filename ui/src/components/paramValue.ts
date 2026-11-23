/**
 * The one reading of a typed setting value, used by the step knobs in the workspace, the workspace defaults on
 * Methods and the box fields of the active-site view. Lists split on commas or whitespace and drop empty pieces, so
 * "1,2," is [1, 2] and "1 2 3" is a vec3.
 */

/** Text for an input from a stored value. Empty text means unset. */
export function paramText(v: unknown): string {
  if (v === undefined || v === null) return "";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

const pieces = (t: string) => t.split(/[,\s]+/).filter(Boolean);

function num(s: string, int: boolean): number {
  const n = Number(s);
  if (!Number.isFinite(n) || (int && !Number.isInteger(n))) throw new Error(`${s} is not ${int ? "an integer" : "a number"}`);
  return n;
}

/** Parse what was typed into the setting's kind. Empty means unset (null). Throws on text the kind refuses. */
export function parseParam(kind: string, text: string): unknown {
  const t = text.trim();
  if (t === "") return null;
  switch (kind) {
    case "int":
      return num(t, true);
    case "float":
      return num(t, false);
    case "bool":
      if (t !== "true" && t !== "false") throw new Error(`${t} is not true or false`);
      return t === "true";
    case "vec3": {
      const parts = pieces(t);
      if (parts.length !== 3) throw new Error("three numbers, x y z, in Angstrom");
      return parts.map((p) => num(p, false));
    }
    case "int_list":
      return pieces(t).map((p) => num(p, true));
    case "str_list":
      return t
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean);
    default:
      return t;
  }
}

/** Three finite numbers, or undefined for anything else (including empty text). */
export function parseVec3(text: string): number[] | undefined {
  try {
    return (parseParam("vec3", text) as number[] | null) ?? undefined;
  } catch {
    return undefined;
  }
}
