import { Suspense } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { useCapabilities } from "../../api/capabilities";
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

export function RunLayout() {
  const { runId } = useParams();
  const { readOnly } = useCapabilities();
  const tabs = (
    <nav className="tabs" aria-label="Run views">
      {RUN_TABS.filter((t) => !(readOnly && WRITE_TABS.has(t.to))).map((t) => (
        <NavLink key={t.to} to={`/run/${runId}/${t.to}`}>
          {t.label}
        </NavLink>
      ))}
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
