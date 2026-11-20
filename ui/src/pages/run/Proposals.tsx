import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useCanWrite } from "../../api/capabilities";
import { CommentThread } from "../../components/CommentThread";
import { EmptyState } from "../../components/EmptyState";
import { Writable } from "../../components/ReadOnly";
import { Structure2D } from "../../components/Structure2D";
import { useStartNextRound } from "../../components/workspace/openFromRun";
import { useRunId } from "./RunLayout";
import { day } from "../../components/time";
import { proposalsQuery, useRunDigest } from "../../api/queries";

/** The computational chemist's review queue: what bench chemists proposed after reading this run's report. */
export function Proposals() {
  const runId = useRunId();
  // Not Writable: the tick boxes are not a control of their own, they are the argument to the one button below,
  // and that button is hidden on a read-only server. A box the reader can tick that can never be submitted is an
  // instruction to do something impossible, so on a read-only Bench the queue is a table and nothing more.
  const canWrite = useCanWrite();
  const { digest, runs } = useRunDigest(runId);
  const proposals = useQuery({ ...proposalsQuery(digest), enabled: digest !== undefined });
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
          {canWrite ? (
            <>
              Candidates proposed from this run's report. Tick the ones you agree with and start the next round: they
              go into the new pipeline's <span className="mono">[candidates]</span> beside this run's, which is what
              records that you took them.
            </>
          ) : (
            // The queue is readable on a read-only Bench by design: a published Bench exists to be read, and what
            // colleagues proposed is part of the record. What it must not do is describe the button it is hiding.
            <>
              Candidates proposed from this run's report, with who proposed each one and why. This Bench is
              read-only, so none of them can be taken into a next round from here.
            </>
          )}
        </p>
        {/* "Nothing proposed yet" and "the queue could not be read" are different facts, and the EmptyState below
            states the first one. A failure must not borrow it. */}
        {runs.isError ? (
          <div className="banner" role="alert">
            The run listing could not be read: {runs.error.message}. Without it this run's proposals cannot be
            looked up; reload to try again.
          </div>
        ) : null}
        {proposals.isError ? (
          <div className="banner" role="alert">
            The review queue could not be read: {proposals.error.message}. Whether anything has been proposed from
            this run is unknown; reload to try again.
          </div>
        ) : null}
        {proposals.data && proposals.data.length === 0 ? (
          // The report is where proposing happens, so it is the place to send someone who can propose. On a
          // read-only Bench that form is hidden there too, and sending the reader to look for it would waste the
          // trip; the sentence stops at the fact instead.
          <EmptyState action={canWrite ? { label: "Open the report", to: `/report/${runId}` } : undefined}>
            Nothing has been proposed from this run yet. Bench chemists propose candidates after reading its report.
          </EmptyState>
        ) : null}
        {proposals.data && proposals.data.length ? (
          <>
            <table className="grid" aria-label="Review queue">
              <thead>
                <tr>
                  {canWrite ? (
                    <th>
                      <input
                        type="checkbox"
                        checked={picked.length === proposals.data.length}
                        onChange={(e) => setPicked(e.target.checked ? proposals.data.map((p) => p.id) : [])}
                        aria-label="Select all proposals"
                      />
                    </th>
                  ) : null}
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
                    {canWrite ? (
                      <td>
                        <input
                          type="checkbox"
                          checked={picked.includes(p.id)}
                          onChange={(e) => toggle(p.id, e.target.checked)}
                          aria-label={`Take ${p.candidate || p.smiles} into the next round`}
                        />
                      </td>
                    ) : null}
                    <td>
                      <Structure2D smiles={p.smiles} width={120} height={80} />
                    </td>
                    <td className="mono">{p.smiles}</td>
                    <td>{p.reason}</td>
                    <td>{p.note}</td>
                    <td>{p.author}</td>
                    {/* Not p.status: proposals have no lifecycle (the store only adds and lists), so it reads
                        "pending" forever. A date is a fact; a status that never moves is a broken promise. */}
                    <td>{p.created_at ? day(p.created_at) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Writable>
              <TakeIntoNextRound runId={runId} picked={picked} />
            </Writable>
          </>
        ) : null}
        {/* The other half of what a colleague sent back. Comments are made on the Report page and were readable
            only there, so a chemist working through the run's tabs never saw them -- though the comment store is
            keyed by the run, not the report, so the run area could always have asked for them. Proposals and
            comments are the same conversation, which is why they share a page and the tab's count. */}
        <section aria-label="Discussion">
          <h2>Discussion</h2>
          <p className="lede">
            Comments left on this run's report, on the run as a whole. Comments on a single candidate or pose stay
            beside what they are about: <Link to={`/report/${runId}`}>open the report</Link> to read those.
          </p>
          <CommentThread runId={runId} anchor="run" title="this run" />
        </section>
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
 *
 * The caller wraps the whole section in `Writable`, not just the button: its heading and its "tick a proposal to
 * add it" instruction describe the button, so on a read-only server they would outlive what they describe.
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
      <button
        type="button"
        disabled={!n || start.isPending}
        className="primary"
        onClick={() => start.mutate({ addProposals: picked })}
        data-testid="take-proposals"
      >
        {start.isPending ? "Starting the next round…" : "Start the next round"}
      </button>
    </section>
  );
}
