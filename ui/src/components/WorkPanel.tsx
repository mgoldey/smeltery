import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useCapabilities } from "../api/capabilities";
import { api, unwrap, type Schemas } from "../api/client";

type Task = Schemas["Task"];
const LIVE = new Set(["queued", "running"]);
const QUEUED_HELP =
  "Launched runs share a fixed number of slots (one by default) so two heavy runs never compete for memory. This one " +
  "starts by itself when a slot frees, even if Bench restarts meanwhile.";
const STALLED_HELP =
  "No output for longer than the queue's threshold. A long SCF can legitimately print nothing, so this is a " +
  "place to look, not a verdict: the task is still running.";

function seconds(from: string | null | undefined, to: string | null | undefined, now: number): number | null {
  if (!from) return null;
  const end = to ? Date.parse(to) : now;
  return Math.max(0, Math.round((end - Date.parse(from)) / 1000));
}

function fmt(s: number | null): string {
  if (s === null) return "";
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min ${s % 60} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

/**
 * How long a running task has been silent, in seconds, counted from the browser's clock so it advances between
 * polls. `heartbeat_age_s` is the server's age at the moment it answered, so the time since that answer is added.
 */
function silenceFor(t: Task, now: number, fetchedAt: number): number | null {
  if (!LIVE.has(t.status) || t.heartbeat_age_s === null || t.heartbeat_age_s === undefined) return null;
  return Math.max(0, Math.round(t.heartbeat_age_s + (now - fetchedAt) / 1000));
}

/** The tasks the queue knows, refreshed while any is queued or running. A dead backend degrades this panel only. */
export function WorkPanel({ selected, onSelect }: { selected: string | null; onSelect: (id: string | null) => void }) {
  const [now, setNow] = useState(Date.now());
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: () => unwrap(api.GET("/api/tasks")),
    refetchInterval: (q) => ((q.state.data ?? []).some((t) => LIVE.has(t.status)) ? 1500 : 10_000),
  });
  const fetchedAt = tasks.dataUpdatedAt || Date.now();
  const live = (tasks.data ?? []).some((t) => LIVE.has(t.status));
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [live]);

  return (
    <section aria-labelledby="work-h">
      <h2 id="work-h">Work</h2>
      <p className="lede">
        Launched runs take turns: one runs at a time by default, and the rest wait as queued until it ends. Previews and
        other tasks start at once. Cancelling a queued run means it never starts. A task whose supervisor dies is reported
        failed, not retried: re-running a command is your call.
      </p>
      {tasks.isError ? (
        <div className="banner" role="alert">
          The task queue cannot be reached: {tasks.error.message}. Runs and results still work; this panel will recover
          when the queue does.
        </div>
      ) : tasks.data && tasks.data.length === 0 ? (
        <p className="empty-state">No tasks yet. Launch a run from a pipeline to see it here.</p>
      ) : (
        <table className="grid" aria-label="Tasks">
          <thead>
            <tr>
              <th>Task</th>
              <th>Status</th>
              <th className="num">Elapsed</th>
              <th className="num">Last output</th>
              <th className="num">Peak memory</th>
              <th className="num">Exit code</th>
            </tr>
          </thead>
          <tbody>
            {(tasks.data ?? []).map((t: Task) => (
              <tr key={t.id} className={t.id === selected ? "selected" : undefined} data-task={t.id}>
                <td>
                  <button type="button" className="link" onClick={() => onSelect(t.id)} aria-label={`Show task ${t.name}`}>
                    {t.name}
                  </button>
                </td>
                <td>
                  <span className="status" data-state={t.status}>
                    {t.status}
                  </span>
                  {(t.max_attempts ?? 1) > 1 ? ` attempt ${t.attempt ?? 1}/${t.max_attempts}` : ""}
                  {t.status === "queued" ? (
                    <span className="queued-hint" data-testid="queued-hint" title={QUEUED_HELP}>
                      {" "}
                      waiting for a run slot
                    </span>
                  ) : null}
                  {t.stalled ? (
                    <span className="status" data-state="stalled" data-testid="stalled" title={STALLED_HELP}>
                      stalled
                    </span>
                  ) : null}
                  {t.oom ? (
                    <span className="status" data-state="oom" data-testid="oom" title={t.note}>
                      OOM
                    </span>
                  ) : null}
                </td>
                <td className="num" data-testid="elapsed">
                  {fmt(seconds(t.started_at ?? t.created_at, t.finished_at, now))}
                </td>
                <td className="num" data-testid="silence">
                  {fmt(silenceFor(t, now, fetchedAt))}
                </td>
                <td className="num" data-testid="peak-rss">
                  {t.peak_rss_gb === null || t.peak_rss_gb === undefined ? "" : `${t.peak_rss_gb.toFixed(2)} GB`}
                </td>
                <td className="num">{t.returncode ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** One task: its command, its log tail (as text, never HTML), and cancel while it is live. */
export function TaskInspector({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => unwrap(api.GET("/api/tasks")) });
  const task = tasks.data?.find((t) => t.id === id);
  const log = useQuery({
    queryKey: ["task-log", id],
    queryFn: () => unwrap(api.GET("/api/tasks/{task_id}/log", { params: { path: { task_id: id }, query: { lines: 200 } } })),
    refetchInterval: task && LIVE.has(task.status) ? 1500 : false,
  });
  const { readOnly } = useCapabilities();
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/tasks/{task_id}/cancel", { params: { path: { task_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tasks"] }),
  });
  return (
    <aside className="inspector" aria-label="Task">
      <h2 style={{ marginTop: 0 }}>{task?.name ?? id}</h2>
      {task ? (
        <>
          <p className="mono">{task.command.join(" ")}</p>
          <p>
            <span className="status" data-state={task.status}>
              {task.status}
            </span>
            {(task.max_attempts ?? 1) > 1 ? ` attempt ${task.attempt ?? 1}/${task.max_attempts}` : ""}
          </p>
          {task.status === "queued" ? (
            <p className="lede" data-testid="queued-help">
              Waiting for a run slot. {QUEUED_HELP} Cancelling it now means it never starts.
            </p>
          ) : null}
          <p className="lede" data-testid="task-health">
            {task.peak_rss_gb === null || task.peak_rss_gb === undefined
              ? "Peak memory not sampled" +
                (task.memory_limit_gb ? `; limit ${task.memory_limit_gb.toFixed(2)} GB.` : ".")
              : `Peak memory ${task.peak_rss_gb.toFixed(2)} GB` +
                (task.memory_limit_gb ? ` of a ${task.memory_limit_gb.toFixed(2)} GB limit.` : ", no limit set.")}
            {task.oom ? ` ${task.note}` : ""}
            {task.stalled ? ` No output for ${fmt(Math.round(task.heartbeat_age_s ?? 0))}. ${STALLED_HELP}` : ""}
            {task.returncode !== null && task.returncode !== undefined ? ` with exit code ${task.returncode}` : ""}
          </p>
          {LIVE.has(task.status) && !readOnly ? (
            <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              Cancel task
            </button>
          ) : null}
          {cancel.isError ? <p className="field-error">{cancel.error.message}</p> : null}
        </>
      ) : null}
      <h3>Log</h3>
      <pre className="log" data-testid="task-log">
        {log.data?.log ?? (log.isError ? log.error.message : "")}
      </pre>
      <button type="button" onClick={onClose}>
        Close
      </button>
    </aside>
  );
}
