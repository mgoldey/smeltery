import { useState, type KeyboardEvent } from "react";
import type { Schemas } from "../../api/client";

export type Param = Schemas["StepParam"];
type Value = unknown;

/** Text for an input from a stored value. */
export function asText(v: Value): string {
  if (v === undefined || v === null) return "";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

/** Parse what was typed into the knob's kind. Empty means unset (back to the method's default). Throws on nonsense. */
export function parse(kind: string, text: string): Value {
  const t = text.trim();
  if (t === "") return null;
  const num = (s: string, int: boolean) => {
    const n = Number(s);
    if (!Number.isFinite(n) || (int && !Number.isInteger(n))) throw new Error(`${s} is not ${int ? "an integer" : "a number"}`);
    return n;
  };
  switch (kind) {
    case "int":
      return num(t, true);
    case "float":
      return num(t, false);
    case "vec3": {
      const parts = t.split(/[,\s]+/).filter(Boolean);
      if (parts.length !== 3) throw new Error("three numbers, x y z, in Angstrom");
      return parts.map((p) => num(p, false));
    }
    case "int_list":
      return t.split(/[,\s]+/).filter(Boolean).map((p) => num(p, true));
    case "str_list":
      return t.split(",").map((p) => p.trim()).filter(Boolean);
    default:
      return t;
  }
}

const same = (a: Value, b: Value) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/**
 * One knob of a step: its value (editable), the method's own one-line help, and, when it differs, the kept value with
 * a one-click reset and the workspace default from the Methods tab. Every value comes from the steps API.
 */
export function ParamRow({ p, disabled, onSet }: { p: Param; disabled: boolean; onSet: (v: Value) => void }) {
  const id = `param-${p.method}-${p.name}`;
  const [text, setText] = useState(asText(p.value));
  const [bad, setBad] = useState<string | null>(null);
  const changed = p.changed_from !== null && p.changed_from !== undefined;
  const commit = () => {
    try {
      const v = parse(p.kind, text);
      setBad(null);
      if (!same(v, p.value) && !(v === null && same(p.value, p.default))) onSet(v);
    } catch (e) {
      setBad(e instanceof Error ? e.message : String(e));
    }
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter") commit();
  };
  // Reset to the kept value; when that is the method's default, unset the knob rather than write the default out.
  const reset = () => onSet(same(p.changed_from, p.default) ? null : p.changed_from);
  let input;
  if (p.kind === "bool" || p.kind === "choice") {
    const options = p.kind === "bool" ? [true, false] : p.choices;
    input = (
      <select
        id={id}
        disabled={disabled}
        value={p.value === null || p.value === undefined ? "" : String(p.value)}
        onChange={(e) => {
          const raw = e.target.value;
          onSet(raw === "" ? null : p.kind === "bool" ? raw === "true" : raw);
        }}
      >
        <option value="">default ({String(p.default ?? "none")})</option>
        {options.map((c) => (
          <option key={String(c)} value={String(c)}>
            {String(c)}
          </option>
        ))}
      </select>
    );
  } else {
    input = (
      <input
        id={id}
        className={p.kind === "path" || p.kind === "str" ? "mono" : "num"}
        value={text}
        disabled={disabled}
        placeholder={p.default === null || p.default === undefined ? "" : asText(p.default)}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={onKey}
        aria-invalid={bad ? true : undefined}
      />
    );
  }
  const wsDiffers = p.workspace_default !== null && p.workspace_default !== undefined && !same(p.workspace_default, p.value);
  return (
    <div className={changed ? "param changed" : "param"} data-param={p.name} data-changed={changed ? "true" : undefined}>
      <label htmlFor={id} className="mono">
        {p.name}
      </label>
      {input}
      <span className="help">
        {bad ? <span className="field-error">{bad}</span> : p.help}
        {changed ? (
          <span className="was" data-testid="was">
            was {asText(p.changed_from)} ·{" "}
            <button type="button" className="link" disabled={disabled} onClick={reset} aria-label={`Reset ${p.name}`}>
              reset
            </button>
          </span>
        ) : null}
        {wsDiffers ? (
          <span className="ws-default" data-testid="workspace-default">
            workspace default {asText(p.workspace_default)}
          </span>
        ) : null}
      </span>
    </div>
  );
}
