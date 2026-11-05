import type { Schemas } from "../../api/client";
import { SitePlacer } from "./SitePlacer";
import { ParamRow } from "./ParamRow";

type Step = Schemas["StepOut"];
type Catalog = Schemas["CatalogOut"];
type MethodInfo = Schemas["MethodOut"];
export type EditOp = Schemas["StepEdit"]["op"];
export type EditExtra = { params?: Record<string, unknown>; site?: Schemas["SiteSpec"] };
type Site = { q: number; atom: number; from_atom: number; distance: number };

function Flow({ items, steps, dir }: { items: Step["inputs"]; steps: Step[]; dir: "from" | "to" }) {
  const name = (key: string | null) => {
    if (key === null) return dir === "from" ? "you give" : "";
    const i = steps.findIndex((s) => s.key === key);
    return `${dir === "from" ? "from" : "→"} step ${i + 1} · ${steps[i]?.label ?? key}`;
  };
  return (
    <>
      {items.map((x, i) => (
        <div key={i} className="flow-item">
          {dir === "from" ? <div className="from">{name(x.step)}</div> : null}
          <div>{x.text}</div>
          {dir === "to" ? <div className="from">{name(x.step)}</div> : null}
        </div>
      ))}
    </>
  );
}

function Sites({ sites, disabled, onSet }: { sites: Site[]; disabled: boolean; onSet: (v: Site[] | null) => void }) {
  return (
    <div>
      {sites.length === 0 ? <p className="empty-state">No charges placed yet. Place one by clicking two atoms of the parent below.</p> : null}
      {sites.length ? (
        <table className="grid" aria-label="Pocket sites">
          <thead>
            <tr>
              <th className="num">q (e)</th>
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
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => {
                      const left = sites.filter((_, j) => j !== i);
                      onSet(left.length ? left : null);
                    }}
                    aria-label={`Remove site ${i + 1}`}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

/** Add, Use (the step's other method: replaces the one it excludes) or nothing, for a method this step offers. */
function offer(m: MethodInfo, step: Step): "add" | "choose" | null {
  if (step.placed.includes(m.key)) return null;
  return m.exclusive.some((o) => step.placed.includes(o)) ? "choose" : "add";
}

/**
 * One step edited in place: what flows in and out (Inputs), the method choice and every knob with its own help
 * (Method), and the step's output (View). Every edit goes to the API, which writes it into the TOML text.
 */
export function StepPanel({
  step,
  steps,
  index,
  catalog,
  ligands,
  disabled,
  onEdit,
}: {
  step: Step;
  steps: Step[];
  index: number;
  catalog: Catalog | undefined;
  ligands: Schemas["Ligands"] | null | undefined;
  disabled: boolean;
  onEdit: (op: EditOp, key: string, extra?: EditExtra) => Promise<unknown>;
}) {
  const info = (k: string) => catalog?.methods[k];
  const label = (k: string) => info(k)?.label ?? k;
  const sites = step.params.find((p) => p.kind === "sites");
  const parentSmiles = ligands ? ligands.candidates[ligands.parent] : undefined;
  return (
    <section className="step-panel" aria-label={`Step ${index + 1} · ${step.label}`} data-panel={step.key}>
      <div className="step-head">
        <h2>
          {index + 1} · {step.label}
        </h2>
        <span className="say">{step.summary}</span>
      </div>
      <div className="step-cols">
        <section aria-label="Inputs">
          <h3>Inputs</h3>
          {step.key === "ligands" && ligands ? (
            <div className="flow-item">
              <div className="from">campaign {ligands.campaign}</div>
              <div>
                {Object.keys(ligands.candidates).map((n, i) => (
                  <span key={n}>
                    {i ? ", " : ""}
                    {n}
                    {n === ligands.parent ? " (parent)" : ""}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
          <Flow items={step.inputs} steps={steps} dir="from" />
          <h3 style={{ marginTop: 12 }}>Gives</h3>
          {step.gives.length ? <Flow items={step.gives} steps={steps} dir="to" /> : <p className="lede">the ranked shortlist</p>}
        </section>
        <section aria-label="Method">
          <h3>Method</h3>
          {step.methods.length === 0 ? (
            <p className="lede">
              The campaign, parent and candidates are the pipeline's own inputs: edit <code>[candidates]</code> in the TOML pane.
            </p>
          ) : null}
          <div className="method-choice">
            {step.methods.map((k) => {
              const m = info(k);
              const how = m ? offer(m, step) : null;
              if (!m || how === null) return null;
              if (!m.wired)
                return (
                  <span key={k} className="lede" title={m.why_unwired}>
                    {m.label}: not runnable yet ({m.why_unwired})
                  </span>
                );
              const verb = how === "choose" ? "Use" : "Add";
              return (
                <button key={k} type="button" disabled={disabled} onClick={() => void onEdit(how, k)} aria-label={`${verb} ${m.label}`}>
                  {verb} {m.label}
                </button>
              );
            })}
          </div>
          {step.placed.map((k) => {
            const m = info(k);
            const params = step.params.filter((p) => p.method === k);
            return (
              <div key={k} className="method-block" data-method={k}>
                <h4>
                  {label(k)}
                  {m && !m.required ? (
                    <button type="button" disabled={disabled} onClick={() => void onEdit("remove", k)} aria-label={`Remove ${label(k)}`}>
                      Remove
                    </button>
                  ) : null}
                </h4>
                {params.length === 0 ? <p className="lede">No settings.</p> : null}
                {params.map((p) =>
                  p.kind === "sites" ? (
                    <div key={p.name}>
                      <Sites
                        sites={(p.value as Site[] | null) ?? []}
                        disabled={disabled}
                        onSet={(v) => void onEdit("set", k, { params: { [p.name]: v } })}
                      />
                    </div>
                  ) : (
                    <ParamRow
                      key={`${p.method}.${p.name}.${JSON.stringify(p.value)}`}
                      p={p}
                      disabled={disabled}
                      onSet={(v) => void onEdit("set", k, { params: { [p.name]: v } })}
                    />
                  ),
                )}
              </div>
            );
          })}
          {step.issues.length ? (
            <ul className="issues step-issues" aria-label={`Issues of ${step.label}`}>
              {step.issues.map((iss, i) => (
                <li key={i} className="issue" data-level={iss.level}>
                  <span className="issue-level">{iss.level}</span>
                  <span>{iss.message}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
        <section aria-label="View">
          <h3>View</h3>
          <p className="lede">What this step produced will be shown here; the step views are not built yet.</p>
        </section>
      </div>
      {sites && ligands && parentSmiles ? (
        <div className="step-wide">
          <SitePlacer
            parent={ligands.parent}
            smiles={parentSmiles}
            disabled={disabled}
            onPlace={(atom, from_atom, q, distance) => onEdit("site", sites.method, { site: { atom, from_atom, q, distance } })}
          />
        </div>
      ) : null}
    </section>
  );
}
