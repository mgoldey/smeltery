import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, unwrap } from "../../../api/client";
import { PlotlyFigure } from "../../PlotlyFigure";
import { useScheme } from "../../../styles/scheme";
import type { StepViewProps } from "./registry";
import { CorePose, getJson, NotRun, useRunDetail } from "./PosesView";

type Picked = { candidate: string; pose: number };
type CalcLines = { file: string | null; lines: Record<string, unknown>[] };
type ClickEvent = { points: { customdata?: unknown[] }[] };
type PlotDiv = HTMLElement & { on?: (name: string, fn: (e: ClickEvent) => void) => void };

/** Plotly attaches `.on` once it has drawn; wait for it, then listen for clicks on a point. */
function useClicks(host: React.RefObject<HTMLDivElement | null>, onPick: (p: Picked) => void) {
  const handler = useRef(onPick);
  handler.current = onPick;
  useEffect(() => {
    let bound = false;
    const timer = window.setInterval(() => {
      const el = host.current?.querySelector<PlotDiv>("[data-figure]");
      if (!el || typeof el.on !== "function" || bound) return;
      bound = true;
      el.on("plotly_click", (e) => {
        const d = e.points[0]?.customdata;
        if (d && typeof d[0] === "string" && typeof d[1] === "number") handler.current({ candidate: d[0], pose: d[1] });
      });
      window.clearInterval(timer);
    }, 100);
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
