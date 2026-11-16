import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { useCapabilities } from "../../api/capabilities";
import { api, runByRef, unwrap } from "../../api/client";
import { Shell } from "../../components/Shell";

/**
 * The Run area's tabs: views OF this run, in the order you read them.
 *
 * Two former tabs are deliberately absent. "Pipeline" was not a view -- it rendered "Opening the pipeline that made
 * this run…" and navigated away, so a tab click landed in a different area with different nav state; it is a button
 * in the context bar instead, which is what it always was. "Structure" never referenced the run at all (no `runId`),
 * so nesting it under `/run/:runId` misstated its scope; it is a top-level inspector now.
 */
export const RUN_TABS: { to: string; label: string }[] = [
  { to: "results", label: "Results" },
  { to: "poses", label: "Poses" },
  { to: "decisions", label: "Decisions" },
  { to: "proposals", label: "Proposals" },
  { to: "provenance", label: "Provenance" },
];

/** Tabs whose page only writes (or reads through a POST): not offered when the server is read-only. */
const WRITE_TABS = new Set(["decisions"]);

/** The run this page is about: the stable id from the URL (`/run/:runId`). An old all-digits link is passed through
 * unchanged -- the API still resolves it positionally and says so with a Deprecation header. */
export function useRunId(): string {
  const { runId } = useParams();
  if (!runId) throw new Error("no run in the URL");
  return runId;
}

/**
 * How much of someone else's input is waiting on the Proposals tab.
 *
 * Both halves of the chemist/colleague conversation happen on the Report page, which the chemist does not visit,
 * and the Proposals tab used to look identical whether a colleague had written five proposals or none. The counts
 * come from the same two queries the Proposals page itself runs -- `["runs"]` is already loaded by the context bar
 * on every run page, and react-query shares the other two -- so naming the number here costs the tab nothing that
 * opening it would not have cost anyway.
 *
 * Nothing is reported while either read is in flight or has failed: a tab that says "0" because a fetch broke
 * would be a worse signal than a tab that says nothing.
 */
function useWaiting(runId: string | undefined): number | undefined {
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => unwrap(api.GET("/api/runs")),
    enabled: runId !== undefined,
  });
  const digest = runByRef(runs.data, runId)?.digest ?? undefined;
  const proposals = useQuery({
    queryKey: ["proposals", digest],
    queryFn: () => unwrap(api.GET("/api/proposals", { params: { query: { run: digest ?? "" } } })),
    enabled: digest !== undefined,
  });
  const comments = useQuery({
    queryKey: ["comments", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/comments", { params: { path: { run_id: runId ?? "" } } })),
    enabled: runId !== undefined,
  });
  if (proposals.data === undefined || comments.data === undefined) return undefined;
  return proposals.data.length + comments.data.length;
}

export function RunLayout() {
  const { runId } = useParams();
  const { readOnly } = useCapabilities();
  const waiting = useWaiting(runId);
  const tabs = (
    <nav className="tabs" aria-label="Run views">
      {RUN_TABS.filter((t) => !(readOnly && WRITE_TABS.has(t.to))).map((t) => {
        const n = t.to === "proposals" && waiting ? waiting : undefined;
        return (
          <NavLink
            key={t.to}
            to={`/run/${runId}/${t.to}`}
            aria-label={n ? `${t.label}, ${n} waiting` : undefined}
          >
            {t.label}
            {n ? (
              <>
                {" "}
                <span className="num" data-testid="proposals-waiting">
                  {n}
                </span>
              </>
            ) : null}
          </NavLink>
        );
      })}
    </nav>
  );
  return (
    <Shell tabs={tabs}>
      <Suspense fallback={<p className="lede">Loading the view.</p>}>
        <Outlet />
      </Suspense>
    </Shell>
  );
}
