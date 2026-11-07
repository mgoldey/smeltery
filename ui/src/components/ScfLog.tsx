import { useQuery } from "@tanstack/react-query";
import { getJson } from "./workspace/views/PosesView";

type CalcLines = { file: string | null; lines: Record<string, unknown>[] };

/** "3 SCF lines, 2 converged": the lines that carry a boolean `converged` are the SCF legs. */
export function convergenceSummary(lines: Record<string, unknown>[]): string {
  const legs = lines.filter((l) => typeof l.converged === "boolean");
  const ok = legs.filter((l) => l.converged === true).length;
  return `${lines.length} SCF line${lines.length === 1 ? "" : "s"}, ${ok} of ${legs.length} legs converged`;
}

/**
 * The lines of the SCF log for one pose of one ligand (`/calcs`, as written). `position` is the record's place in the
 * candidate's poses, as `/scene` takes it; `pose` is only the number the empty-log message names.
 */
export function ScfLog({
  idx,
  candidate,
  position,
  pose,
  summary = false,
}: {
  idx: number;
  candidate: string;
  position: number;
  pose: number;
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
        <p className="lede">The SCF log ({log.data.file}) has no line for {candidate} pose {pose}.</p>
      ) : (
        <>
          {summary ? <p className="lede" data-testid="scf-summary">{convergenceSummary(log.data.lines)}</p> : null}
          <pre>{log.data.lines.map((l) => JSON.stringify(l)).join("\n")}</pre>
        </>
      )}
    </div>
  );
}
