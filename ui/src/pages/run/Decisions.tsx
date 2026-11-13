import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap, type Schemas } from "../../api/client";
import { Structure2D } from "../../components/Structure2D";
import { EmptyState } from "../../components/EmptyState";
import { Writable } from "../../components/ReadOnly";
import { useStartNextRound } from "../../components/workspace/openFromRun";
import { useRunId } from "./RunLayout";

type Decision = Schemas["DecisionOut"];
type DdeRow = Schemas["DdeTable"]["rows"][number];

function Row({
  runId,
  name,
  smiles,
  current,
  vocab,
  group,
  evidence,
  unit,
  selected,
  onSelect,
}: {
  runId: string;
  name: string;
  smiles: string;
  current: Decision | undefined;
  vocab: Schemas["Vocabulary"];
  group: string;
  evidence: DdeRow | undefined;
  unit: string;
  selected: boolean;
  onSelect: (on: boolean) => void;
}) {
  const qc = useQueryClient();
  const [status, setStatus] = useState(current?.status ?? "");
  const [reason, setReason] = useState(current?.reason ?? "");
  const [note, setNote] = useState(current?.note ?? "");
  const save = useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs/{run_id}/decisions", { params: { path: { run_id: runId } }, body: { candidate: name, status, reason, note } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["decisions", runId] }),
  });
  const needsNote = vocab.note_required.includes(reason);
  const id = (k: string) => `${k}-${name}`;
  return (
    <tr data-candidate={name}>
      <td>
        <input type="checkbox" checked={selected} onChange={(e) => onSelect(e.target.checked)} aria-label={`Select ${name}`} />
      </td>
      <td>
        <Structure2D smiles={smiles} />
      </td>
      <td>
        <div>{name}</div>
        {/* The number the decision is about. Without it the user reads a verdict on Results, comes here, and is
            asked to judge a bare name -- so they tab back and forth, or decide on the name alone. */}
        {evidence ? (
          <div className="lede" data-testid={`evidence-${name}`}>
            {evidence.mean >= 0 ? "+" : ""}
            {evidence.mean.toFixed(2)} ± {evidence.sem.toFixed(2)} {unit} ·{" "}
            {/* Three states, as on Results: an unranked run has no survivors, and calling those "not kept"
                would state a cut outcome the run never reached. */}
            {evidence.survivor === null ? "not ranked" : evidence.survivor ? "kept by the cut" : "not kept"}
          </div>
        ) : null}
        <div className="lede">{group}</div>
        {current ? (
          <div className="lede" data-testid={`recorded-${name}`}>
            Recorded: {current.status}, {current.reason} by {current.user}
          </div>
        ) : null}
      </td>
      <td>
        <label htmlFor={id("status")}>Status</label>
        <select id={id("status")} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">choose</option>
          {vocab.statuses.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </td>
      <td>
        <label htmlFor={id("reason")}>Reason</label>
        <select id={id("reason")} value={reason} onChange={(e) => setReason(e.target.value)}>
          <option value="">choose</option>
          {vocab.reasons.map((r) => (
            <option key={r} value={r}>
              {r.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </td>
      <td>
        <label htmlFor={id("note")}>Note{needsNote ? " (required for this reason)" : ""}</label>
        <input id={id("note")} value={note} onChange={(e) => setNote(e.target.value)} />
      </td>
      <td>
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending || !status || !reason} aria-label={`Record decision for ${name}`}>
          Record
        </button>
        {save.isError ? (
          <div className="field-error" role="alert" data-testid={`refusal-${name}`}>
            {save.error.message}
          </div>
        ) : null}
        {save.isSuccess ? <div className="lede">Recorded</div> : null}
      </td>
    </tr>
  );
}

function BulkBar({ runId, selected, vocab, onDone }: { runId: string; selected: string[]; vocab: Schemas["Vocabulary"]; onDone: () => void }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const apply = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/runs/{run_id}/decisions/bulk", { params: { path: { run_id: runId } }, body: { candidates: selected, status, reason, note } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["decisions", runId] });
      onDone();
    },
  });
  const needsNote = vocab.note_required.includes(reason);
  return (
    <div className="bulk-bar" role="group" aria-label="Bulk decision">
      <span>{selected.length} selected</span>
      <label htmlFor="bulk-status">Status</label>
      <select id="bulk-status" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">choose</option>
        {vocab.statuses.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      <label htmlFor="bulk-reason">Reason</label>
      <select id="bulk-reason" value={reason} onChange={(e) => setReason(e.target.value)}>
        <option value="">choose</option>
        {vocab.reasons.map((r) => (
          <option key={r} value={r}>
            {r.replaceAll("_", " ")}
          </option>
        ))}
      </select>
      <label htmlFor="bulk-note">Note{needsNote ? " (required for this reason)" : ""}</label>
      <input id="bulk-note" value={note} onChange={(e) => setNote(e.target.value)} />
      <button type="button" onClick={() => apply.mutate()} disabled={apply.isPending || !selected.length || !status || !reason}>
        Apply to selected
      </button>
      {apply.isError ? (
        <div className="field-error" role="alert">
          {apply.error.message}
        </div>
      ) : null}
    </div>
  );
}

export function Decisions() {
  const runId = useRunId();
  const detail = useQuery({ queryKey: ["run", runId], queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } })) });
  const vocab = useQuery({ queryKey: ["vocab"], queryFn: () => unwrap(api.GET("/api/decisions/vocabulary")) });
  const decisions = useQuery({
    queryKey: ["decisions", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/decisions", { params: { path: { run_id: runId } } })),
  });
  // The same table Results draws its forest plot from, so the row and the plot cannot disagree.
  const dde = useQuery({
    queryKey: ["ddE", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/ddE", { params: { path: { run_id: runId } } })),
  });
  const byCandidate = new Map((dde.data?.rows ?? []).map((r) => [r.candidate, r]));
  const d = detail.data;
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => setPicked([]), [runId]);
  const toggle = (n: string, on: boolean) => setPicked((p) => (on ? [...p.filter((x) => x !== n), n] : p.filter((x) => x !== n)));
  const groupOf = new Map<string, string>();
  d?.groups.forEach((g, i) => g.forEach((n) => groupOf.set(n, g.length > 1 ? `tie group ${i + 1} (no order)` : `group ${i + 1}`)));
  return (
    <div className="body">
      <main className="work">
        <h1>Decisions</h1>
        <p className="lede">
          Keep, reject or hold each candidate, with a reason. Choosing inside a tie group is a judgement, not a ranking, so
          it needs a note saying why. Decisions are kept apart from the run record and carried into the next run.
        </p>
        {detail.isError ? (
          <div className="banner" role="alert">
            {detail.error.message}
          </div>
        ) : null}
        <p>
          <a href={`/api/runs/${runId}/decisions.csv`} download>
            Download CSV
          </a>{" "}
          <a href={`/api/runs/${runId}/decisions.sdf`} download>
            Download SDF
          </a>{" "}
          <a href={`/api/runs/${runId}/export.sdf`} download title="3D poses with ddE, tie group, gates and decisions as properties">
            Download enriched pose SDF
          </a>
        </p>
        {d && d.analogues.length === 0 ? (
          <EmptyState action={{ label: "Open the run provenance", to: `/run/${runId}/provenance` }}>
            This run holds only the parent; there are no analogues to decide on.
          </EmptyState>
        ) : null}
        {d && vocab.data && decisions.data && picked.length ? (
          <BulkBar runId={runId} selected={picked} vocab={vocab.data} onDone={() => setPicked([])} />
        ) : null}
        {d && d.analogues.length > 0 && vocab.data && decisions.data ? (
          <table className="grid" aria-label="Decisions">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    checked={d.analogues.length > 0 && picked.length === d.analogues.length}
                    onChange={(e) => setPicked(e.target.checked ? [...d.analogues] : [])}
                    aria-label="Select all"
                  />
                </th>
                <th>Structure</th>
                <th>Candidate</th>
                <th>Status</th>
                <th>Reason</th>
                <th>Note</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {d.analogues.map((n) => (
                <Row
                  key={`${n}-${decisions.data.find((x) => x.candidate === n)?.time ?? ""}`}
                  runId={runId}
                  name={n}
                  smiles={d.candidates[n] ?? ""}
                  current={decisions.data.find((x) => x.candidate === n)}
                  vocab={vocab.data}
                  group={groupOf.get(n) ?? ""}
                  evidence={byCandidate.get(n)}
                  unit={d.unit}
                  selected={picked.includes(n)}
                  onSelect={(on) => toggle(n, on)}
                />
              ))}
            </tbody>
          </table>
        ) : null}
        {decisions.data?.length ? <NextRound runId={runId} recorded={decisions.data.length} /> : null}
      </main>
    </div>
  );
}

