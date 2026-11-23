import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useCanWrite } from "../api/capabilities";
import { api, unwrap, type Schemas } from "../api/client";
import { Shell } from "../components/Shell";
import { EmptyState } from "../components/EmptyState";
import { Limits } from "../components/Limits";
import { paramText, parseParam } from "../components/paramValue";
import { pipelineHref } from "../components/runIdentity";
import { runsQuery } from "../api/queries";

type Method = Schemas["MethodInfo"];
type Param = Schemas["MethodParam"];

const muted = { color: "var(--ink-muted)" } as const;

/** Methods' own wording for a refused default: it names the setting, since the card shows several at once. */
const REFUSED: Record<string, string> = {
  int: "must be a whole number",
  float: "must be a number",
  bool: "must be true or false",
  vec3: "needs three numbers",
  int_list: "needs whole numbers, comma separated",
};

function parse(p: Param, text: string): { value: unknown } | { error: string } {
  try {
    return { value: parseParam(p.kind, text) };
  } catch {
    return { error: `${p.name} ${REFUSED[p.kind] ?? "is not valid"}` };
  }
}

/** The workspace default overrides the built-in one for this setting. */
function differs(p: Param): boolean {
  return (
    p.kind !== "sites" &&
    p.workspace_default != null &&
    paramText(p.workspace_default) !== paramText(p.default)
  );
}

