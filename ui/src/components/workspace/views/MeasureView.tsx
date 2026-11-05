import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, unwrap } from "../../../api/client";
import type { components } from "../../../api/schema";
import { PlotlyFigure } from "../../PlotlyFigure";
import { useScheme } from "../../../styles/scheme";
import type { StepViewProps } from "./registry";
import { CorePose, getJson, NotRun, useRunDetail } from "./PosesView";

type Sensitivity = components["schemas"]["ModelSensitivity"];

/** One table row per (model, analogue): the analogue's paired ddE under the primary and the other charge model. */
export function sensitivityRows(models: Record<string, Sensitivity> | null | undefined) {
  return Object.entries(models ?? {}).flatMap(([model, m]) =>
    Object.entries(m.paired_ddE).map(([candidate, r]) => ({ ...r, chargeModel: model, candidate })),
  );
}

const fmt = (v: number | null | undefined, digits = 3) => (v === null || v === undefined ? "—" : v.toFixed(digits));

/** `results.charge_sensitivity`: the measurement repeated under another force field's pocket charges. Nothing when absent. */
export function SensitivityTable({ models, unit, parent }: { models: Record<string, Sensitivity> | null | undefined; unit: string; parent?: string }) {
  if (!models || Object.keys(models).length === 0) return null;
  return (
    <section aria-label="Charge-model sensitivity" data-testid="charge-sensitivity">
      <h4>Charge-model sensitivity</h4>
      <table className="grid" aria-label="Charge-model sensitivity summary">
        <thead>
          <tr>
            <th>Model</th>
            <th>vs</th>
            <th className="num">Sign flips</th>
            <th className="num">Spearman</th>
            <th className="num">Poses dropped</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(models).map(([model, m]) => (
            <tr key={model} data-model={model}>
              <td>{model}</td>
              <td>{m.primary_model ?? "primary"}</td>
              <td className="num" data-col="sign_flips">{m.sign_flips ?? "—"}</td>
              <td className="num" data-col="spearman">{fmt(m.spearman, 2)}</td>
              <td className="num" data-col="dropped">{m.n_dropped ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.entries(models).map(([model, m]) => (m.note ? <p className="lede" key={model}>{model}: {m.note}</p> : null))}
      <table className="grid" aria-label="Paired ddE under each charge model">
        <thead>
          <tr>
            <th>Model</th>
            <th>Ligand</th>
            <th className="num">Paired ΔΔE{parent ? ` vs ${parent}` : ""}, primary charges ({unit})</th>
            <th className="num">Paired ΔΔE, this model ({unit})</th>
            <th className="num">Shift</th>
            <th className="num">Poses</th>
          </tr>
        </thead>
        <tbody>
          {sensitivityRows(models).map((r) => (
            <tr key={`${r.chargeModel}/${r.candidate}`} data-model={r.chargeModel} data-ligand={r.candidate}>
              <td>{r.chargeModel}</td>
              <td>{r.candidate}</td>
              <td className="num" data-col="primary">{fmt(r.primary)}</td>
              <td className="num" data-col="model">{fmt(r.model)}</td>
              <td className="num" data-col="delta">{fmt(r.delta)}</td>
              <td className="num" data-col="n">{r.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

type Picked = { candidate: string; pose: number };
type CalcLines = { file: string | null; lines: Record<string, unknown>[] };
type ClickEvent = { points: { customdata?: unknown[] }[] };
export type PlotDiv = HTMLElement & { on?: (name: string, fn: (e: ClickEvent) => void) => void };

/**
 * Listen for `plotly_click` on the plot inside `host`. Plotly attaches `.on` once it has drawn, and the plot is a new
 * element when the figure is refetched (a theme change), so this binds to whichever element is there now and again
 * to the next one. `state.el` is the element already bound.
 */
export function bindClicks(host: HTMLElement | null, state: { el: PlotDiv | null }, onPick: (p: Picked) => void): void {
  const el = host?.querySelector<PlotDiv>("[data-figure]") ?? null;
  if (!el || typeof el.on !== "function" || el === state.el) return;
  state.el = el;
  el.on("plotly_click", (e) => {
    const d = e.points[0]?.customdata;
    if (d && typeof d[0] === "string" && typeof d[1] === "number") onPick({ candidate: d[0], pose: d[1] });
  });
}

function useClicks(host: React.RefObject<HTMLDivElement | null>, onPick: (p: Picked) => void) {
  const handler = useRef(onPick);
  handler.current = onPick;
  useEffect(() => {
    const state: { el: PlotDiv | null } = { el: null };
    const timer = window.setInterval(() => bindClicks(host.current, state, (p) => handler.current(p)), 100);
    return () => window.clearInterval(timer);
  }, [host]);
}

/** Step 5: what the SCFs measured, a point per ligand and pose. Click one to see that pose and every ligand's value there. */
export function MeasureView({ sourceRun }: StepViewProps) {
  const idx = sourceRun ?? 0;
  const theme = useScheme();
  const host = useRef<HTMLDivElement>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [showLog, setShowLog] = useState(false);
  const detail = useRunDetail(sourceRun);
  const figure = useQuery({
    queryKey: ["figure", idx, "per_pose_values", theme],
    queryFn: () =>
      unwrap(api.GET("/api/runs/{idx}/figures/{name}", { params: { path: { idx, name: "per_pose_values" }, query: { theme } } })),
    enabled: sourceRun !== null,
  });
  const rows = useQuery({
    queryKey: ["per_pose", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/per_pose", { params: { path: { idx } } })),
    enabled: sourceRun !== null,
  });
  const sensitivity = useQuery({
    queryKey: ["charge_sensitivity", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/charge_sensitivity", { params: { path: { idx } } })),
    enabled: sourceRun !== null,
  });
  const log = useQuery({
    queryKey: ["calcs", idx, picked?.candidate, picked?.pose],
    queryFn: () =>
      getJson<CalcLines>(`/api/runs/${idx}/calcs?candidate=${encodeURIComponent(picked?.candidate ?? "")}&pose=${picked?.pose ?? 0}`),
    enabled: sourceRun !== null && picked !== null && showLog,
  });
  useClicks(host, (p) => {
    setPicked(p);
    setShowLog(false);
  });
  if (sourceRun === null) return <NotRun step="measure" shows="the interaction energy of every ligand at every pose, and the SCF behind each point" />;
  const err = figure.error ?? rows.error ?? detail.error;
  if (err) return <div className="banner" role="alert">{err.message}</div>;
  const unit = detail.data?.unit ?? "";
  const parent = detail.data?.parent;
  const atPose = picked ? (rows.data ?? []).filter((r) => r.pose === picked.pose) : [];
  const parentValue = atPose[0]?.parent_value;
  return (
    <div data-view="measure">
      <p className="lede">Each marker is one pose of one ligand. Click a marker to see that pose and every ligand's value at it.</p>
      <div ref={host}>{figure.data ? <PlotlyFigure figure={figure.data} name="per_pose_values" /> : <p className="lede">Loading the measurements.</p>}</div>
      <SensitivityTable models={sensitivity.data?.models} unit={unit} parent={parent} />
      {picked ? (
        <section aria-label="Selected pose" data-testid="picked" data-candidate={picked.candidate} data-pose={picked.pose}>
          <h4>
            Pose {picked.pose}
            <button type="button" style={{ marginLeft: 12 }} onClick={() => setShowLog((v) => !v)}>
              SCF log
            </button>
          </h4>
          <table className="grid" aria-label="Values at the selected pose">
            <thead>
              <tr>
                <th>Ligand</th>
                <th className="num">ΔE_int ({unit})</th>
                <th className="num">Difference from {parent}</th>
              </tr>
            </thead>
            <tbody>
              {parent && parentValue !== undefined ? (
                <tr data-ligand={parent}>
                  <td>{parent} (parent)</td>
                  <td className="num" data-col="value">{parentValue.toFixed(3)}</td>
                  <td className="num" data-col="paired"></td>
                </tr>
              ) : null}
              {atPose.map((r) => (
                <tr key={r.candidate} data-ligand={r.candidate} data-selected={r.candidate === picked.candidate}>
                  <td>{r.candidate}</td>
                  <td className="num" data-col="value">{r.value.toFixed(3)}</td>
                  <td className="num" data-col="paired">{r.paired.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {showLog ? (
            log.isError ? (
              <div className="banner" role="alert">{log.error.message}</div>
            ) : log.data ? (
              <div data-testid="scf-log">
                {log.data.lines.length === 0 ? (
                  <p className="lede">The SCF log ({log.data.file}) has no line for {picked.candidate} pose {picked.pose}.</p>
                ) : (
                  <pre>{log.data.lines.map((l) => JSON.stringify(l)).join("\n")}</pre>
                )}
              </div>
            ) : (
              <p className="lede">Loading the SCF log.</p>
            )
          ) : null}
          <CorePose idx={sourceRun} candidate={picked.candidate} pose={picked.pose} label="measure step" />
        </section>
      ) : null}
    </div>
  );
}
