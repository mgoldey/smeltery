import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../../api/client";
import type { Schemas } from "../../../api/client";
import type { StepViewProps } from "./registry";
import { NotRun } from "./PosesView";

type GateRow = Schemas["GateRow"];

/** A pose whose MMFF strain was recorded but that no limit judged (`over_strain` is null without a limit). */
export function strainNotJudged(r: GateRow): boolean {
  return r.strain_kcal !== null && r.over_strain === null;
}

/** Step 4: the gates that ran before any SCF, one row per pose, with what failed. */
export function ChecksView({ sourceRun }: StepViewProps) {
  const gates = useQuery({
    queryKey: ["gates", sourceRun],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/gates", { params: { path: { run_id: sourceRun ?? "" } } })),
    enabled: sourceRun !== null,
  });
  if (sourceRun === null) return <NotRun step="checks" shows="each pose with pass or fail, the PoseBusters checks it failed and its MMFF strain" />;
  if (gates.isError) return <div className="banner" role="alert">{gates.error.message}</div>;
  const g = gates.data;
  if (!g) return <p className="lede">Loading the gate results.</p>;
  if (g.rows.length === 0) {
    return (
      <p className="lede" data-testid="no-gates">
        No gate ran in this run. Turn on PoseBusters or the MMFF strain gate in this step's settings and run again to get a
        pass or fail for every pose.
      </p>
    );
  }
  const failing = g.rows.filter((r) => !r.ok).length;
  const unchecked = g.rows.filter((r) => !r.checked).length;
  // Strain was measured but nothing judged it: no limit set, so `over_strain` is null for a pose that has a strain.
  const strainUnjudged = g.rows.some(strainNotJudged);
  const allPass = failing === 0 && unchecked === 0;
  return (
    <div data-view="checks">
      <p className="lede" data-testid="checks-summary">
        {!allPass
          ? `${failing} of ${g.rows.length} poses fail.`
          : strainUnjudged
            ? `All ${g.rows.length} poses pass PoseBusters · strain not judged (no limit set).`
            : `All ${g.rows.length} poses pass.`}
        {unchecked > 0 ? ` ${unchecked} not checked: strain is recorded but no limit judges it.` : ""} Gates that ran:{" "}
        {g.ran.join(", ")}.
        {g.max_strain_kcal !== null
          ? ` Strain limit ${g.max_strain_kcal} kcal/mol.`
          : allPass && strainUnjudged
            ? ""
            : " Strain is recorded, with no limit set."}
      </p>
      <table className="grid" aria-label="Gate results">
        <thead>
          <tr>
            <th>Ligand</th>
            <th className="num">Pose</th>
            <th>Result</th>
            <th className="num">Strain (kcal/mol)</th>
            <th>Failed checks</th>
          </tr>
        </thead>
        <tbody>
          {g.rows.map((r) => (
            <tr key={`${r.candidate}-${r.pose}`} data-candidate={r.candidate} data-pose={r.pose} data-ok={r.ok}>
              <td>{r.candidate}</td>
              <td className="num">{r.pose}</td>
              <td>
                <span className="status" data-state={!r.checked ? "cancelled" : r.ok ? "succeeded" : "failed"}>
                  {!r.checked ? "not checked" : r.ok ? "pass" : "fail"}
                </span>
              </td>
              <td className="num strain" data-judged={strainNotJudged(r) ? "false" : undefined}>
                {r.strain_kcal === null ? "" : r.strain_kcal.toFixed(2)}
                {strainNotJudged(r) ? (
                  <>
                    {" "}
                    <span className="status" data-state="cancelled" data-testid="strain-not-judged">
                      not judged
                    </span>
                  </>
                ) : null}
              </td>
              <td className="failed-checks">
                {r.failed_checks.join(", ") || ""}
                {r.over_strain ? `${r.failed_checks.length ? "; " : ""}strain over the limit` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
