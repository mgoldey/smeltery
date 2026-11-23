import { useQuery } from "@tanstack/react-query";
import { api, unwrap, type Schemas } from "../../api/client";
import { DroppedPoses } from "../../components/DroppedPoses";
import { VerdictBanner } from "../../components/VerdictBanner";
import { useRunId } from "./RunLayout";
import { droppedPosesQuery, runQuery, verdictQuery } from "../../api/queries";

export function Overview() {
  const runId = useRunId();
  const detail = useQuery(runQuery(runId));
  const verdict = useQuery(verdictQuery(runId));
  const drops = useQuery(droppedPosesQuery(runId));
  const d = detail.data;
  return (
    <div className="body">
      <main className="work">
        <h1>Provenance</h1>
        {/* The reading this record supports, beside the record itself. Provenance answered "what ran" and left
            "what it concluded" to another tab, so an archived page was a path and a digest with no result attached. */}
        <VerdictBanner runId={runId} />
        {/* A read failure the verdict does not already explain. The banner above states INCOMPLETE in the app's one
            wording for it, so repeating that here in a second wording would be two voices on one fact; what is left
            for this banner is the case the verdict cannot account for. */}
        {detail.isError && verdict.data?.state !== "INCOMPLETE" ? (
          <div className="banner" role="alert">
            This run cannot be read: {detail.error.message}
          </div>
        ) : null}
        {d ? (
          <>
            <p className="lede">
              {d.analogues.length} analogues of <strong>{d.parent}</strong>, cut at keep {d.keep} with z = {d.z}. The floor is{" "}
              <span className="num">
                {d.floor} {d.unit}
              </span>{" "}
              ({d.floor_source}).
            </p>
            <h2>Candidates</h2>
            <table className="grid">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>SMILES</th>
                  <th className="num">Poses</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(d.candidates).map(([name, smiles]) => (
                  <tr key={name}>
                    <td>
                      {name}
                      {name === d.parent ? <span className="lede"> (parent)</span> : null}
                    </td>
                    <td className="mono">{smiles}</td>
                    <td className="num">{d.n_poses[name] ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <DroppedPoses dropped={drops.data?.dropped} />
            <h2>Record</h2>
            <p className="mono">{d.path}</p>
            <p className="mono">input digest {d.digest}</p>
            <CarriedIn carried={d.decisions_in} />
            <RunConfig runId={runId} />
          </>
        ) : null}
      </main>
    </div>
  );
}

/**
 * What this round carried in from the last one.
 *
 * `results.decisions_in` has been in the record since `[decisions]` existed and nothing rendered it, so a round
 * started from a previous round's judgement looked exactly like a round started from nothing: the only trace of
 * what had been dropped was a comment inside the config text below. The digest is the point -- it is the one
 * hashed into `input_digest`, so it is what ties this run to the decisions file that shaped it.
 *
 * Three states, not two. No `[decisions]` section is "carried nothing in"; a file that rejected nobody is a file
 * that was read and excluded no one, which is a different fact and is said in those words.
 */
function CarriedIn({ carried }: { carried: Schemas["RunDetail"]["decisions_in"] }) {
  if (!carried) {
    return (
      <p className="lede" data-testid="carried-none">
        This round carried no decisions in: its config named no <span className="mono">[decisions]</span> file, so
        every candidate in it was posed.
      </p>
    );
  }
  const { file, sha256, excluded } = carried;
  return (
    <section aria-label="Carried in" data-testid="carried-in">
      <p className="lede">
        Carried in from the decisions made on the previous round: <span className="mono">{file}</span>, sha256{" "}
        <span className="mono">{sha256.slice(0, 12)}</span>. That digest is hashed into this run's input digest, so
        the two cannot drift apart.
      </p>
      {excluded.length === 0 ? (
        <p className="lede" data-testid="carried-excluded-none">
          It rejected nobody, so every candidate was posed anyway.
        </p>
      ) : (
        <p className="lede" data-testid="carried-excluded">
          Rejected before this run, so never posed or measured:{" "}
          {excluded.map((n, i) => (
            <span key={n}>
              {i ? ", " : ""}
              <span className="mono">{n}</span>
            </span>
          ))}
          .
        </p>
      )}
    </section>
  );
}

/**
 * The config this run was executed from, as text.
 *
 * Provenance named the file and its hash but never showed it, so answering "what did this actually run?" meant
 * leaving the app for a shell. The comments come with it: `config_text` preserves them on purpose, and in this
 * project they are the documentation of why a round exists.
 */
function RunConfig({ runId }: { runId: string }) {
  const cfg = useQuery({
    queryKey: ["run_config", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/config", { params: { path: { run_id: runId } } })),
  });
  const c = cfg.data;
  if (!c) return null;
  return (
    <section aria-label="Config">
      <h2>Config</h2>
      {c.text === null ? (
        <p className="lede" data-testid="config-absent">
          {c.reason}
        </p>
      ) : (
        <>
          <p className="lede">
            <span className="mono">{c.file}</span>, sha256 <span className="mono">{c.sha256?.slice(0, 12)}</span>.
            This is the file as it is on disk, comments and all.
          </p>
          {c.changed ? (
            <div className="banner" role="alert" data-testid="config-changed">
              {c.reason}: the run is reproducible only from the bytes it read, so treat this as the current file
              rather than as provenance.
            </div>
          ) : null}
          <pre className="mono config-text" data-testid="config-text">
            {c.text}
          </pre>
        </>
      )}
    </section>
  );
}
