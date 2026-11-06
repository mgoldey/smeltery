import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../api/client";
import { Shell } from "../components/Shell";

type Method = Schemas["MethodInfo"];
type Param = Schemas["MethodParam"];

const muted = { color: "var(--ink-muted)" } as const;

/** What a text box holds for a value, and back. Empty text means "no workspace default". */
function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  return Array.isArray(v) ? v.join(", ") : String(v);
}

function parse(p: Param, text: string): { value: unknown } | { error: string } {
  const t = text.trim();
  const nums = (s: string) => s.split(",").map((x) => Number(x.trim()));
  switch (p.kind) {
    case "int":
      return /^-?\d+$/.test(t) ? { value: Number(t) } : { error: `${p.name} must be a whole number` };
    case "float":
      return t !== "" && Number.isFinite(Number(t)) ? { value: Number(t) } : { error: `${p.name} must be a number` };
    case "bool":
      return t === "true" || t === "false" ? { value: t === "true" } : { error: `${p.name} must be true or false` };
    case "vec3": {
      const v = nums(t);
      return v.length === 3 && v.every(Number.isFinite) ? { value: v } : { error: `${p.name} needs three numbers` };
    }
    case "int_list": {
      const v = nums(t);
      return v.every(Number.isInteger) ? { value: v } : { error: `${p.name} needs whole numbers, comma separated` };
    }
    case "str_list":
      return { value: t.split(",").map((x) => x.trim()).filter(Boolean) };
    default:
      return { value: t };
  }
}

function MethodCard({ m }: { m: Method }) {
  const qc = useQueryClient();
  const editable = m.params.filter((p) => p.kind !== "sites");
  const saved = Object.fromEntries(editable.map((p) => [p.name, show(p.workspace_default)]));
  const [draft, setDraft] = useState<Record<string, string>>(saved);
  const [problem, setProblem] = useState<string | null>(null);
  const dirty = editable.some((p) => (draft[p.name] ?? "") !== saved[p.name]);
  const save = useMutation({
    mutationFn: () => {
      const defaults: Record<string, unknown> = {};
      for (const p of editable) {
        const text = (draft[p.name] ?? "").trim();
        if (text === "") continue;
        const r = parse(p, text);
        if ("error" in r) throw new Error(r.error);
        defaults[p.name] = r.value;
      }
      return unwrap(api.PUT("/api/methods/{key}", { params: { path: { key: m.key } }, body: { defaults } }));
    },
    onSuccess: () => {
      setProblem(null);
      return qc.invalidateQueries({ queryKey: ["methods"] });
    },
    onError: (e: Error) => setProblem(e.message),
  });
  return (
    <section aria-label={`Method ${m.key}`} data-method={m.key} style={{ marginBottom: 32 }}>
      <h2 style={{ marginBottom: 4 }}>{m.label}</h2>
      <p className="lede" style={{ marginBottom: 8 }}>
        {m.summary || "No summary."}
      </p>
      <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "2px 16px", margin: "0 0 12px" }}>
        <dt style={muted}>Measures</dt>
        <dd data-fact="quantity">{m.quantity ?? "nothing (prepares, refuses or decides)"}</dd>
        <dt style={muted}>Cost</dt>
        <dd data-fact="cost">{m.cost_model}</dd>
        <dt style={muted}>Memory</dt>
        <dd data-fact="memory">{m.memory_model}</dd>
        <dt style={muted}>Floor</dt>
        <dd data-fact="floor">{m.floor === null ? "unmeasured" : `${m.floor} kcal/mol`}</dd>
        <dt style={muted}>Used by</dt>
        <dd data-fact="used-by">{m.used_by.length ? m.used_by.join(", ") : "no pipeline in this workspace"}</dd>
      </dl>
      {m.params.length === 0 ? (
        <p style={muted}>This method has no settings.</p>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>Setting</th>
              <th>Default</th>
              <th>Workspace default</th>
              <th>What it does</th>
            </tr>
          </thead>
          <tbody>
            {m.params.map((p) => (
              <tr key={p.name} data-param={p.name}>
                <td className="mono">{p.name}</td>
                <td className="mono" data-cell="default">
                  {show(p.default) || (p.required ? "required" : "unset")}
                </td>
                <td>
                  {p.kind === "sites" ? (
                    <span style={muted}>set per pipeline</span>
                  ) : p.kind === "choice" || p.kind === "bool" ? (
                    <select
                      aria-label={`${m.key} ${p.name} workspace default`}
                      value={draft[p.name] ?? ""}
                      onChange={(e) => setDraft({ ...draft, [p.name]: e.target.value })}
                    >
                      <option value="">(none)</option>
                      {(p.kind === "bool" ? ["true", "false"] : p.choices.map(String)).map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      aria-label={`${m.key} ${p.name} workspace default`}
                      value={draft[p.name] ?? ""}
                      onChange={(e) => setDraft({ ...draft, [p.name]: e.target.value })}
                      style={{ width: "100%", font: "inherit" }}
                    />
                  )}
                </td>
                <td data-cell="help">{p.help}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {editable.length ? (
        <p>
          <button className="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            Save defaults for {m.label}
          </button>{" "}
          <span style={muted}>Copied into a step when the method is added; existing steps do not change.</span>
        </p>
      ) : null}
      {problem ? (
        <div className="banner" role="alert">
          {problem}
        </div>
      ) : null}
    </section>
  );
}

/** Every method a pipeline can place: what each setting does, what it costs, and the workspace's default for it. */
export function Methods() {
  const q = useQuery({ queryKey: ["methods"], queryFn: () => unwrap(api.GET("/api/methods")) });
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>Methods</h1>
          <p className="lede">
            Every method a pipeline can place, with each setting explained. A workspace default is copied into a step
            when you add the method, so a run can always be redone from its own folder.
          </p>
          {q.isError ? (
            <div className="banner" role="alert">
              {q.error.message}
            </div>
          ) : null}
          {(q.data ?? []).map((m) => (
            <MethodCard key={`${m.key}:${JSON.stringify(m.params.map((p) => p.workspace_default))}`} m={m} />
          ))}
        </main>
      </div>
    </Shell>
  );
}
