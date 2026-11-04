import dagre from "@dagrejs/dagre";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { Shell } from "../components/Shell";

type Row = { step: string; upstream?: string[]; order?: number };

function Dag({ rows }: { rows: Row[] }) {
  const layout = useMemo(() => {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 56, marginx: 12, marginy: 12 });
    g.setDefaultEdgeLabel(() => ({}));
    for (const r of rows) g.setNode(r.step, { label: r.step, width: Math.max(96, r.step.length * 8 + 24), height: 34 });
    for (const r of rows) for (const u of r.upstream ?? []) g.setEdge(u, r.step);
    dagre.layout(g);
    return g;
  }, [rows]);
  const w = (layout.graph().width ?? 200) + 2;
  const h = (layout.graph().height ?? 80) + 2;
  return (
    <svg className="dag" viewBox={`0 0 ${w} ${h}`} width={w} height={h} role="img" aria-label="Study steps and their dependencies">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" className="dag-arrow" />
        </marker>
      </defs>
      {layout.edges().map((e) => {
        const pts = layout.edge(e).points ?? [];
        return (
          <polyline
            key={`${e.v}->${e.w}`}
            className="dag-edge"
            data-edge={`${e.v}->${e.w}`}
            points={pts.map((p: { x: number; y: number }) => `${p.x},${p.y}`).join(" ")}
            markerEnd="url(#arrow)"
          />
        );
      })}
      {layout.nodes().map((n) => {
        const node = layout.node(n);
        return (
          <g key={n} className="dag-node" data-step={n}>
            <rect x={node.x - node.width / 2} y={node.y - node.height / 2} width={node.width} height={node.height} rx={3} />
            <text x={node.x} y={node.y} dy="4.5">
              {n}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function Study() {
  const { module = "" } = useParams();
  const dag = useQuery({
    queryKey: ["study-dag", module],
    queryFn: () => unwrap(api.GET("/api/studies/{module_path}/dag", { params: { path: { module_path: module } } })),
  });
  const cells = useQuery({
    queryKey: ["study-cells", module],
    queryFn: () => unwrap(api.GET("/api/studies/{module_path}/cells", { params: { path: { module_path: module } } })),
  });
  const rows = (dag.data ?? []) as Row[];
  const steps = rows.map((r) => r.step);
  const byCondition = new Map<string, Map<string, string>>();
  for (const c of cells.data ?? []) {
    byCondition.set(c.label, (byCondition.get(c.label) ?? new Map()).set(c.step, c.state));
  }
  const toRun = (cells.data ?? []).filter((c) => c.state === "to_run").length;
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1 className="mono">{module}</h1>
          {dag.isError ? (
            <div className="banner" role="alert">
              {dag.error.message}
            </div>
          ) : null}
          {rows.length ? (
            <>
              <h2>Steps</h2>
              <p className="lede">Each step runs after the steps it needs. Results are cached by their inputs, so a step reruns only when something upstream changed.</p>
              <Dag rows={rows} />
            </>
          ) : null}
          {cells.data ? (
            <>
              <h2>
                Cells <span className="lede">{toRun === 0 ? "everything is cached" : `${toRun} of ${cells.data.length} would run`}</span>
              </h2>
              <table className="grid" aria-label="Study cells">
                <thead>
                  <tr>
                    <th>Condition</th>
                    {steps.map((s) => (
                      <th key={s}>{s}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...byCondition.entries()].map(([label, m]) => (
                    <tr key={label}>
                      <td className="mono">{label}</td>
                      {steps.map((s) => (
                        <td key={s}>
                          <span className="status" data-state={m.get(s) === "cached" ? "succeeded" : "queued"} data-cell={`${label}|${s}`}>
                            {m.get(s) === "cached" ? "cached" : "to run"}
                          </span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </main>
      </div>
    </Shell>
  );
}
