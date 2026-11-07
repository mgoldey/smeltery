import { useQuery } from "@tanstack/react-query";
import { getJson } from "./workspace/views/PosesView";

type CalcLines = { file: string | null; pose: number; lines: Record<string, unknown>[] };

/**
 * "2 legs, 2 converged (3 SCF attempts)". /calcs has one line per remedy-ladder rung and outcome lines (an `outcome`
 * key, no `converged`); outcomes are not attempts. A leg, (leg, field model), converged if any of its rungs did.
 */
export function convergenceSummary(lines: Record<string, unknown>[]): string {
  const attempts = lines.filter((l) => !("outcome" in l));
  const legs = new Map<string, boolean>();
  for (const l of attempts) {
    const k = JSON.stringify([l.leg ?? null, l.field_model ?? null]);
    legs.set(k, (legs.get(k) ?? false) || l.converged === true);
  }
  const ok = [...legs.values()].filter(Boolean).length;
  return `${legs.size} leg${legs.size === 1 ? "" : "s"}, ${ok} converged (${attempts.length} SCF attempt${attempts.length === 1 ? "" : "s"})`;
}

/**
 * The lines of the SCF log for one pose of one ligand (`/calcs`, as written). `position` is the record's place in the
 * candidate's poses, as `/scene` takes it; the log is read at the original pose index the API maps it to.
 */
export function ScfLog({
  idx,
  candidate,
  position,
  summary = false,
}: {
  idx: number;
  candidate: string;
  position: number;
  summary?: boolean;
}) {
  const log = useQuery({
    queryKey: ["calcs", idx, candidate, position],
    queryFn: () => getJson<CalcLines>(`/api/runs/${idx}/calcs?candidate=${encodeURIComponent(candidate)}&pose=${position}`),
  });
  if (log.isError) return <div className="banner" role="alert">{log.error.message}</div>;
  if (!log.data) return <p className="lede">Loading the SCF log.</p>;
  return (
    <div data-testid="scf-log">
      {log.data.lines.length === 0 ? (
        <p className="lede">The SCF log ({log.data.file}) has no line for {candidate} pose {log.data.pose}.</p>
      ) : (
        <>
          {summary ? <p className="lede" data-testid="scf-summary">{convergenceSummary(log.data.lines)}</p> : null}
          <pre>{log.data.lines.map((l) => JSON.stringify(l)).join("\n")}</pre>
        </>
      )}
    </div>
  );
}
