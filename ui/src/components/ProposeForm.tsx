import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap } from "../api/client";
import { Structure2D } from "./Structure2D";

/** A bench chemist proposes a candidate for the computational chemist to review. Proposing starts no work. */
export function ProposeForm({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const vocab = useQuery({ queryKey: ["proposal-vocab"], queryFn: () => unwrap(api.GET("/api/proposals/vocabulary")) });
  const [smiles, setSmiles] = useState("");
  const [preview, setPreview] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setPreview(smiles.trim()), 400);
    return () => window.clearTimeout(t);
  }, [smiles]);
  const propose = useMutation({
    mutationFn: () => unwrap(api.POST("/api/runs/{run_id}/proposals", { params: { path: { run_id: runId } }, body: { smiles: smiles.trim(), reason, note, candidate: "" } })),
    onSuccess: () => {
      setSmiles("");
      setNote("");
      return qc.invalidateQueries({ queryKey: ["proposals"] });
    },
  });
  return (
    <section aria-label="Propose a candidate">
      <h2>Propose a candidate</h2>
      <p className="lede">Suggest a molecule for the next round. It goes to the computational chemist's review queue; nothing runs until they choose to.</p>
      <form
        className="propose"
        onSubmit={(e) => {
          e.preventDefault();
          propose.mutate();
        }}
      >
        <div>
          <label htmlFor="propose-smiles">SMILES</label>
          <input id="propose-smiles" className="mono" value={smiles} onChange={(e) => setSmiles(e.target.value)} />
          <div className="propose-preview" data-testid="propose-preview">
            {preview ? <Structure2D smiles={preview} width={180} height={120} /> : <span className="lede">The structure appears here.</span>}
          </div>
        </div>
        <div>
          <label htmlFor="propose-reason">Reason</label>
          <select id="propose-reason" value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="">choose</option>
            {(vocab.data?.reasons ?? []).map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <label htmlFor="propose-note">Note</label>
          <textarea id="propose-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
          <p>
            <button className="primary" type="submit" disabled={propose.isPending || !smiles.trim() || !reason}>
              Propose
            </button>
          </p>
          {propose.isError ? (
            <p className="field-error" role="alert">
              {propose.error.message}
            </p>
          ) : null}
          {propose.isSuccess ? <p role="status">Proposed. It is waiting for review.</p> : null}
        </div>
      </form>
    </section>
  );
}
