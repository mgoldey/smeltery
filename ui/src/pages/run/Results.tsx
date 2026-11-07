import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap } from "../../api/client";
import { LineageInspector } from "../../components/LineageInspector";
import { PerPoseTable } from "../../components/PerPoseTable";
import { PlotlyFigure } from "../../components/PlotlyFigure";
import { VerdictBanner } from "../../components/VerdictBanner";
import { useScheme } from "../../styles/scheme";
import { useRunIdx } from "./RunLayout";

function useFigure(idx: number, name: "forest" | "matrix" | "per_pose" | "per_pose_values", enabled: boolean) {
  const theme = useScheme();
  return useQuery({
    queryKey: ["figure", idx, name, theme],
    queryFn: () =>
      unwrap(api.GET("/api/runs/{idx}/figures/{name}", { params: { path: { idx, name }, query: { theme } } })),
    enabled,
  });
}

function groupLabel(group: number, size: number): string {
  return size > 1 ? `tie group ${group + 1} (no order)` : `group ${group + 1}`;
}

export function Results() {
  const idx = useRunIdx();
  const [traced, setTraced] = useState<string | null>(null);
  const [pairs, setPairs] = useState(true);
  const verdict = useQuery({
    queryKey: ["verdict", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/verdict", { params: { path: { idx } } })),
  });
  const ok = verdict.data !== undefined && verdict.data.state !== "INCOMPLETE";
  const table = useQuery({
    queryKey: ["ddE", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/ddE", { params: { path: { idx } } })),
    enabled: ok,
  });
  const forest = useFigure(idx, "forest", ok);
  const matrix = useFigure(idx, "matrix", ok);
  const perPose = useFigure(idx, "per_pose", ok);
  const perPoseValues = useFigure(idx, "per_pose_values", ok && !pairs);
  const rows = useQuery({
    queryKey: ["per_pose", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/per_pose", { params: { path: { idx } } })),
    enabled: ok,
  });
  const sizes = new Map<number, number>();
  for (const r of table.data?.rows ?? []) sizes.set(r.group, (sizes.get(r.group) ?? 0) + 1);

  return (
    <div className="body">
      <main className="work">
        <VerdictBanner idx={idx} />
        {ok && table.data ? (
          <>
            <h2>Paired ΔΔE against the parent</h2>
            <p className="lede">
              Mean ± {table.data.z}·SEM per analogue. Candidates the cut could not separate share a band and a label; their
              order inside it means nothing.
            </p>
            {forest.data ? <PlotlyFigure figure={forest.data} name="forest" /> : null}
            <table className="grid" aria-label="Paired ΔΔE">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th className="num">Mean ({table.data.unit})</th>
                  <th className="num">SEM</th>
                  <th className="num">Interval</th>
                  <th className="num">Poses</th>
                  <th>Group</th>
                  <th>Cut</th>
                </tr>
              </thead>
              <tbody>
                {table.data.rows.map((r) => (
                  <tr key={r.candidate}>
                    <td>
                      <button
                        type="button"
                        className="link"
                        aria-label={`Trace ${r.candidate}`}
                        aria-pressed={traced === r.candidate}
                        onClick={() => setTraced(r.candidate)}
                      >
                        {r.candidate}
                      </button>
                    </td>
                    <td className="num">{r.mean.toFixed(2)}</td>
                    <td className="num">{r.sem.toFixed(2)}</td>
                    <td className="num">
                      {r.lo.toFixed(2)} to {r.hi.toFixed(2)}
                    </td>
                    <td className="num">{r.n}</td>
                    <td>{groupLabel(r.group, sizes.get(r.group) ?? 1)}</td>
                    <td>{r.survivor === null ? "unranked" : r.survivor ? "kept" : "not kept"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h2>Can these two be told apart?</h2>
            <p className="lede">The funnel's own test for each pair, at the same z and floor as the cut.</p>
            {matrix.data ? <PlotlyFigure figure={matrix.data} name="matrix" /> : null}
            <h2>Per-pose pairs</h2>
            <div className="controls" role="group" aria-label="Per-pose view">
              <button type="button" aria-pressed={pairs} onClick={() => setPairs(true)}>
                Paired lines
              </button>
              <button type="button" aria-pressed={!pairs} onClick={() => setPairs(false)}>
                Values per pose
              </button>
            </div>
            {pairs ? (
              <>
                <p className="lede">Each line joins an analogue's pose to the parent's pose with the same index; pairing is why the paired SEM is small.</p>
                {perPose.isError ? <p className="empty-state">{perPose.error.message}</p> : null}
                {perPose.data ? <PlotlyFigure figure={perPose.data} name="per_pose" /> : null}
              </>
            ) : (
              <>
                <p className="lede">The parent's and each analogue's own value at every pose, before pairing.</p>
                {perPoseValues.isError ? <p className="empty-state">{perPoseValues.error.message}</p> : null}
                {perPoseValues.data ? <PlotlyFigure figure={perPoseValues.data} name="per_pose_values" /> : null}
              </>
            )}
            {rows.isError && !(pairs ? perPose.isError : perPoseValues.isError) ? <p className="empty-state">{rows.error.message}</p> : null}
            {rows.data ? <PerPoseTable rows={rows.data} unit={table.data.unit} fileName={`per_pose_${idx}.csv`} /> : null}
          </>
        ) : null}
      </main>
      {traced !== null ? <LineageInspector idx={idx} candidate={traced} onClose={() => setTraced(null)} /> : null}
    </div>
  );
}
