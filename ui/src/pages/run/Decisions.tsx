import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../../api/client";
import { Structure2D } from "../../components/Structure2D";
import { useRunIdx } from "./RunLayout";

type Decision = Schemas["DecisionOut"];

function Row({
  idx,
  name,
  smiles,
  current,
  vocab,
  group,
}: {
  idx: number;
  name: string;
  smiles: string;
  current: Decision | undefined;
  vocab: Schemas["Vocabulary"];
  group: string;
}) {
  const qc = useQueryClient();
  const [status, setStatus] = useState(current?.status ?? "");
  const [reason, setReason] = useState(current?.reason ?? "");
  const [note, setNote] = useState(current?.note ?? "");
  const save = useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs/{idx}/decisions", { params: { path: { idx } }, body: { candidate: name, status, reason, note } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["decisions", idx] }),
  });
  const needsNote = vocab.note_required.includes(reason);
  const id = (k: string) => `${k}-${name}`;
  return (
    <tr data-candidate={name}>
      <td>
        <Structure2D smiles={smiles} />
      </td>
      <td>
        <div>{name}</div>
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

export function Decisions() {
  const idx = useRunIdx();
  const detail = useQuery({ queryKey: ["run", idx], queryFn: () => unwrap(api.GET("/api/runs/{idx}", { params: { path: { idx } } })) });
  const vocab = useQuery({ queryKey: ["vocab"], queryFn: () => unwrap(api.GET("/api/decisions/vocabulary")) });
  const decisions = useQuery({
    queryKey: ["decisions", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/decisions", { params: { path: { idx } } })),
  });
  const d = detail.data;
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
        {d && vocab.data && decisions.data ? (
          <table className="grid" aria-label="Decisions">
            <thead>
              <tr>
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
                  idx={idx}
                  name={n}
                  smiles={d.candidates[n] ?? ""}
                  current={decisions.data.find((x) => x.candidate === n)}
                  vocab={vocab.data}
                  group={groupOf.get(n) ?? ""}
                />
              ))}
            </tbody>
          </table>
        ) : null}
      </main>
    </div>
  );
}
