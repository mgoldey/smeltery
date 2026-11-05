import { useQuery } from "@tanstack/react-query";
import type { StepViewProps } from "./registry";
import { getJson, NotRun } from "./PosesView";

type GateRow = {
  candidate: string;
  pose: number;
  posebusters: boolean | null;
  failed_checks: string[];
  strain_kcal: number | null;
  over_strain: boolean | null;
  ok: boolean;
};
type Gates = { ran: string[]; max_strain_kcal: number | null; rows: GateRow[] };

/** Step 4: the gates that ran before any SCF, one row per pose, with what failed. */
export function ChecksView({ sourceRun }: StepViewProps) {
  const gates = useQuery({
    queryKey: ["gates", sourceRun],
    queryFn: () => getJson<Gates>(`/api/runs/${sourceRun}/gates`),
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
  return (
    <div data-view="checks">
      <p className="lede" data-testid="checks-summary">
        {failing === 0 ? `All ${g.rows.length} poses pass.` : `${failing} of ${g.rows.length} poses fail.`} Gates that ran:{" "}
        {g.ran.join(", ")}.
        {g.max_strain_kcal === null ? " Strain is recorded, with no limit set." : ` Strain limit ${g.max_strain_kcal} kcal/mol.`}
      </p>
      <table className="grid" aria-label="Gate results">
        <thead>
          <tr>
            <th>Ligand</th>
            <th className="num">Pose</th>
            <th>Result</th>
            <th>Failed checks</th>
            <th className="num">Strain (kcal/mol)</th>
          </tr>
        </thead>
        <tbody>
          {g.rows.map((r) => (
            <tr key={`${r.candidate}-${r.pose}`} data-candidate={r.candidate} data-pose={r.pose} data-ok={r.ok}>
              <td>{r.candidate}</td>
              <td className="num">{r.pose}</td>
              <td>
                <span className="status" data-state={r.ok ? "succeeded" : "failed"}>
                  {r.ok ? "pass" : "fail"}
                </span>
              </td>
              <td>
                {r.failed_checks.join(", ") || ""}
                {r.over_strain ? `${r.failed_checks.length ? "; " : ""}strain over the limit` : ""}
              </td>
              <td className="num">{r.strain_kcal === null ? "" : r.strain_kcal.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
