import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";

/** The one loud thing on a run page. It makes exactly the decision the cut made and no more: UNRANKED is a result,
 * shown as prominently as a resolved one, and INCOMPLETE is never a blank. */
export function VerdictBanner({ runId }: { runId: string }) {
  const v = useQuery({
    queryKey: ["verdict", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/verdict", { params: { path: { run_id: runId } } })),
  });
  const d = useQuery({
    queryKey: ["run", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } })),
    enabled: v.data !== undefined && v.data.state !== "INCOMPLETE",
  });
  if (v.isError) {
    return (
      <div className="banner" role="alert">
        The verdict could not be read: {v.error.message}
      </div>
    );
  }
  if (!v.data) return <div className="verdict" aria-busy="true" />;
  const { state, missing, floor } = v.data;
  const unit = d.data?.unit ?? "";
  const floorText = floor === null ? "" : `${floor} ${unit}`.trim();
  if (state === "INCOMPLETE") {
    return (
      <section className="verdict" data-state={state} aria-label="Verdict">
        <p className="verdict-headline">Incomplete</p>
        <p className="verdict-detail">
          This record has no {missing.join(", ")}. The run stopped before its cut, so there is no result to read.
        </p>
      </section>
    );
  }
  if (state === "UNRANKED") {
    return (
      <section className="verdict" data-state={state} aria-label="Verdict">
        <p className="verdict-headline">Does not resolve</p>
        <p className="verdict-detail">
          The cut cannot separate the candidates at its keep boundary: their differences are inside the floor of{" "}
          <span className="num">{floorText}</span>
          {d.data ? ` (${d.data.floor_source})` : ""}. Choosing among them is a judgement, not a ranking.
        </p>
      </section>
    );
  }
  const kept = d.data?.survivors ?? [];
  return (
    <section className="verdict" data-state={state} aria-label="Verdict">
      <p className="verdict-headline">Keep {kept.length ? kept.join(", ") : "…"}</p>
      <p className="verdict-detail">
        Resolved at keep {d.data?.keep}: the kept set is separated from the rest beyond z = {d.data?.z} and the floor of{" "}
        <span className="num">{floorText}</span>
        {d.data ? ` (${d.data.floor_source})` : ""}.
      </p>
    </section>
  );
}