/**
 * The exit this page never had.
 *
 * The lede above has always promised decisions are "carried into the next run", and until now the only way to do that
 * was to leave Bench and hand-edit `[decisions] file = "..."` into a TOML. This makes the promise true: the server
 * copies the decisions file into a new pipeline, writes the reference, and says in `notes` what it carried.
 *
 * Shown only once something is recorded -- an empty next round is refused by the server, and offering a button that
 * can only fail is worse than offering none.
 */
function NextRound({ runId, recorded }: { runId: string; recorded: number }) {
  const start = useStartNextRound(runId);
  return (
    <section className="next-round">
      <h2>Carry these forward</h2>
      <p className="lede">
        Starts a pipeline from this run's config with {recorded === 1 ? "this decision" : `these ${recorded} decisions`}{" "}
        referenced, so <span className="mono">smeltery run</span> drops what you rejected. Opens on its candidates; the
        original run is untouched.
      </p>
      {start.isError ? (
        <p className="field-error" role="alert">
          {start.error.message}
        </p>
      ) : null}
      <Writable>
        <button
          className="primary"
          type="button"
          disabled={start.isPending}
          onClick={() => start.mutate({})}
          data-testid="start-next-round"
        >
          {start.isPending ? "Starting the next round…" : "Start the next round"}
        </button>
      </Writable>
    </section>
  );
}
