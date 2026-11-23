import { useQuery } from "@tanstack/react-query";
import { hasResult } from "../api/client";
import type { Schemas } from "../api/client";
import { ddeQuery, runQuery, verdictQuery } from "../api/queries";

type DdeRow = Schemas["DdeTable"]["rows"][number];

/**
 * The kept analogues when none of them scored better than the parent, else null. ΔΔE is analogue minus parent
 * (`funnel.paired_delta`), so the parent is the reference at 0, it has no row, and lower is better (the cut keeps
 * the lowest). Null too when a kept name has no row: the banner says nothing it cannot read off the table.
 */
export function keptNoBetterThanParent(kept: string[], rows: DdeRow[]): DdeRow[] | null {
  if (!kept.length) return null;
  const byName = new Map(rows.map((r) => [r.candidate, r]));
  const keptRows = kept.map((k) => byName.get(k));
  if (keptRows.some((r) => r === undefined)) return null;
  const all = keptRows as DdeRow[];
  return all.every((r) => r.mean >= 0) ? all : null;
}

const signed = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(2)}`;

/** The one loud thing on a run page. It makes exactly the decision the cut made and no more: UNRANKED is a result,
 * shown as prominently as a resolved one, and INCOMPLETE is never a blank.
 *
 * Every state with a result carries the run's floor statement under its headline, so "Keep 4-F" is never read
 * without the bound it was decided against. `floorStatement={false}` is for a page that already prints it. */
export function VerdictBanner({ runId, floorStatement = true }: { runId: string; floorStatement?: boolean }) {
  const v = useQuery(verdictQuery(runId));
  const d = useQuery({ ...runQuery(runId), enabled: hasResult(v.data) });
  const dde = useQuery({ ...ddeQuery(runId), enabled: v.data?.state === "RESOLVED" });
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
  const statement =
    floorStatement && d.data?.floor_statement ? (
      <p className="verdict-floor" data-testid="verdict-floor-statement">
        {d.data.floor_statement}
      </p>
    ) : null;
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
        {statement}
        <p className="verdict-detail">
          The cut cannot separate the candidates at its keep boundary: their differences are inside the floor of{" "}
          <span className="num">{floorText}</span>
          {d.data ? ` (${d.data.floor_source})` : ""}. Choosing among them is a judgement, not a ranking.
        </p>
      </section>
    );
  }
  const kept = d.data?.survivors ?? [];
  // A mocked or failed read can hand back something without rows; read nothing into it.
  const notBetter = keptNoBetterThanParent(kept, dde.data?.rows ?? []);
  return (
    <section className="verdict" data-state={state} aria-label="Verdict">
      <p className="verdict-headline">Keep {kept.length ? kept.join(", ") : "…"}</p>
      {notBetter ? (
        <p className="verdict-caveat" data-testid="verdict-not-better">
          {notBetter.map((r) => r.candidate).join(notBetter.length === 2 ? " and " : ", ")}{" "}
          {notBetter.length === 1 ? "does" : "do"} not score better than the parent (ΔΔE{" "}
          <span className="num">{notBetter.map((r) => signed(r.mean)).join(", ")}</span> {dde.data?.unit ?? unit};
          lower is better and the parent is 0): {notBetter.length === 1 ? "it is" : "they are"} kept for ranking
          best among the analogues.
        </p>
      ) : null}
      {statement}
      <p className="verdict-detail">
        Resolved at keep {d.data?.keep}: the kept set is separated from the rest beyond z = {d.data?.z} and the floor of{" "}
        <span className="num">{floorText}</span>
        {d.data ? ` (${d.data.floor_source})` : ""}.
      </p>
    </section>
  );
}
