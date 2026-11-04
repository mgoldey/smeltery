import { useState, type KeyboardEvent } from "react";
import type { Schemas } from "../../../api/client";

type Param = Schemas["ParamOut"];
type Value = unknown;

/** Text for an input from a stored value; unset knobs show the method's default as a placeholder. */
function asText(kind: string, v: Value): string {
  if (v === undefined || v === null) return "";
  if (kind === "int_list" || kind === "str_list" || kind === "vec3") return (v as unknown[]).join(", ");
  return String(v);
}

/** Parse what was typed into the knob's kind. Empty means unset (back to the default). Throws on nonsense. */
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

function Field({ p, value, onSet }: { p: Param; value: Value; onSet: (v: Value) => void }) {
  const id = `knob-${p.name}`;
  const [text, setText] = useState(asText(p.kind, value));
  const [bad, setBad] = useState<string | null>(null);
  const commit = () => {
    try {
      const v = parse(p.kind, text);
      setBad(null);
      if (JSON.stringify(v) !== JSON.stringify(value ?? null)) onSet(v);
    } catch (e) {
      setBad(e instanceof Error ? e.message : String(e));
    }
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter") commit();
  };
  const help = p.help ? <span className="lede">{p.help}</span> : null;
  if (p.kind === "bool") {
    return (
      <div>
        <label htmlFor={id}>{p.name}</label>
        <select id={id} value={value === null || value === undefined ? "" : String(value)} onChange={(e) => onSet(e.target.value === "" ? null : e.target.value === "true")}>
          <option value="">default ({String(p.default)})</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
        {help}
      </div>
    );
  }
  if (p.kind === "choice") {
    return (
      <div>
        <label htmlFor={id}>{p.name}</label>
        <select id={id} value={value === null || value === undefined ? "" : String(value)} onChange={(e) => onSet(e.target.value === "" ? null : e.target.value)}>
          <option value="">default ({String(p.default ?? "none")})</option>
          {p.choices.map((c) => (
            <option key={String(c)} value={String(c)}>
              {String(c)}
            </option>
          ))}
        </select>
        {help}
      </div>
    );
  }
  return (
    <div>
      <label htmlFor={id}>
        {p.name}
        {p.required ? " (required)" : ""}
      </label>
      <input
        id={id}
        className={p.kind === "path" || p.kind === "str" ? "mono" : "num"}
        value={text}
        placeholder={p.default === null || p.default === undefined ? "" : asText(p.kind, p.default)}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={onKey}
        aria-invalid={bad ? true : undefined}
      />
      {bad ? <div className="field-error">{bad}</div> : help}
    </div>
  );
}

type Site = { q: number; atom: number; from_atom: number; distance: number };

function Sites({ sites, onSet }: { sites: Site[]; onSet: (v: Value) => void }) {
  return (
    <div>
      <label>Pocket sites (charges placed beyond parent atoms)</label>
      {sites.length === 0 ? <p className="empty-state">No sites yet. Place one by clicking two atoms of the parent.</p> : null}
      <table className="grid" aria-label="Pocket sites">
        <thead>
          <tr>
            <th className="num">q</th>
            <th className="num">atom</th>
            <th className="num">from atom</th>
            <th className="num">distance (Å)</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sites.map((s, i) => (
            <tr key={i}>
              <td className="num">{s.q}</td>
              <td className="num">{s.atom}</td>
              <td className="num">{s.from_atom}</td>
              <td className="num">{s.distance}</td>
              <td>
                <button type="button" onClick={() => onSet(sites.filter((_, j) => j !== i))} aria-label={`Remove site ${i + 1}`}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function KnobForm({
  method,
  params,
  onSet,
}: {
  method: Schemas["MethodOut"];
  params: Record<string, Value>;
  onSet: (name: string, value: Value) => void;
}) {
  if (method.params.length === 0) return <p className="empty-state">{method.label} has no knobs.</p>;
  return (
    <form className="knobs" onSubmit={(e) => e.preventDefault()} aria-label={`${method.label} knobs`}>
      {method.params.map((p) =>
        p.kind === "sites" ? (
          <Sites key={p.name} sites={(params[p.name] as Site[] | undefined) ?? []} onSet={(v) => onSet(p.name, v)} />
        ) : (
          <Field key={`${method.key}.${p.name}.${JSON.stringify(params[p.name] ?? null)}`} p={p} value={params[p.name]} onSet={(v) => onSet(p.name, v)} />
        ),
      )}
    </form>
  );
}
