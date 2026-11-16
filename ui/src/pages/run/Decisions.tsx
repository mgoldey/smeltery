import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
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
  tie,
  evidence,
  unit,
  selected,
  onSelect,
  onEdited,
}: {
  runId: string;
  name: string;
  smiles: string;
  current: Decision | undefined;
  vocab: Schemas["Vocabulary"];
  group: string;
  /** The other members of this candidate's tie group, or null when it is alone in its group. */
  tie: string[] | null;
  evidence: DdeRow | undefined;
  unit: string;
  selected: boolean;
  onSelect: (on: boolean) => void;
  onEdited: (edited: boolean) => void;
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
  // The core refuses these reasons without a note (decisions.py, `.strip()`), so a blank one here can only come back
  // as a red server error. Hold the button and say which reason is asking, rather than offering a doomed click.
  const missingNote = needsNote && !note.trim();
  // Typed into but not sent. The page used to offer the next round the moment ONE decision was recorded, with no sign
  // that four other rows had been filled in and left; this is what the exit below counts as being about to lose. A
  // recorded row refetches and remounts (the key carries `current.time`), so a saved row's draft is never "edited".
  const edited = status !== (current?.status ?? "") || reason !== (current?.reason ?? "") || note !== (current?.note ?? "");
  useEffect(() => {
    onEdited(edited);
    return () => onEdited(false);
  }, [edited, onEdited]);
  // A tie group is the one place the page's own lede promised more than the server enforces: the server requires a
  // note for the REASON `within_tie_choice`, never for the position. Keeping one member as `cut_survivor` with no
  // note is accepted, and reads as "the cut chose this", which inside a tie group is exactly what did not happen.
  // So say what the row is and point at the reason that is honest about it -- without refusing the others, because a
  // liability or a synthesis problem is a legitimate reason to break a tie and has its own name here.
  //
  // Named from the served vocabulary (`smeltery.decisions.REASONS`), so a renamed reason drops this guidance rather
  // than pointing at a dead option.
  const tieReason = vocab.reasons.includes("within_tie_choice") ? "within_tie_choice" : null;
  // Only `keep` can misread: keeping a tie member for `cut_survivor` says the cut chose it, which inside a tie group
  // is what did not happen. Rejecting one for a liability claims nothing about the cut, and a reason that already
  // requires a note (`other`) is asking for the explanation anyway.
  const tieMisread = tie !== null && tieReason !== null && status === "keep" && reason !== "" && reason !== tieReason && !needsNote;
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
        {/* The group, and -- when it is a tie -- who it is tied WITH. A bare "tie group 2 (no order)" told the user
            a fact about the cut; it did not tell them that choosing this row over those names is the judgement the
            lede has always said needs a note. */}
        <div className="lede" data-testid={`group-${name}`}>
          {group}
          {tie !== null ? (
            <>
              {" · tied with "}
              {tie.join(", ")}
              {": the statistics do not separate them, so keeping this one over those is a judgement."}
            </>
          ) : null}
        </div>
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
              {tie !== null && r === tieReason ? " — the one for a tie" : ""}
            </option>
          ))}
        </select>
        {/* Guidance, not a rule: the server takes `cut_survivor` here and will go on taking it, and a liability or a
            synthesis problem really can break a tie. What it must not do is pass silently as though the cut had
            chosen, so the honest reason is named and offered, and the click that records it stays available. */}
        {tieMisread ? (
          <div className="lede" data-testid={`tie-reason-${name}`}>
            Inside a tie group the cut kept nobody, so {reason.replaceAll("_", " ")} is a reason of your own.{" "}
            <button type="button" className="link" onClick={() => setReason(tieReason ?? "")}>
              Call it a tie choice
            </button>{" "}
            to record why, or record this reason as it stands.
          </div>
        ) : null}
      </td>
      <td>
        <label htmlFor={id("note")}>Note{needsNote ? " (required for this reason)" : ""}</label>
        <input id={id("note")} value={note} onChange={(e) => setNote(e.target.value)} />
      </td>
      <td>
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending || !status || !reason || missingNote}
          aria-label={`Record decision for ${name}`}
        >
          Record
        </button>
        {missingNote ? (
          <div className="lede" data-testid={`needs-note-${name}`}>
            {reason.replaceAll("_", " ")} needs a note saying why, so Record waits for one.
          </div>
        ) : null}
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
  // A bulk apply is all-or-nothing in the core, so one blank note refuses every selected candidate -- not one of
  // them. Hold the button and say how many the blank note would cost.
  const missingNote = needsNote && !note.trim();
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
      <button type="button" onClick={() => apply.mutate()} disabled={apply.isPending || !selected.length || !status || !reason || missingNote}>
        Apply to selected
      </button>
      {missingNote ? (
        <span className="lede" data-testid="bulk-needs-note">
          {reason.replaceAll("_", " ")} needs a note saying why, and one apply is all or nothing, so a blank note would
          refuse all {selected.length}. Apply waits for a note.
        </span>
      ) : null}
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
  const tieOf = new Map<string, string[]>();
  d?.groups.forEach((g, i) => {
    g.forEach((n) => {
      groupOf.set(n, g.length > 1 ? `tie group ${i + 1} (no order)` : `group ${i + 1}`);
      // The row's own view of its tie: the OTHER members, which is what makes keeping this one a judgement. A
      // single-member group is not a tie and gets nothing.
      if (g.length > 1) tieOf.set(n, g.filter((m) => m !== n));
    });
  });
  // Rows typed into and left unrecorded. A decision reaches the next round only by being recorded, so this is what
  // the exit below is about to leave behind. One stable callback per candidate: an unstable one would re-fire the
  // row's effect on every render of this page.
  const [edited, setEdited] = useState<Record<string, boolean>>({});
  useEffect(() => setEdited({}), [runId]);
  const onEdited = useRef(new Map<string, (on: boolean) => void>()).current;
  const editedCb = (n: string) => {
    let cb = onEdited.get(n);
    if (!cb) {
      cb = (on: boolean) => setEdited((e) => (!!e[n] === on ? e : { ...e, [n]: on }));
      onEdited.set(n, cb);
    }
    return cb;
  };
  const unrecorded = (d?.analogues ?? []).filter((n) => edited[n]);
  const recorded = new Set((decisions.data ?? []).map((x) => x.candidate));
  const decidedAnalogues = (d?.analogues ?? []).filter((n) => recorded.has(n));
  return (
    <div className="body">
      <main className="work">
        <h1>Decisions</h1>
        {/* The old wording said a tie choice "needs a note saying why", which is not the rule: the note is required
            for the REASON `within_tie_choice` (or `other`), never for the position in a group. Rows in a tie say so
            themselves now, so this says what the rule actually is. */}
        <p className="lede">
          Keep, reject or hold each candidate, with a reason. Choosing inside a tie group is a judgement, not a ranking:
          each such row says who it is tied with, and the reason that names it asks for a note. Decisions are kept apart
          from the run record and carried into the next run.
        </p>
        {detail.isError ? (
          <div className="banner" role="alert">
            {detail.error.message}
          </div>
        ) : null}
        {/* How far through this is. Nothing on the page said how many analogues there were against how many had been
            judged, so a half-finished page and a finished one looked the same -- and the exit appeared on the first
            recorded row either way. Said only when both reads are in: "0 of N" because a fetch broke would be worse
            than saying nothing. */}
        {d && d.analogues.length > 0 && decisions.data ? (
          <p className="lede" data-testid="decided-progress">
            <strong>
              {decidedAnalogues.length} of {d.analogues.length}
            </strong>{" "}
            {d.analogues.length === 1 ? "analogue has" : "analogues have"} a recorded decision
            {unrecorded.length ? (
              <>
                , and {unrecorded.length === 1 ? "one row is" : `${unrecorded.length} rows are`} filled in but not
                recorded: <span className="mono">{unrecorded.join(", ")}</span>. A decision reaches the next round
                only once Record has been pressed.
              </>
            ) : (
              "."
            )}
          </p>
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
                  tie={tieOf.get(n) ?? null}
                  evidence={byCandidate.get(n)}
                  unit={d.unit}
                  selected={picked.includes(n)}
                  onSelect={(on) => toggle(n, on)}
                  onEdited={editedCb(n)}
                />
              ))}
            </tbody>
          </table>
        ) : null}
        {decisions.data?.length ? (
          <NextRound
            runId={runId}
            recorded={decisions.data.length}
            analogues={d?.analogues.length ?? 0}
            decided={decidedAnalogues.length}
            unrecorded={unrecorded}
          />
        ) : null}
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
 *
 * It appears on the FIRST recorded decision, which is correct (one judgement is a legitimate round) and was also the
 * defect: it said "these N decisions" and nothing else, so a user who had judged three of twelve analogues, and had
 * four more rows typed in and unsent, read it as carrying their work forward. A decision cannot be cleared -- there
 * is no DELETE anywhere in this API, by design, because the file is an auditable record -- so the remedy is not an
 * undo but saying exactly what the click takes: the recorded rows, and only those.
 */
