import type { Schemas } from "../../api/client";

type Impact = Schemas["Impact"];

function fmtSeconds(s: number): string {
  if (s < 90) return `${s.toFixed(0)} s`;
  if (s < 5400) return `${(s / 60).toFixed(1)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

const POSES_FROM: Record<Impact["poses_from"], string> = {
  computed: "from poses placed now (no docking, no SCF)",
  "source run": "from the source run's poses: nothing upstream of them changed",
  none: "cannot say until poses exist",
};

function Memory({ m }: { m: Impact["memory"] }) {
  const est = m.estimate as { total_gb?: number; budget_gb?: number } | null;
  const free = m.free_gb === null ? null : `${m.free_gb.toFixed(1)} GB free`;
  const need = m.require_free_gb === null ? null : `requires ${m.require_free_gb} GB free`;
  const facts = [free, need].filter(Boolean).join(", ");
  if (m.verdict === "fits" || m.verdict === "does not fit") {
    return (
      <span data-testid="impact-memory" className={m.verdict === "fits" ? undefined : "bad"}>
        Memory <b>{est?.total_gb?.toFixed(2)} GB</b> per calculation / {est?.budget_gb} GB budget: {m.verdict}
        {facts ? <span className="basis"> ({facts})</span> : null}
      </span>
    );
  }
  return (
    <span data-testid="impact-memory" className="warn">
      Memory: {m.verdict}
      {m.basis ? <span className="basis"> ({m.basis})</span> : null}
    </span>
  );
}

/**
 * What keeping the draft does, every figure from the impact API (the tier's own cost and memory models and
 * `Pipeline.check`), plus Discard (the selected step back to the kept text, or every edit) and Keep. With no draft it states the kept pipeline's own cost.
 */
export function ImpactStrip({
  dirty,
  impact,
  loading,
  error,
  pending,
  stepLabel,
  stepChanged,
  onDiscardStep,
  onDiscard,
  onKeep,
  onPreview,
  previewing,
}: {
  dirty: boolean;
  impact: Impact | undefined;
  loading: boolean;
  error: string | null;
  pending: boolean;
  stepLabel: string | null;
  stepChanged: boolean;
  onDiscardStep: () => void;
  onDiscard: () => void;
  onKeep: () => void;
  onPreview: (() => void) | null;
  previewing: string | null;
}) {
  return (
    <div className="impact-strip" role="region" aria-label="Impact">
      {!dirty ? <span>No edits to keep. The kept pipeline:</span> : null}
      {error ? (
        <span className="bad">{error}</span>
      ) : !impact ? (
        <span>{loading ? "Estimating…" : ""}</span>
      ) : (
        <>
          <span data-testid="impact-seconds">
            {impact.seconds.value === null ? (
              <>
                Wall time: <span className="warn">cannot say</span>
              </>
            ) : (
              <>
                Wall time ≈ <b>{fmtSeconds(impact.seconds.value)}</b>
              </>
            )}{" "}
            <span className="basis">({impact.seconds.basis})</span>
          </span>
          <Memory m={impact.memory} />
          <span data-testid="impact-changes">
            {impact.changes.length ? `Changes ${impact.changes.join(", ")}` : "No step changes"}
          </span>
          {impact.new_digest ? <span>New input digest: a new run computes every step</span> : null}
          <span className="basis" data-testid="impact-poses">
            Estimates {POSES_FROM[impact.poses_from]}
          </span>
          {impact.poses_from === "none" && onPreview ? (
            <button
              type="button"
              onClick={onPreview}
              disabled={dirty || previewing !== null || impact.preview_task !== null}
              title={dirty ? "Keep or discard the draft first: a preview runs the kept config" : undefined}
            >
              {previewing || impact.preview_task ? "Previewing poses…" : "Preview poses"}
            </button>
          ) : null}
        </>
      )}
      <span className="grow" />
      <button type="button" disabled={!dirty || !stepChanged || pending} onClick={onDiscardStep}>
        Discard {stepLabel ? `${stepLabel} edits` : "this step"}
      </button>
      <button type="button" disabled={!dirty || pending} onClick={onDiscard}>
        Discard all
      </button>
      <button type="button" className="primary" disabled={!dirty || pending} onClick={onKeep}>
        Keep
      </button>
    </div>
  );
}