function MethodCard({ m }: { m: Method }) {
  const qc = useQueryClient();
  // "Used by" links into the pipeline editor, where the step views read a run. Without `?run=` each of them says
  // the step has not run, so the link that is meant to show this method at work shows nothing of it.
  const runs = useQuery(runsQuery());
  const editable = m.params.filter((p) => p.kind !== "sites");
  const saved = Object.fromEntries(
    editable.map((p) => [p.name, paramText(p.workspace_default)]),
  );
  const [draft, setDraft] = useState<Record<string, string>>(saved);
  const [problem, setProblem] = useState<string | null>(null);
  const dirty = editable.some((p) => (draft[p.name] ?? "") !== saved[p.name]);
  const canWrite = useCanWrite();
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
      return unwrap(
        api.PUT("/api/methods/{key}", {
          params: { path: { key: m.key } },
          body: { defaults },
        }),
      );
    },
    onSuccess: () => {
      setProblem(null);
      return qc.invalidateQueries({ queryKey: ["methods"] });
    },
    onError: (e: Error) => setProblem(e.message),
  });
  const reset = useMutation({
    mutationFn: (name: string) => {
      const defaults: Record<string, unknown> = {};
      for (const p of editable)
        if (p.name !== name && p.workspace_default != null)
          defaults[p.name] = p.workspace_default;
      return unwrap(
        api.PUT("/api/methods/{key}", {
          params: { path: { key: m.key } },
          body: { defaults },
        }),
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["methods"] }),
    onError: (e: Error) => setProblem(e.message),
  });
  return (
    <section
      aria-label={`Method ${m.key}`}
      data-method={m.key}
      style={{ marginBottom: 32 }}
    >
      <h2 style={{ marginBottom: 4 }}>{m.label}</h2>
      <p className="lede" style={{ marginBottom: 8 }}>
        {m.summary || "No summary."}
      </p>
      <dl
        style={{
          display: "grid",
          gridTemplateColumns: "max-content 1fr",
          gap: "2px 16px",
          margin: "0 0 12px",
        }}
      >
        <dt style={muted}>Measures</dt>
        <dd data-fact="quantity">
          {m.quantity ?? "nothing (prepares, refuses or decides)"}
        </dd>
        <dt style={muted}>Cost</dt>
        <dd data-fact="cost">{m.cost_model}</dd>
        <dt style={muted}>Memory</dt>
        <dd data-fact="memory">{m.memory_model}</dd>
        <dt style={muted}>Floor</dt>
        <dd data-fact="floor">
          {m.floor === null ? "unmeasured" : `${m.floor} kcal/mol`}
          <span style={muted}> ({m.floor_status})</span>
          {m.tier_doc ? (
            <>
              {" "}
              <span style={muted}>see</span>{" "}
              <code data-fact="tier-doc">{m.tier_doc}</code>
            </>
          ) : null}
        </dd>
        <dt style={muted}>Used by</dt>
        <dd data-fact="used-by">
          {m.used_by.length
            ? m.used_by.map((n, i) => (
                <span key={n}>
                  {i ? ", " : ""}
                  <Link to={pipelineHref(n, runs.data)} data-pipeline={n}>
                    {n}
                  </Link>
                </span>
              ))
            : "no pipeline in this workspace"}
        </dd>
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
              <tr
                key={p.name}
                data-param={p.name}
                data-differs={differs(p) || undefined}
              >
                <td className="mono">{p.name}</td>
                <td className="mono" data-cell="default">
                  {paramText(p.default) || (p.required ? "required" : "unset")}
                </td>
                <td>
                  {p.kind === "sites" ? (
                    <span style={muted}>set per pipeline</span>
                  ) : p.kind === "choice" || p.kind === "bool" ? (
                    <select
                      disabled={!canWrite}
                      aria-label={`${m.key} ${p.name} workspace default`}
                      value={draft[p.name] ?? ""}
                      onChange={(e) =>
                        setDraft({ ...draft, [p.name]: e.target.value })
                      }
                    >
                      <option value="">(none)</option>
                      {(p.kind === "bool"
                        ? ["true", "false"]
                        : p.choices.map(String)
                      ).map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      disabled={!canWrite}
                      aria-label={`${m.key} ${p.name} workspace default`}
                      value={draft[p.name] ?? ""}
                      onChange={(e) =>
                        setDraft({ ...draft, [p.name]: e.target.value })
                      }
                      style={{ width: "100%", font: "inherit" }}
                    />
                  )}
                </td>
                <td data-cell="help">
                  {p.help}
                  {differs(p) && canWrite ? (
                    <>
                      {" "}
                      <button
                        disabled={dirty || save.isPending || reset.isPending}
                        onClick={() => reset.mutate(p.name)}
                      >
                        Reset to built-in
                      </button>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {editable.length && canWrite ? (
        <p>
          <button
            className="primary"
            disabled={!dirty || save.isPending || reset.isPending}
            onClick={() => save.mutate()}
          >
            Save defaults for {m.label}
          </button>{" "}
          <span style={muted}>
            Copied into a step when the method is added; existing steps do not
            change.
          </span>
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
  const q = useQuery({
    queryKey: ["methods"],
    queryFn: () => unwrap(api.GET("/api/methods")),
  });
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>Methods</h1>
          <p className="lede">
            Every method a pipeline can place, with each setting explained. A
            workspace default is copied into a step when you add the method, so
            a run can always be redone from its own folder.
          </p>
          {/* The limits come before the methods: they are the frame a method is chosen inside, and they used to
              live only on Report, collapsed, where the person picking a method never saw them. Collapsed, but
              with its count in the summary, so it reads as something to open rather than something to scroll
              past: opened by default it is 1561px of table and the page's own methods start below the fold. */}
          <Limits lede="What the funnel cannot tell you, whichever method you place here. Each limit rests on one system or one model pair (docs/limits.md)." />
          {q.isError ? (
            <div className="banner" role="alert">
              {q.error.message}
            </div>
          ) : null}
          {q.data && q.data.length === 0 ? (
            <EmptyState>
              No methods are registered, so a pipeline has nothing to place.
              Check that smeltery is installed with its method modules, then
              reload.
            </EmptyState>
          ) : null}
          {(q.data ?? []).map((m) => (
            <MethodCard
              key={`${m.key}:${JSON.stringify(m.params.map((p) => p.workspace_default))}`}
              m={m}
            />
          ))}
        </main>
      </div>
    </Shell>
  );
}
