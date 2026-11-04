import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../../api/client";
import { TomlEditor } from "../../components/TomlEditor";
import { KnobForm } from "./pipeline/KnobForm";
import { Palette } from "./pipeline/Palette";
import { usePipeline } from "./pipeline/usePipeline";
import { useRunIdx } from "./RunLayout";

export function Pipeline() {
  const idx = useRunIdx();
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: () => unwrap(api.GET("/api/catalog")) });
  const { state, origin, error, pending, edit, check } = usePipeline(idx);
  const [selected, setSelected] = useState<string | null>(null);
  const cat = catalog.data;
  const label = (k: string) => cat?.methods[k]?.label ?? k;
  const selectedMethod = selected ? cat?.methods[selected] : undefined;
  const errors = state?.issues.filter((i) => i.level === "error").length ?? 0;

  return (
    <div className="body">
      <main className="work">
        <h1>Pipeline</h1>
        <p className="lede">
          The next run's method, starting from this run's. Every change is checked by the same rules <code>smeltery run</code>{" "}
          uses, and the TOML below is the pipeline: edit either and the other follows.
        </p>
        {error ? (
          <div className="banner" role="alert">
            {error}
          </div>
        ) : null}
        {state?.notes.map((n) => (
          <p key={n} className="banner note" role="note">
            {n}
          </p>
        ))}
        {cat && state ? (
          <div className="pipeline-grid">
            <Palette catalog={cat} placed={state.placed} addable={state.addable} disabled={pending} onAdd={(k) => void edit("add", k)} />
            <div>
              <section aria-label="Stages">
                <h2>Stages</h2>
                <p className="lede">In the order <code>smeltery run</code> executes them. That order is fixed, so stages are added and removed, not rearranged.</p>
                <ol className="stages">
                  {state.placed.map((k) => (
                    <li key={k} className={k === selected ? "stage selected" : "stage"} data-stage={k}>
                      <span className="stage-label">{label(k)}</span>
                      <span className="stage-rung">{cat.methods[k]?.rung.replace("_", " ")}</span>
                      <span className="stage-actions">
                        <button type="button" onClick={() => setSelected(k)} aria-label={`Edit ${label(k)} knobs`}>
                          Knobs
                        </button>
                        <button
                          type="button"
                          disabled={pending || cat.methods[k]?.required}
                          onClick={() => {
                            if (selected === k) setSelected(null);
                            void edit("remove", k);
                          }}
                          aria-label={`Remove ${label(k)}`}
                        >
                          Remove
                        </button>
                      </span>
                    </li>
                  ))}
                </ol>
              </section>
              <section aria-label="Issues">
                <h2>
                  Issues{" "}
                  <span className="lede">
                    {state.issues.length === 0 ? "none: `smeltery run` accepts this pipeline" : `${errors} blocking, ${state.issues.length - errors} other`}
                  </span>
                </h2>
                <ul className="issues">
                  {state.issues.map((iss, i) => (
                    <li key={i} className="issue" data-level={iss.level}>
                      <span className="issue-level">{iss.level}</span>
                      {iss.stage ? <span className="mono">{iss.stage}</span> : null}
                      <span>{iss.message}</span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          </div>
        ) : null}
        {state ? (
          <section aria-label="Pipeline TOML">
            <h2>TOML</h2>
            <TomlEditor value={state.toml} onCommit={(t) => void check(t)} label="Pipeline TOML" key={origin === "load" ? "load" : "live"} />
          </section>
        ) : null}
      </main>
      {selectedMethod && state ? (
        <aside className="inspector" aria-label={`${selectedMethod.label} knobs`}>
          <h2 style={{ marginTop: 0 }}>{selectedMethod.label}</h2>
          <p className="lede">{selectedMethod.summary}</p>
          <KnobForm
            method={selectedMethod}
            params={(state.params[selectedMethod.key] as Record<string, unknown> | undefined) ?? {}}
            onSet={(name, value) => void edit("set", selectedMethod.key, { params: { [name]: value } })}
          />
          <p>
            <button type="button" onClick={() => setSelected(null)}>
              Close
            </button>
          </p>
        </aside>
      ) : null}
    </div>
  );
}