function NextRound({
  runId,
  recorded,
  analogues,
  decided,
  unrecorded,
}: {
  runId: string;
  recorded: number;
  analogues: number;
  decided: number;
  unrecorded: string[];
}) {
  const start = useStartNextRound(runId);
  const partial = decided < analogues;
  return (
    <section className="next-round">
      <h2>Carry these forward</h2>
      <p className="lede">
        Starts a pipeline from this run's config with {recorded === 1 ? "this decision" : `these ${recorded} decisions`}{" "}
        referenced, so <span className="mono">smeltery run</span> drops what you rejected. Opens on its candidates; the
        original run is untouched.
      </p>
      {/* What the click carries, against what is on this page. Two different gaps, said separately: an undecided row
          is a choice (the next round simply poses it again), an unrecorded draft is work about to be lost. */}
      <p className="lede" data-testid="next-round-carries">
        It carries the {decided === 1 ? "one analogue" : `${decided} analogues`} with a recorded decision.{" "}
        {partial ? (
          <>
            The other {analogues - decided === 1 ? "one is" : `${analogues - decided} are`} undecided and will be posed
            again unchanged.
          </>
        ) : (
          <>Every analogue on this run has one.</>
        )}
      </p>
      {unrecorded.length ? (
        <p className="banner note" role="status" data-testid="next-round-unrecorded">
          {unrecorded.length === 1 ? "One row is" : `${unrecorded.length} rows are`} filled in and not recorded:{" "}
          <span className="mono">{unrecorded.join(", ")}</span>. Starting the next round now leaves{" "}
          {unrecorded.length === 1 ? "that one" : "those"} out, and this page's drafts do not survive it. Press Record
          on {unrecorded.length === 1 ? "it" : "each"} first to carry {unrecorded.length === 1 ? "it" : "them"} too.
        </p>
      ) : null}
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
