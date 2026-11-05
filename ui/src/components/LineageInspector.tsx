import { useQuery } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../api/client";
import { useOpenRunPipeline } from "./workspace/openFromRun";

type Lineage = Schemas["Lineage"];
const ABSENT = "not recorded for this run";

function Absent() {
  return <span className="absent">{ABSENT}</span>;
}

function fmtSeconds(s: number | null): string {
  return s === null ? "not timed" : `${s.toFixed(1)} s`;
}

function settingsText(settings: Record<string, unknown>): string {
  return Object.entries(settings)
    .map(([k, v]) => `${k} ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

function MadeBy({ l }: { l: Lineage }) {
  const miss = (key: string) => l.missing.includes(key);
  return (
    <section aria-labelledby="made-by-h">
      <h3 id="made-by-h">Made by</h3>
      <table className="grid" aria-label="Made by">
        <tbody>
          {l.steps.map((s) => (
            <tr key={s.stage} data-testid="made-step">
              <td>{s.stage}</td>
              <td>
                {s.settings === null ? <Absent /> : Object.keys(s.settings).length === 0 ? "no settings" : settingsText(s.settings)}
                {`, ${fmtSeconds(s.seconds)}`}
              </td>
            </tr>
          ))}
          <tr data-testid="made-field">
            <td>Field</td>
            <td>
              {l.field.n_charges} charges
              {l.field.provenance === null || miss("field.provenance") ? (
                <>
                  , provenance <Absent />
                </>
              ) : (
                `, ${settingsText(l.field.provenance)}`
              )}
            </td>
          </tr>
          <tr data-testid="made-engine">
            <td>Engine</td>
            <td>{Object.keys(l.engine).length === 0 ? <Absent /> : settingsText(l.engine)}</td>
          </tr>
          <tr data-testid="made-smeltery">
            <td>smeltery</td>
            <td>{l.smeltery === null ? <Absent /> : settingsText(l.smeltery)}</td>
          </tr>
          <tr data-testid="made-digest">
            <td>Digest</td>
            <td className="mono" title={l.digest}>
              {l.digest.slice(0, 12)}
            </td>
          </tr>
          <tr data-testid="made-config">
            <td>Config file</td>
            <td>{l.config === null ? <Absent /> : `${l.config.file} (sha256 ${l.config.sha256.slice(0, 12)})`}</td>
          </tr>
          <tr data-testid="made-calcs">
            <td>Calculations</td>
            <td>{l.calcs === null ? <Absent /> : `${l.calcs.file}${l.calcs.n != null ? `, ${l.calcs.n} calculations` : ""}`}</td>
          </tr>
          <tr data-testid="made-ran">
            <td>Ran at</td>
            <td>{l.started_utc ?? <Absent />}</td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}

/** What made one candidate's paired ΔΔE: its poses, then the steps and records behind them. */
export function LineageInspector({ idx, candidate, onClose }: { idx: number; candidate: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["lineage", idx, candidate],
    queryFn: () =>
      unwrap(api.GET("/api/runs/{idx}/lineage", { params: { path: { idx }, query: { candidate } } })),
  });
  const l = q.data;
  const open = useOpenRunPipeline(idx);
  return (
    <aside className="inspector" aria-label="Lineage">
      <h2 style={{ marginTop: 0 }}>{candidate}</h2>
      {q.isError ? <p className="field-error">{q.error.message}</p> : null}
      {l ? (
        <>
          <p className="lede">
            Paired {l.quantity} = {l.mean.toFixed(2)} ± {l.sem.toFixed(2)}: the mean over {l.n} paired poses of {candidate}'s
            value minus {l.parent}'s in the same pose.
          </p>
          <table className="grid" aria-label="Per-pose values">
            <thead>
              <tr>
                <th className="num">Pose</th>
                <th className="num">{candidate}</th>
                <th className="num">{l.parent}</th>
                <th className="num">Difference</th>
              </tr>
            </thead>
            <tbody>
              {l.poses.map((p) => (
                <tr key={p.pose}>
                  <td className="num">{p.pose}</td>
                  <td className="num">{p.value.toFixed(2)}</td>
                  <td className="num">{p.parent_value.toFixed(2)}</td>
                  <td className="num">{p.difference.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <MadeBy l={l} />
          <p className="controls">
            <button type="button" disabled={open.isPending} onClick={() => open.mutate({})}>
              Open the pipeline that made this
            </button>
            <button type="button" disabled={open.isPending} onClick={() => open.mutate({ step: "measure" })}>
              Change a setting and re-run
            </button>
          </p>
          {open.isError ? <p className="field-error">{open.error.message}</p> : null}
        </>
      ) : null}
      <button type="button" onClick={onClose}>
        Close
      </button>
    </aside>
  );
}
