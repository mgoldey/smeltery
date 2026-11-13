import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, runByRef, unwrap } from "../../api/client";
import { EmptyState } from "../../components/EmptyState";
import { Writable } from "../../components/ReadOnly";
import { Structure2D } from "../../components/Structure2D";
import { useStartNextRound } from "../../components/workspace/openFromRun";
import { useRunId } from "./RunLayout";

/** The computational chemist's review queue: what bench chemists proposed after reading this run's report. */
export function Proposals() {
  const runId = useRunId();
  const verdict = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
  const digest = runByRef(verdict.data, runId)?.digest ?? undefined;
  const proposals = useQuery({
    queryKey: ["proposals", digest],
    queryFn: () => unwrap(api.GET("/api/proposals", { params: { query: { run: digest ?? "" } } })),
    enabled: digest !== undefined,
  });
  // Unchecked by default: taking a molecule into a round is a judgement, and a pre-ticked box would make it by
  // default for anyone who only came to read the queue.
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => setPicked([]), [runId]);
  const toggle = (id: string, on: boolean) =>
    setPicked((p) => (on ? [...p.filter((x) => x !== id), id] : p.filter((x) => x !== id)));
  return (
    <div className="body">
      <main className="work">
        <h1>Proposals</h1>
        <p className="lede">
          Candidates proposed from this run's report. Tick the ones you agree with and start the next round: they go
          into the new pipeline's <span className="mono">[candidates]</span> beside this run's, which is what records
          that you took them.
        </p>
        {proposals.data && proposals.data.length === 0 ? (
          <EmptyState action={{ label: "Open the report", to: `/report/${runId}` }}>
            Nothing has been proposed from this run yet. Bench chemists propose candidates after reading its report.
          </EmptyState>
        ) : null}
        {proposals.data && proposals.data.length ? (
          <>
            <table className="grid" aria-label="Review queue">
              <thead>
                <tr>
                  <th>
                    <input
                      type="checkbox"
                      checked={picked.length === proposals.data.length}
                      onChange={(e) => setPicked(e.target.checked ? proposals.data.map((p) => p.id) : [])}
                      aria-label="Select all proposals"
                    />
                  </th>
                  <th>Structure</th>
                  <th>SMILES</th>
                  <th>Reason</th>
                  <th>Note</th>
                  <th>From</th>
                  <th>Proposed</th>
                </tr>
              </thead>
              <tbody>
                {proposals.data.map((p) => (
                  <tr key={p.id} data-proposal={p.smiles}>
                    <td>
                      <input
                        type="checkbox"
                        checked={picked.includes(p.id)}
                        onChange={(e) => toggle(p.id, e.target.checked)}
                        aria-label={`Take ${p.candidate || p.smiles} into the next round`}
                      />
                    </td>
                    <td>
                      <Structure2D smiles={p.smiles} width={120} height={80} />
                    </td>
                    <td className="mono">{p.smiles}</td>
                    <td>{p.reason}</td>
                    <td>{p.note}</td>
                    <td>{p.author}</td>
                    {/* Not p.status: proposals have no lifecycle (the store only adds and lists), so it reads
                        "pending" forever. A date is a fact; a status that never moves is a broken promise. */}
                    <td>{p.created_at ? new Date(p.created_at).toLocaleDateString() : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <TakeIntoNextRound runId={runId} picked={picked} />
          </>
        ) : null}
      </main>
    </div>
  );
}

/**
 * The verb this tab was named for and never had.
 *
 * The lede used to end "Add one to the next pipeline's candidates if you agree", describing a control that did not
 * exist: the only way to act on a proposal was to copy its SMILES, leave Bench, and hand-edit a TOML. The server
 * has taken `add_proposals` since the loop-closer went in; this is the button for it.
 */
function TakeIntoNextRound({ runId, picked }: { runId: string; picked: string[] }) {
  const start = useStartNextRound(runId);
  const n = picked.length;
  return (
    <section className="next-round">
      <h2>Take these into the next round</h2>
      <p className="lede">
        {n === 0
          ? "Tick a proposal to add it. The round also carries this run's decisions, so anything you rejected is dropped before posing."
          : `Starts a pipeline from this run's config with ${n === 1 ? "this proposal" : `these ${n} proposals`} added to its candidates, and this run's decisions carried. The original run is untouched.`}
      </p>
      {start.isError ? (
        <p className="field-error" role="alert">
          {start.error.message}
        </p>
      ) : null}
      <Writable>
        <button
          type="button"
          disabled={!n || start.isPending}
          className="primary"
          onClick={() => start.mutate({ addProposals: picked })}
          data-testid="take-proposals"
        >
          {start.isPending ? "Starting the next round…" : "Start the next round"}
        </button>
      </Writable>
    </section>
  );
}
