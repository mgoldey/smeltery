import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../../api/client";
import { PlotlyFigure } from "../../PlotlyFigure";
import { useScheme } from "../../../styles/scheme";
import type { StepViewProps } from "./registry";
import { NotRun } from "./PosesView";

function useCutFigure(idx: number, name: "forest" | "matrix", enabled: boolean) {
  const theme = useScheme();
  return useQuery({
    queryKey: ["figure", idx, name, theme],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/figures/{name}", { params: { path: { idx, name }, query: { theme } } })),
    enabled,
  });
}

/** Step 6: the cut. The forest and the resolved matrix Results draws, unchanged. */
export function CutView({ sourceRun }: StepViewProps) {
  const forest = useCutFigure(sourceRun ?? 0, "forest", sourceRun !== null);
  const matrix = useCutFigure(sourceRun ?? 0, "matrix", sourceRun !== null);
  if (sourceRun === null) return <NotRun step="cut" shows="the paired ΔΔE of each analogue against the parent and which pairs the cut could tell apart" />;
  const err = forest.error ?? matrix.error;
  if (err) return <div className="banner" role="alert">{err.message}</div>;
  return (
    <div data-view="cut">
      {forest.data ? <PlotlyFigure figure={forest.data} name="forest" /> : <p className="lede">Loading the cut.</p>}
      {matrix.data ? <PlotlyFigure figure={matrix.data} name="matrix" /> : null}
    </div>
  );
}
